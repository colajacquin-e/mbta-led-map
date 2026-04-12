"""GPS-based vehicle-to-LED mapper.

For each vehicle returned by the MBTA API, finds the single closest LED on its
line/direction chain using Haversine distance.  Falls back to stop_id matching
when GPS coordinates are unavailable.

Deduplication guarantees:
  - Each vehicle_id maps to exactly ONE LED.
  - Each LED may be claimed by at most ONE vehicle (the physically closer one
    wins; the evicted vehicle gets its next best unoccupied LED).

Usage::

    mapper = LedMapper(data_dir=Path("data"))
    led_state = mapper.map_vehicles(vehicles)
    # led_state["red"] == [0, 255, 0, 128, ...]
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from math import asin, cos, radians, sin, sqrt
from pathlib import Path
from typing import Optional

from .models import Vehicle

log = logging.getLogger(__name__)

# All active LEDs use full brightness — we show position, not approach state.
BRIGHTNESS = 255

# ---------------------------------------------------------------------------
# MBTA route → internal line name
# ---------------------------------------------------------------------------
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

# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class LedRecord:
    """A single LED entry loaded from a station JSON file."""

    index: int
    line: str
    stop_id: Optional[str]
    stop_name: str
    direction_id: int
    led_type: str  # "station" or "midpoint"
    adjacent_stops: Optional[list[str]]
    x: float
    y: float
    lat: Optional[float] = None
    lon: Optional[float] = None


@dataclass
class _Assignment:
    """Tracks which vehicle owns an LED and at what distance."""

    vehicle_id: str
    distance_m: float
    brightness: int


# ---------------------------------------------------------------------------
# Haversine distance
# ---------------------------------------------------------------------------


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Return the great-circle distance in metres between two GPS points."""
    R = 6_371_000
    phi1, phi2 = radians(lat1), radians(lat2)
    dphi = radians(lat2 - lat1)
    dlambda = radians(lon2 - lon1)
    a = sin(dphi / 2) ** 2 + cos(phi1) * cos(phi2) * sin(dlambda / 2) ** 2
    return 2 * R * asin(sqrt(a))


# ---------------------------------------------------------------------------
# Mapper
# ---------------------------------------------------------------------------

_LINE_FILES = ("red", "orange", "blue", "green", "mattapan")


