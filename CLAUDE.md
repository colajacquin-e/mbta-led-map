# CLAUDE.md

## Project Overview

**MBTA Live Map** — A custom PCB wall display that shows real-time Boston MBTA subway train positions using LEDs. Each LED represents either a station (per-track, per-direction) or a midpoint between stations. The system is developed software-first with a web-based virtual prototype, then ported to physical hardware.

## Architecture

```
┌─────────────────────────────────────────────┐
│              MBTA V3 API                     │
│   api-v3.mbta.com/vehicles?filter[route]=   │
└──────────────────┬──────────────────────────┘
                   │ polls every ~12s
                   ▼
┌─────────────────────────────────────────────┐
│         Python Backend (FastAPI)             │
│                                             │
│  MBTA Poller (async) → Vehicle-to-LED       │
│  Mapper → LED State Array [0-255 per LED]   │
│                                             │
│  Serves: ws://localhost:8000/ws/leds        │
│          GET /api/stations                  │
│          GET /api/status                    │
└──────────────────┬──────────────────────────┘
                   │ WebSocket push
          ┌────────┴────────┐
          ▼                 ▼
   Web Frontend       ESP32 Firmware
   (SVG/Canvas)       (FastLED, future)
```

### Key Abstraction

The **LED state array** is the core data contract. Both consumers (web frontend and eventual ESP32 firmware) receive the same data structure:

```json
{
  "timestamp": "2026-04-05T18:30:00Z",
  "lines": {
    "red":    { "leds": [0, 0, 255, 0, 128, ...], "count": 90 },
    "orange": { "leds": [0, 255, 0, ...],          "count": 78 },
    "blue":   { "leds": [0, 0, 0, 255, ...],       "count": 46 },
    "green":  { "leds": [0, 0, 0, ...],             "count": 280 }
  }
}
```

Values: `255` = train at station, `180` = train approaching (INCOMING_AT), `128` = train at midpoint (IN_TRANSIT_TO), `0` = off.

## Station Definition File

The **single source of truth** lives in `data/stations.json` (or `.yaml`). Both backend and frontend read this file. Schema per LED entry:

- `index` — position in the daisy chain for this line
- `line` — red, orange, blue, green
- `chain` — which physical LED chain (matches line)
- `stop_id` — MBTA API stop ID (null for midpoints)
- `stop_name` — human-readable name
- `direction` — inbound or outbound
- `type` — "station" or "midpoint"
- `adjacent_stops` — [stop_A_id, stop_B_id] for midpoints
- `x`, `y` — visual coordinates for map rendering

### LED Count Breakdown

| Line | Stations | Tracks/Station | Midpoints | Total LEDs |
|------|----------|---------------|-----------|------------|
| Red | 22 (incl. JFK/UMass 4-track) | 2 (4 at JFK) | ~21 gaps × 2 dir | ~90 |
| Orange | 20 | 2 | 19 gaps × 2 dir | ~78 |
| Blue | 12 | 2 | 11 gaps × 2 dir | ~46 |
| Green | ~66 (complex branching) | 2 (4 at Kenmore) | ~68 gaps × 2 dir | ~280 |
| **Total** | | | | **~492** |

### Critical Track Count Rules

LEDs per station = **number of physical tracks**, NOT number of lines serving the station:

