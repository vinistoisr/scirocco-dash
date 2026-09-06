"""Single geometry source for the Scirocco RealDash v2 assets and previews.

All rectangles use the deck's 1280x660 design grid.  RealDash stores the v2
dashboard at 1920x990; multiply these coordinates by EDITOR_SCALE (1.5) when
patching or verifying the binary.

Why 660 and not 720: the head unit keeps its Android navigation bar on screen
(the owner switches apps with it), which leaves RealDash a 1280x660 window.
RealDash fits a dashboard to the WINDOW WIDTH and then bottom-anchors it, so a
720-tall design does not letterback -- it loses its top 60px off the top of
the screen.  Measured on the deck 2026-08-29: content drew at scale 1.000 with
a 60px upward shift, and the page titles were simply gone.  Matching the
design aspect to the window is the fix; the alternative is a uniform 8% shrink
that wastes 107px of width to side bars.
"""

CANVAS = (1280, 660)
EDITOR_CANVAS = (1920, 990)
EDITOR_SCALE = 1.5


PAGE1_DIALS = {
    "rpm": (10, 56, 396, 396),
    "boost": (416, 56, 396, 396),
    "coolant": (826, 56, 200, 200),
    "oil": (1038, 56, 200, 200),
    "charge": (826, 264, 200, 200),
    "battery": (1038, 264, 200, 200),
}

PAGE1_VALUES = {
    "rpm": (118, 355, 180, 46),
    "boost": (524, 355, 180, 46),
    "coolant": (863, 198, 126, 38),
    "oil": (1075, 198, 126, 38),
    "charge": (863, 406, 126, 38),
    "battery": (1075, 406, 126, 38),
}

PAGE1_INDICATORS = {
    "turn_left": (24, 6, 44, 44),
    "mil": (508, 6, 44, 44),
    "overtemp": (578, 6, 44, 44),
    "knock": (648, 6, 44, 44),
    "boost_deviation": (718, 6, 44, 44),
    "turn_right": (1212, 6, 44, 44),
}

# Bottom band, rebuilt on a two-row grid (2026-08-29).  The panels used to
# start at three different y values (516, 550, 616) and run to three different
# widths, so the whole lower third read as loose parts.  Both rows now use the
# full 20..1260 measure in equal thirds with equal gutters, and every panel in
# a row shares a top edge and a height.
PAGE1_ROW1_Y, PAGE1_ROW1_H = 476, 72
PAGE1_ROW2_Y, PAGE1_ROW2_H = 560, 86
PAGE1_PANELS = {
    "trip":  (20, PAGE1_ROW1_Y, 400, PAGE1_ROW1_H),
    "knock": (20, PAGE1_ROW2_Y, 400, PAGE1_ROW2_H),
    "duty":  (440, PAGE1_ROW2_Y, 400, PAGE1_ROW2_H),
    "trim":  (860, PAGE1_ROW2_Y, 400, PAGE1_ROW2_H),
}

PAGE1_TEXT = {
    "odometer": (200, 476, 200, 30),
    "trip": (200, 514, 200, 30),
    "speed": (540, 466, 200, 54),
    "gear": (462, 470, 46, 46),
    # Parked off-canvas: the moving red notch on the boost dial is the
    # readout now.  Delete from the snapshot when convenient.
    "boost_target": (1320, 480, 160, 40),
}

PAGE1_BARS = {
    "knock_1": (103, 592, 18, 36),
    "knock_2": (175, 592, 18, 36),
    "knock_3": (247, 592, 18, 36),
    "knock_4": (319, 592, 18, 36),
    "n75": (532, 586, 300, 11),
    "load": (532, 618, 300, 11),
    "trim_short_negative": (952, 586, 144, 11),
    "trim_short_positive": (1096, 586, 144, 11),
    "trim_long_negative": (952, 618, 144, 11),
    "trim_long_positive": (1096, 618, 144, 11),
}


