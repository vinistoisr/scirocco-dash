#!/usr/bin/env python3
"""Apply verified fixed-width RealDash v2 bindings, ranges and visual fields.

The proprietary ``.rd`` format is only patched where controlled editor files
have established a stable record-local signature:

* a six-float threshold/range block follows a ``uint32(16)``;
* exactly 0x7c bytes later, ``uint32(4)`` precedes the input identifier;
* needle start/sweep radians follow the unique ``uint32(5), uint32(6)`` pair;
* text height follows the unique text-area marker and is stored in canvas
  pixels, even though the editor presents it as a percentage; and
* default needle scale text uses six repeated ``FF FF FF 32`` colours;
* level colours are serialized as six BGRA values ordered
  Normal/Warning/Critical twice; and
* image blend colours use the final six-colour block in an image record.

No variable-length field is changed.  Every signature and final value is
re-read before the atomic output replaces its temporary file.
"""

from __future__ import annotations

import argparse
import math
import struct
import tempfile
from pathlib import Path

import rd_manifest
import v2_manifest


INPUTS = {
    "Image Gauge 7": 160,                      # turn_left
    "Image Gauge 8": 161,                      # turn_right
    "Image Gauge 9": 65,                       # built-in MIL
    "Image Gauge 10": 0x1852FA0C,             # Overtemp Warning
    "Image Gauge 11": 0xD14683F8,             # Knock Warning
    "Image Gauge 12": 0x68974748,             # Boost Deviation Warning
    # "Boost Live" (0xC81 word 0, unitless copy), NOT targetId 83: RealDash
    # converts a built-in pressure channel into its own pressure unit, so
    # bound to 83 these read -10.1 for a true -0.695 bar -- that is psi, and
    # the dial face is drawn in bar.  A plain named channel is left alone.
    "Needle Gauge 2": 0xDA839F71,              # live boost
    "Needle Gauge 9": 0xDA839F71,              # boost peak ghost
    "Text Gauge 44": 0xDA839F71,               # boost numeric readout
    "Needle Gauge 8": 37,                       # built-in RPM
    "Needle Gauge 10": 38,                     # built-in ignition timing
    "Needle Gauge 13": 35,                     # built-in injector pulse
    "Needle Gauge 11": 0x82A93F20,             # steering_deg
    "Needle Gauge 12": 0x82A93F20,
    "Text Gauge 43": 38,
    "Text Gauge 54": 37,
    "Text Gauge 55": 14,                       # coolant
    "Text Gauge 56": 152,                      # oil temperature
    "Text Gauge 57": 0xBBE114DE,               # Charge Air Temp
    "Text Gauge 58": 12,                       # battery
    # Corrected 2026-08-29 after auditing every binding against what the XML
    # actually publishes, with the simulator running as the referee.  All
    # four pointed at target ids nothing publishes, so they displayed a
    # default rather than their channel -- and two of them looked plausible
    # only by coincidence.
    "Text Gauge 1": 31,                        # MAP: was 10, unpublished
    "Text Gauge 2": 11,                        # Baro: was 31, i.e. the MAP
    "Text Gauge 9": 0xE3038137,                # Pedal Position: was 230
    "Text Gauge 12": 0x906057F9,               # Lambda Commanded: was 254
    "Text Gauge 18": 0x82A93F20,
    "Bar Gauge 7": 17,                         # short trim, positive half
    "Bar Gauge 8": 102,                        # long trim, positive half
    # Outward halves read the board-clamped channels from 0xC94, which are
    # already max(0, -trim): they simply read zero for the wrong sign, so the
    # half goes dark on its own.
    "Bar Gauge 9": 0x99AB7B1F,                 # Trim Short Negative
    "Bar Gauge 10": 0x9A89C9E3,                # Trim Long Negative
    "Bar Gauge 11": 0x5F8C23E7,               # damper_1
    "Bar Gauge 12": 0x5F8C23E8,               # damper_2
    "Bar Gauge 13": 0x5F8C23E9,               # damper_3
    "Bar Gauge 14": 0x5F8C23EA,               # damper_4
    "Bar Gauge 16": 0xFE9AAE3E,               # Steering Left (negated)
    "Bar Gauge 17": 0x82A93F20,               # steering_deg
}

# Boost binds to the raw targetId, NOT to a name hash.  The v1 editor bound
# the live needle, its ghost and the numeric readout to hash("Boost") -- but
# the XML has never published a value NAMED "Boost"; it publishes boost as
# targetId 83 (0xC81 word 0).  So all three resolved to nothing and sat
# pinned at zero forever, while the boost TARGET needle -- the one gauge
# bound to a raw id, 270 -- moved.  That is exactly the reported symptom:
# "the boost gauge never moves, only the target".  Confirmed 2026-08-29 by
# grepping the XML for name="Boost": zero matches.
# Text Gauge 44 is the page 1 boost readout, but it was bound to
# hash("Lambda Commanded") -- and lambda sits at 1.00, so it read "1.0"
# whenever boost happened to be near 1 bar and looked correct.  Take the
# live boost needle's own identifier instead, the same way the ghost does.
COPY_INPUT = {}