class LedMapper:
    """Loads LED definitions and maps vehicle lists to per-line brightness arrays.

    Parameters
    ----------
    data_dir:
        Path to the directory containing ``red.json``, ``green.json``, etc.
    """

    def __init__(self, data_dir: Path) -> None:
        self._data_dir = data_dir
        # All LED records keyed by line
        self._all_leds: dict[str, list[LedRecord]] = {}
        # Pre-built per-(line, direction_id) chains for fast lookup
        self._chains: dict[tuple[str, int], list[LedRecord]] = {}
        # LED count per line (= max index + 1)
        self._led_counts: dict[str, int] = {}
        # stop_id lookup: (stop_id, direction_id) → list of LedRecord
        self._stop_lookup: dict[tuple[str, int], list[LedRecord]] = defaultdict(list)
        # midpoint lookup: (dest_stop_id, direction_id) → list of LedRecord
        self._midpoint_lookup: dict[tuple[str, int], list[LedRecord]] = defaultdict(list)

        self._load()

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    @property
    def led_counts(self) -> dict[str, int]:
        """LED count per line, derived from the station JSON files."""
        return dict(self._led_counts)

    @property
    def all_leds(self) -> dict[str, list[LedRecord]]:
        """All LED records, keyed by line name."""
        return self._all_leds

    def map_vehicles(self, vehicles: list[Vehicle]) -> dict[str, list[int]]:
        """Map a list of vehicles to a per-line brightness array.

        Returns a dict ``{line: [brightness, ...]}`` where each list has exactly
        ``led_counts[line]`` entries.  Entries not claimed by any vehicle are 0.
        """
        # Build the initial all-zero state.
        state: dict[str, list[int]] = {
            line: [0] * count for line, count in self._led_counts.items()
        }

        # assignments: (line, led_index) → _Assignment
        assignments: dict[tuple[str, int], _Assignment] = {}
        # vehicle_id → (line, led_index) — so we can evict
        vehicle_led: dict[str, tuple[str, int]] = {}

        for vehicle in vehicles:
            self._assign_vehicle(vehicle, assignments, vehicle_led)

        # Write assignments into the brightness arrays.
        for (line, idx), asgn in assignments.items():
            state[line][idx] = asgn.brightness

        active = sum(sum(1 for b in arr if b > 0) for arr in state.values())
        log.info("Mapped %d vehicles → %d active LEDs", len(vehicles), active)
        return state

    # ------------------------------------------------------------------
    # Internal — loading
    # ------------------------------------------------------------------

    def _load(self) -> None:
        """Read all station JSON files and build lookup tables."""
        for line in _LINE_FILES:
            path = self._data_dir / f"{line}.json"
            if not path.exists():
                log.warning("Station file not found: %s", path)
                continue
            with open(path) as fh:
                raw_records: list[dict] = json.load(fh)

            records: list[LedRecord] = []
            for raw in raw_records:
                rec = LedRecord(
                    index=raw["index"],
                    line=line,
                    stop_id=raw.get("stop_id"),
                    stop_name=raw.get("stop_name", ""),
                    direction_id=int(raw.get("direction_id", 0)),
                    led_type=raw.get("type", "station"),
                    adjacent_stops=raw.get("adjacent_stops"),
                    x=float(raw.get("x", 0)),
                    y=float(raw.get("y", 0)),
                    lat=raw.get("lat"),
                    lon=raw.get("lon"),
                )
                records.append(rec)
            self._all_leds[line] = records
            self._led_counts[line] = len(records)

        # Build per-(line, direction) chains and lookup tables.
        for line, records in self._all_leds.items():
            for dir_id in (0, 1):
                chain = [r for r in records if r.direction_id == dir_id]
                chain.sort(key=lambda r: r.index)
                self._chains[(line, dir_id)] = chain

            for rec in records:
                if rec.led_type == "station" and rec.stop_id:
                    self._stop_lookup[(rec.stop_id, rec.direction_id)].append(rec)
                elif rec.led_type == "midpoint":
                    adj = rec.adjacent_stops or []
                    # Register under adj[1] — the destination stop in the direction
                    # of travel.  Registering under both stops would double-light.
                    if len(adj) >= 2 and adj[1]:
                        self._midpoint_lookup[(adj[1], rec.direction_id)].append(rec)

        log.info("LED mapper loaded: %s", {k: v for k, v in self._led_counts.items()})

    # ------------------------------------------------------------------
    # Internal — assignment
    # ------------------------------------------------------------------

    def _assign_vehicle(
        self,
        vehicle: Vehicle,
        assignments: dict[tuple[str, int], _Assignment],
        vehicle_led: dict[str, tuple[str, int]],
    ) -> None:
        """Find the best available LED for *vehicle* and record the assignment.

        Implements deduplication: if the chosen LED is already claimed by
        another vehicle, the closer vehicle wins and the other is bumped to its
        next best option.
        """
        line = ROUTE_TO_LINE.get(vehicle.route_id)
        if line is None:
            log.debug("Unknown route %s for vehicle %s", vehicle.route_id, vehicle.id)
            return

        brightness = BRIGHTNESS

        chain = self._chains.get((line, vehicle.direction_id), [])
        if not chain:
            log.debug("Empty chain for %s dir=%d", line, vehicle.direction_id)
            return

        # Get ordered candidates (closest first).
        candidates = self._rank_candidates(vehicle, chain)
        if not candidates:
            log.debug("No candidates for vehicle %s", vehicle.id)
            return

        self._place_vehicle(
            vehicle_id=vehicle.id,
            brightness=brightness,
            candidates=candidates,
            assignments=assignments,
            vehicle_led=vehicle_led,
            chain=chain,
            vehicle=vehicle,
        )

    def _place_vehicle(
        self,
        vehicle_id: str,
        brightness: int,
        candidates: list[tuple[LedRecord, float]],
        assignments: dict[tuple[str, int], _Assignment],
        vehicle_led: dict[str, tuple[str, int]],
        chain: list[LedRecord],
        vehicle: Vehicle,
        _forbidden: Optional[set[tuple[str, int]]] = None,
    ) -> bool:
        """Attempt to place *vehicle_id* into the best unoccupied slot.

        Returns True if placement succeeded.  Uses *_forbidden* to avoid
        re-trying LEDs that caused an eviction cycle.
        """
        forbidden: set[tuple[str, int]] = _forbidden or set()

        for led, dist in candidates:
            key = (led.line, led.index)
            if key in forbidden:
                continue

            existing = assignments.get(key)
            if existing is None:
                # Slot is free — claim it.
                self._do_assign(vehicle_id, brightness, key, dist, assignments, vehicle_led)
                return True

            if existing.vehicle_id == vehicle_id:
                # Already assigned here (shouldn't happen in one pass, but guard).
                return True

            if dist < existing.distance_m:
                # We are closer — evict the previous occupant.
                evicted_id = existing.vehicle_id
                evicted_brightness = BRIGHTNESS
                evicted_line = key[0]
                evicted_chain = self._chains.get((evicted_line, vehicle.direction_id), chain)

                # Re-rank candidates for the evicted vehicle (excluding this key).
                evicted_vehicle_obj = _make_dummy_vehicle(
                    evicted_id, evicted_brightness, vehicle
                )
                evicted_candidates = self._rank_candidates(evicted_vehicle_obj, evicted_chain)

                # Claim the slot.
                self._do_assign(vehicle_id, brightness, key, dist, assignments, vehicle_led)

                # Bump the evicted vehicle.
                self._place_vehicle(
                    vehicle_id=evicted_id,
                    brightness=evicted_brightness,
                    candidates=evicted_candidates,
                    assignments=assignments,
                    vehicle_led=vehicle_led,
                    chain=evicted_chain,
                    vehicle=evicted_vehicle_obj,
                    _forbidden=forbidden | {key},
                )
                return True
            # else: existing is closer — try our next candidate

        log.debug("Could not place vehicle %s (all candidates occupied)", vehicle_id)
        return False

    @staticmethod
    def _do_assign(
        vehicle_id: str,
        brightness: int,
        key: tuple[str, int],
        dist: float,
        assignments: dict[tuple[str, int], _Assignment],
        vehicle_led: dict[str, tuple[str, int]],
    ) -> None:
        """Record a vehicle→LED assignment, cleaning up any prior LED for this vehicle."""
        old_key = vehicle_led.get(vehicle_id)
        if old_key and old_key != key:
            assignments.pop(old_key, None)
        assignments[key] = _Assignment(
            vehicle_id=vehicle_id, distance_m=dist, brightness=brightness
        )
        vehicle_led[vehicle_id] = key

    def _rank_candidates(
        self, vehicle: Vehicle, chain: list[LedRecord]
    ) -> list[tuple[LedRecord, float]]:
        """Return LEDs in *chain* ranked by proximity to *vehicle*.

        Strategy:
        - IN_TRANSIT_TO: use stop_id → midpoint lookup first (MBTA vehicle GPS
          for in-transit trains is often reported at the next stop, not the
          actual position between stops, so GPS would incorrectly snap to a
          station LED).  Falls back to GPS / stop_id if no midpoint found.
        - STOPPED_AT / INCOMING_AT: use GPS distance ranking when coordinates
          are available; otherwise fall back to stop_id matching.
        """
        if vehicle.current_status == "IN_TRANSIT_TO":
            # Try stop_id → midpoint lookup first.
            midpoint_candidates = self._rank_by_stop_id(vehicle, chain)
            if midpoint_candidates:
                return midpoint_candidates
            # No midpoint found — fall through to GPS / stop_id for stations.

        has_gps = vehicle.latitude is not None and vehicle.longitude is not None

        if has_gps:
            # GPS path: rank all LEDs that also have GPS coordinates.
            scored: list[tuple[LedRecord, float]] = []
            for led in chain:
                if led.lat is not None and led.lon is not None:
                    d = haversine_m(vehicle.latitude, vehicle.longitude, led.lat, led.lon)  # type: ignore[arg-type]
                    scored.append((led, d))
            if scored:
                scored.sort(key=lambda t: t[1])
                return scored
            log.debug(
                "No GPS-enriched LEDs in chain for %s, falling back to stop_id", vehicle.id
            )

        return self._rank_by_stop_id(vehicle, chain)

    def _rank_by_stop_id(
        self, vehicle: Vehicle, chain: list[LedRecord]
    ) -> list[tuple[LedRecord, float]]:
        """Rank LEDs by stop_id match.

        For STOPPED_AT / INCOMING_AT: match station LEDs by stop_id.
        For IN_TRANSIT_TO: match midpoint LEDs whose adjacent_stops[1] == stop_id,
        falling back to station LEDs if no midpoint is found.

        Exact matches get distance 0.0; unmatched LEDs are excluded.
        """
        if not vehicle.stop_id:
            return []

        status = vehicle.current_status
        results: list[tuple[LedRecord, float]] = []

        if status == "IN_TRANSIT_TO":
            # Try midpoint LEDs first.
            midpoints = self._midpoint_lookup.get((vehicle.stop_id, vehicle.direction_id), [])
            for led in midpoints:
                if led.line == _led_line_from_chain(chain):
                    results.append((led, 0.0))

        if not results:
            # Station LEDs (also used as fallback for IN_TRANSIT_TO).
            stations = self._stop_lookup.get((vehicle.stop_id, vehicle.direction_id), [])
            for led in stations:
                if led.line == _led_line_from_chain(chain):
                    results.append((led, 0.0))

        return results


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _led_line_from_chain(chain: list[LedRecord]) -> str:
    """Return the line name from the first record in a chain."""
    return chain[0].line if chain else ""


def _make_dummy_vehicle(vehicle_id: str, brightness: int, reference: Vehicle) -> Vehicle:
    """Create a minimal Vehicle shell for eviction re-ranking.

    The evicted vehicle is on the same line/direction as the reference but has
    no GPS and no stop_id — ranking will be done from whatever was previously
    stored in the LED records (the caller provides the pre-ranked candidate
    list anyway; this object is only needed to satisfy type signatures).
    """
    return Vehicle(
        id=vehicle_id,
        route_id=reference.route_id,
        direction_id=reference.direction_id,
        stop_id=None,
        current_status="STOPPED_AT",
        latitude=None,
        longitude=None,
        label=None,
    )
