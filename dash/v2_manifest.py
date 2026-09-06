#!/usr/bin/env python3
"""Semantic gauge manifest for ``scirocco-v2.rd``.

RealDash assigns generic record names (``Text Gauge 18``), so this file is
the auditable bridge between those names, their real inputs, page ownership,
assets, settings and the geometry shared with the rendered previews.  The
editor remains authoritative for bindings and page moves; ``rd_manifest.py``
uses only the verified rectangle fields from this manifest.
"""

from __future__ import annotations

import json

from layout_v2 import PAGE1_BARS, PAGE1_DIALS, PAGE1_INDICATORS
from layout_v2 import PAGE2_INJ_NEEDLE, PAGE2_INJECTION_VALUE
from layout_v2 import PAGE3_STEER_BARS
from layout_v2 import PAGE1_TEXT, PAGE1_VALUES, PAGE2_SPARK_NEEDLE
from layout_v2 import PAGE2_SPARK_VALUE, PAGE3_BARS, PAGE3_TEXT, PAGE3_WHEELS
from layout_v2 import PAGE4_CLEAR_CODES
from layout_v2 import EDITOR_CANVAS
from layout_v2 import editor_rect, page2_value_rects, page4_value_rects


TEXT_NORMAL = "F2F4F5FF"
TEXT_WARNING = "F5A000FF"
TEXT_CRITICAL = "FF2D26FF"
NEUTRAL_BLEND = "FFFFFFFF"

PAGE_BACKGROUNDS = {
    1: "bg_page1.png",
    2: "bg_page2.png",
    3: "bg_page3.png",
    4: "bg_page4.png",
}

GAUGES: dict[str, dict] = {}


def original_page(name):
    """Page in the untouched 80-gauge dashboard; new records return None."""
    if name == "Clear Codes (hold 2 s)":
        return None
    kind, _, number_text = name.partition(" Gauge ")
    number = int(number_text)
    if kind == "Image":
        return 1 if number <= 12 else None
    if kind == "Needle":
        return 1 if number <= 7 else None
    if kind == "Bar":
        return 1 if number <= 8 else None
    if kind == "Text":
        if 43 <= number <= 49:
            return 1
        if 1 <= number <= 42 or 50 <= number <= 53:
            return 2
    return None


def add(name, page, role, rect, channel=None, **settings):
    if name in GAUGES:
        raise ValueError("duplicate gauge %s" % name)
    GAUGES[name] = {
        "page": page,
        "source_page": original_page(name),
        "role": role,
        "rect_design": rect,
        "rect_editor": editor_rect(rect),
        "channel": channel,
        **settings,
    }


# ---------------------------------------------------------------- page 1
for name, key, asset in (
    ("Image Gauge 1", "boost", "face_boost.png"),
    ("Image Gauge 2", "rpm", "face_rpm.png"),
    ("Image Gauge 3", "coolant", "face_coolant.png"),
    ("Image Gauge 4", "oil", "face_oil.png"),
    ("Image Gauge 5", "charge", "face_charge.png"),
    ("Image Gauge 6", "battery", "face_volt.png"),
):
    add(name, 1, "%s face" % key, PAGE1_DIALS[key], asset=asset,
        image_blend=NEUTRAL_BLEND)

for name, key, channel, limits, asset, smoothing in (
    ("Needle Gauge 1", "rpm", "rpm", (0, 7000), "needle_main.png", 0),
    ("Needle Gauge 2", "boost", "boost", (-1, 2), "needle_main.png", 0),
    ("Needle Gauge 3", "boost", "boost_target", (-1, 2), "needle_target.png", 0),
    ("Needle Gauge 4", "coolant", "coolant_c", (50, 130), "needle_small.png", 80),
    ("Needle Gauge 5", "oil", "oil_c", (50, 150), "needle_small.png", 80),
    ("Needle Gauge 6", "charge", "charge_air_temp", (0, 120), "needle_small.png", 80),
    ("Needle Gauge 7", "battery", "battery_v", (8, 16), "needle_small.png", 80),
):
    add(name, 1, "%s live needle" % key, PAGE1_DIALS[key], channel,
        value_range=limits, start_angle=225, sweep_angle=270,
        smoothing=smoothing, asset=asset, image_blend=NEUTRAL_BLEND,
        needle_blend=NEUTRAL_BLEND, scale_text_opacity=0)