# Pairs are (critical window, warning window, full value range).  A value
# INSIDE a window takes that level -- established 2026-08-29 by driving the
# outward trim halves from the level colours and watching which half lit.
# (An earlier comment here had it backwards.)
RANGES = {
    # Off (0) is Normal and therefore uses the faint telltale blend.  Any
    # non-zero value leaves both level windows and uses the fully opaque
    # Critical blend.  Warning is also fully opaque as a safe fallback for
    # non-binary sources.
    "Image Gauge 7": ((0, 0), (0, 0), (0, 1)),
    "Image Gauge 8": ((0, 0), (0, 0), (0, 1)),
    "Image Gauge 9": ((0, 0), (0, 0), (0, 1)),
    "Image Gauge 10": ((0, 0), (0, 0), (0, 1)),
    "Image Gauge 11": ((0, 0), (0, 0), (0, 1)),
    "Image Gauge 12": ((0, 0), (0, 0), (0, 1)),
    "Needle Gauge 8": ((0, 7000), (0, 7000), (0, 7000)),
    "Needle Gauge 9": ((-1, 2), (-1, 2), (-1, 2)),
    "Needle Gauge 10": ((-30, 45), (-30, 45), (-30, 45)),
    "Needle Gauge 13": ((0, 20), (0, 20), (0, 20)),
    "Needle Gauge 11": ((-540, 540), (-540, 540), (-540, 540)),
    "Needle Gauge 12": ((-540, 540), (-540, 540), (-540, 540)),
    "Text Gauge 43": ((-30, 45), (-30, 45), (-30, 45)),
    "Text Gauge 44": ((-1, 1.6), (-1, 1.2), (-1, 2)),
    "Text Gauge 54": ((0, 6800), (0, 6300), (0, 7000)),
    "Text Gauge 55": ((50, 110), (50, 105), (50, 130)),
    "Text Gauge 56": ((50, 130), (50, 120), (50, 150)),
    "Text Gauge 57": ((0, 60), (0, 50), (0, 120)),
    "Text Gauge 58": ((10.5, 15.8), (11, 15.2), (8, 16)),
    "Text Gauge 18": ((-600, 600), (-600, 600), (-600, 600)),
    "Bar Gauge 7": ((0, 25), (0, 25), (0, 25)),
    "Bar Gauge 8": ((0, 25), (0, 25), (0, 25)),
    "Bar Gauge 9": ((0, 25), (0, 25), (0, 25)),
    "Bar Gauge 10": ((0, 25), (0, 25), (0, 25)),
    "Bar Gauge 11": ((0, 60), (0, 60), (0, 60)),
    "Bar Gauge 12": ((0, 60), (0, 60), (0, 60)),
    "Bar Gauge 13": ((0, 60), (0, 60), (0, 60)),
    "Bar Gauge 14": ((0, 60), (0, 60), (0, 60)),
    "Bar Gauge 16": ((0, 600), (0, 600), (0, 600)),
    "Bar Gauge 17": ((0, 600), (0, 600), (0, 600)),
}

ANGLES_DEGREES = {
    "Needle Gauge 8": (225, 270),
    "Needle Gauge 9": (225, 270),
    "Needle Gauge 10": (285, 150),
    "Needle Gauge 13": (285, 150),
    "Needle Gauge 11": (328, 64),
    "Needle Gauge 12": (328, 64),
}

# RealDash presents this field as a percentage of the gauge rectangle, but
# serializes the computed height in dashboard pixels.  The editor-created text
# gauges retained their old 100%-of-75.6px value after the deterministic layout
# shrank them, which is why their placeholders appeared oversized.  Recompute
# the absolute value from the final verified rectangle instead of copying a
# stale number from an earlier canvas geometry.
TEXT_HEIGHT_PERCENT = {
    "Text Gauge 43": 70,
    "Text Gauge 54": 70,
    # The four small dials are 200 design px across; a 70% readout filled
    # roughly an eighth of the dial and dominated the face it sits on.  55%
    # puts it back in proportion with the tick band and title.
    "Text Gauge 55": 55,
    "Text Gauge 56": 55,
    "Text Gauge 57": 55,
    "Text Gauge 58": 55,
}

# UI RGBA strings.  The file stores every colour as BGRA and keeps six
# values in Normal/Warning/Critical, Normal/Warning/Critical order.
TEXT_NORMAL = "F2F4F5FF"
TEXT_WARNING = "F5A000FF"
TEXT_CRITICAL = "FF2D26FF"
# Not 00000000: a fully transparent bar colour reads as "unset" to RealDash
# and it falls back to drawing the bar red.  The bar track is filled #040506
# by the background artwork, so painting the fill that colour at full alpha
# is what actually makes a bar invisible.
TRANSPARENT = "040506FF"

