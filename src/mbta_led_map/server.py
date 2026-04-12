"""MBTA LED Map — FastAPI server (proof-of-concept)."""

from __future__ import annotations

import asyncio
import json
import logging
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

load_dotenv()

import os

log = logging.getLogger("mbta_led_map")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = PROJECT_ROOT / "data"
STATIC_DIR = Path(__file__).resolve().parent / "static"

# ---------------------------------------------------------------------------
# Load LED data at import time so it is available before the app starts
# ---------------------------------------------------------------------------
LINE_FILES = {
    "red": DATA_DIR / "red.json",
    "orange": DATA_DIR / "orange.json",
    "blue": DATA_DIR / "blue.json",
    "green": DATA_DIR / "green.json",
    "mattapan": DATA_DIR / "mattapan.json",
}

# Full LED records keyed by line
LED_DATA: dict[str, list[dict]] = {}
# LED count per line (derived from max index + 1)
LED_COUNTS: dict[str, int] = {}

for _line, _path in LINE_FILES.items():
    with open(_path) as _f:
        records = json.load(_f)
    LED_DATA[_line] = records
    LED_COUNTS[_line] = len(records)

# ---------------------------------------------------------------------------
# Lookup tables
# ---------------------------------------------------------------------------

# Station LEDs: (stop_id, direction_id) -> [(line, led_index), ...]
STOP_LOOKUP: dict[tuple[str, int], list[tuple[str, int]]] = defaultdict(list)

# Midpoint LEDs: (adjacent_stop_id, direction_id) -> [(line, led_index), ...]
# A train IN_TRANSIT_TO stop X in direction D lights the midpoint whose
# adjacent_stops contains X and whose direction_id matches D.
MIDPOINT_LOOKUP: dict[tuple[str, int], list[tuple[str, int]]] = defaultdict(list)

for _line, _records in LED_DATA.items():
    for _rec in _records:
        if _rec.get("type") == "station" and _rec.get("stop_id"):
            key = (_rec["stop_id"], _rec["direction_id"])
            STOP_LOOKUP[key].append((_line, _rec["index"]))
        elif _rec.get("type") == "midpoint":
            adj = _rec.get("adjacent_stops") or []
            # Only register under adjacent_stops[1] — the destination stop in
            # the direction of travel (midpoints are named "[from] – [to]").
            # Registering under both stops would light two midpoints per vehicle.
            if len(adj) >= 2 and adj[1]:
                key = (adj[1], _rec["direction_id"])
                MIDPOINT_LOOKUP[key].append((_line, _rec["index"]))

# MBTA route → internal line name
ROUTE_TO_LINE: dict[str, str] = {
    "Red": "red",
    "Orange": "orange",
    "Blue": "blue",
    "Green-B": "green",
    "Green-C": "green",
    "Green-D": "green",
    "Green-E": "green",
    "Mattapan": "mattapan",
}

BRIGHTNESS = {
    "STOPPED_AT": 255,
    "INCOMING_AT": 180,
    "IN_TRANSIT_TO": 128,
}

POLL_INTERVAL = 10  # seconds
MBTA_BASE = "https://api-v3.mbta.com"
MBTA_ROUTES = "Red,Orange,Blue,Green-B,Green-C,Green-D,Green-E,Mattapan"

# ---------------------------------------------------------------------------
# Shared mutable state
# ---------------------------------------------------------------------------
_led_state: dict[str, list[int]] = {line: [0] * count for line, count in LED_COUNTS.items()}
_connected_clients: list[WebSocket] = []
_last_updated: str = ""
_vehicle_count: int = 0

# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------
app = FastAPI(title="MBTA LED Map")

STATIC_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


# ---------------------------------------------------------------------------
# REST endpoints
# ---------------------------------------------------------------------------

@app.get("/")
async def serve_index() -> FileResponse:
    return FileResponse(str(STATIC_DIR / "index.html"))


@app.get("/api/leds")
async def get_leds() -> JSONResponse:
    """Return full LED layout (positions + metadata) for all lines."""
    payload: dict[str, Any] = {}
    for line, records in LED_DATA.items():
        payload[line] = records
    return JSONResponse(payload)


@app.get("/api/status")
async def get_status() -> JSONResponse:
    return JSONResponse({"last_updated": _last_updated, "led_counts": LED_COUNTS})


# ---------------------------------------------------------------------------
# WebSocket
# ---------------------------------------------------------------------------

@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket) -> None:
    await ws.accept()
    _connected_clients.append(ws)
    log.info("WS client connected — total %d", len(_connected_clients))
    try:
        # Send current state immediately on connect
        await ws.send_text(_build_message())
        while True:
            # Keep the connection alive; we push on poll cycle
            await asyncio.sleep(1)
    except WebSocketDisconnect:
        pass
    except Exception as exc:  # noqa: BLE001
        log.warning("WS error: %s", exc)
    finally:
        _connected_clients.remove(ws)
        log.info("WS client disconnected — total %d", len(_connected_clients))


# ---------------------------------------------------------------------------
# Background polling
# ---------------------------------------------------------------------------

