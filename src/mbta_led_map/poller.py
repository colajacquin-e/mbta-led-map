"""Async MBTA Vehicle API poller.

Polls ``GET /vehicles`` every :data:`config.POLL_INTERVAL_SECONDS` seconds and
yields lists of :class:`~models.Vehicle` objects to registered async callbacks.
Uses the ``If-Modified-Since`` header so unchanged responses don't re-trigger
state recomputation.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from typing import Optional

import httpx

from . import config
from .models import Vehicle

log = logging.getLogger(__name__)

MBTA_BASE = "https://api-v3.mbta.com"
MBTA_ROUTES = "Red,Orange,Blue,Green-B,Green-C,Green-D,Green-E,Mattapan"

# Type alias for the callback consumers register.
VehicleCallback = Callable[[list[Vehicle]], Awaitable[None]]


class MbtaPoller:
    """Polls the MBTA API and dispatches parsed Vehicle lists to callbacks.

    Usage::

        poller = MbtaPoller()
        poller.register(my_async_callback)
        asyncio.create_task(poller.run())
    """

    def __init__(self) -> None:
        self._callbacks: list[VehicleCallback] = []
        self._last_modified: Optional[str] = None
        self._last_vehicles: list[Vehicle] = []

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def register(self, callback: VehicleCallback) -> None:
        """Register an async callback to receive vehicle lists on each poll."""
        self._callbacks.append(callback)

    async def run(self) -> None:
        """Blocking poll loop — run this as an asyncio Task."""
        headers: dict[str, str] = {}
        if config.MBTA_API_KEY:
            headers["x-api-key"] = config.MBTA_API_KEY

        async with httpx.AsyncClient(timeout=15) as client:
            while True:
                try:
                    await self._poll_once(client, headers)
                except Exception:  # noqa: BLE001
                    log.exception("Unexpected error in poll loop")
                await asyncio.sleep(config.POLL_INTERVAL_SECONDS)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _poll_once(self, client: httpx.AsyncClient, base_headers: dict[str, str]) -> None:
        """Perform a single API call and notify callbacks if data changed."""
        request_headers = dict(base_headers)
        if self._last_modified:
            request_headers["If-Modified-Since"] = self._last_modified

        try:
            resp = await client.get(
                f"{MBTA_BASE}/vehicles",
                params={"filter[route]": MBTA_ROUTES, "include": "stop"},
                headers=request_headers,
            )
        except httpx.RequestError as exc:
            log.error("MBTA API request error: %s", exc)
            return

        if resp.status_code == 304:
            log.debug("MBTA API 304 Not Modified — reusing cached vehicles")
            # Still notify callbacks so the frontend stays live.
            await self._dispatch(self._last_vehicles)
            return

        try:
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            log.error("MBTA API HTTP error: %s", exc)
            return

        # Cache the Last-Modified header for next poll.
        lm = resp.headers.get("Last-Modified")
        if lm:
            self._last_modified = lm

        payload = resp.json()
        vehicles = self._parse_vehicles(payload)
        self._last_vehicles = vehicles

        log.info(
            "Polled MBTA API at %s → %d vehicles",
            datetime.now(timezone.utc).isoformat(),
            len(vehicles),
        )
        await self._dispatch(vehicles)

    def _parse_vehicles(self, payload: dict) -> list[Vehicle]:
        """Parse raw MBTA API JSON into Vehicle objects.

        Handles platform-ID → parent-station-ID resolution via the ``included``
        stop records so callers always get a canonical stop ID.
        """
        # Build platform→parent map from included stops.
        stop_parent: dict[str, str] = {}
        for stop in payload.get("included", []):
            if stop.get("type") != "stop":
                continue
            parent = (
                stop.get("relationships", {})
                .get("parent_station", {})
                .get("data") or {}
            ).get("id")
            if parent:
                stop_parent[stop["id"]] = parent

        vehicles: list[Vehicle] = []
        for raw in payload.get("data", []):
            try:
                vehicle = self._parse_one(raw, stop_parent)
            except Exception:  # noqa: BLE001
                log.debug("Failed to parse vehicle: %s", raw.get("id"))
                continue
            if vehicle is not None:
                vehicles.append(vehicle)
        return vehicles

    @staticmethod
    def _parse_one(raw: dict, stop_parent: dict[str, str]) -> Optional[Vehicle]:
        """Parse a single vehicle dict; return None if required fields are missing."""
        attrs = raw.get("attributes", {})
        rels = raw.get("relationships", {})

        vehicle_id = raw.get("id", "")
        if not vehicle_id:
            return None

        route_id = (rels.get("route", {}).get("data") or {}).get("id", "")
        if not route_id:
            return None

        direction_id = attrs.get("direction_id")
        if direction_id is None:
            return None

        current_status = attrs.get("current_status", "")
        if not current_status:
            return None

        platform_stop_id = (rels.get("stop", {}).get("data") or {}).get("id", "")
        # Resolve platform stop to parent station where available.
        stop_id: Optional[str] = stop_parent.get(platform_stop_id, platform_stop_id) or None

        lat = attrs.get("latitude")
        lon = attrs.get("longitude")

        return Vehicle(
            id=vehicle_id,
            route_id=route_id,
            direction_id=int(direction_id),
            stop_id=stop_id,
            current_status=current_status,
            latitude=float(lat) if lat is not None else None,
            longitude=float(lon) if lon is not None else None,
            label=attrs.get("label"),
        )

    async def _dispatch(self, vehicles: list[Vehicle]) -> None:
        """Call all registered callbacks with the vehicle list."""
        for cb in self._callbacks:
            try:
                await cb(vehicles)
            except Exception:  # noqa: BLE001
                log.exception("Error in poller callback %s", cb)
