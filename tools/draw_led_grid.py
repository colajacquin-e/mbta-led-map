#!/usr/bin/env python3
"""Draw all LED positions on a 400x400mm grid.

Reads per-line JSON files from data/ and renders each LED as a dot at its
exact (x, y) coordinate. Label placement is driven by each LED's
label_position field. No position or label logic — the JSON is the source
of truth.

Usage:
    python tools/draw_led_grid.py                 # output to data/led-grid.png
    python tools/draw_led_grid.py --simple         # one label per station, saves led-grid-simple.png
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

LABEL_GAP = 4  # mm between LED and label


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


def draw_leds(draw, img, leds, color, font, simple=False):
    gap_px = int(LABEL_GAP * MM)

    for led in leds:
        px, py = mm2px(led["x"], led["y"])
        is_station = led["type"] == "station"
        pos = led.get("label_position", "none")

        if is_station:
            r = 4
            draw.ellipse([(px - r, py - r), (px + r, py + r)],
                         fill=color, outline="white", width=1)

            if pos == "none":
                continue

            if simple:
                label = led["stop_name"]
            else:
                stop = led["stop_id"] or "?"
                label = f"{led['stop_name']} [{stop}]"

            if pos == "below":
                txt_img = Image.new("RGBA", (300, 20), (0, 0, 0, 0))
                txt_draw = ImageDraw.Draw(txt_img)
                txt_draw.text((0, 0), label, fill="white", font=font,
                              stroke_width=1, stroke_fill=(30, 30, 30))
                txt_img = txt_img.rotate(270, expand=True)
                img.paste(txt_img,
                          (px - txt_img.width // 2, py + gap_px),
                          txt_img)
            elif pos == "left":
                bbox = draw.textbbox((0, 0), label, font=font)
                tw = bbox[2] - bbox[0]
                draw.text((px - gap_px - tw, py - 6), label,
                          fill="white", font=font,
                          stroke_width=1, stroke_fill=(30, 30, 30))
            else:  # "right"
                draw.text((px + gap_px, py - 6), label,
                          fill="white", font=font,
                          stroke_width=1, stroke_fill=(30, 30, 30))
        else:
            r = 2
            draw.ellipse([(px - r, py - r), (px + r, py + r)],
                         fill=color, outline=(100, 100, 100))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-o", "--output", default="data/led-grid.png",
                        help="Output image path (default: data/led-grid.png)")
    parser.add_argument("--simple", action="store_true",
                        help="One label per station (no stop IDs), saves to led-grid-simple.png")
    args = parser.parse_args()

    if args.simple and args.output == "data/led-grid.png":
        args.output = "data/led-grid-simple.png"

    img = Image.new("RGB", (PX, PX), (20, 20, 20))
    draw = ImageDraw.Draw(img)

    font_candidates = [
        "/System/Library/Fonts/Helvetica.ttc",        # macOS
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",  # Linux
        "C:\\Windows\\Fonts\\arial.ttf",               # Windows
    ]
    font = grid_font = ImageFont.load_default()
    for path in font_candidates:
        try:
            font = ImageFont.truetype(path, 9)
            grid_font = ImageFont.truetype(path, 11)
            break
        except OSError:
            continue

    draw_grid(draw)
    draw_grid_labels(draw, grid_font)

    for line_name, filepath in LINE_FILES.items():
        leds = load_line(filepath)
        if leds:
            color = LINE_COLORS.get(line_name, (200, 200, 200))
            draw_leds(draw, img, leds, color, font, simple=args.simple)
            print(f"  {line_name}: {len(leds)} LEDs")

    img.save(args.output)
    print(f"Saved: {args.output}")


if __name__ == "__main__":
    main()