def _build_message() -> str:
    lines_payload: dict[str, Any] = {}
    for line, brightness_list in _led_state.items():
        lines_payload[line] = {
            "leds": brightness_list,
            "count": LED_COUNTS[line],
        }
    msg = {
        "timestamp": _last_updated or datetime.now(timezone.utc).isoformat(),
        "vehicle_count": _vehicle_count,
        "lines": lines_payload,
    }
    return json.dumps(msg)


async def _broadcast(message: str) -> None:
    dead: list[WebSocket] = []
    for ws in list(_connected_clients):
        try:
            await ws.send_text(message)
        except Exception:  # noqa: BLE001
            dead.append(ws)
    for ws in dead:
        try:
            _connected_clients.remove(ws)
        except ValueError:
            pass


async def _poll_mbta() -> None:
    """Continuously poll the MBTA API and update _led_state."""
    global _last_updated, _vehicle_count

    api_key = os.getenv("MBTA_API_KEY", "")
    headers = {"x-api-key": api_key} if api_key else {}

    async with httpx.AsyncClient(timeout=15) as client:
        while True:
            try:
                resp = await client.get(
                    f"{MBTA_BASE}/vehicles",
                    params={"filter[route]": MBTA_ROUTES, "include": "stop"},
                    headers=headers,
                )
                resp.raise_for_status()
                payload = resp.json()
                vehicles = payload.get("data", [])
                # Build platform→parent map from included stops
                stop_parent: dict[str, str] = {}
                for stop in payload.get("included", []):
                    if stop.get("type") != "stop":
                        continue
                    parent = (stop.get("relationships", {})
                              .get("parent_station", {})
                              .get("data") or {}).get("id")
                    if parent:
                        stop_parent[stop["id"]] = parent

                # Reset state
                new_state: dict[str, list[int]] = {
                    line: [0] * count for line, count in LED_COUNTS.items()
                }

                for vehicle in vehicles:
                    attrs = vehicle.get("attributes", {})
                    status = attrs.get("current_status", "")
                    direction_id = attrs.get("direction_id")
                    route_id = (
                        vehicle.get("relationships", {})
                        .get("route", {})
                        .get("data", {})
                        .get("id", "")
                    )
                    stop_id = (
                        vehicle.get("relationships", {})
                        .get("stop", {})
                        .get("data", {})
                        or {}
                    ).get("id", "")

                    if not stop_id or direction_id is None:
                        continue

                    brightness = BRIGHTNESS.get(status)
                    if brightness is None:
                        continue

                    line = ROUTE_TO_LINE.get(route_id)
                    if line is None:
                        continue

                    # Build fallback list: platform ID → parent station ID
                    stop_ids_to_try = [stop_id]
                    parent_id = stop_parent.get(stop_id)
                    if parent_id:
                        stop_ids_to_try.append(parent_id)

                    # IN_TRANSIT_TO → light the midpoint between previous and
                    # next stop; fall back to the station LED if no midpoint found.
                    candidates = []
                    for sid in stop_ids_to_try:
                        if status == "IN_TRANSIT_TO":
                            candidates = MIDPOINT_LOOKUP.get((sid, direction_id), [])
                            if not candidates:
                                candidates = STOP_LOOKUP.get((sid, direction_id), [])
                        else:
                            candidates = STOP_LOOKUP.get((sid, direction_id), [])
                        if candidates:
                            break

                    for led_line, led_index in candidates:
                        if led_line == line:
                            if new_state[led_line][led_index] < brightness:
                                new_state[led_line][led_index] = brightness

                # Commit new state
                for line in _led_state:
                    _led_state[line] = new_state[line]

                _last_updated = datetime.now(timezone.utc).isoformat()
                _vehicle_count = len(vehicles)
                active_leds = sum(sum(1 for b in arr if b > 0) for arr in new_state.values())
                log.info(
                    "Polled MBTA: %d vehicles → %d LEDs lit", _vehicle_count, active_leds
                )
                # Debug: log unmatched vehicles
                for vehicle in vehicles:
                    attrs = vehicle.get("attributes", {})
                    status = attrs.get("current_status", "")
                    direction_id = attrs.get("direction_id")
                    route_id = vehicle.get("relationships", {}).get("route", {}).get("data", {}).get("id", "")
                    stop_id = (vehicle.get("relationships", {}).get("stop", {}).get("data") or {}).get("id", "")
                    line = ROUTE_TO_LINE.get(route_id)
                    if not line or not stop_id or direction_id is None:
                        continue
                    if status == "IN_TRANSIT_TO":
                        hits = [x for x in MIDPOINT_LOOKUP.get((stop_id, direction_id), []) if x[0] == line]
                    else:
                        hits = [x for x in STOP_LOOKUP.get((stop_id, direction_id), []) if x[0] == line]
                    if not hits:
                        log.debug("UNMATCHED %s %s dir=%s stop=%s → no LED", route_id, status, direction_id, stop_id)

                await _broadcast(_build_message())

            except httpx.HTTPStatusError as exc:
                log.error("MBTA API HTTP error: %s", exc)
            except Exception as exc:  # noqa: BLE001
                log.error("Polling error: %s", exc)

            await asyncio.sleep(POLL_INTERVAL)


@app.on_event("startup")
async def startup() -> None:
    log.info("LED counts: %s", LED_COUNTS)
    asyncio.create_task(_poll_mbta())
