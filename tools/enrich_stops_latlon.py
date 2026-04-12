#!/usr/bin/env python3
"""Enrich station JSON files with GPS lat/lon coordinates.

For each LED entry:
  - If ``type == "station"`` and ``stop_id`` is set: fetches lat/lon from the
    MBTA Stops API (batched — all stop IDs in one request).
  - If ``type == "midpoint"``: linearly interpolates lat/lon between the two
    adjacent stops (which must already have been enriched or fetched in the
    same run).
  - Entries that already have both ``lat`` and ``lon`` are skipped (idempotent).

The JSON files are written back in place.

Usage::

    python tools/enrich_stops_latlon.py [--api-key KEY] [--dry-run]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
from pathlib import Path

import httpx
from dotenv import load_dotenv

load_dotenv()

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

MBTA_BASE = "https://api-v3.mbta.com"

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = PROJECT_ROOT / "data"

LINE_FILES = {
    "red": DATA_DIR / "red.json",
    "orange": DATA_DIR / "orange.json",
    "blue": DATA_DIR / "blue.json",
    "green": DATA_DIR / "green.json",
    "mattapan": DATA_DIR / "mattapan.json",
}

# MBTA API limits filter[id] query string length; batch at most this many IDs
# per request to stay well under URL length limits.
BATCH_SIZE = 200


# ---------------------------------------------------------------------------
# API fetch — batched
# ---------------------------------------------------------------------------

async def fetch_all_stops(
    client: httpx.AsyncClient,
    stop_ids: list[str],
    api_key: str,
) -> dict[str, tuple[float, float]]:
    """Fetch lat/lon for *all* stop_ids in as few requests as possible.

    Returns a mapping ``{stop_id: (lat, lon)}``.
    """
    headers: dict[str, str] = {}
    if api_key:
        headers["x-api-key"] = api_key

    cache: dict[str, tuple[float, float]] = {}

    # Deduplicate and split into batches.
    unique_ids = list(dict.fromkeys(stop_ids))  # preserve order, deduplicate
    batches = [
        unique_ids[i : i + BATCH_SIZE] for i in range(0, len(unique_ids), BATCH_SIZE)
    ]

    log.info("Fetching %d unique stop IDs in %d batch(es)…", len(unique_ids), len(batches))

    for batch_num, batch in enumerate(batches, start=1):
        params = {"filter[id]": ",".join(batch)}
        url = f"{MBTA_BASE}/stops"
        try:
            resp = await client.get(url, params=params, headers=headers)
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            log.error("HTTP error fetching stops batch %d: %s", batch_num, exc)
            continue
        except httpx.RequestError as exc:
            log.error("Request error fetching stops batch %d: %s", batch_num, exc)
            continue

        stops_data = resp.json().get("data", [])
        for stop in stops_data:
            sid = stop.get("id")
            attrs = stop.get("attributes", {})
            lat = attrs.get("latitude")
            lon = attrs.get("longitude")
            if sid and lat is not None and lon is not None:
                cache[sid] = (float(lat), float(lon))
                log.debug("  stop %s → (%.6f, %.6f)", sid, float(lat), float(lon))

        log.info(
            "  Batch %d/%d: received %d stops (cache now has %d)",
            batch_num,
            len(batches),
            len(stops_data),
            len(cache),
        )

    # Report any stop IDs that the API didn't return.
    missing = [sid for sid in unique_ids if sid not in cache]
    if missing:
        log.warning("%d stop IDs not found in API response: %s", len(missing), missing)

    return cache


# ---------------------------------------------------------------------------
# Interpolation
# ---------------------------------------------------------------------------

def interpolate_latlon(
    stop_a: str,
    stop_b: str,
    cache: dict[str, tuple[float, float]],
) -> tuple[float, float] | None:
    """Return the midpoint between two stops' lat/lon coordinates."""
    pos_a = cache.get(stop_a)
    pos_b = cache.get(stop_b)
    if pos_a is None or pos_b is None:
        return None
    lat = (pos_a[0] + pos_b[0]) / 2.0
    lon = (pos_a[1] + pos_b[1]) / 2.0
    return lat, lon


# ---------------------------------------------------------------------------
# Main enrichment logic
# ---------------------------------------------------------------------------

def collect_stop_ids(all_records: dict[str, list[dict]]) -> list[str]:
    """Return all stop_ids that need fetching (not yet enriched)."""
    ids: list[str] = []
    for records in all_records.values():
        for rec in records:
            if rec.get("lat") is not None and rec.get("lon") is not None:
                continue
            if rec.get("type") == "station" and rec.get("stop_id"):
                ids.append(rec["stop_id"])
    return ids