TEXT_LEVEL_COLORS = {
    "Text Gauge 18": (TEXT_NORMAL, TEXT_NORMAL, TEXT_NORMAL),
    "Text Gauge 43": (TEXT_NORMAL, TEXT_WARNING, TEXT_CRITICAL),
    "Text Gauge 44": (TEXT_NORMAL, TEXT_WARNING, TEXT_CRITICAL),
    "Text Gauge 54": (TEXT_NORMAL, TEXT_WARNING, TEXT_CRITICAL),
    "Text Gauge 55": (TEXT_NORMAL, TEXT_WARNING, TEXT_CRITICAL),
    "Text Gauge 56": (TEXT_NORMAL, TEXT_WARNING, TEXT_CRITICAL),
    "Text Gauge 57": (TEXT_NORMAL, TEXT_WARNING, TEXT_CRITICAL),
    "Text Gauge 58": (TEXT_NORMAL, TEXT_WARNING, TEXT_CRITICAL),
}

BAR_LEVEL_COLORS = {
    **{"Bar Gauge %d" % number:
       (TEXT_CRITICAL, TEXT_CRITICAL, TEXT_CRITICAL)
       for number in range(1, 11)},
    **{"Bar Gauge %d" % number:
       (TEXT_NORMAL, TEXT_NORMAL, TEXT_NORMAL)
       for number in range(11, 15)},
    "Bar Gauge 17": (TEXT_NORMAL, TEXT_NORMAL, TEXT_NORMAL),
    # Outward-growing halves: invisible until their value goes negative.
    "Bar Gauge 16": (TEXT_NORMAL, TEXT_NORMAL, TEXT_NORMAL),
}

IMAGE_BLEND_COLORS = {
    "Image Gauge %d" % number: ("FFFFFF1C", "FFFFFFFF", "FFFFFFFF")
    for number in range(7, 13)
}
# The Clear Codes button rendered its artwork so dark the caption was barely
# legible, which is a tint, not an artwork problem: it carries the same
# six-colour blend block as an image gauge and was inheriting a dimmed
# default.  Full white at every level lets btn_clear_codes.png show as drawn.
IMAGE_BLEND_COLORS["Clear Codes (hold 2 s)"] = ("FFFFFFFF", "FFFFFFFF",
                                                "FFFFFFFF")

# Every needle has a baked face or a transparent overlay face.  RealDash's
# built-in white/grey scale text therefore duplicates the artwork and must be
# suppressed on the original seven needles as well as the five v2 additions.
HIDE_SCALE = {"Needle Gauge %d" % number for number in range(1, 14)}

# Peak-hold ("ghost") history and the displayed decimal count both sit at a
# fixed distance from the already-asserted range block.  Two independent
# sources agree on the layout: the v1 editor left Needle Gauges 1, 2 and 4-7
# at mode 2 with a five-second show time, and a controlled 2026-08-28 editor
# pass that set Needle Gauges 8/9 to MAX/5 s changed exactly these two fields
# and nothing else structural.  A 0xFFFFFFFF guard word immediately precedes
# the mode, which is what the signature assertion checks.
#
# Turning history OFF on the six live needles is the actual fix for the
# reported "needle hangs at peak for four or five seconds, then drops": the
# live pointers were carrying their own peak-hold all along.  The ghosts on
# Gauges 8/9 are what should hold instead.
HISTORY_SHOW_TIME_DELTA = 0x30
HISTORY_GUARD_DELTA = 0x6C
HISTORY_MODE_DELTA = 0x70
DECIMALS_DELTA = 0x84

HISTORY_MODES = {"OFF": 0, "MAX": 2}
DEFAULT_SHOW_TIME_S = 2.0
MAX_DECIMALS = 4

# RealDash presents "Value smoothing" as a percentage under Look'n Feel ->
# Special, but serializes the per-frame blend coefficient (100 - percent)/100
# in three copies, one per editing level.  Confirmed 2026-08-28 by setting
# Needle Gauge 4 to 33% between two otherwise identical saves: all three
# floats moved 0.2 -> 0.67, and the 80% it started at reads back as 0.2.
#
# Every needle and text gauge in this dashboard was sitting at RealDash's 80%
# default (bar gauges default to 10%), which is the "some gauges respond super
# fast, others are balls slow and barely move" report: at 80% a needle only
# closes a fifth of the remaining distance each frame.
SMOOTHING_DELTAS = (0x1C, 0x38, 0x3C)

# Horizontal text alignment is two single bytes immediately before the Text
# Area marker that _text_height_offset already locates, at marker - 7 and
# marker - 3.  0 = LEFT (RealDash's default), 1 = CENTER, 2 = RIGHT.
# Confirmed 2026-08-28 by switching Text Gauge 55 to CENTER between two
# otherwise identical saves: exactly those two bytes moved 0 -> 1, across the
# 71 gauges that carry a text area.
#
# The six round-gauge readouts sit in rectangles centred on their dials, so
# left-aligned text rendered hard against the rectangle's left edge and hung
# off-centre against the dial face.  Everything else keeps RealDash's LEFT:
# the page 2-4 label/value rows are built around a left-aligned value column.
TEXT_AREA_MARKER_LEN = 20
TEXT_ALIGN_MARKER_DELTAS = (-7, -3)
TEXT_ALIGNMENTS = {"LEFT": 0, "CENTER": 1, "RIGHT": 2}