# Page 2: three equal half-moon instruments across the top, three panels
# below.  The two 560px arcs left a dead band across the middle of the page
# and pushed the panels into a 282px strip; three 400px arcs on a common
# baseline read as a set, and the panels gain 86px of height, which is what
# actually fixes the "squished together" rows.
#
# Arc boxes are square because _semi_scale takes its centre from the box; the
# drawn arc only occupies the upper half, so the lower half is free space that
# the value readouts sit in.
# Vertical rhythm: page title baseline 30, instrument headings 66/84, arc box
# top 92 (so the drawn arc runs 120..292), readouts 300..352, panels 372..704.
# The arcs started at 56, which put their headings straight through the page
# title.
# Each instrument is centred on the panel column beneath it (267/708/1081),
# so the top and bottom halves of the page share one set of vertical axes.
PAGE2_ARC_SIZE = 330
PAGE2_ARC_TOP = 76
PAGE2_SPARK_NEEDLE = (102, PAGE2_ARC_TOP, PAGE2_ARC_SIZE, PAGE2_ARC_SIZE)
PAGE2_INJ_NEEDLE = (543, PAGE2_ARC_TOP, PAGE2_ARC_SIZE, PAGE2_ARC_SIZE)
PAGE2_AFR_RESERVED_NEEDLE = (916, PAGE2_ARC_TOP, PAGE2_ARC_SIZE, PAGE2_ARC_SIZE)

# Readouts sit on a common baseline under the three arc centres (220/640/1060).
PAGE2_SPARK_VALUE = (187, 276, 160, 50)
PAGE2_INJECTION_VALUE = (628, 276, 160, 50)
PAGE2_AFR_RESERVED_VALUE = (1001, 276, 160, 50)


# Rebalanced 2026-08-29.  AIR & LOAD carried ten rows against the other
# panels' six and four, so its pitch was cramped and it read as a wall.
#
# Intake air is the airbox temperature ahead of the turbo; charge air is
# post-intercooler manifold temperature, which is the one that drives knock.
# Ambient covers the outside air, so intake air is the redundant third.
# Throttle is the plate angle the ECU actually commanded; pedal is driver
# intent AND is snapshot-only on this ECU (see the confidence notes in
# board/scirocco_realdash.xml), so throttle is the live, diagnostic one.
# Both of those plus barometric move to the reference panel on the right,
# which had room, leaving three panels of 7/6/7 instead of 10/6/4.
PAGE2_GROUPS = [
    (
        "AIR & LOAD",
        (20, 330, 494, 316),
        [
            ("MAP", "kPa"),
            ("Charge air", "°C"),
            ("Ambient", "°C"),
            ("MAF", "g/s"),
            ("Throttle", "%"),
            ("Engine load", "%"),
            ("Boost peak", "bar"),
        ],
    ),
    (
        "FUEL DELIVERY",
        (530, 330, 356, 316),
        [
            ("Rail pressure", "bar"),
            ("Rail specified", "bar"),
            ("Rail actual", "bar"),
            ("Pump duty", "%"),
            ("Fuel temperature", "°C"),
        ],
    ),
    (
        "MIXTURE & REFERENCE",
        (902, 330, 358, 316),
        [
            ("Trim short", "%"),
            ("Trim long", "%"),
            ("Catalyst", "°C"),
            ("Lambda snapshot", ""),
            ("Barometric", "kPa"),
            ("Intake air", "°C"),
            ("Pedal", "%"),
        ],
    ),
]


# Page 3: the numbers stay outside the silhouette; the associated bars point
# toward the matching wheel.  Wheel gauges are square so their image pivots
# remain at the wheel centres.

# The car silhouette is drawn inside a group scaled about (640, PAGE3_CAR_TOP)
# by PAGE3_CAR_SCALE, which frees a band at the top for the steering readout
# and its bar and a band at the bottom for the rear ride height.  Both used to
# sit on top of the car.
PAGE3_CAR_TOP = 70
PAGE3_CAR_SCALE = 0.80