- **Green Line trunk** (Gov't Center → Copley): 2 tracks shared by B+C+D+E = **2 LEDs/direction**
- **Kenmore**: 4 tracks (B inner, C/D outer) = **4 LEDs/direction**
- **JFK/UMass**: 4 tracks (Ashmont + Braintree platforms) = **4 LEDs/direction**
- **Park Street**: 2 Red tracks + 2 Green tracks = **4 LEDs/direction** (2 per line)
- **Copley**: E branch diverges just west of here; station itself has 2 tracks
- All other stations: 2 tracks = **2 LEDs/direction** (inbound + outbound)

### Green Line Branch Topology

```
Medford/Tufts ──── ... ──── East Somerville ─┐ (E only)
                                              ├── Lechmere ── Science Park ── North Station ── Haymarket ─┐ (D+E)
                            Union Square ─────┘ (D only)                                                  │
                                                                                                          │
Gov't Center ── Park St ── Boylston ── Arlington ── Copley ──┬── Hynes ── Kenmore(4trk) ──┬── B branch (16 stn)
(B+C+D+E share 2 tracks)                                     │                             ├── C branch (13 stn)
                                                              │                             └── D branch (13 stn)
                                                              └── Prudential ── Symphony ── ... ── Heath St (E branch, 11 stn)
```

## MBTA V3 API

- **Base URL**: `https://api-v3.mbta.com`
- **Key endpoint**: `GET /vehicles?filter[route]=Red,Orange,Blue,Green-B,Green-C,Green-D,Green-E&include=stop`
- **Auth**: API key via `x-api-key` header or `api_key` query param. Key stored in `.env`.
- **Rate limit**: 1000 req/min with key, 20/min without
- **Caching**: Use `If-Modified-Since` header; 304 responses don't count against rate limit
- **Streaming**: API supports SSE streaming as alternative to polling (future optimization)

### Vehicle Response Fields We Care About

- `attributes.current_status` — `STOPPED_AT`, `IN_TRANSIT_TO`, `INCOMING_AT`
- `attributes.direction_id` — 0 (outbound) or 1 (inbound)
- `relationships.stop.data.id` — current/next stop ID
- `relationships.route.data.id` — `Red`, `Orange`, `Blue`, `Green-B`, `Green-C`, `Green-D`, `Green-E`

### Green Line Mapping Logic

- Vehicle on `Green-B` at a **trunk station** (e.g., Park Street) → light the shared trunk LED, not a branch-specific LED
- Vehicle on `Green-B` **past Kenmore** → light B-branch LEDs only
- Trunk stations don't distinguish branches; branch identity only matters after the physical split

## Tech Stack

### Backend (Python)
- **FastAPI** — WebSocket server + REST endpoints
- **uvicorn** — ASGI server
- **httpx** — async HTTP client for MBTA API
- **pydantic** — data models
- **python-dotenv** — env var management
- **pytest + pytest-asyncio** — testing

### Frontend (Web)
- Vanilla HTML + CSS + JS (no framework needed)
- SVG for map rendering
- Native WebSocket API
- Dark background, MBTA line colors: Red `#DA291C`, Orange `#ED8B00`, Blue `#003DA5`, Green `#00843D`

### Hardware (Future — Epic 5+6)
- **ESP32-S3-WROOM** — MCU, 4 RMT channels for LED chains
- **WS2812B-2020** — addressable RGB LEDs, ~492 total
- **CH224K** — USB-C PD sink controller (negotiates 9V)
- **AP63203 or similar** — 9V → 5V buck converter
- **AMS1117-3.3 or AP2112K** — 5V → 3.3V LDO for ESP32
- **SN74LVC1T45** — 3.3V → 5V level shifter per LED chain DIN
- **KiCad 10** — schematic + PCB layout
- 4-layer PCB: signal / GND / 5V / signal
- Board IS the subway map (matte black mask, white silkscreen)

### Firmware (Future — Epic 6)
- PlatformIO + Arduino framework
- FastLED library (4 chains, RMT peripheral)
- ArduinoJson for API parsing
- `FastLED.setMaxPowerInVoltsAndMilliamps(5, 2500)` — hard power cap
- Two modes: standalone (polls API directly) or WebSocket client (receives states from backend)

## Project Structure

```
mbta-led-map/
├── CLAUDE.md
├── README.md
├── .env                    # MBTA_API_KEY=... (gitignored)
├── .gitignore
├── data/
│   └── stations.json       # Single source of truth: all LEDs, stop IDs, coordinates
├── src/
│   ├── backend/
│   │   ├── main.py         # FastAPI app, WebSocket endpoint, REST endpoints
│   │   ├── poller.py       # Async MBTA API polling loop
│   │   ├── mapper.py       # Vehicle → LED state mapping
│   │   └── models.py       # Pydantic models for vehicles, LED state
│   └── frontend/
│       ├── index.html       # Map page
│       ├── style.css
│       └── app.js           # WebSocket client, SVG rendering
├── tests/
│   ├── fixtures/            # Captured API responses for mocking
│   ├── test_poller.py
│   ├── test_mapper.py
│   └── validate_stations.py # Station definition file validator
├── firmware/                # (Future) PlatformIO ESP32 project
│   ├── platformio.ini
│   ├── src/
│   │   └── main.cpp
│   └── include/
│       └── stations.h       # Compiled station definitions
└── hardware/                # (Future) KiCad project
    ├── mbta-led-map.kicad_pro
    ├── mbta-led-map.kicad_sch
    └── mbta-led-map.kicad_pcb
```

## Release Plan

| Release | Scope | Status |
|---------|-------|--------|
| **R1 — Virtual Prototype** | Backend + frontend, Red Line only, end-to-end proof | Active |
| **R2 — Full Virtual Map** | All 4 lines, polished UI, full LED layout | Backlog |
| **R3 — Hardware Design** | KiCad schematic + PCB, fab order | Backlog |
| **R4 — Integration** | ESP32 firmware, physical board, wall mount | Backlog |

## Working Agreements

- Start with **Red Line only** for R1 — simplest topology, proves the full pipeline
- Station definition file is the **single source of truth** — both backend mapper and frontend renderer read it
- LED brightness values are the **shared data contract** — same array drives web dots and physical LEDs
- Green Line uses **track count model** not line count — trunk stations get 2 LEDs regardless of how many branches pass through
- The web frontend should eventually have a **"PCB aesthetic mode"** (matte black + white silkscreen style) to preview the physical board appearance

## Development Commands

```bash
# Backend
cd src/backend
pip install -r requirements.txt
uvicorn main:app --reload --port 8000

# Validate station data
python tests/validate_stations.py

# Run tests
pytest tests/

# Test WebSocket
wscat -c ws://localhost:8000/ws/leds
```

## GitHub Project

Issues are tracked on the GitHub Project board with these conventions:
- **Epic labels**: `epic:data-model`, `epic:api`, `epic:backend`, `epic:frontend`, `epic:hardware`, `epic:firmware`
- **Line labels**: `line:red`, `line:orange`, `line:blue`, `line:green`
- **Type labels**: `story`, `task`, `bug`, `nice-to-have`
- **Priority labels**: `priority:high`, `priority:medium`, `priority:low`
- **Milestones**: R1 through R4 matching release plan
- **PR linking**: Reference issues in PRs with `Closes #N`
- **Branch naming**: `feat/<issue-number>-short-description` (e.g., `feat/1-define-led-schema`)