TEXT_ALIGN = {_name: "CENTER" for _name in
              ("Text Gauge 44", "Text Gauge 54", "Text Gauge 55",
               "Text Gauge 56", "Text Gauge 57", "Text Gauge 58",
               # page 2's two live instrument readings, centred under their
               # own arcs rather than left-aligned inside a centred box
               "Text Gauge 43", "Text Gauge 10")}
# Road speed sits above its own "km/h" caption now, so it wants centring.
# Note RIGHT (2) was tried first and RealDash ignored it -- the byte reads
# back as 2 and the gauge still renders left-aligned -- so only LEFT and
# CENTER are proven values for this field.
TEXT_ALIGN["Text Gauge 48"] = "CENTER"
# Rear ride height reads under a centred caption on the car's axis.
TEXT_ALIGN["Text Gauge 28"] = "CENTER"
# Page 3 steering angle sits over the centre of its L/R bar, and road speed
# over its own "km/h" caption; left-aligned both read ~70 px left of centre.
TEXT_ALIGN["Text Gauge 18"] = "CENTER"
TEXT_ALIGN["Text Gauge 16"] = "CENTER"

# Bar fill direction, one byte at range + 0x1D4.  0 = LEFT TO RIGHT
# (RealDash's default), 1 = RIGHT TO LEFT.  Confirmed 2026-08-28 by cycling
# Bar Gauge 9's Look'n Feel -> Special -> Style between two saves: exactly
# that byte moved 0 -> 1.
#
# The fuel-trim readout is two bars meeting at a shared zero line, the
# negative half to the left of it.  RealDash normalises a reversed range
# (0 .. -25) by magnitude, so a left-to-right negative half renders FULL at
# zero trim and empties as trim goes negative -- backwards, and the reason
# both trim rows read solid red.  Filling right-to-left anchors the bar on
# the zero line and grows it outward, which is the intended behaviour.
# Verified live against the simulator: Bar Gauge 9 correct, Bar Gauge 10
# still full, with the style byte the only difference between them.
BAR_STYLE_DELTA = 0x1D4
BAR_STYLES = {"LEFT_TO_RIGHT": 0, "RIGHT_TO_LEFT": 1}

BAR_STYLE = {"Bar Gauge 9": "RIGHT_TO_LEFT",
             "Bar Gauge 10": "RIGHT_TO_LEFT",
             # left half of the two-way steering bar
             "Bar Gauge 16": "RIGHT_TO_LEFT"}

HISTORY = {}
for _name, _gauge in v2_manifest.GAUGES.items():
    if not _name.startswith("Needle Gauge"):
        continue
    _show = _gauge.get("history_show_time_s")
    HISTORY[_name] = (_gauge.get("history_mode") or "OFF",
                      DEFAULT_SHOW_TIME_S if _show is None else float(_show))

# The manifest owns every gauge it describes.  It does not describe the
# legacy page-4 trip block, which inherited RealDash's default single decimal
# and therefore renders the odometer as "185880.0" -- the same overflow the
# page-1 readout had.  Name that one here rather than widening the manifest.
LEGACY_DECIMALS = {
    "Text Gauge 29": 0,      # odometer, page 4
    # Counts and whole-second ages, all inherited with RealDash's default
    # single decimal, so page 4 read "Fault count 0.0" and "Aux visits 1.0"
    # (2026-08-29 visual review).  Sample rate keeps its decimal: 14.2 Hz
    # is the number that actually tells you the link is healthy.
    "Text Gauge 31": 0,      # fault count
    "Text Gauge 37": 0,      # reconnects
    "Text Gauge 38": 0,      # aux visits
    "Text Gauge 39": 0,      # aux failures
    "Text Gauge 40": 0,      # gear age, seconds
    "Text Gauge 41": 0,      # CAN-box age, seconds
    "Text Gauge 20": 0,      # catalyst temperature, page 2
}

DECIMALS = {_name: _gauge["decimals"]
            for _name, _gauge in v2_manifest.GAUGES.items()
            if _gauge.get("decimals") is not None}
DECIMALS.update(LEGACY_DECIMALS)

SMOOTHING = {_name: _gauge["smoothing"]
             for _name, _gauge in v2_manifest.GAUGES.items()
             if _gauge.get("smoothing") is not None}


