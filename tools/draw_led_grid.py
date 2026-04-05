#!/usr/bin/env python3
"""Draw all LED positions on a 400x400mm grid.

Reads per-line JSON files from data/ and renders stations (large dots with
labels) and midpoints (small dots) on a dark background with mm gridlines.

Usage:
    python tools/draw_led_grid.py                 # output to data/led-grid.png
    python tools/draw_led_grid.py -o my-grid.png  # custom output path
"""

import argparse
import json
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

BOARD_MM = 400
PX = 1134  # pixels for 400mm
MM = PX / BOARD_MM

LINE_COLORS = {
    "red":      (218, 41, 28),
    "orange":   (237, 139, 0),
    "blue":     (0, 61, 165),
    "green":    (0, 132, 61),
    "mattapan": (218, 41, 28),
}

LINE_FILES = {
    "red":      "data/red.json",
    "orange":   "data/orange.json",
    "blue":     "data/blue.json",
    "green":    "data/green.json",
    "mattapan": "data/mattapan.json",
}


def mm2px(x, y):
    return int(x * MM), int(y * MM)


def load_line(filepath):
    path = Path(filepath)
    if not path.exists():
        return []
    with open(path) as f:
        return json.load(f)


def draw_grid(draw):
    for mm in range(0, BOARD_MM + 1, 10):
        px = int(mm * MM)
        if mm % 50 == 0:
            draw.line([(px, 0), (px, PX)], fill=(50, 50, 50), width=1)
            draw.line([(0, px), (PX, px)], fill=(50, 50, 50), width=1)
        else:
            draw.line([(px, 0), (px, PX)], fill=(35, 35, 35), width=1)
            draw.line([(0, px), (PX, px)], fill=(35, 35, 35), width=1)


def draw_grid_labels(draw, font):
    for mm in range(0, BOARD_MM + 1, 50):
        px = int(mm * MM)
        draw.text((px + 2, 2), f"{mm}", fill=(80, 80, 80), font=font)
        draw.text((2, px + 2), f"{mm}", fill=(80, 80, 80), font=font)


def draw_leds(draw, leds, color, font):
    for led in leds:
        px, py = mm2px(led["x"], led["y"])
        is_station = led["type"] == "station"

        if is_station:
            r = 5
            draw.ellipse([(px - r, py - r), (px + r, py + r)],
                         fill=color, outline="white", width=2)
            stop = led["stop_id"] or "?"
            name = led["stop_name"]
            label = f"{name} [{stop}]"
            draw.text((px + 9, py - 6), label, fill="white", font=font,
                      stroke_width=1, stroke_fill=(30, 30, 30))
        else:
            r = 2
            draw.ellipse([(px - r, py - r), (px + r, py + r)],
                         fill=color, outline=(150, 150, 150))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-o", "--output", default="data/led-grid.png",
                        help="Output image path (default: data/led-grid.png)")
    args = parser.parse_args()

    img = Image.new("RGB", (PX, PX), (20, 20, 20))
    draw = ImageDraw.Draw(img)

    try:
        font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 9)
        grid_font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 11)
    except OSError:
        font = grid_font = ImageFont.load_default()

    draw_grid(draw)
    draw_grid_labels(draw, grid_font)

    for line_name, filepath in LINE_FILES.items():
        leds = load_line(filepath)
        if leds:
            color = LINE_COLORS.get(line_name, (200, 200, 200))
            draw_leds(draw, leds, color, font)
            print(f"  {line_name}: {len(leds)} LEDs")

    img.save(args.output)
    print(f"Saved: {args.output}")


if __name__ == "__main__":
    main()