for name, key, channel, limits in (
    ("Needle Gauge 8", "rpm", "rpm", (0, 7000)),
    ("Needle Gauge 9", "boost", "boost", (-1, 2)),
):
    add(name, 1, "%s peak ghost" % key, PAGE1_DIALS[key], channel,
        value_range=limits, start_angle=225, sweep_angle=270, smoothing=0,
        # 60s, not 5.  A short window means the ghost only separates from the
        # live needle during a decay, so at steady rpm it hides underneath and
        # reads as "the peak-hold isn't working".  A minute makes it the
        # thing a tuner actually wants: the peak from the last pull.
        history_mode="MAX", history_show_time_s=60,
        visibility_channel="Show Ghost %s" % ("RPM" if key == "rpm" else "Boost"),
        asset="needle_main_ghost.png", image_blend=NEUTRAL_BLEND,
        needle_blend=NEUTRAL_BLEND, scale_text_opacity=0)

readouts = (
    ("Text Gauge 44", "boost", "boost", 1, (-1, 2), (-1, 1.2), (-1, 1.6)),
    ("Text Gauge 54", "rpm", "rpm", 0, (0, 7000), (0, 6300), (0, 6800)),
    ("Text Gauge 55", "coolant", "coolant_c", 0, (50, 130), (50, 105), (50, 110)),
    ("Text Gauge 56", "oil", "oil_c", 0, (50, 150), (50, 120), (50, 130)),
    ("Text Gauge 57", "charge", "charge_air_temp", 0, (0, 120), (0, 50), (0, 60)),
    ("Text Gauge 58", "battery", "battery_v", 1, (8, 16), (11, 15.2), (10.5, 15.8)),
)
for name, key, channel, decimals, limits, warning, critical in readouts:
    add(name, 1, "%s numeric readout" % key, PAGE1_VALUES[key], channel,
        decimals=decimals, value_range=limits, warning_window=warning,
        critical_window=critical, smoothing=0 if key in ("rpm", "boost") else 80,
        text_colors=(TEXT_NORMAL, TEXT_WARNING, TEXT_CRITICAL))

for name, key, channel, decimals in (
    ("Text Gauge 45", "boost_target", "boost_target", 2),
    ("Text Gauge 46", "odometer", "odometer", 0),
    ("Text Gauge 47", "trip", "distance_since_clear", 1),
    ("Text Gauge 48", "speed", "speed_kmh", 0),
    ("Text Gauge 49", "gear", "gear", 0),
):
    add(name, 1, key.replace("_", " "), PAGE1_TEXT[key], channel,
        decimals=decimals, smoothing=0, text_color=TEXT_NORMAL)

for index, channel in enumerate(("knock_cyl_1", "knock_cyl_2",
                                 "knock_cyl_3", "knock_cyl_4"), 1):
    add("Bar Gauge %d" % index, 1, "knock cylinder %d" % index,
        PAGE1_BARS["knock_%d" % index], channel, value_range=(0, -12),
        smoothing=0, bar_color=TEXT_CRITICAL)

add("Bar Gauge 5", 1, "N75 duty", PAGE1_BARS["n75"], "n75_duty",
    value_range=(0, 100), smoothing=0, bar_color=TEXT_CRITICAL)
add("Bar Gauge 6", 1, "engine load", PAGE1_BARS["load"], "engine_load",
    value_range=(0, 100), smoothing=0, bar_color=TEXT_CRITICAL)
add("Bar Gauge 7", 1, "short trim positive", PAGE1_BARS["trim_short_positive"],
    "trim_short", value_range=(0, 25), direction="left-to-right",
    smoothing=0, bar_color=TEXT_CRITICAL)
add("Bar Gauge 8", 1, "long trim positive", PAGE1_BARS["trim_long_positive"],
    "trim_long", value_range=(0, 25), direction="left-to-right",
    smoothing=0, bar_color=TEXT_CRITICAL)
add("Bar Gauge 9", 1, "short trim negative", PAGE1_BARS["trim_short_negative"],
    "trim_short", value_range=(0, -25), direction="right-to-left",
    smoothing=0, bar_color=TEXT_CRITICAL)
add("Bar Gauge 10", 1, "long trim negative", PAGE1_BARS["trim_long_negative"],
    "trim_long", value_range=(0, -25), direction="right-to-left",
    smoothing=0, bar_color=TEXT_CRITICAL)