def _records(path: Path, data: bytes) -> dict[str, tuple[int, int]]:
    report = rd_manifest.inspect(path)
    want_w, want_h = v2_manifest.EDITOR_CANVAS
    if (report["canvas"]["width"], report["canvas"]["height"]) != (want_w, want_h):
        raise ValueError("refusing dashboard that is not %dx%d" % (want_w, want_h))
    if report["gauge_count"] != v2_manifest.EXPECTED_GAUGE_COUNT:
        raise ValueError("refusing dashboard with %d controls, expected %d"
                         % (report["gauge_count"],
                            v2_manifest.EXPECTED_GAUGE_COUNT))
    gauges = sorted(report["gauges"], key=lambda item: item["name_offset"])
    result = {}
    for index, gauge in enumerate(gauges):
        start = gauge["name_offset"] - 4
        end = (gauges[index + 1]["name_offset"] - 4
               if index + 1 < len(gauges) else len(data))
        result[gauge["name"]] = (start, end)
    return result


def _binding_and_range_offsets(record: bytes, name: str) -> tuple[int, int]:
    marker = struct.pack("<I", 4)
    candidates = []
    for pos in range(0x100, len(record) - 8):
        range_pos = pos - 0x7C
        if (record[pos:pos + 4] == marker and range_pos >= 4 and
                struct.unpack_from("<I", record, range_pos - 4)[0] == 16):
            candidates.append((pos, range_pos))
    if len(candidates) != 1:
        raise ValueError("%s: expected one binding/range signature, found %r"
                         % (name, candidates))
    return candidates[0]


def _angle_offset(record: bytes, name: str) -> int:
    marker = struct.pack("<II", 5, 6)
    candidates = []
    pos = 0
    while True:
        pos = record.find(marker, pos)
        if pos < 0:
            break
        if pos + 16 <= len(record):
            start, sweep = struct.unpack_from("<ff", record, pos + 8)
            if 0 <= start <= math.tau * 1.1 and 0 < sweep <= math.tau * 1.1:
                candidates.append(pos + 8)
        pos += 1
    if len(candidates) != 1:
        raise ValueError("%s: expected one needle-angle signature, found %r"
                         % (name, candidates))
    return candidates[0]


def _text_height_offset(record: bytes, name: str) -> int:
    # This is the fixed-width Text Area block: u32(20), two zero u32s,
    # X/Y scale floats of 1.0, then the computed text height float.
    marker = struct.pack("<I2I2f", 20, 0, 0, 1.0, 1.0)
    candidates = []
    pos = 0
    while True:
        pos = record.find(marker, pos)
        if pos < 0:
            break
        value_pos = pos + len(marker)
        if value_pos + 4 <= len(record):
            value = struct.unpack_from("<f", record, value_pos)[0]
            if 0 < value <= 1080:
                candidates.append(value_pos)
        pos += 1
    if len(candidates) != 1:
        raise ValueError("%s: expected one text-height signature, found %r"
                         % (name, candidates))
    return candidates[0]


def _history_offsets(record: bytes, name: str, range_pos: int) -> tuple[int, int]:
    guard = struct.unpack_from("<I", record, range_pos + HISTORY_GUARD_DELTA)[0]
    if guard != 0xFFFFFFFF:
        raise ValueError("%s: history guard word is %#010x, not FFFFFFFF"
                         % (name, guard))
    mode = struct.unpack_from("<I", record, range_pos + HISTORY_MODE_DELTA)[0]
    if mode > max(HISTORY_MODES.values()):
        raise ValueError("%s: history mode reads %d" % (name, mode))
    return (range_pos + HISTORY_SHOW_TIME_DELTA,
            range_pos + HISTORY_MODE_DELTA)


def _decimals_offset(record: bytes, name: str, range_pos: int) -> int:
    position = range_pos + DECIMALS_DELTA
    current = struct.unpack_from("<I", record, position)[0]
    if current > MAX_DECIMALS:
        raise ValueError("%s: decimal count reads %d" % (name, current))
    return position


def _smoothing_offsets(record: bytes, name: str, range_pos: int) -> list[int]:
    positions = [range_pos + delta for delta in SMOOTHING_DELTAS]
    values = [struct.unpack_from("<f", record, position)[0]
              for position in positions]
    if len({round(value, 6) for value in values}) != 1:
        raise ValueError("%s: smoothing copies disagree: %r" % (name, values))
    if not 0.0 < values[0] <= 1.0:
        raise ValueError("%s: smoothing coefficient is %r" % (name, values[0]))
    return positions


def _text_align_offsets(record: bytes, name: str) -> list[int]:
    marker_start = _text_height_offset(record, name) - TEXT_AREA_MARKER_LEN
    positions = [marker_start + delta for delta in TEXT_ALIGN_MARKER_DELTAS]
    values = {record[position] for position in positions}
    if len(values) != 1:
        raise ValueError("%s: alignment copies disagree: %r" % (name, values))
    current = values.pop()
    if current not in TEXT_ALIGNMENTS.values():
        raise ValueError("%s: alignment reads %d" % (name, current))
    return positions


def _bar_style_offset(record: bytes, name: str, range_pos: int) -> int:
    position = range_pos + BAR_STYLE_DELTA
    current = record[position]
    if current not in BAR_STYLES.values():
        raise ValueError("%s: bar style reads %d" % (name, current))
    return position


def _smoothing_coefficient(percent: float) -> float:
    if not 0 <= percent <= 100:
        raise ValueError("smoothing must be a percentage, got %r" % percent)
    return (100.0 - percent) / 100.0


