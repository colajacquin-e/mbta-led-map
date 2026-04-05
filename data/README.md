# Station Data Schema

The `data/` directory is the **single source of truth** for every LED in the MBTA LED Map. Both the backend (vehicle → LED mapper) and the frontend (SVG renderer) read these files.

## File Layout

```
data/
├── lines.json          # Line metadata (all lines)
├── red.json            # Red Line LED entries
├── mattapan.json       # Mattapan Trolley LED entries
├── orange.json         # (future) Orange Line LED entries
├── blue.json           # (future) Blue Line LED entries
├── green.json          # (future) Green Line LED entries
├── schema.json         # JSON Schema definitions
└── README.md
```

### `lines.json` — Per-Line Metadata

Each key is a line identifier (`red`, `orange`, `blue`, `green`, `mattapan`).

| Field | Type | Description |
|-------|------|-------------|
| `name` | string | Display name (e.g., "Red Line") |
| `color_hex` | string | CSS hex color (e.g., `"#DA291C"`) |
| `led_count` | integer | Total LEDs on this line's chain |
| `chain_gpio` | integer | ESP32 GPIO pin for this chain's DIN signal |

### `<line>.json` — Per-Line LED Arrays

Each file is a JSON array of LED entry objects. One file per line.

| Field | Type | Description |
|-------|------|-------------|
| `index` | integer | Zero-based position in this line's daisy chain |
| `line` | enum | `"red"`, `"orange"`, `"blue"`, `"green"`, `"mattapan"` |
| `stop_id` | string \| null | MBTA V3 API stop ID; `null` for midpoints |
| `stop_name` | string | Human name; midpoints use `"StopA – StopB"` format |
| `direction` | enum | `"southbound"`, `"northbound"`, `"westbound"`, `"eastbound"`, `"inbound"`, `"outbound"` |
| `direction_id` | integer | MBTA API direction_id (`0` or `1`); meaning is route-specific |
| `type` | enum | `"station"` or `"midpoint"` |
| `adjacent_stops` | [string, string] \| null | For midpoints: the two stop IDs flanking this LED. `null` for stations |
| `x` | number | Visual x-coordinate for map rendering |
| `y` | number | Visual y-coordinate for map rendering |

## Constraints

### Enforced by JSON Schema (`schema.json`)

- **Stations** must have a non-null `stop_id` and `adjacent_stops` must be `null`.
- **Midpoints** must have `stop_id` as `null` and `adjacent_stops` as a 2-element array.
- All required fields must be present with correct types.
- `line`, `direction`, `type`, and `direction_id` values must be from their allowed enums.

### Requires additional validation (`tests/validate_stations.py`, issue #8)

- Indices must be sequential per line (0, 1, 2, ...) with no gaps or duplicates.
- `adjacent_stops` entries must reference `stop_id` values that exist elsewhere in the file.
- `led_count` per line must match the actual number of LED entries for that line.
- `direction_id` must match what the MBTA API returns for that route and direction.

## LED Count Breakdown

| Line | Stations | LEDs/Station | Midpoint Gaps | Total LEDs |
|------|----------|-------------|---------------|------------|
| Red | 22 (4-track at JFK/UMass) | 2 (4 at JFK) | 21 × 2 dir | 88 |
| Orange | 20 | 2 | 19 × 2 dir | ~78 |
| Blue | 12 | 2 | 11 × 2 dir | ~46 |
| Green | ~66 (4-track at Kenmore) | 2 (4 at Kenmore) | ~68 × 2 dir | ~280 |
| Mattapan | 8 | 2 | 7 × 2 dir | ~30 |

## Validation

Validate with the JSON Schema definitions in `schema.json`:

```python
import json, jsonschema

with open("data/schema.json") as f:
    schema = json.load(f)

def validate_def(instance, def_name):
    wrapper = {"$ref": f"#/$defs/{def_name}", "$defs": schema["$defs"]}
    jsonschema.validate(instance, wrapper)

# Validate line metadata
with open("data/lines.json") as f:
    validate_def(json.load(f), "line_metadata")

# Validate a per-line LED file
with open("data/red.json") as f:
    validate_def(json.load(f), "led_file")
```

A full validation script is at `tests/validate_stations.py` (see issue #8).