for name, key, channel, asset in (
    ("Image Gauge 7", "turn_left", "turn_left", "ind_turn_left.png"),
    ("Image Gauge 8", "turn_right", "turn_right", "ind_turn_right.png"),
    ("Image Gauge 9", "mil", "check_engine", "ind_mil.png"),
    ("Image Gauge 10", "overtemp", "overtemp_warning", "ind_overtemp.png"),
    ("Image Gauge 11", "knock", "knock_warning", "ind_knock.png"),
    ("Image Gauge 12", "boost_deviation", "boost_deviation_warning", "ind_boostdev.png"),
):
    add(name, 1, "%s telltale" % key, PAGE1_INDICATORS[key], channel,
        value_range=(0, 1), asset=asset, normal_opacity=0.11,
        active_opacity=1.0, smoothing=0)


# ---------------------------------------------------------------- page 2
add("Needle Gauge 10", 2, "spark advance needle", PAGE2_SPARK_NEEDLE,
    "timing_deg", value_range=(-30, 45), start_angle=285, sweep_angle=150,
    smoothing=0, asset="needle_spark.png", image_blend=NEUTRAL_BLEND,
    needle_blend=NEUTRAL_BLEND, scale_text_opacity=0)
add("Text Gauge 43", 2, "spark advance value", PAGE2_SPARK_VALUE,
    "timing_deg", decimals=1, value_range=(-30, 45), smoothing=0,
    text_color=TEXT_NORMAL)

# Order must match layout_v2.PAGE2_GROUPS row for row: page2_value_rects()
# walks the groups and these are zipped against it.
page2_records = (
    # AIR & LOAD
    ("Text Gauge 1", "map_kpa"),
    ("Text Gauge 4", "charge_air_temp"),
    ("Text Gauge 5", "ambient_temp"),
    ("Text Gauge 6", "maf"),
    ("Text Gauge 8", "throttle"),
    ("Text Gauge 17", "engine_load"),
    ("Text Gauge 7", "boost_peak"),
    # FUEL DELIVERY  (injection is promoted to the top row, below)
    ("Text Gauge 11", "fuel_rail_pressure"),
    ("Text Gauge 50", "rail_spec_bar_abs"),
    ("Text Gauge 51", "rail_actual_bar_abs"),
    ("Text Gauge 52", "fuel_pump_duty"),
    ("Text Gauge 53", "fuel_temp"),
    # MIXTURE & REFERENCE
    ("Text Gauge 13", "trim_short"),
    ("Text Gauge 14", "trim_long"),
    ("Text Gauge 20", "catalyst_temp"),
    ("Text Gauge 12", "lambda_commanded"),
    ("Text Gauge 2", "baro"),
    ("Text Gauge 3", "iat"),
    ("Text Gauge 9", "pedal_position"),
)
for (name, channel), (label, rect) in zip(page2_records, page2_value_rects()):
    add(name, 2, label, rect, channel, smoothing=0, text_color=TEXT_NORMAL)

# Injector pulse width, promoted to the top row.  The bar carries the amber
# and red context: warning past 12 ms, critical past 16, on a 20 ms scale --
# at 7000 rpm one engine cycle is about 17 ms, so 16 is genuinely close to
# the injector running out of time.
add("Text Gauge 10", 2, "Injection", PAGE2_INJECTION_VALUE, "inj_ms",
    decimals=1, smoothing=0, text_color=TEXT_NORMAL)
add("Needle Gauge 13", 2, "injection pulse width needle", PAGE2_INJ_NEEDLE,
    "inj_ms", value_range=(0, 20), start_angle=285, sweep_angle=150,
    smoothing=0, asset="needle_spark.png", image_blend=NEUTRAL_BLEND,
    needle_blend=NEUTRAL_BLEND, scale_text_opacity=0)