def _bgra(rgba: str) -> bytes:
    value = bytes.fromhex(rgba)
    if len(value) != 4:
        raise ValueError("invalid RGBA colour %r" % rgba)
    red, green, blue, alpha = value
    return bytes((blue, green, red, alpha))


def _level_color_bytes(colors: tuple[str, str, str]) -> bytes:
    normal, warning, critical = colors
    return b"".join(_bgra(value) for value in
                    (normal, warning, critical, normal, warning, critical))


def _text_color_offset(record: bytes, name: str) -> int:
    white = bytes.fromhex("FF FF FF FF") * 6
    black = bytes.fromhex("00 00 00 AA") * 6
    header = struct.pack("<II", 1, 0)
    marker = header + white + header + white + header + black
    positions = []
    pos = 0
    while True:
        pos = record.find(marker, pos)
        if pos < 0:
            break
        positions.append(pos + len(header))
        pos += 1
    if len(positions) != 1:
        raise ValueError("%s: expected one text-colour signature, found %r"
                         % (name, positions))
    return positions[0]


def _bar_color_offset(record: bytes, name: str) -> int:
    marker = bytes.fromhex("04 03 02 01") + struct.pack("<II", 1, 0)
    positions = []
    pos = 0
    while True:
        pos = record.find(marker, pos)
        if pos < 0:
            break
        positions.append(pos + len(marker))
        pos += 1
    if len(positions) != 1:
        raise ValueError("%s: expected one bar-colour signature, found %r"
                         % (name, positions))
    return positions[0]


def _image_blend_offset(record: bytes, name: str) -> int:
    # The generic image record contains several state-colour arrays.  The
    # image-blend array is the final contiguous six-white block before the
    # record footer; controlled editor saves established this stable locator.
    white = bytes.fromhex("FF FF FF FF") * 6
    positions = []
    pos = 0
    while True:
        pos = record.find(white, pos)
        if pos < 0:
            break
        positions.append(pos)
        pos += 1
    if not positions:
        raise ValueError("%s: image-blend colour block not found" % name)
    return positions[-1]


def _apply_scalar_fields(data: bytearray, records: dict,
                                reference: bytes) -> list[str]:
    """Write the peak-hold history, decimal-count and smoothing fields.

    Unlike the colour and scale-text stages, neither field consumes the
    signature used to find it, so this pass is idempotent and may be applied
    on its own to an already-configured dashboard.
    """
    report = []
    for name, (mode, show_time) in sorted(HISTORY.items()):
        start, end = records[name]
        record = reference[start:end]
        _, range_pos = _binding_and_range_offsets(record, name)
        show_pos, mode_pos = _history_offsets(record, name, range_pos)
        struct.pack_into("<f", data, start + show_pos, show_time)
        struct.pack_into("<I", data, start + mode_pos, HISTORY_MODES[mode])
        report.append("%s history=%s/%gs" % (name, mode, show_time))

    for name, decimals in sorted(DECIMALS.items()):
        start, end = records[name]
        record = reference[start:end]
        _, range_pos = _binding_and_range_offsets(record, name)
        position = _decimals_offset(record, name, range_pos)
        struct.pack_into("<I", data, start + position, decimals)
        report.append("%s decimals=%d" % (name, decimals))

    for name, percent in sorted(SMOOTHING.items()):
        start, end = records[name]
        record = reference[start:end]
        _, range_pos = _binding_and_range_offsets(record, name)
        coefficient = _smoothing_coefficient(percent)
        for position in _smoothing_offsets(record, name, range_pos):
            struct.pack_into("<f", data, start + position, coefficient)
        report.append("%s smoothing=%g%% (%.2f)" % (name, percent, coefficient))

    for name, alignment in sorted(TEXT_ALIGN.items()):
        start, end = records[name]
        record = reference[start:end]
        for position in _text_align_offsets(record, name):
            data[start + position] = TEXT_ALIGNMENTS[alignment]
        report.append("%s text-align=%s" % (name, alignment))

    for name, style in sorted(BAR_STYLE.items()):
        start, end = records[name]
        record = reference[start:end]
        _, range_pos = _binding_and_range_offsets(record, name)
        position = _bar_style_offset(record, name, range_pos)
        data[start + position] = BAR_STYLES[style]
        report.append("%s bar-style=%s" % (name, style))
    return report