PAGE3_TEXT = {
    "steering": (550, 2, 180, 44),
    "speed": (1080, 20, 148, 38),
    "dcc_fl": (44, 122, 100, 44),
    "height_fl": (44, 196, 110, 30),
    "dcc_fr": (1136, 122, 100, 44),
    "height_fr": (1018, 196, 110, 30),
    "dcc_rl": (44, 378, 100, 44),
    "dcc_rr": (1136, 378, 100, 44),
    "height_rear": (555, 598, 170, 36),
}

# Steering as a two-way bar growing outward from a centred zero, so the
# direction and magnitude of lock read at a glance instead of having to
# parse a signed number.  Two halves meeting at 640, same pattern as the
# fuel-trim pair on page 1.
PAGE3_STEER_BARS = {
    "left": (432, 52, 208, 10),
    "right": (640, 52, 208, 10),
}

PAGE3_BARS = {
    "dcc_fl": (44, 170, 218, 7),
    "dcc_fr": (1018, 170, 218, 7),
    "dcc_rl": (44, 426, 218, 7),
    "dcc_rr": (1018, 426, 218, 7),
}

PAGE3_WHEELS = {
    "front_left": (472, 112, 88, 88),
    "front_right": (704, 112, 88, 88),
}


PAGE4_GROUPS = [
    (
        "TRIP",
        (34, 80, 380, 540),
        [
            ("Odometer", "km"),
            ("Engine run time", "s"),
            ("Distance since clear", "km"),
        ],
    ),
    (
        "DIAGNOSTICS",
        (450, 80, 380, 540),
        [
            ("Fault count", ""),
            ("Fault 1", ""),
            ("Fault 2", ""),
            ("Fault 3", ""),
            ("Fault 4", ""),
            ("Check engine", ""),
        ],
    ),
    (
        "SYSTEM HEALTH",
        (866, 80, 380, 540),
        [
            ("Sample rate", "Hz"),
            ("Reconnects", ""),
            ("Aux visits", ""),
            ("Aux failures", ""),
            ("Gear age", "s"),
            ("CAN-box age", "s"),
        ],
    ),
]

PAGE4_CLEAR_CODES = (542, 566, 196, 40)


def editor_rect(rect):
    """Convert a design-space rectangle to integer 1920x1080 coordinates."""
    return tuple(round(value * EDITOR_SCALE) for value in rect)


def page2_value_rects():
    """Return ordered (label, rect) pairs for the 20 page-2 text gauges."""
    result = []
    for title, (px, py, pw, ph), rows in PAGE2_GROUPS:
        pitch = (ph - 50) / len(rows)
        for index, (label, unit) in enumerate(rows):
            baseline = py + 48 + index * pitch
            reserve = 86 if len(unit) > 4 else 58 if unit else 18
            right = px + pw - reserve
            # baseline - 22, not - 18: RealDash centres the glyph in the
            # box, so a box centred on the label's baseline puts the value's
            # own baseline about two pixels below it.  Lifting the box by two
            # lands the two baselines together, which is what reads as
            # "aligned" when the label and its value sit side by side.
            result.append((label, (right - 118, baseline - 22, 118, 23)))
    return result


def page4_value_rects():
    """Return ordered (label, rect) pairs for the 15 page-4 text gauges."""
    result = []
    for title, (px, py, pw, ph), rows in PAGE4_GROUPS:
        # Fault text is free-form ("P0234 overboost"), not a number.
        vw = 260 if title == "DIAGNOSTICS" else 150
        pitch = min(74, (ph - 86) / max(1, len(rows)))
        for index, (label, unit) in enumerate(rows):
            baseline = py + 92 + index * pitch
            right = px + pw - (48 if unit else 18)
            result.append((label, (right - vw, baseline - 22, vw, 34)))
    return result
