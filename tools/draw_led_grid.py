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


def draw_leds(draw, img, leds, color, font, simple=False, all_station_leds=None):
    gap_px = int(LABEL_GAP * MM)
    if all_station_leds is None:
        all_station_leds = {}

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
            elif pos == "above-right":
                # Canvas height scales to line count; line 2 anchors at LED
                ar_lines = label.split("\n")
                ar_h = len(ar_lines) * 14 + 4
                txt_img = Image.new("RGBA", (300, ar_h), (0, 0, 0, 0))
                txt_draw = ImageDraw.Draw(txt_img)
                txt_draw.text((0, 0), label, fill="white", font=font,
                              stroke_width=1, stroke_fill=(30, 30, 30))
                bbox = txt_img.getbbox()
                if bbox:
                    txt_img = txt_img.crop(bbox)
                txt_img = txt_img.rotate(45, expand=True)
                max_x = max(l["x"] for l in all_station_leds.get(led["stop_name"].replace("\n", " "), [led]))
                anchor_px = int(max_x * MM)
                label_y_off = int(led.get("label_y_offset", 0) * MM)
                label_x_off = int(led.get("label_x_offset", 0) * MM)
                img.paste(txt_img,
                          (anchor_px + gap_px + label_x_off, py - txt_img.height + label_y_off),
                          txt_img)
            elif pos == "below-left":
                bl_lines = label.split("\n")
                bl_h = len(bl_lines) * 14 + 4
                txt_img = Image.new("RGBA", (300, bl_h), (0, 0, 0, 0))
                txt_draw = ImageDraw.Draw(txt_img)
                align = led.get("label_alignment", "left")
                bl_widths = [txt_draw.textbbox((0, 0), ln, font=font)[2] - txt_draw.textbbox((0, 0), ln, font=font)[0] for ln in bl_lines]
                bl_max_w = max(bl_widths)
                for j, ln in enumerate(bl_lines):
                    lx = bl_max_w - bl_widths[j] if align == "right" else 0
                    txt_draw.text((lx, j * 14), ln, fill="white", font=font,
                                  stroke_width=1, stroke_fill=(30, 30, 30))
                bbox = txt_img.getbbox()
                if bbox:
                    txt_img = txt_img.crop(bbox)
                txt_img = txt_img.rotate(45, expand=True)
                max_y = max(l["y"] for l in all_station_leds.get(led["stop_name"].replace("\n", " "), [led]))
                anchor_py = int(max_y * MM)
                img.paste(txt_img,
                          (px - txt_img.width, anchor_py + gap_px // 2),
                          txt_img)
            elif pos == "left":
                min_x = min(l["x"] for l in all_station_leds.get(led["stop_name"].replace("\n", " "), [led]))
                anchor_px = int(min_x * MM)
                align = led.get("label_alignment", "right")
                label_y_off = int(led.get("label_y_offset", 0) * MM)
                label_x_off = int(led.get("label_x_offset", 0) * MM)
                text_lines = label.split("\n")
                line_widths = [draw.textbbox((0, 0), ln, font=font)[2] - draw.textbbox((0, 0), ln, font=font)[0] for ln in text_lines]
                max_tw = max(line_widths)
                for j, ln in enumerate(text_lines):
                    tw = line_widths[j]
                    if align == "right":
                        lx = anchor_px - gap_px - tw + label_x_off
                    elif align == "center":
                        lx = anchor_px - gap_px - max_tw // 2 - tw // 2 + label_x_off
                    else:
                        lx = anchor_px - gap_px - max_tw + label_x_off
                    ly = py - r - int(2 * MM) + j * 12 + label_y_off
                    draw.text((lx, ly), ln, fill="white", font=font,
                              stroke_width=1, stroke_fill=(30, 30, 30))
            elif pos == "right":
                max_x = max(l["x"] for l in all_station_leds.get(led["stop_name"].replace("\n", " "), [led]))
                anchor_px = int(max_x * MM)
                align = led.get("label_alignment", "left")
                label_y_off = int(led.get("label_y_offset", 0) * MM)
                label_x_off = int(led.get("label_x_offset", 0) * MM)
                text_lines = label.split("\n")
                line_widths = [draw.textbbox((0, 0), ln, font=font)[2] - draw.textbbox((0, 0), ln, font=font)[0] for ln in text_lines]
                max_tw = max(line_widths)
                for j, ln in enumerate(text_lines):
                    tw = line_widths[j]
                    if align == "right":
                        lx = anchor_px + gap_px + max_tw - tw + label_x_off
                    elif align == "center":
                        lx = anchor_px + gap_px + max_tw // 2 - tw // 2 + label_x_off
                    else:
                        lx = anchor_px + gap_px + label_x_off
                    ly = py - r - int(2 * MM) + j * 12 + label_y_off
                    draw.text((lx, ly), ln, fill="white", font=font,
                              stroke_width=1, stroke_fill=(30, 30, 30))
            elif pos == "center":
                # Label at the vertical center of all LEDs at this station
                # with left/right positioning based on label_alignment
                stn_leds = all_station_leds.get(led["stop_name"].replace("\n", " "), [led])
                center_y = int((min(l["y"] for l in stn_leds) + max(l["y"] for l in stn_leds)) / 2 * MM)
                min_x = int(min(l["x"] for l in stn_leds) * MM)
                max_x = int(max(l["x"] for l in stn_leds) * MM)
                align = led.get("label_alignment", "center")
                text_lines = label.split("\n")
                line_widths = [draw.textbbox((0, 0), ln, font=font)[2] - draw.textbbox((0, 0), ln, font=font)[0] for ln in text_lines]
                max_tw = max(line_widths)
                total_height = len(text_lines) * 12
                for j, ln in enumerate(text_lines):
                    tw = line_widths[j]
                    if align == "right":
                        lx = min_x - gap_px - tw
                    elif align == "left":
                        lx = max_x + gap_px
                    else:
                        center_x = (min_x + max_x) // 2
                        lx = center_x - tw // 2
                    ly = center_y - total_height // 2 + j * 12
                    draw.text((lx, ly), ln, fill="white", font=font,
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

    # Build lookup of all station LEDs by name (across all lines) for label anchoring
    # Normalize names by stripping newlines so transfer stations group together
    all_station_leds = {}
    all_line_data = []
    for line_name, filepath in LINE_FILES.items():
        leds = load_line(filepath)
        if leds:
            all_line_data.append((line_name, leds))
            for led in leds:
                if led["type"] == "station":
                    key = led["stop_name"].replace("\n", " ")
                    all_station_leds.setdefault(key, []).append(led)

    for line_name, leds in all_line_data:
        color = LINE_COLORS.get(line_name, (200, 200, 200))
        draw_leds(draw, img, leds, color, font, simple=args.simple,
                  all_station_leds=all_station_leds)
        print(f"  {line_name}: {len(leds)} LEDs")

    img.save(args.output)
    print(f"Saved: {args.output}")


if __name__ == "__main__":
    main()