def _verify_scalar_fields(finished: bytes, records: dict) -> None:
    for name, (mode, show_time) in HISTORY.items():
        start, end = records[name]
        _, range_pos = _binding_and_range_offsets(finished[start:end], name)
        actual_mode = struct.unpack_from(
            "<I", finished, start + range_pos + HISTORY_MODE_DELTA)[0]
        actual_show = struct.unpack_from(
            "<f", finished, start + range_pos + HISTORY_SHOW_TIME_DELTA)[0]
        if (actual_mode != HISTORY_MODES[mode] or
                abs(actual_show - show_time) > 0.001):
            raise AssertionError("%s history verification failed: %d/%r"
                                 % (name, actual_mode, actual_show))

    for name, decimals in DECIMALS.items():
        start, end = records[name]
        _, range_pos = _binding_and_range_offsets(finished[start:end], name)
        actual = struct.unpack_from("<I", finished,
                                    start + range_pos + DECIMALS_DELTA)[0]
        if actual != decimals:
            raise AssertionError("%s decimals verification failed: %d"
                                 % (name, actual))

    for name, percent in SMOOTHING.items():
        start, end = records[name]
        record = finished[start:end]
        _, range_pos = _binding_and_range_offsets(record, name)
        expected = _smoothing_coefficient(percent)
        for delta in SMOOTHING_DELTAS:
            actual = struct.unpack_from("<f", finished,
                                        start + range_pos + delta)[0]
            if abs(actual - expected) > 0.0001:
                raise AssertionError("%s smoothing verification failed: %r"
                                     % (name, actual))

    for name, alignment in TEXT_ALIGN.items():
        start, end = records[name]
        record = finished[start:end]
        for position in _text_align_offsets(record, name):
            if record[position] != TEXT_ALIGNMENTS[alignment]:
                raise AssertionError("%s alignment verification failed: %d"
                                     % (name, record[position]))

    for name, style in BAR_STYLE.items():
        start, end = records[name]
        record = finished[start:end]
        _, range_pos = _binding_and_range_offsets(record, name)
        if record[range_pos + BAR_STYLE_DELTA] != BAR_STYLES[style]:
            raise AssertionError("%s bar-style verification failed: %d"
                                 % (name, record[range_pos + BAR_STYLE_DELTA]))


def configure_fields(source: Path, output: Path) -> list[str]:
    """Apply only the idempotent history, decimal and smoothing fields.

    The full ``patch`` is a one-shot: its colour and scale-text stages
    recognise RealDash's defaults and consume them, so it must run on a
    freshly transformed file.  This entry point exists for the case where
    that pipeline cannot be re-run -- notably when ``dash/assets/png`` is
    unreadable and ``rd_transform_v2`` therefore cannot rebuild the asset
    table -- and only these fields need to change.
    """
    original = source.read_bytes()
    data = bytearray(original)
    records = _records(source, original)
    missing = sorted((set(HISTORY) | set(DECIMALS) | set(SMOOTHING) |
                      set(TEXT_ALIGN) | set(BAR_STYLE)) - set(records))
    if missing:
        raise ValueError("missing controls: %r" % missing)

    report = _apply_scalar_fields(data, records, original)

    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=output.parent, delete=False,
                                     prefix=output.name + ".",
                                     suffix=".tmp") as temp:
        temp.write(data)
        temp_path = Path(temp.name)
    temp_path.replace(output)

    finished = output.read_bytes()
    final_records = _records(output, finished)
    if set(final_records) != set(records):
        raise AssertionError("gauge inventory changed")
    _verify_scalar_fields(finished, final_records)
    return report


