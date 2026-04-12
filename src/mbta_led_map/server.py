"""MBTA LED Map — FastAPI server.

Serves:
  - GET /              → static/index.html
  - GET /api/stations  → all LED definitions (for frontend rendering)
  - GET /api/status    → connection status + last update time
  - GET /api/leds      → full LED layout (alias kept for backwards compatibility)
  - WS  /ws            → pushes MapState JSON on each poll cycle
  - WS  /ws/leds       → alias for /ws (matches documented API contract)
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import config
from .mapper import LedMapper
from .models import LineState, MapState
from .poller import MbtaPoller

log = logging.getLogger("mbta_led_map")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = PROJECT_ROOT / "data"
STATIC_DIR = Path(__file__).resolve().parent / "static"

# ---------------------------------------------------------------------------
# Core components (initialised at import time so they are ready before startup)
# ---------------------------------------------------------------------------

_mapper = LedMapper(data_dir=DATA_DIR)
_poller = MbtaPoller()

# ---------------------------------------------------------------------------
# Shared mutable state
# ---------------------------------------------------------------------------
_map_state: MapState = MapState.empty(_mapper.led_counts)
_connected_clients: list[WebSocket] = []

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
    """Serve the frontend map page."""
    return FileResponse(str(STATIC_DIR / "index.html"))


@app.get("/api/stations")
async def get_stations() -> JSONResponse:
    """Return all LED definitions (index, stop_id, x, y, lat, lon, …) for every line.

    The frontend uses this to render the map.  The mapper uses a copy of this
    data internally; the JSON files on disk remain the single source of truth.
    """
    payload: dict[str, Any] = {}
    for line, records in _mapper.all_leds.items():
        serialized = []
        for r in records:
            d = dict(r.__dict__)
            # Rename led_type → type so the frontend matches the raw JSON schema
            d["type"] = d.pop("led_type", None)
            serialized.append(d)
        payload[line] = serialized
    return JSONResponse(payload)


@app.get("/api/leds")
async def get_leds() -> JSONResponse:
    """Alias for /api/stations kept for backwards compatibility."""
    return await get_stations()


@app.get("/api/status")
async def get_status() -> JSONResponse:
    """Return connection status and last update timestamp."""
    return JSONResponse(
        {
            "last_updated": _map_state.timestamp,
            "vehicle_count": _map_state.vehicle_count,
            "led_counts": _mapper.led_counts,
            "connected_clients": len(_connected_clients),
        }
    )


# ---------------------------------------------------------------------------
# WebSocket
# ---------------------------------------------------------------------------


def _build_message() -> str:
    """Serialise the current MapState to a JSON string."""
    return _map_state.model_dump_json()


async def _broadcast(message: str) -> None:
    """Send *message* to all connected WebSocket clients, pruning dead ones."""
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


async def _ws_handler(ws: WebSocket) -> None:
    """Shared handler for both WebSocket endpoints."""
    await ws.accept()
    _connected_clients.append(ws)
    log.info("WS client connected — total %d", len(_connected_clients))
    try:
        # Push current state immediately on connect.
        await ws.send_text(_build_message())
        while True:
            # Keep alive; pushes happen from the poll callback.
            await asyncio.sleep(1)
    except WebSocketDisconnect:
        pass
    except Exception as exc:  # noqa: BLE001
        log.warning("WS error: %s", exc)
    finally:
        try:
            _connected_clients.remove(ws)
        except ValueError:
            pass
        log.info("WS client disconnected — total %d", len(_connected_clients))


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket) -> None:
    """Primary WebSocket endpoint — pushes MapState JSON on each poll cycle."""
    await _ws_handler(ws)


@app.websocket("/ws/leds")
async def websocket_leds_endpoint(ws: WebSocket) -> None:
    """Alias WebSocket endpoint matching the documented API contract."""
    await _ws_handler(ws)


# ---------------------------------------------------------------------------
# Poll callback
# ---------------------------------------------------------------------------


async def _on_vehicles(vehicles) -> None:  # type: ignore[type-arg]
    """Receive vehicle list from the poller, map to LEDs, and broadcast."""
    global _map_state

    brightness_map = _mapper.map_vehicles(vehicles)

    lines: dict[str, LineState] = {}
    for line, brightness_list in brightness_map.items():
        lines[line] = LineState(leds=brightness_list, count=len(brightness_list))

    _map_state = MapState(
        timestamp=datetime.now(timezone.utc).isoformat(),
        vehicle_count=len(vehicles),
        lines=lines,
    )

    message = _build_message()
    await _broadcast(message)


# ---------------------------------------------------------------------------
# Startup
# ---------------------------------------------------------------------------


@app.on_event("startup")
async def startup() -> None:
    """Register poll callback and launch the background polling task."""
    log.info("LED counts: %s", _mapper.led_counts)
    _poller.register(_on_vehicles)
    asyncio.create_task(_poller.run())