def seed_cache_from_records(
    all_records: dict[str, list[dict]],
) -> dict[str, tuple[float, float]]:
    """Pre-populate cache from records that already have lat/lon.

    This ensures midpoints can interpolate even if their adjacent stops were
    enriched in a previous run (and therefore aren't re-fetched this run).
    """
    cache: dict[str, tuple[float, float]] = {}
    for records in all_records.values():
        for rec in records:
            sid = rec.get("stop_id")
            lat = rec.get("lat")
            lon = rec.get("lon")
            if sid and lat is not None and lon is not None:
                cache[sid] = (float(lat), float(lon))
    return cache


def enrich_records(
    records: list[dict],
    latlon_cache: dict[str, tuple[float, float]],
) -> int:
    """Apply lat/lon from *latlon_cache* to *records* in place.

    Returns the number of entries updated.
    """
    updated = 0

    # First pass: stations.
    for rec in records:
        if rec.get("lat") is not None and rec.get("lon") is not None:
            continue
        if rec.get("type") == "station" and rec.get("stop_id"):
            stop_id = rec["stop_id"]
            if stop_id in latlon_cache:
                lat, lon = latlon_cache[stop_id]
                rec["lat"] = lat
                rec["lon"] = lon
                updated += 1
                log.info("  station '%s' (%s) → (%.6f, %.6f)", rec.get("stop_name", "?"), stop_id, lat, lon)

    # Second pass: midpoints.
    for rec in records:
        if rec.get("lat") is not None and rec.get("lon") is not None:
            continue
        if rec.get("type") == "midpoint":
            adj = rec.get("adjacent_stops") or []
            if len(adj) >= 2 and adj[0] and adj[1]:
                result = interpolate_latlon(adj[0], adj[1], latlon_cache)
                if result is not None:
                    lat, lon = result
                    rec["lat"] = lat
                    rec["lon"] = lon
                    updated += 1
                    log.info(
                        "  midpoint '%s' → (%.6f, %.6f)",
                        rec.get("stop_name", "?"),
                        lat,
                        lon,
                    )
                else:
                    log.warning(
                        "  midpoint '%s': adjacent stops not in cache (%s, %s)",
                        rec.get("stop_name", "?"),
                        adj[0],
                        adj[1],
                    )

    return updated


async def main(api_key: str, dry_run: bool) -> None:
    """Enrich all station JSON files."""
    # Load all files first.
    all_records: dict[str, list[dict]] = {}
    for line, path in LINE_FILES.items():
        if not path.exists():
            log.warning("File not found, skipping: %s", path)
            continue
        with open(path) as fh:
            all_records[line] = json.load(fh)

    # Pre-populate cache from records that already have lat/lon.
    latlon_cache = seed_cache_from_records(all_records)
    log.info("Seeded cache with %d already-enriched stop IDs.", len(latlon_cache))

    # Collect all stop IDs that still need fetching across all files.
    stop_ids_needed = collect_stop_ids(all_records)

    if stop_ids_needed:
        # Fetch them all in one (or a few) batched API call(s).
        async with httpx.AsyncClient(timeout=30) as client:
            fetched = await fetch_all_stops(client, stop_ids_needed, api_key)
        latlon_cache.update(fetched)
    else:
        log.info("All station entries already have lat/lon — skipping API fetch.")

    log.info("Fetched lat/lon for %d/%d requested stops.", len(latlon_cache), len(set(stop_ids_needed)))

    # Apply to each file and write back.
    total_updated = 0
    for line, records in all_records.items():
        path = LINE_FILES[line]
        log.info("Enriching %s …", path.name)
        n = enrich_records(records, latlon_cache)
        total_updated += n

        if not dry_run:
            with open(path, "w") as fh:
                json.dump(records, fh, indent=2, ensure_ascii=False)
            log.info("  Wrote %s (%d entries updated)", path.name, n)
        else:
            log.info("  DRY RUN — would update %d entries in %s", n, path.name)

    log.info("Done. Total entries updated: %d", total_updated)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Enrich MBTA LED station JSON files with GPS lat/lon coordinates."
    )
    parser.add_argument(
        "--api-key",
        default=os.getenv("MBTA_API_KEY", ""),
        help="MBTA API key (defaults to MBTA_API_KEY env var).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Compute enrichment but do not write files.",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    asyncio.run(main(api_key=args.api_key, dry_run=args.dry_run))