# ---------------------------------------------------------------- page 3
for name, key, channel, decimals in (
    ("Text Gauge 18", "steering", "steering_deg", 0),
    ("Text Gauge 16", "speed", "speed_kmh", 0),
    ("Text Gauge 22", "dcc_fl", "damper_1", 0),
    ("Text Gauge 26", "height_fl", "ride_height_fl", 2),
    ("Text Gauge 23", "dcc_fr", "damper_2", 0),
    ("Text Gauge 27", "height_fr", "ride_height_fr", 2),
    ("Text Gauge 24", "dcc_rl", "damper_3", 0),
    ("Text Gauge 25", "dcc_rr", "damper_4", 0),
    ("Text Gauge 28", "height_rear", "ride_height_rear", 2),
):
    settings = {"decimals": decimals, "smoothing": 0, "text_color": TEXT_NORMAL}
    if key == "steering":
        settings["value_range"] = (-600, 600)
    if key.startswith("dcc_") and key != "dcc_age" or key.startswith("height_"):
        settings["visibility_channel"] = "dcc_status"
        settings["visibility_range"] = (1, 2)
    if key == "dcc_age":
        settings.update(value_range=(0, 65535), warning_window=(0, 30),
                        critical_window=(0, 60),
                        text_colors=(TEXT_NORMAL, TEXT_WARNING, "666A70FF"))
    add(name, 3, key.replace("_", " "), PAGE3_TEXT[key], channel, **settings)

for name, key, asset in (
    ("Needle Gauge 11", "front_left", "wheel_front_left.png"),
    ("Needle Gauge 12", "front_right", "wheel_front_right.png"),
):
    add(name, 3, "%s schematic wheel" % key, PAGE3_WHEELS[key],
        "steering_deg", value_range=(-540, 540), start_angle=328,
        sweep_angle=64, smoothing=0, asset=asset,
        image_blend=NEUTRAL_BLEND, needle_blend=NEUTRAL_BLEND,
        scale_text_opacity=0)

for number, key, channel in (
    (11, "dcc_fl", "damper_1"),
    (12, "dcc_fr", "damper_2"),
    (13, "dcc_rl", "damper_3"),
    (14, "dcc_rr", "damper_4"),
):
    add("Bar Gauge %d" % number, 3, "%s response" % key,
        PAGE3_BARS[key], channel, value_range=(0, 60), smoothing=0,
        bar_color=TEXT_NORMAL, visibility_channel="dcc_status",
        visibility_range=(1, 2))

# Two-way steering bar.  The left half reads the negated steering channel
# with a positive range so a right-hand lock clamps it to empty instead of
# saturating it -- the same arrangement as the fuel-trim pair, and for the
# same reason (see board/scirocco_realdash.xml).
add("Bar Gauge 16", 3, "steering left", PAGE3_STEER_BARS["left"],
    "steering_left", value_range=(0, 600), direction="right-to-left",
    smoothing=0, bar_color=TEXT_NORMAL)
add("Bar Gauge 17", 3, "steering right", PAGE3_STEER_BARS["right"],
    "steering_deg", value_range=(0, 600), direction="left-to-right",
    smoothing=0, bar_color=TEXT_NORMAL)


# ---------------------------------------------------------------- page 4
page4_records = (
    ("Text Gauge 29", "odometer"),
    ("Text Gauge 21", "run_time"),
    ("Text Gauge 30", "distance_since_clear"),
    ("Text Gauge 31", "fault_codes"),
    ("Text Gauge 32", "fault_1"),
    ("Text Gauge 33", "fault_2"),
    ("Text Gauge 34", "fault_3"),
    ("Text Gauge 35", "fault_4"),
    ("Text Gauge 42", "check_engine"),
    ("Text Gauge 36", "sample_rate"),
    ("Text Gauge 37", "reconnects"),
    ("Text Gauge 38", "aux_visits"),
    ("Text Gauge 39", "aux_fails"),
    ("Text Gauge 40", "gear_age_s"),
    ("Text Gauge 41", "canbox_age"),
)
for (name, channel), (label, rect) in zip(page4_records, page4_value_rects()):
    add(name, 4, label, rect, channel, smoothing=0, text_color=TEXT_NORMAL)

add("Clear Codes (hold 2 s)", 4, "guarded clear fault codes", PAGE4_CLEAR_CODES,
    "clear_codes", asset="btn_clear_codes.png", action_type="Hold Value",
    action_target="Cmd Clear Codes", hold_value=1, initial_action_delay_ms=2000,
    actions_when_pressed_down=True,
    safety="board refuses command above 0 km/h")


EXPECTED_GAUGE_COUNT = 98
assert len(GAUGES) == EXPECTED_GAUGE_COUNT, len(GAUGES)


if __name__ == "__main__":
    print(json.dumps({
        "expected_gauge_count": EXPECTED_GAUGE_COUNT,
        "page_backgrounds": PAGE_BACKGROUNDS,
        "gauges": GAUGES,
    }, indent=2))
