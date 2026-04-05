# Station Data Schema

`stations.json` is the **single source of truth** for every LED in the MBTA LED Map. Both the backend (vehicle → LED mapper) and the frontend (SVG renderer) read this file.

## File Structure

```json
{
  "lines": { ... },
  "leds": [ ... ]
}
```

### `lines` — Per-Line Metadata

Each key is a line identifier (`red`, `orange`, `blue`, `green`).

| Field | Type | Description |
|-------|------|-------------|
| `name` | string | Display name (e.g., "Red Line") |
| `color_hex` | string | CSS hex color (e.g., `"#DA291C"`) |
| `led_count` | integer | Total LEDs on this line's chain |
| `chain_gpio` | integer | ESP32 GPIO pin for this chain's DIN signal |

### `leds` — LED Entry Array

Each object in the array represents one physical LED.

| Field | Type | Description |
|-------|------|-------------|
| `index` | integer | Zero-based position in this line's daisy chain |
| `line` | enum | `"red"`, `"orange"`, `"blue"`, `"green"` |
|`stop_id` | string \| null | MBTA V3 API stop ID; `null` for midpoints |
| `stop_name` | string | Human name; midpoints use `"StopA – StopB"` format |
| `direction` | enum | `"southbound"`, `"northbound"`, `"westbound"`, `"eastbound"`, `"inbound"`, `"outbound"` |
| `direction_id` | integer | `0` = outbound (away from downtown), `1` = inbound (toward downtown) |
| `type` | enum | `"station"` or `"midpoint"` |
| `adjacent_stops` | [string, string] \| null | For midpoints: the two stop IDs flanking this LED. `null` for stations |
| `x` | number | Visual x-coordinate for map rendering |
| `y` | number | Visual y-coordinate for map rendering |

## Constraints

- **Stations** must have a non-null `stop_id` and `adjacent_stops` must be `null`.
- **Midpoints** must have `stop_id` as `null` and `adjacent_stops` as a 2-element array of valid stop IDs.
- Indices must be sequential per line (0, 1, 2, ...) with no gaps.
- `direction_id` must be consistent with `direction` (e.g., southbound = 0 for Red Line).

## LED Count Breakdown

| Line | Stations | LEDs/Station | Midpoint Gaps | Total LEDs |
|------|----------|-------------|---------------|------------|
| Red | 22 (4-track at JFK/UMass) | 2 (4 at JFK) | 21 × 2 dir | ~90 |
| Orange | 20 | 2 | 19 × 2 dir | ~78 |
| Blue | 12 | 2 | 11 × 2 dir | ~46 |
| Green | ~66 (4-track at Kenmore) | 2 (4 at Kenmore) | ~68 × 2 dir | ~280 |

## Validation

Validate with the JSON Schema in `schema.json`:

```bash
# Python (requires jsonschema package)
python -c "
import json, jsonschema
schema = json.load(open('data/schema.json'))
data = json.load(open('data/stations.json'))
jsonschema.validate(data, schema)
print('Valid')
"
```

A full validation script is at `tests/validate_stations.py` (see issue #8).