def patch(source: Path, output: Path) -> list[str]:
    original = source.read_bytes()
    data = bytearray(original)
    records = _records(source, original)
    required = (set(INPUTS) | set(COPY_INPUT) | set(RANGES) |
                set(ANGLES_DEGREES) | set(TEXT_HEIGHT_PERCENT) |
                set(TEXT_LEVEL_COLORS) | set(BAR_LEVEL_COLORS) |
                set(IMAGE_BLEND_COLORS) | HIDE_SCALE |
                set(HISTORY) | set(DECIMALS) | set(SMOOTHING) |
                set(TEXT_ALIGN) | set(BAR_STYLE))
    missing = sorted(required - set(records))
    if missing:
        raise ValueError("missing controls: %r" % missing)

    locations = {}
    for name in required | set(COPY_INPUT.values()):
        start, end = records[name]
        binding, range_pos = _binding_and_range_offsets(original[start:end], name)
        locations[name] = (start + binding, start + range_pos)

    bindings = dict(INPUTS)
    for name, donor in COPY_INPUT.items():
        donor_binding = locations[donor][0]
        bindings[name] = struct.unpack_from("<I", original, donor_binding + 4)[0]

    report = []
    for name, input_id in bindings.items():
        binding = locations[name][0]
        struct.pack_into("<I", data, binding + 4, input_id)
        report.append("%s input=0x%08X" % (name, input_id))

    for name, windows in RANGES.items():
        range_pos = locations[name][1]
        values = tuple(float(value) for pair in windows for value in pair)
        struct.pack_into("<6f", data, range_pos, *values)
        report.append("%s ranges=%r" % (name, windows))

    for name, (start_deg, sweep_deg) in ANGLES_DEGREES.items():
        record_start, record_end = records[name]
        record = bytes(data[record_start:record_end])
        local_angle = _angle_offset(record, name)
        struct.pack_into("<ff", data, record_start + local_angle,
                         math.radians(start_deg), math.radians(sweep_deg))
        report.append("%s angles=%g/%g" % (name, start_deg, sweep_deg))

    report.extend(_apply_scalar_fields(data, records, original))

    manifest_gauges = {item["name"]: item for item in rd_manifest.inspect(source)["gauges"]}
    for name, percent in TEXT_HEIGHT_PERCENT.items():
        record_start, record_end = records[name]
        record = bytes(data[record_start:record_end])
        local_height = _text_height_offset(record, name)
        rect = manifest_gauges[name]["rect_normalised"]
        if rect is None:
            raise ValueError("%s: no verified rectangle" % name)
        gauge_height = (rect[3] - rect[1]) * 1080.0
        height = gauge_height * percent / 100.0
        struct.pack_into("<f", data, record_start + local_height, height)
        report.append("%s text-height=%g%% (%g px)" % (name, percent, height))

    color_locations = {}
    for name, colors in TEXT_LEVEL_COLORS.items():
        record_start, record_end = records[name]
        local = _text_color_offset(original[record_start:record_end], name)
        absolute = record_start + local
        encoded = _level_color_bytes(colors)
        data[absolute:absolute + len(encoded)] = encoded
        color_locations[name] = (absolute, encoded)
        report.append("%s text-colors=%s/%s/%s" % ((name,) + colors))

    for name, colors in BAR_LEVEL_COLORS.items():
        record_start, record_end = records[name]
        local = _bar_color_offset(original[record_start:record_end], name)
        absolute = record_start + local
        encoded = _level_color_bytes(colors)
        data[absolute:absolute + len(encoded)] = encoded
        color_locations[name] = (absolute, encoded)
        report.append("%s bar-colors=%s/%s/%s" % ((name,) + colors))

    for name, colors in IMAGE_BLEND_COLORS.items():
        record_start, record_end = records[name]
        local = _image_blend_offset(original[record_start:record_end], name)
        absolute = record_start + local
        encoded = _level_color_bytes(colors)
        data[absolute:absolute + len(encoded)] = encoded
        color_locations[name] = (absolute, encoded)
        report.append("%s blend-colors=%s/%s/%s" % ((name,) + colors))

    hidden = bytes.fromhex("FF FF FF 32") * 6
    # RealDash stores these colours as RGBA.  Alpha AA merely made the
    # duplicate native labels dark, which remained visible on the baked
    # faces.  Zero alpha is the actual invisible state.
    invisible = bytes.fromhex("00 00 00 00") * 6
    for name in sorted(HIDE_SCALE):
        record_start, record_end = records[name]
        current = bytes(data[record_start:record_end])
        count = current.count(hidden)
        if count < 1:
            raise ValueError("%s: default scale-colour block not found" % name)
        data[record_start:record_end] = current.replace(hidden, invisible)
        report.append("%s scale-text=hidden (%d blocks)" % (name, count))

    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=output.parent, delete=False,
                                     prefix=output.name + ".", suffix=".tmp") as temp:
        temp.write(data)
        temp_path = Path(temp.name)
    temp_path.replace(output)

    finished = output.read_bytes()
    final_records = _records(output, finished)
    if set(final_records) != set(records):
        raise AssertionError("gauge inventory changed")
    for name, input_id in bindings.items():
        start, end = final_records[name]
        binding, _ = _binding_and_range_offsets(finished[start:end], name)
        actual = struct.unpack_from("<I", finished, start + binding + 4)[0]
        if actual != input_id:
            raise AssertionError("%s input verification failed" % name)
    for name, windows in RANGES.items():
        start, end = final_records[name]
        _, range_pos = _binding_and_range_offsets(finished[start:end], name)
        actual = struct.unpack_from("<6f", finished, start + range_pos)
        expected = tuple(float(value) for pair in windows for value in pair)
        if any(abs(a - b) > 0.001 for a, b in zip(actual, expected)):
            raise AssertionError("%s range verification failed: %r" % (name, actual))
    _verify_scalar_fields(finished, final_records)
    for name, percent in TEXT_HEIGHT_PERCENT.items():
        start, end = final_records[name]
        local_height = _text_height_offset(finished[start:end], name)
        actual = struct.unpack_from("<f", finished, start + local_height)[0]
        gauge = {item["name"]: item for item in rd_manifest.inspect(output)["gauges"]}[name]
        rect = gauge["rect_normalised"]
        expected = (rect[3] - rect[1]) * 1080.0 * percent / 100.0
        if abs(actual - expected) > 0.001:
            raise AssertionError("%s text-height verification failed: %r" %
                                 (name, actual))
    for name, (absolute, expected) in color_locations.items():
        actual = finished[absolute:absolute + len(expected)]
        if actual != expected:
            raise AssertionError("%s colour verification failed: %s" %
                                 (name, actual.hex()))
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--fields-only", action="store_true",
                        help="apply only the idempotent history, decimal and "
                             "smoothing fields to an already-configured "
                             "dashboard")
    args = parser.parse_args()
    run = configure_fields if args.fields_only else patch
    for line in run(args.source, args.output):
        print(line)


if __name__ == "__main__":
    main()
