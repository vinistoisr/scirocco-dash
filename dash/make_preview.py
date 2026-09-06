#!/usr/bin/env python3
"""Render the four Scirocco RealDash v2 pages at the deck's native 1280x720.

The preview and the binary patch manifest import the same layout_v2 geometry,
so a visual adjustment cannot silently drift away from RealDash coordinates.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from layout_v2 import CANVAS, PAGE1_BARS, PAGE1_DIALS, PAGE1_INDICATORS
from layout_v2 import PAGE1_TEXT, PAGE1_VALUES, PAGE2_GROUPS
from layout_v2 import PAGE2_SPARK_NEEDLE, PAGE2_SPARK_VALUE
from layout_v2 import PAGE3_BARS, PAGE3_TEXT, PAGE3_WHEELS, PAGE4_GROUPS
from layout_v2 import PAGE4_CLEAR_CODES, page2_value_rects, page4_value_rects


HERE = Path(__file__).resolve().parent
ASSETS = HERE / "assets" / "png"
OUT = HERE / "preview"
WHITE = "#f4f6f7"
WHITE_DIM = "#9ca2a9"
RED = "#e02a1c"
AMBER = "#e0951f"


def _font(size: int, bold: bool = False):
    candidates = [
        Path(r"C:\Windows\Fonts\bahnschrift.ttf"),
        Path(r"C:\Windows\Fonts\arialbd.ttf" if bold else r"C:\Windows\Fonts\arial.ttf"),
    ]
    for path in candidates:
        if path.exists():
            return ImageFont.truetype(str(path), size=size)
    return ImageFont.load_default()


def _asset(name: str, size: tuple[int, int] | None = None) -> Image.Image:
    image = Image.open(ASSETS / f"{name}.png").convert("RGBA")
    if size and image.size != size:
        image = image.resize(size, Image.Resampling.LANCZOS)
    return image


def _paste(canvas: Image.Image, image: Image.Image, x: int, y: int, opacity=1.0):
    if opacity != 1.0:
        image = image.copy()
        alpha = image.getchannel("A").point(lambda value: round(value * opacity))
        image.putalpha(alpha)
    canvas.alpha_composite(image, (round(x), round(y)))


def _place_asset(canvas: Image.Image, name: str, rect, rotation=0.0, opacity=1.0):
    x, y, width, height = rect
    image = _asset(name, (round(width), round(height)))
    if rotation:
        image = image.rotate(-rotation, resample=Image.Resampling.BICUBIC, expand=False)
    _paste(canvas, image, x, y, opacity)


def _text(draw: ImageDraw.ImageDraw, rect, value, size, colour=WHITE,
          anchor="mm", bold=False):
    x, y, width, height = rect
    anchors = {
        "mm": (x + width / 2, y + height / 2),
        "lm": (x, y + height / 2),
        "rm": (x + width, y + height / 2),
    }
    draw.text(anchors[anchor], str(value), font=_font(size, bold), fill=colour,
              anchor=anchor)


def _angle(value, minimum, maximum, start=-135, sweep=270):
    return start + sweep * (value - minimum) / (maximum - minimum)


def _bar(draw, rect, fraction, colour=RED, rtl=False):
    x, y, width, height = rect
    fill = max(0, min(width, width * abs(fraction)))
    left = x + width - fill if rtl else x
    draw.rounded_rectangle((left, y, left + fill, y + height), radius=2, fill=colour)


def preview_page1():
    samples = {
        "rpm": 3450, "boost": 0.85, "boost_target": 0.90,
        "coolant": 92, "oil": 104, "charge": 63, "battery": 14.1,
        "odometer": "185885", "trip": "147.6", "speed": 118, "gear": 4,
        "n75": 62, "load": 78, "trim_short": 2.3, "trim_long": -4.7,
        "knock": [0.0, -2.1, 0.0, -0.7], "ghost_boost": 1.32,
        "ghost_rpm": 5600,
    }
    canvas = _asset("bg_page1")
    draw = ImageDraw.Draw(canvas)

    faces = {
        "rpm": "face_rpm", "boost": "face_boost", "coolant": "face_coolant",
        "oil": "face_oil", "charge": "face_charge", "battery": "face_volt",
    }
    for key, name in faces.items():
        _place_asset(canvas, name, PAGE1_DIALS[key])

    _place_asset(canvas, "needle_main_ghost", PAGE1_DIALS["rpm"],
                 _angle(samples["ghost_rpm"], 0, 7000))
    _place_asset(canvas, "needle_main_ghost", PAGE1_DIALS["boost"],
                 _angle(samples["ghost_boost"], -1, 2))
    _place_asset(canvas, "needle_target", PAGE1_DIALS["boost"],
                 _angle(samples["boost_target"], -1, 2))
    _place_asset(canvas, "needle_main", PAGE1_DIALS["rpm"],
                 _angle(samples["rpm"], 0, 7000))
    _place_asset(canvas, "needle_main", PAGE1_DIALS["boost"],
                 _angle(samples["boost"], -1, 2))
    for key, minimum, maximum in (
        ("coolant", 50, 130), ("oil", 50, 150),
        ("charge", 0, 120), ("battery", 8, 16),
    ):
        _place_asset(canvas, "needle_small", PAGE1_DIALS[key],
                     _angle(samples[key], minimum, maximum))

    numeric = {
        "rpm": f"{samples['rpm']:.0f}", "boost": f"{samples['boost']:.1f}",
        "coolant": f"{samples['coolant']:.0f}", "oil": f"{samples['oil']:.0f}",
        "charge": f"{samples['charge']:.0f}", "battery": f"{samples['battery']:.1f}",
    }
    colours = {"rpm": WHITE, "boost": WHITE, "coolant": WHITE, "oil": WHITE,
               "charge": RED, "battery": WHITE}
    for key, value in numeric.items():
        _text(draw, PAGE1_VALUES[key], value, 27 if key in ("rpm", "boost") else 21,
              colours[key], bold=True)

    for key, active in (("turn_left", True), ("mil", False), ("overtemp", False),
                        ("knock", True), ("boost_deviation", False), ("turn_right", False)):
        asset = {"boost_deviation": "ind_boostdev"}.get(key, f"ind_{key}")
        _place_asset(canvas, asset, PAGE1_INDICATORS[key], opacity=1.0 if active else .11)

    _text(draw, PAGE1_TEXT["odometer"], samples["odometer"], 22, anchor="rm")
    _text(draw, PAGE1_TEXT["trip"], samples["trip"], 22, anchor="rm")
    _text(draw, PAGE1_TEXT["speed"], samples["speed"], 42, bold=True)
    _text(draw, PAGE1_TEXT["gear"], samples["gear"], 28, bold=True)
    _text(draw, PAGE1_TEXT["boost_target"], f"{samples['boost_target']:.2f}", 24, anchor="rm")

    for key, value in (("n75", samples["n75"] / 100), ("load", samples["load"] / 100)):
        _bar(draw, PAGE1_BARS[key], value)
    for key, value in (("trim_short", samples["trim_short"]), ("trim_long", samples["trim_long"])):
        side = "negative" if value < 0 else "positive"
        _bar(draw, PAGE1_BARS[f"{key}_{side}"], value / 25, rtl=value < 0)
    for index, value in enumerate(samples["knock"], 1):
        _bar(draw, PAGE1_BARS[f"knock_{index}"], abs(value) / 12, rtl=True)
    return canvas


def preview_page2():
    canvas = _asset("bg_page2")
    draw = ImageDraw.Draw(canvas)
    timing = 25.0
    _place_asset(canvas, "needle_spark", PAGE2_SPARK_NEEDLE,
                 _angle(timing, -30, 45, start=-75, sweep=150))
    _text(draw, PAGE2_SPARK_VALUE, f"{timing:.1f}°", 35, bold=True)
    values = [100.2, 100.5, 38, 63, 19, 142.4, 78.2, 34.1, 82, 1.32,
              1.53, 112.0, 108.5, 111.2, 62, 41,
              2.3, -4.7, 742, "1.000"]
    for (label, rect), value in zip(page2_value_rects(), values):
        shown = f"{value:.1f}" if isinstance(value, float) else str(value)
        _text(draw, rect, shown, 15, anchor="rm")
    return canvas


def preview_page3(dcc_state="fresh"):
    canvas = _asset("bg_page3")
    draw = ImageDraw.Draw(canvas)
    steering = -118
    road_wheel = max(-32, min(32, steering / 15))
    _place_asset(canvas, "wheel_front_left", PAGE3_WHEELS["front_left"], road_wheel)
    _place_asset(canvas, "wheel_front_right", PAGE3_WHEELS["front_right"], road_wheel)
    values = {
        "steering": f"{steering:+d}°", "speed": "84 km/h",
        "dcc_fl": "44 raw", "height_fl": "2.62 V", "dcc_fr": "42 raw",
        "height_fr": "2.59 V", "dcc_rl": "18 raw", "dcc_rr": "20 raw",
        "height_rear": "2.43 V",
    }
    dcc_values = {"dcc_fl", "height_fl", "dcc_fr", "height_fr",
                  "dcc_rl", "dcc_rr", "height_rear"}
    for key, value in values.items():
        if dcc_state == "unavailable" and key in dcc_values:
            continue
        size = 28 if key == "steering" else 19 if key.startswith("dcc_") else 16
        anchor = "rm" if key in ("dcc_fr", "dcc_rr", "height_fr") else "mm"
        colour = WHITE
        _text(draw, PAGE3_TEXT[key], value, size, colour=colour,
              anchor=anchor, bold=(key == "steering"))
    if dcc_state != "unavailable":
        for key, value in (("dcc_fl", 44), ("dcc_fr", 42), ("dcc_rl", 18), ("dcc_rr", 20)):
            _bar(draw, PAGE3_BARS[key], value / 60, colour=WHITE)
    return canvas


def preview_page4():
    canvas = _asset("bg_page4")
    draw = ImageDraw.Draw(canvas)
    values = [185885, 4219, 148.2, 2, "P0234", "P2261", "—", "—", "ON",
              17.6, 3, 0, 0, 6.2, 0.4]
    for (label, rect), value in zip(page4_value_rects(), values):
        _text(draw, rect, value, 22, anchor="rm",
              colour=AMBER if label == "Check engine" else WHITE)
    _place_asset(canvas, "btn_clear_codes", PAGE4_CLEAR_CODES)
    return canvas


def main():
    OUT.mkdir(exist_ok=True)
    pages = [preview_page1(), preview_page2(), preview_page3(), preview_page4()]
    for index, page in enumerate(pages, 1):
        path = OUT / f"page{index}_v2.png"
        page.convert("RGB").save(path, quality=96)
        print("wrote", path)

    contact = Image.new("RGB", (CANVAS[0] * 2, CANVAS[1] * 2), "black")
    for index, page in enumerate(pages):
        contact.paste(page.convert("RGB"), ((index % 2) * CANVAS[0],
                                            (index // 2) * CANVAS[1]))
    contact.save(OUT / "v2-all-pages.png", quality=96)
    print("wrote", OUT / "v2-all-pages.png")
    for state in ("stale", "unavailable"):
        path = OUT / f"page3_v2_{state}.png"
        preview_page3(state).convert("RGB").save(path, quality=96)
        print("wrote", path)


if __name__ == "__main__":
    main()
