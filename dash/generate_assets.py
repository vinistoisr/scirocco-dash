#!/usr/bin/env python3
"""Generate the Scirocco RealDash v2 asset pack (4 pages).

Writes SVG sources to dash/assets/svg/, then renders transparent PNGs to
dash/assets/png/ with the bundled Node.js/Sharp runtime. Faces render at 2x for crispness;
the deck canvas is 1280x660 (built on a 1920x990 editor canvas, 1.5x).
The height is 660, not 720, because the head unit keeps its Android nav bar
on screen -- see the note at the top of layout_v2.py.

STYLE REFERENCE: the car's own Mk3 Scirocco cluster and the dash-top pod
(oil / chrono / turbo). Photographed details this file reproduces:
  * bright chrome bezel ring, thin, lit from top and bottom
  * dead-black face, no coloured zone blocks
  * dense band of fine WHITE ticks at the rim; longer/heavier majors
  * redline drawn as RED TICKS inside that band, not a filled arc
  * large white numerals sitting INSIDE the tick band
  * thin red needle from a small dark hub with a chrome collar
  * small white sub-labels ("1/min x1000", "bar", "Oil")

NOTE ON COLOUR: everything here is rendered in true colour (white markings,
neutral chrome). RealDash multiplies a per-gauge "colour" over image gauges,
so each image gauge must have its colour set to pure white (#FFFFFFFF) or
the whole face renders tinted (it came out pink under the stock red theme).
"""

import math
import os
import shutil
import subprocess
import sys
import tempfile
from xml.sax.saxutils import escape

from layout_v2 import CANVAS
from layout_v2 import PAGE1_BARS, PAGE1_DIALS, PAGE1_INDICATORS
from layout_v2 import PAGE1_PANELS, PAGE1_TEXT
from layout_v2 import PAGE2_AFR_RESERVED_NEEDLE, PAGE2_AFR_RESERVED_VALUE
from layout_v2 import PAGE2_GROUPS
from layout_v2 import PAGE2_INJ_NEEDLE, PAGE2_INJECTION_VALUE
from layout_v2 import PAGE2_SPARK_NEEDLE, PAGE2_SPARK_VALUE
from layout_v2 import PAGE3_BARS, PAGE3_CAR_SCALE, PAGE3_CAR_TOP
from layout_v2 import PAGE3_STEER_BARS, PAGE3_TEXT
from layout_v2 import PAGE4_CLEAR_CODES, PAGE4_GROUPS

HERE = os.path.dirname(os.path.abspath(__file__))
SVG_DIR = os.path.join(HERE, "assets", "svg")
PNG_DIR = os.path.join(HERE, "assets", "png")
# Renderer: Node.js + sharp. Uses `node` from PATH and a sharp package that
# resolves from the current directory (`npm install sharp`), unless overridden
# with SCIROCCO_NODE / SCIROCCO_SHARP_MJS.
NODE = os.environ.get("SCIROCCO_NODE") or shutil.which("node") or "node"
SHARP_MJS = os.environ.get("SCIROCCO_SHARP_MJS", "sharp")

# --- palette, sampled off the cluster photos -------------------------------
WHITE = "#f4f6f7"        # marking white
WHITE_DIM = "#a8adb2"    # secondary labels
RED = "#d81f26"          # redline ticks / zone ticks
AMBER = "#e0951f"        # pre-redline caution (tuner addition, not OEM)
GREEN = "#35c04a"        # turn signal telltales
NEEDLE = "#e02a1c"       # VW cluster needle red
NEEDLE_GHOST = "#7d1710"  # same red, brightness pulled down, for peak holds

# Dial geometry, shared so the boost-target notch can be drawn at exactly the
# tick band's radius, length and weight.  Fractions of the face radius.
BEZEL_OUTER_FRAC = 0.995
BEZEL_THICK_FRAC = 0.035        # halved from v1: the OEM VW ring is much finer
SEPARATOR_FRAC = 0.015
FACE_OUT_FRAC = BEZEL_OUTER_FRAC - BEZEL_THICK_FRAC - SEPARATOR_FRAC
BAND_OUT_FRAC = FACE_OUT_FRAC - 0.015
MINOR_TICK_LEN_FRAC = 0.048
MAJOR_TICK_LEN_FRAC = 0.108
MINOR_TICK_W_FRAC = 0.0095
MAJOR_TICK_W_FRAC = 0.027
FACE_CORE = "#131417"    # face centre
FACE_EDGE = "#020203"    # face rim
PANEL_BG = "#0b0c0e"
PANEL_LINE = "#23262a"
FONT = "Bahnschrift"     # DIN-derived, closest system face to VW's cluster
NUM_FONT = "Bahnschrift"

ASSETS = []  # (name, w, h)


# ------------------------------------------------------------------ helpers

def pt(cx, cy, r, ang):
    a = math.radians(ang)
    return (cx + r * math.sin(a), cy - r * math.cos(a))


def fmt(v):
    return ("%g" % v)


def tick(cx, cy, r_out, length, width, ang, color, cap="butt"):
    x0, y0 = pt(cx, cy, r_out, ang)
    x1, y1 = pt(cx, cy, r_out - length, ang)
    return (
        f'<line x1="{x0:.2f}" y1="{y0:.2f}" x2="{x1:.2f}" y2="{y1:.2f}" '
        f'stroke="{color}" stroke-width="{width:.2f}" stroke-linecap="{cap}"/>'
    )


def svg_open(w, h):
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" '
        f'viewBox="0 0 {w} {h}">'
    )


def text(x, y, s, size, color=WHITE, weight=400, anchor="middle", spacing=0,
         family=FONT, opacity=1.0):
    sp = f' letter-spacing="{spacing:.2f}"' if spacing else ""
    safe = escape(str(s))
    return (
        f'<text x="{x:.2f}" y="{y:.2f}" font-family="{family}" '
        f'font-size="{size:.1f}" font-weight="{weight}" fill="{color}" '
        f'text-anchor="{anchor}" opacity="{opacity}"{sp}>{safe}</text>'
    )


def chrome_defs(idsuffix=""):
    """Bezel + face gradients. The chrome ring is lit top and bottom with a
    dark band through the middle, which is what sells it as metal."""
    c = f"chrome{idsuffix}"
    f = f"face{idsuffix}"
    return (
        "<defs>"
        f'<linearGradient id="{c}" x1="0" y1="0" x2="0.15" y2="1">'
        '<stop offset="0%" stop-color="#ffffff"/>'
        '<stop offset="12%" stop-color="#e9edf0"/>'
        '<stop offset="30%" stop-color="#9aa1a8"/>'
        '<stop offset="48%" stop-color="#5d646b"/>'
        '<stop offset="62%" stop-color="#7c848b"/>'
        '<stop offset="82%" stop-color="#d5dade"/>'
        '<stop offset="100%" stop-color="#f7f9fa"/>'
        "</linearGradient>"
        f'<radialGradient id="{f}" cx="50%" cy="42%" r="62%">'
        f'<stop offset="0%" stop-color="{FACE_CORE}"/>'
        '<stop offset="70%" stop-color="#08090b"/>'
        f'<stop offset="100%" stop-color="{FACE_EDGE}"/>'
        "</radialGradient>"
        "</defs>"
    )


# --------------------------------------------------------------- gauge face

def gauge_face(name, size, vmin, vmax, major_step, minor_step,
               redline=None, amber=None, unit=None, title=None,
               label_step=None, start=-135.0, sweep=270.0,
               label_scale=1.0, brand=None, decimals=None):
    """Round face in the cluster's idiom.

    redline / amber are (from, to) value spans drawn as coloured ticks in
    the band -- the way the real tach marks 6.3+, rather than a filled arc.
    """
    cx = cy = size / 2.0
    R = size / 2.0

    def ang(v):
        return start + sweep * (v - vmin) / (vmax - vmin)

    def zone_color(v):
        eps = minor_step * 0.001
        if redline and redline[0] - eps <= v <= redline[1] + eps:
            return RED
        if amber and amber[0] - eps <= v <= amber[1] + eps:
            return AMBER
        return WHITE

    e = [svg_open(size, size), chrome_defs()]

    # chrome bezel ring, thin dark separator, then the black face.
    #
    # The v1 ring was 0.070 R thick, noticeably heavier than the OEM VW
    # cluster; halved to 0.035 R on 2026-08-29. The outer edge stays at
    # 0.995 R so the dial's overall size and its gauge rectangle are
    # unchanged. The tick band moves out by the same amount, keeping its
    # 0.015 R gap from the face edge -- otherwise a thinner bezel just
    # leaves the ticks floating inboard behind a wide black margin instead
    # of reading as a bigger face.
    bezel_in = BEZEL_OUTER_FRAC - BEZEL_THICK_FRAC
    e.append(f'<circle cx="{cx}" cy="{cy}" r="{R * BEZEL_OUTER_FRAC:.2f}" fill="url(#chrome)"/>')
    e.append(f'<circle cx="{cx}" cy="{cy}" r="{R * bezel_in:.2f}" fill="#0a0b0c"/>')
    e.append(f'<circle cx="{cx}" cy="{cy}" r="{R * FACE_OUT_FRAC:.2f}" fill="url(#face)"/>')

    # ---- tick band -------------------------------------------------------
    band_out = R * BAND_OUT_FRAC
    minor_len = R * MINOR_TICK_LEN_FRAC
    major_len = R * MAJOR_TICK_LEN_FRAC
    minor_w = R * MINOR_TICK_W_FRAC
    major_w = R * MAJOR_TICK_W_FRAC

    n_minor = int(round((vmax - vmin) / minor_step))
    for i in range(n_minor + 1):
        v = vmin + i * minor_step
        # skip minors that coincide with a major
        k = (v - vmin) / major_step
        if abs(k - round(k)) < 1e-6:
            continue
        e.append(tick(cx, cy, band_out, minor_len, minor_w, ang(v), zone_color(v)))

    n_major = int(round((vmax - vmin) / major_step))
    for i in range(n_major + 1):
        v = vmin + i * major_step
        e.append(tick(cx, cy, band_out, major_len, major_w, ang(v), zone_color(v)))

    # ---- numerals, inside the band --------------------------------------
    lstep = label_step or major_step
    n_lab = int(round((vmax - vmin) / lstep))
    fs = R * 0.165 * label_scale
    for i in range(n_lab + 1):
        v = vmin + i * lstep
        lx, ly = pt(cx, cy, R * 0.672, ang(v))
        s = ("%.*f" % (decimals, v)) if decimals is not None else fmt(v)
        e.append(text(lx, ly + fs * 0.355, s, fs, WHITE, weight=400,
                      family=NUM_FONT))

    # ---- centre labels ---------------------------------------------------
    if brand:
        e.append(text(cx, cy - R * 0.34, brand, R * 0.070, WHITE_DIM,
                      weight=400, spacing=R * 0.020))
    if unit:
        e.append(text(cx, cy + R * 0.335, unit, R * 0.098, WHITE_DIM, weight=400))
    if title:
        e.append(text(cx, cy + R * 0.485, title, R * 0.132, WHITE, weight=400))

    e.append("</svg>")
    write_svg(name, "".join(e), size, size)


# ------------------------------------------------------------------ needles

def needle(name, size, length_frac, tail_frac=0.135, width_frac=0.020,
           ghost=False):
    """Pivot at image centre, blade pointing straight up.

    ghost=True is the translucent peak-hold pointer: same blade, no hub."""
    c = size / 2.0
    R = size / 2.0
    ln = R * length_frac
    tail = R * tail_frac
    w = R * width_frac
    e = [svg_open(size, size)]
    # The peak-hold pointer is a SHORT, DIM red segment out at the rim, not a
    # full-length translucent copy of the live needle.  Two problems, one
    # shape: a full-length ghost is drawn over the live needle and hides it,
    # and at the same length on the same pivot the two are hard to tell apart
    # at a glance.  A stub near the scale cannot cover the live pointer and
    # reads as a mark left behind, which is what a peak hold is.  Colour is
    # the needle red with the brightness pulled down, so it stays obviously
    # the same family of thing rather than becoming a second highlight.
    op = ' opacity="0.9"' if ghost else ""
    blade = NEEDLE_GHOST if ghost else NEEDLE
    inner = ln * 0.70 if ghost else 0.0

    # blade: broad at the hub, tapering to a fine tip
    # Widths taper from the hub (or from the stub's inner end) to the tip.
    w_in = w * (1.0 - 0.78 * (inner / ln)) if ln else w
    e.append(
        f'<path d="M {c - w_in:.2f} {c - inner:.2f} '
        f'L {c - w * 0.22:.2f} {c - ln:.2f} '
        f'L {c + w * 0.22:.2f} {c - ln:.2f} '
        f'L {c + w_in:.2f} {c - inner:.2f} Z" fill="{blade}"{op}/>'
    )
    if not ghost:
        # short counterweight tail
        e.append(
            f'<path d="M {c - w * 0.92:.2f} {c:.2f} '
            f'L {c - w * 0.62:.2f} {c + tail:.2f} '
            f'L {c + w * 0.62:.2f} {c + tail:.2f} '
            f'L {c + w * 0.92:.2f} {c:.2f} Z" fill="{NEEDLE}"/>'
        )
        # dark hub with a chrome collar, as on the real gauges
        e.append(f'<circle cx="{c}" cy="{c}" r="{R * 0.082:.2f}" '
                 f'fill="#0c0d0f" stroke="#c3c9ce" '
                 f'stroke-width="{R * 0.016:.2f}"/>')
        e.append(f'<circle cx="{c}" cy="{c}" r="{R * 0.028:.2f}" fill="#2a2d31"/>')
    e.append("</svg>")
    write_svg(name, "".join(e), size, size)


def needle_marker(name, size, length_frac=None):
    """Boost target: a red notch that rides the tick band.

    Amber read as a foreign colour on a face whose only accent is red, and a
    wedge floating inboard of the scale read as an artefact rather than a
    mark.  Drawing it at exactly the tick band's radius, length and weight
    makes it a *moving graduation* -- the needle visibly chases a notch that
    belongs to the dial -- which is what a target wants to be."""
    c = size / 2.0
    R = size / 2.0
    band_out = R * BAND_OUT_FRAC
    length = R * MAJOR_TICK_LEN_FRAC
    half = R * MAJOR_TICK_W_FRAC * 0.62
    e = [svg_open(size, size)]
    e.append(f'<rect x="{c - half:.2f}" y="{c - band_out:.2f}" '
             f'width="{half * 2:.2f}" height="{length:.2f}" rx="{half * 0.35:.2f}" '
             f'fill="{NEEDLE}"/>')
    e.append("</svg>")
    write_svg(name, "".join(e), size, size)


# ---------------------------------------------------------------- telltales

def icon(name, size, body):
    write_svg(name, "".join([svg_open(size, size), body, "</svg>"]), size, size)


def make_icons(s=96):
    c = s / 2
    # VW-style hollow turn arrow
    arrow = (
        f'<path d="M {s*0.10:.1f} {c:.1f} L {s*0.46:.1f} {s*0.14:.1f} '
        f'L {s*0.46:.1f} {s*0.36:.1f} L {s*0.90:.1f} {s*0.36:.1f} '
        f'L {s*0.90:.1f} {s*0.64:.1f} L {s*0.46:.1f} {s*0.64:.1f} '
        f'L {s*0.46:.1f} {s*0.86:.1f} Z" fill="{GREEN}"/>'
    )
    icon("ind_turn_left", s, arrow)
    icon("ind_turn_right", s,
         f'<g transform="translate({s},0) scale(-1,1)">{arrow}</g>')

    # MIL: engine block silhouette, amber (as on the real cluster)
    mil = (
        f'<g fill="{AMBER}">'
        f'<rect x="{s*0.19:.1f}" y="{s*0.35:.1f}" width="{s*0.50:.1f}" '
        f'height="{s*0.33:.1f}" rx="{s*0.05:.1f}"/>'
        f'<rect x="{s*0.29:.1f}" y="{s*0.24:.1f}" width="{s*0.25:.1f}" height="{s*0.13:.1f}" rx="{s*0.02:.1f}"/>'
        f'<rect x="{s*0.69:.1f}" y="{s*0.43:.1f}" width="{s*0.14:.1f}" height="{s*0.17:.1f}" rx="{s*0.02:.1f}"/>'
        f'<rect x="{s*0.09:.1f}" y="{s*0.45:.1f}" width="{s*0.11:.1f}" height="{s*0.13:.1f}" rx="{s*0.02:.1f}"/>'
        f'<rect x="{s*0.36:.1f}" y="{s*0.44:.1f}" width="{s*0.05:.1f}" height="{s*0.15:.1f}" fill="#0b0c0e"/>'
        f'<rect x="{s*0.46:.1f}" y="{s*0.44:.1f}" width="{s*0.05:.1f}" height="{s*0.15:.1f}" fill="#0b0c0e"/>'
        f'</g>'
    )
    icon("ind_mil", s, mil)

    # coolant overtemp: thermometer in waves, red
    temp = (
        f'<g stroke="{RED}" stroke-width="{s*0.055:.1f}" fill="none" '
        f'stroke-linecap="round">'
        f'<line x1="{c:.1f}" y1="{s*0.15:.1f}" x2="{c:.1f}" y2="{s*0.50:.1f}"/>'
        f'<line x1="{s*0.36:.1f}" y1="{s*0.26:.1f}" x2="{s*0.44:.1f}" y2="{s*0.26:.1f}"/>'
        f'<line x1="{s*0.36:.1f}" y1="{s*0.38:.1f}" x2="{s*0.44:.1f}" y2="{s*0.38:.1f}"/>'
        f'<path d="M {s*0.14:.1f} {s*0.76:.1f} q {s*0.09:.1f} {-s*0.10:.1f} {s*0.18:.1f} 0 '
        f'q {s*0.09:.1f} {s*0.10:.1f} {s*0.18:.1f} 0 '
        f'q {s*0.09:.1f} {-s*0.10:.1f} {s*0.18:.1f} 0"/>'
        f'</g>'
        f'<circle cx="{c:.1f}" cy="{s*0.55:.1f}" r="{s*0.105:.1f}" fill="{RED}"/>'
    )
    icon("ind_overtemp", s, temp)

    # knock: bolt in a circle, red
    knock = (
        f'<circle cx="{c:.1f}" cy="{c:.1f}" r="{s*0.39:.1f}" fill="none" '
        f'stroke="{RED}" stroke-width="{s*0.055:.1f}"/>'
        f'<path d="M {s*0.57:.1f} {s*0.21:.1f} L {s*0.33:.1f} {s*0.55:.1f} '
        f'L {s*0.47:.1f} {s*0.55:.1f} L {s*0.41:.1f} {s*0.79:.1f} '
        f'L {s*0.67:.1f} {s*0.43:.1f} L {s*0.51:.1f} {s*0.43:.1f} Z" '
        f'fill="{RED}"/>'
    )
    icon("ind_knock", s, knock)

    # boost deviation: turbo snail, amber
    boost = (
        f'<g stroke="{AMBER}" stroke-width="{s*0.058:.1f}" fill="none">'
        f'<circle cx="{s*0.46:.1f}" cy="{s*0.54:.1f}" r="{s*0.27:.1f}"/>'
        f'<path d="M {s*0.46:.1f} {s*0.20:.1f} L {s*0.84:.1f} {s*0.20:.1f} '
        f'L {s*0.84:.1f} {s*0.40:.1f}"/>'
        f'</g>'
        f'<circle cx="{s*0.46:.1f}" cy="{s*0.54:.1f}" r="{s*0.095:.1f}" fill="{AMBER}"/>'
    )
    icon("ind_boostdev", s, boost)

    # oil pressure/temp telltale, red (spare, for the data page)
    oil = (
        f'<g fill="{RED}">'
        f'<path d="M {s*0.12:.1f} {s*0.60:.1f} q {s*0.10:.1f} {-s*0.20:.1f} {s*0.30:.1f} {-s*0.16:.1f} '
        f'l {s*0.26:.1f} {s*0.05:.1f} q {s*0.12:.1f} {s*0.03:.1f} {s*0.10:.1f} {s*0.13:.1f} '
        f'l {-s*0.06:.1f} {s*0.10:.1f} q {-s*0.30:.1f} {s*0.05:.1f} {-s*0.60:.1f} {-s*0.12:.1f} Z"/>'
        f'<circle cx="{s*0.26:.1f}" cy="{s*0.72:.1f}" r="{s*0.05:.1f}"/>'
        f'<circle cx="{s*0.40:.1f}" cy="{s*0.76:.1f}" r="{s*0.05:.1f}"/>'
        f'</g>'
    )
    icon("ind_oil", s, oil)


# ------------------------------------------------------- page 1 background

def panel(x, y, w, h, label=None, lx=None):
    p = (
        f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="7" '
        f'fill="{PANEL_BG}" stroke="{PANEL_LINE}" stroke-width="1.5"/>'
    )
    if label:
        p += text(lx if lx is not None else x + w / 2, y + 21, label, 15,
                  WHITE_DIM, weight=600, spacing=1.6)
    return p


def bg_common(W, H, title):
    e = [svg_open(W, H)]
    e.append(
        '<defs>'
        '<radialGradient id="vig" cx="50%" cy="40%" r="86%">'
        '<stop offset="0%" stop-color="#0c0d0f"/>'
        '<stop offset="68%" stop-color="#07080a"/>'
        '<stop offset="100%" stop-color="#030304"/>'
        '</radialGradient>'
        '<pattern id="grain" width="4" height="3" patternUnits="userSpaceOnUse">'
        '<line x1="0" y1="1.5" x2="4" y2="1.5" stroke="#ffffff" '
        'stroke-width="0.5" opacity="0.020"/>'
        '</pattern>'
        '</defs>'
    )
    e.append(f'<rect width="{W}" height="{H}" fill="url(#vig)"/>')
    e.append(f'<rect width="{W}" height="{H}" fill="url(#grain)"/>')
    # Header sits high and tight: the wordmark and its hairline were dropped
    # from y=40/96 to y=28/48 to hand ~50 px of canvas back to the gauges.
    e.append(text(W / 2, 28, title, 19, WHITE_DIM, weight=600, spacing=9))
    e.append(f'<rect x="{W/2-240:.0f}" y="48" width="480" height="2" '
             f'fill="{RED}" opacity="0.55"/>')
    return e


def background_page1():
    W, H = CANVAS
    e = bg_common(W, H, "SCIROCCO")

    # ---- bottom tuner strip ---------------------------------------------
    e.append(panel(28, 600, 264, 112, "KNOCK RETARD °"))
    for i, bx in enumerate((64, 122, 180, 238)):
        e.append(f'<rect x="{bx - 12}" y="630" width="24" height="56" rx="3" '
                 f'fill="#050506" stroke="{PANEL_LINE}" stroke-width="1"/>')
        e.append(text(bx, 704, str(i + 1), 13, WHITE_DIM, weight=400))
    for frac, lab in ((0.0, "0"), (0.5, "6"), (1.0, "12")):
        y = 630 + 56 * frac
        e.append(f'<line x1="42" y1="{y:.0f}" x2="48" y2="{y:.0f}" '
                 f'stroke="{WHITE_DIM}" stroke-width="1.5"/>')
        e.append(text(36, y + 4, lab, 11, WHITE_DIM, weight=400, anchor="end"))

    e.append(panel(304, 600, 150, 112, "IGN °BTDC"))
    e.append(panel(466, 600, 300, 112))
    for label, y in (("N75 %", 632), ("LOAD %", 676)):
        e.append(text(508, y + 5, label, 14, WHITE_DIM, weight=600, anchor="end"))
        e.append(f'<rect x="520" y="{y - 9}" width="220" height="18" rx="3" '
                 f'fill="#050506" stroke="{PANEL_LINE}" stroke-width="1"/>')
        for fx in (0.25, 0.5, 0.75):
            e.append(f'<line x1="{520 + 220 * fx:.0f}" y1="{y - 9}" '
                     f'x2="{520 + 220 * fx:.0f}" y2="{y + 9}" '
                     f'stroke="{PANEL_LINE}" stroke-width="1"/>')

    e.append(panel(778, 600, 300, 112))
    for label, y in (("ST %", 632), ("LT %", 676)):
        e.append(text(820, y + 5, label, 14, WHITE_DIM, weight=600, anchor="end"))
        e.append(f'<rect x="832" y="{y - 9}" width="220" height="18" rx="3" '
                 f'fill="#050506" stroke="{PANEL_LINE}" stroke-width="1"/>')
        e.append(f'<line x1="942" y1="{y - 12}" x2="942" y2="{y + 12}" '
                 f'stroke="{WHITE_DIM}" stroke-width="1.5"/>')
    e.append(text(832, 620, "-25", 11, WHITE_DIM, weight=400, anchor="start"))
    e.append(text(1052, 620, "+25", 11, WHITE_DIM, weight=400, anchor="end"))

    e.append(panel(1090, 600, 162, 112))
    e.append(text(1112, 637, "λ", 20, WHITE_DIM, weight=600, anchor="start"))
    e.append(text(1112, 681, "TGT", 14, WHITE_DIM, weight=600, anchor="start"))

    # ---- odometer / trip plate under the tach ---------------------------
    e.append(panel(28, 470, 264, 104))
    e.append(text(48, 500, "ODOMETER  km", 13, WHITE_DIM, weight=600,
                  anchor="start", spacing=1.2))
    e.append(text(48, 556, "TRIP  km", 13, WHITE_DIM, weight=600,
                  anchor="start", spacing=1.2))

    # ---- speed + gear furniture, centred under the boost gauge ----------
    # The digits themselves are text gauges; only the frame and the unit
    # are baked in so the live elements stay simple.
    e.append(f'<rect x="519" y="504" width="46" height="46" rx="6" '
             f'fill="none" stroke="#43464c" stroke-width="2"/>')
    e.append(text(542, 566, "GEAR", 11, WHITE_DIM, weight=600, spacing=1.0))
    e.append(text(742, 548, "km/h", 15, WHITE_DIM, weight=400, anchor="start"))

    e.append("</svg>")
    write_svg("bg_page1", "".join(e), W, H)


# ------------------------------------------------- page 2 background (data)

# (label, unit) laid out in six titled panels. Values are placed as plain
# RealDash text gauges on top of these baked-in labels.
# Seven rows per panel x six panels = 42 slots. Together with page 1 this
# Legacy two-page background definition retained below for reference only;
# v2 coverage is defined by layout_v2.py and v2_manifest.py.
# (see dash/README.md "Channel coverage"), so nothing the car sends is
# unreachable from the dashboard.
P2_PANELS = [
    ("AIR PATH", 28, 100, 396, 300, [
        ("MAP", "kPa"), ("Baro", "kPa"), ("Intake air", "°C"),
        ("Charge air", "°C"), ("Ambient", "°C"), ("MAF", "g/s"),
        ("Boost peak", "bar"), ("Engine load", "%"),
    ]),
    ("FUEL SYSTEM", 442, 100, 396, 300, [
        ("Throttle", "%"), ("Pedal", "%"), ("Injection", "ms"),
        ("Rail pressure", "bar"), ("Rail spec", "bar"),
        ("Rail actual", "bar"), ("Pump duty", "%"), ("Fuel temp", "°C"),
    ]),
    ("MIXTURE & DRIVELINE", 856, 100, 396, 300, [
        ("Lambda cmd", ""), ("Trim short", "%"), ("Trim long", "%"),
        ("Gear", ""), ("Road speed", "km/h"), ("Steering", "°"),
        ("Steering raw", "cts"), ("Run time", "s"),
    ]),
    ("CHASSIS  (DCC)", 28, 412, 396, 300, [
        ("Damper FL", ""), ("Damper FR", ""), ("Damper RL", ""),
        ("Damper RR", ""), ("Ride height FL", "V"), ("Ride height FR", "V"),
        ("Ride height R", "V"), ("Catalyst", "°C"),
    ]),
    ("TRIP & FAULTS", 442, 412, 396, 300, [
        ("Odometer", "km"), ("Since clear", "km"), ("Fault count", ""),
        ("Fault 1", ""), ("Fault 2", ""), ("Fault 3", ""), ("Fault 4", ""),
    ]),
    ("LINK HEALTH", 856, 412, 396, 300, [
        ("Sample rate", "Hz"), ("Reconnects", ""), ("Aux visits", ""),
        ("Aux fails", ""), ("Gear age", "s"), ("CAN box age", "s"),
        ("Check engine", ""),
    ]),
]

ROW_DY = 33          # row pitch inside a panel
ROW_Y0 = 50          # first row baseline offset from panel top


def background_page2():
    W, H = CANVAS
    e = bg_common(W, H, "SCIROCCO  ·  DATA")

    for (title, px, py, pw, ph, rows) in P2_PANELS:
        e.append(f'<rect x="{px}" y="{py}" width="{pw}" height="{ph}" rx="7" '
                 f'fill="{PANEL_BG}" stroke="{PANEL_LINE}" stroke-width="1.5"/>')
        e.append(text(px + 16, py + 26, title, 14, WHITE, weight=600,
                      anchor="start", spacing=2.0))
        e.append(f'<line x1="{px + 14}" y1="{py + 36}" x2="{px + pw - 14}" '
                 f'y2="{py + 36}" stroke="{RED}" stroke-width="1.5" opacity="0.5"/>')
        for i, (label, unit) in enumerate(rows):
            ry = py + ROW_Y0 + i * ROW_DY
            e.append(text(px + 16, ry, label, 15, WHITE_DIM, weight=400,
                          anchor="start"))
            if unit:
                e.append(text(px + pw - 16, ry, unit, 13, WHITE_DIM,
                              weight=400, anchor="end", opacity=0.75))
            # hairline under each row, so the eye tracks across
            e.append(f'<line x1="{px + 14}" y1="{ry + 11}" x2="{px + pw - 14}" '
                     f'y2="{ry + 11}" stroke="#191c1f" stroke-width="1"/>')

    e.append("</svg>")
    write_svg("bg_page2", "".join(e), W, H)


def dump_page2_layout():
    """Print the value-field coordinates for the build guide / editor work.

    Values are right-aligned just left of the unit column."""
    lines = ["# page 2 value field positions (1280x660 design space)",
             "# panel | row | label | unit | value x (right edge) | value y (top)"]
    for (title, px, py, pw, ph, rows) in P2_PANELS:
        for i, (label, unit) in enumerate(rows):
            ry = py + ROW_Y0 + i * ROW_DY
            vx = px + pw - 46          # right edge of the number
            vy = ry - 15               # top of a ~22px text box
            lines.append("%-16s | %d | %-15s | %-5s | %4d | %4d"
                         % (title, i + 1, label, unit or "-", vx, vy))
    path = os.path.join(HERE, "page2_layout.txt")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print("wrote", path)


# --------------------------------------------------------------- v2 assets

def background_base(page_title=None):
    """Header-free common background for the four-page v2 dashboard."""
    W, H = CANVAS
    e = [svg_open(W, H)]
    e.append(
        '<defs>'
        '<radialGradient id="v2vig" cx="50%" cy="42%" r="84%">'
        '<stop offset="0%" stop-color="#0b0d10"/>'
        '<stop offset="72%" stop-color="#060709"/>'
        '<stop offset="100%" stop-color="#020304"/>'
        '</radialGradient>'
        '<pattern id="v2grain" width="4" height="3" patternUnits="userSpaceOnUse">'
        '<line x1="0" y1="1.5" x2="4" y2="1.5" stroke="#ffffff" '
        'stroke-width="0.5" opacity="0.018"/>'
        '</pattern>'
        '</defs>'
    )
    e.append(f'<rect width="{W}" height="{H}" fill="url(#v2vig)"/>')
    e.append(f'<rect width="{W}" height="{H}" fill="url(#v2grain)"/>')
    if page_title:
        e.append(text(28, 38, page_title, 19, WHITE, weight=600,
                      anchor="start", spacing=3.2))
    return e


def make_icons_v2(s=96):
    """Late-2000s VW/Audi-style telltales with one optical stroke family."""
    sw = s * 0.055
    common = f'fill="none" stroke-width="{sw:.1f}" stroke-linecap="square" stroke-linejoin="miter"'

    arrow_path = (
        f'M {s*0.11:.1f} {s*0.50:.1f} L {s*0.47:.1f} {s*0.17:.1f} '
        f'L {s*0.47:.1f} {s*0.37:.1f} L {s*0.86:.1f} {s*0.37:.1f} '
        f'L {s*0.86:.1f} {s*0.63:.1f} L {s*0.47:.1f} {s*0.63:.1f} '
        f'L {s*0.47:.1f} {s*0.83:.1f} Z'
    )
    left = f'<path d="{arrow_path}" {common} stroke="{GREEN}"/>'
    icon("ind_turn_left", s, left)
    right = f'<g transform="translate({s},0) scale(-1,1)">{left}</g>'
    icon("ind_turn_right", s, right)

    mil = (
        f'<g {common} stroke="{AMBER}">'
        f'<path d="M {s*.18:.1f} {s*.38:.1f} H {s*.29:.1f} L {s*.36:.1f} {s*.27:.1f} '
        f'H {s*.58:.1f} L {s*.65:.1f} {s*.38:.1f} H {s*.78:.1f} V {s*.68:.1f} '
        f'H {s*.18:.1f} Z"/>'
        f'<path d="M {s*.78:.1f} {s*.45:.1f} H {s*.87:.1f} V {s*.61:.1f} H {s*.78:.1f}"/>'
        f'<path d="M {s*.18:.1f} {s*.47:.1f} H {s*.10:.1f} V {s*.59:.1f} H {s*.18:.1f}"/>'
        f'<path d="M {s*.42:.1f} {s*.46:.1f} V {s*.59:.1f} M {s*.55:.1f} {s*.46:.1f} V {s*.59:.1f}"/>'
        '</g>'
    )
    icon("ind_mil", s, mil)

    overtemp = (
        f'<g {common} stroke="{RED}">'
        f'<path d="M {s*.49:.1f} {s*.17:.1f} V {s*.52:.1f}"/>'
        f'<circle cx="{s*.49:.1f}" cy="{s*.59:.1f}" r="{s*.105:.1f}" fill="{RED}"/>'
        f'<path d="M {s*.34:.1f} {s*.27:.1f} H {s*.42:.1f} M {s*.34:.1f} {s*.39:.1f} H {s*.42:.1f}"/>'
        f'<path d="M {s*.12:.1f} {s*.78:.1f} Q {s*.21:.1f} {s*.68:.1f} {s*.30:.1f} {s*.78:.1f} '
        f'T {s*.48:.1f} {s*.78:.1f} T {s*.66:.1f} {s*.78:.1f} T {s*.84:.1f} {s*.78:.1f}"/>'
        '</g>'
    )
    icon("ind_overtemp", s, overtemp)

    knock = (
        f'<g {common} stroke="{RED}">'
        f'<path d="M {s*.30:.1f} {s*.23:.1f} H {s*.62:.1f} L {s*.68:.1f} {s*.38:.1f} '
        f'V {s*.66:.1f} H {s*.25:.1f} V {s*.38:.1f} Z"/>'
        f'<path d="M {s*.25:.1f} {s*.48:.1f} H {s*.15:.1f} V {s*.61:.1f} H {s*.25:.1f}"/>'
        f'<path d="M {s*.48:.1f} {s*.16:.1f} V {s*.31:.1f}"/>'
        f'<path d="M {s*.73:.1f} {s*.28:.1f} L {s*.87:.1f} {s*.20:.1f} '
        f'M {s*.75:.1f} {s*.43:.1f} H {s*.91:.1f} M {s*.73:.1f} {s*.58:.1f} L {s*.87:.1f} {s*.67:.1f}"/>'
        '</g>'
    )
    icon("ind_knock", s, knock)

    boost = (
        f'<g {common} stroke="{AMBER}">'
        f'<circle cx="{s*.43:.1f}" cy="{s*.53:.1f}" r="{s*.27:.1f}"/>'
        f'<circle cx="{s*.43:.1f}" cy="{s*.53:.1f}" r="{s*.075:.1f}"/>'
        f'<path d="M {s*.43:.1f} {s*.46:.1f} L {s*.31:.1f} {s*.31:.1f} '
        f'M {s*.49:.1f} {s*.50:.1f} L {s*.63:.1f} {s*.40:.1f} '
        f'M {s*.46:.1f} {s*.60:.1f} L {s*.56:.1f} {s*.73:.1f}"/>'
        f'<path d="M {s*.43:.1f} {s*.18:.1f} H {s*.75:.1f} V {s*.35:.1f}"/>'
        f'<path d="M {s*.84:.1f} {s*.36:.1f} V {s*.59:.1f} M {s*.84:.1f} {s*.70:.1f} V {s*.73:.1f}"/>'
        '</g>'
    )
    icon("ind_boostdev", s, boost)

    oil = (
        f'<g {common} stroke="{RED}">'
        f'<path d="M {s*.15:.1f} {s*.56:.1f} L {s*.32:.1f} {s*.39:.1f} H {s*.61:.1f} '
        f'L {s*.73:.1f} {s*.51:.1f} H {s*.86:.1f}"/>'
        f'<path d="M {s*.20:.1f} {s*.56:.1f} Q {s*.46:.1f} {s*.72:.1f} {s*.72:.1f} {s*.58:.1f}"/>'
        f'<path d="M {s*.86:.1f} {s*.60:.1f} Q {s*.80:.1f} {s*.71:.1f} {s*.86:.1f} {s*.79:.1f} '
        f'Q {s*.92:.1f} {s*.71:.1f} {s*.86:.1f} {s*.60:.1f} Z" fill="{RED}"/>'
        '</g>'
    )
    icon("ind_oil", s, oil)
    return {
        "turn_left": left,
        "turn_right": right,
        "mil": mil,
        "overtemp": overtemp,
        "knock": knock,
        "boost_deviation": boost,
        "oil": oil,
    }


def spark_needle(size=1120):
    """Tapered pointer for the spark arc.

    The v1 pointer was a hairline with a soft red glow and a large hollow
    hub.  With the cylinder-head illustration removed there is nothing left
    for a glow to read against, and a bare line plus an outline ring looked
    unfinished next to the solid tapered blades on page 1.  This is the same
    blade shape as those needles, narrower to suit a 150-degree sweep, with a
    small solid hub.
    """
    c = size / 2
    R = size / 2
    ln = R * 0.80
    w = R * 0.013
    e = [svg_open(size, size)]
    e.append(
        f'<path d="M {c - w:.2f} {c:.2f} '
        f'L {c - w * 0.20:.2f} {c - ln:.2f} '
        f'L {c + w * 0.20:.2f} {c - ln:.2f} '
        f'L {c + w:.2f} {c:.2f} Z" fill="{NEEDLE}"/>'
    )
    # short counterweight, so the pointer reads as pivoted rather than stuck
    e.append(
        f'<path d="M {c - w * 0.85:.2f} {c:.2f} '
        f'L {c - w * 0.55:.2f} {c + R * 0.075:.2f} '
        f'L {c + w * 0.55:.2f} {c + R * 0.075:.2f} '
        f'L {c + w * 0.85:.2f} {c:.2f} Z" fill="{NEEDLE}" opacity=".8"/>'
    )
    e.append(f'<circle cx="{c}" cy="{c}" r="{R * 0.030:.1f}" fill="#0c0d0f" '
             f'stroke="{NEEDLE}" stroke-width="{R * 0.007:.1f}"/>')
    e.append('</svg>')
    write_svg("needle_spark", "".join(e), size, size)


def front_wheel_asset(name, mirrored=False, size=208):
    c = size / 2
    transform = f' transform="translate({size},0) scale(-1,1)"' if mirrored else ""
    e = [svg_open(size, size), f'<g{transform}>']
    e.append(f'<rect x="{c-25:.1f}" y="{c-70:.1f}" width="50" height="140" rx="17" '
             f'fill="#111419" stroke="{WHITE_DIM}" stroke-width="4"/>')
    for off in (-36, 0, 36):
        e.append(f'<path d="M {c-18:.1f} {c+off:.1f} L {c+18:.1f} {c+off-7:.1f}" '
                 f'stroke="#4b5056" stroke-width="2"/>')
    e.append(f'<rect x="{c-7:.1f}" y="{c-31:.1f}" width="14" height="62" rx="5" '
             f'fill="#20242a" stroke="#8b9198" stroke-width="2"/>')
    e.append('</g></svg>')
    write_svg(name, "".join(e), size, size)


def clear_codes_button_asset(width=392, height=80):
    """Two-times asset for the guarded diagnostics command button."""
    e = [svg_open(width, height),
         '<defs><linearGradient id="buttonFace" x1="0" y1="0" x2="0" y2="1">'
         '<stop offset="0" stop-color="#171a1e"/><stop offset="1" stop-color="#090b0d"/>'
         '</linearGradient></defs>']
    e.append(f'<rect x="2" y="2" width="{width-4}" height="{height-4}" rx="14" '
             f'fill="url(#buttonFace)" stroke="#6f2c29" stroke-width="2"/>')
    # The engine icon is gone too.  RealDash does not scale a button image
    # the way the geometry predicts -- a 44px-wide icon in a 392px asset
    # rendered as an 8px red sliver rather than the 33px it should have been
    # -- so the icon only ever read as a stray mark beside the label.  A
    # plain bordered plate under the native caption is the honest version of
    # this control, and the red border still carries the "this writes to the
    # ECU" warning.
    # No caption baked in.  RealDash draws the native button label on top of
    # this image, so a baked "CLEAR CODES" produced two overlapping captions
    # -- and the baked one is drawn at 75% (a 392px asset in a 294px button),
    # so it was both duplicated and the blurrier of the two.  The frame and
    # the icon are the parts an image does better than a label; the text is
    # the part the label does better, so each does one.  The 2-second hold is
    # already spelled out in the page footer.
    e.append('</svg>')
    write_svg("btn_clear_codes", "".join(e), width, height)


def background_page1_v2(indicator_symbols=None):
    W, H = CANVAS
    e = background_base()

    # Permanent dim telltale silhouettes make the indicator locations legible
    # when their live Image Gauges are off.  The active gauges sit directly on
    # top at full brightness, so no editor-specific Normal opacity is required
    # for the requested late-VW/Audi cluster behaviour.
    if indicator_symbols:
        for key, (x, y, w, h) in PAGE1_INDICATORS.items():
            symbol = indicator_symbols[key]
            e.append(f'<g transform="translate({x},{y}) scale({w/96:.4f},{h/96:.4f})" '
                     f'opacity=".11">{symbol}</g>')

    # ---- row 1: trip, centre readouts, boost target ----------------------
    tx, ty, tw, th = PAGE1_PANELS["trip"]
    e.append(panel(tx, ty, tw, th))
    e.append(text(tx + 16, ty + 28, "ODOMETER  km", 12, WHITE_DIM, weight=600,
                  anchor="start", spacing=1.1))
    e.append(text(tx + 16, ty + 66, "TRIP  km", 12, WHITE_DIM, weight=600,
                  anchor="start", spacing=1.1))

    gx, gy, gw, gh = PAGE1_TEXT["gear"]
    e.append(f'<rect x="{gx}" y="{gy}" width="{gw}" height="{gh}" rx="7" '
             f'fill="none" stroke="#3c4147" stroke-width="2"/>')
    e.append(text(gx + gw / 2, 538, "GEAR", 11, WHITE_DIM, weight=600, spacing=1.2))
    sx, sy, sw, sh = PAGE1_TEXT["speed"]
    e.append(text(sx + sw / 2, 538, "km/h", 11, WHITE_DIM, weight=600, spacing=1.2))

    # ---- row 2: knock, duty, trim ----------------------------------------
    kx, ky, kw, kh = PAGE1_PANELS["knock"]
    e.append(panel(kx, ky, kw, kh, "KNOCK RETARD °"))
    for i, key in enumerate(("knock_1", "knock_2", "knock_3", "knock_4")):
        x, y, w, h = PAGE1_BARS[key]
        e.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="2" '
                 f'fill="#040506" stroke="{PANEL_LINE}" stroke-width="1"/>')
        e.append(text(x + w/2, y + h + 15, str(i + 1), 11, WHITE_DIM))

    dx, dy, dw, dh = PAGE1_PANELS["duty"]
    e.append(panel(dx, dy, dw, dh))
    for label, key in (("N75 %", "n75"), ("LOAD %", "load")):
        x, y, w, h = PAGE1_BARS[key]
        e.append(text(x - 12, y + h, label, 12, WHITE_DIM, weight=600, anchor="end"))
        e.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="2" '
                 f'fill="#040506" stroke="{PANEL_LINE}" stroke-width="1"/>')

    mx, my, mw, mh = PAGE1_PANELS["trim"]
    e.append(panel(mx, my, mw, mh))
    for label, neg, pos in (("ST %", "trim_short_negative", "trim_short_positive"),
                            ("LT %", "trim_long_negative", "trim_long_positive")):
        nx, ny, nw, nh = PAGE1_BARS[neg]
        px, py, pw, ph = PAGE1_BARS[pos]
        e.append(text(nx - 12, ny + nh, label, 12, WHITE_DIM, weight=600, anchor="end"))
        e.append(f'<rect x="{nx}" y="{ny}" width="{nw+pw}" height="{nh}" rx="2" '
                 f'fill="#040506" stroke="{PANEL_LINE}" stroke-width="1"/>')
        e.append(f'<line x1="{px}" y1="{ny-4}" x2="{px}" y2="{ny+nh+4}" '
                 f'stroke="{WHITE_DIM}" stroke-width="1.5"/>')

    e.append("</svg>")
    write_svg("bg_page1", "".join(e), W, H)



def _arc_path(cx, cy, radius, start, end):
    x0, y0 = pt(cx, cy, radius, start)
    x1, y1 = pt(cx, cy, radius, end)
    large = 1 if abs(end - start) > 180 else 0
    return (f'M {x0:.2f} {y0:.2f} A {radius:.2f} {radius:.2f} '
            f'0 {large} 1 {x1:.2f} {y1:.2f}')


def _semi_scale(e, rect, vmin, vmax, majors, mode):
    """High-detail upper sweep used by the paired combustion instruments."""
    x, y, w, h = rect
    cx, cy, radius = x + w / 2, y + h / 2, w * .43
    start, sweep = -75.0, 150.0

    e.append(f'<path d="{_arc_path(cx, cy, radius, start, start+sweep)}" '
             f'fill="none" stroke="#d9dde1" stroke-width="3" opacity=".95"/>')
    e.append(f'<path d="{_arc_path(cx, cy, radius-10, start, start+sweep)}" '
             f'fill="none" stroke="#35393f" stroke-width="1"/>')
    if mode in ("spark", "inj"):
        # Injector headroom: one engine cycle at 7000 rpm is about 17 ms, so
        # 12 ms is where the duty starts to matter and 16 is genuinely close
        # to the injector running out of time.
        cuts = (((vmin, -20, RED), (-20, 0, AMBER), (0, vmax, WHITE))
                if mode == "spark"
                else ((0, 12, WHITE), (12, 16, AMBER), (16, 20, RED)))
        for lo, hi, colour in cuts:
            a0 = start + sweep * (lo-vmin)/(vmax-vmin)
            a1 = start + sweep * (hi-vmin)/(vmax-vmin)
            e.append(f'<path d="{_arc_path(cx, cy, radius+1, a0, a1)}" '
                     f'fill="none" stroke="{colour}" stroke-width="5" opacity=".75"/>')
    else:
        # Styling only: no warning claim without a real load-aware wideband.
        e.append(f'<path d="{_arc_path(cx, cy, radius+1, start, start+28)}" '
                 f'fill="none" stroke="{AMBER}" stroke-width="5" opacity=".6"/>')
        e.append(f'<path d="{_arc_path(cx, cy, radius+1, start+sweep-28, start+sweep)}" '
                 f'fill="none" stroke="{RED}" stroke-width="5" opacity=".45"/>')

    major_set = set(float(value) for value in majors)
    divisions = 32 if mode == "afr" else 30
    for index in range(divisions + 1):
        value = vmin + (vmax-vmin) * index / divisions
        angle = start + sweep * index / divisions
        nearest = min(major_set, key=lambda candidate: abs(candidate-value))
        is_major = abs(nearest-value) < (vmax-vmin)/120.0
        if mode == "spark":
            colour = RED if value <= -20 else AMBER if value < 0 else WHITE
        elif mode == "inj":
            colour = RED if value >= 16 else AMBER if value >= 12 else WHITE
        else:
            colour = WHITE if is_major else WHITE_DIM
        e.append(tick(cx, cy, radius, 24 if is_major else 12,
                      4 if is_major else 1.8, angle, colour))
        if is_major:
            # Keep labels close to the arc.  Pulling them deep into the dial
            # makes them compete with the technical illustrations below.
            tx, ty = pt(cx, cy, radius-28, angle)
            label = str(int(nearest)) if float(nearest).is_integer() else str(nearest)
            e.append(text(tx, ty+5, label, 14, colour, weight=500))
    return cx, cy, radius, start, sweep


def _instrument_heading(e, rect, title, value_rect=None, unit=None):
    """Instrument title, and its unit as a caption under the reading.

    The faded second line under each title ("CRANKSHAFT DEGREES / BTDC" and
    friends) said what the scale already says and competed with the arc
    labels for attention.  The unit is the part of it worth keeping, and it
    belongs beside the number, not in a subtitle -- so it becomes a caption
    under the value, the same treatment GEAR and km/h get on page 1."""
    x, y, w, h = rect
    e.append(text(x + w / 2, y - 16, title, 13, WHITE, weight=600,
                  spacing=1.9))
    if unit and value_rect:
        vx, vy, vw, vh = value_rect
        e.append(text(vx + vw / 2, vy + vh + 20, unit, 11, WHITE_DIM,
                      weight=600, spacing=1.2))


def background_page2_v2():
    """Three equal half-moon instruments over three data panels.

    v1 drew a cylinder-head cross-section inside the spark arc and an oxygen
    sensor with a lambda medallion inside the AFR arc.  Both sat exactly where
    the needle sweeps and where the reading has to be found, so the instrument
    competed with its own illustration; they are gone.  The two remaining arcs
    were also 560px, which left a dead band across the middle and squeezed the
    panels into a 282px strip.  Three 400px arcs on one baseline read as a set
    and give the panels 368px, which is what actually fixes the crowding.
    """
    W, H = CANVAS
    e = background_base("ENGINE & TUNING")

    # ---- spark advance --------------------------------------------------
    cx, cy, radius, start, sweep = _semi_scale(
        e, PAGE2_SPARK_NEEDLE, -30, 45, [-30, -20, -10, 0, 10, 20, 30, 40],
        "spark")
    _instrument_heading(e, PAGE2_SPARK_NEEDLE, "SPARK ADVANCE",
                        PAGE2_SPARK_VALUE, "°BTDC")
    # TDC is the one landmark on this scale that means something on its own.
    zero = start + sweep * 30 / 75.0
    zx0, zy0 = pt(cx, cy, radius - 26, zero)
    zx1, zy1 = pt(cx, cy, radius - 58, zero)
    e.append(f'<line x1="{zx0:.1f}" y1="{zy0:.1f}" x2="{zx1:.1f}" y2="{zy1:.1f}" '
             f'stroke="{WHITE_DIM}" stroke-width="1.1" opacity=".5"/>')
    tx, ty = pt(cx, cy, radius - 70, zero)
    e.append(text(tx, ty + 4, "TDC", 8, WHITE_DIM, weight=600, spacing=1.3,
                  opacity=.65))

    # ---- injector pulse width -------------------------------------------
    icx, icy, iradius, istart, isweep = _semi_scale(
        e, PAGE2_INJ_NEEDLE, 0, 20, [0, 4, 8, 12, 16, 20], "inj")
    _instrument_heading(e, PAGE2_INJ_NEEDLE, "INJECTION",
                        PAGE2_INJECTION_VALUE, "ms")

    # ---- AFR / lambda, reserved -----------------------------------------
    acx, acy, aradius, astart, asweep = _semi_scale(
        e, PAGE2_AFR_RESERVED_NEEDLE, 10, 18, [10, 12, 14, 16, 18], "afr")
    _instrument_heading(e, PAGE2_AFR_RESERVED_NEEDLE, "AFR  /  LAMBDA")
    # Stoichiometric bug: the one number on an AFR scale that is a fact.
    stoich = astart + asweep * (14.7 - 10) / 8
    sx, sy = pt(acx, acy, aradius + 11, stoich)
    e.append(f'<path d="M {sx-5:.1f} {sy-3:.1f} L {sx+5:.1f} {sy-3:.1f} '
             f'L {sx:.1f} {sy+8:.1f} Z" fill="{AMBER}"/>')
    lx, ly = pt(acx, acy, aradius - 34, stoich)
    e.append(text(lx, ly + 4, "14.7", 9, AMBER, weight=600, spacing=1.0,
                  opacity=.85))
    # The reserved state, said once, where the reading will eventually sit.
    vx, vy, vw, vh = PAGE2_AFR_RESERVED_VALUE
    e.append(f'<rect x="{vx - 22}" y="{vy - 2}" width="{vw + 44}" height="{vh}" '
             f'rx="6" fill="#0a0c0f" stroke="#4b3b21" stroke-width="1.2"/>')
    e.append(text(vx + vw / 2, vy + 20, "WIDEBAND INPUT REQUIRED", 11, AMBER,
                  weight=600, spacing=1.15))
    e.append(text(vx + vw / 2, vy + 39, "NO LIVE CHANNEL MAPPED", 8, WHITE_DIM,
                  weight=400, spacing=.7, opacity=.65))

    # ---- data panels -----------------------------------------------------
    for title, (px, py, pw, ph), rows in PAGE2_GROUPS:
        e.append(f'<rect x="{px}" y="{py}" width="{pw}" height="{ph}" rx="7" '
                 f'fill="{PANEL_BG}" stroke="{PANEL_LINE}" stroke-width="1.5"/>')
        e.append(text(px+16, py+25, title, 13, WHITE, weight=600,
                      anchor="start", spacing=1.8))
        pitch = (ph-50) / len(rows)
        for i, (label, unit) in enumerate(rows):
            ry = py + 48 + i*pitch
            e.append(text(px+16, ry, label, 13, WHITE_DIM, weight=400,
                          anchor="start"))
            if unit:
                e.append(text(px+pw-14, ry, unit, 11, WHITE_DIM, weight=400,
                              anchor="end", opacity=.72))
            e.append(f'<line x1="{px+14}" y1="{ry+7:.1f}" x2="{px+pw-14}" '
                     f'y2="{ry+7:.1f}" stroke="#181b1f" stroke-width="1"/>')
    e.append("</svg>")
    write_svg("bg_page2", "".join(e), W, H)



def background_page3_v2():
    W, H = CANVAS
    e = background_base("CHASSIS DYNAMICS")

    # The whole vehicle -- silhouette, subframes, dampers, wheels and the
    # corner leader lines -- is drawn inside one group scaled about its own
    # nose.  At full size the body ran from y=86 to y=692, which left the
    # steering bar sitting on the bonnet and the rear ride height sitting on
    # the hatch.  Shrinking about the nose frees a band at each end without
    # moving the car off centre or changing any of its internal proportions.
    e.append(f'<g transform="translate(640,{PAGE3_CAR_TOP}) '
             f'scale({PAGE3_CAR_SCALE}) translate(-640,-{PAGE3_CAR_TOP})">')

    # Scirocco-derived top view: rounded nose and hatch, but long, nearly
    # parallel flanks through the doors. The earlier continuous oval read as
    # an egg instead of a low, rectangular three-door coupe.
    e.append('<path d="M585 102 Q640 86 695 102 L719 116 Q736 128 741 151 '
             'L754 246 L754 505 L742 603 Q738 632 713 666 '
             'Q687 689 640 692 Q593 689 567 666 Q542 632 538 603 '
             'L526 505 L526 246 L539 151 Q544 128 561 116 Z" '
             'fill="#0b0d10" stroke="#8b9198" stroke-width="2"/>')
    e.append('<path d="M561 157 Q640 137 719 157 L731 267 L549 267 Z '
             'M548 294 L732 294 L742 477 L538 477 Z '
             'M542 503 L738 503 L716 629 Q640 651 564 629 Z" '
             'fill="none" stroke="#3e4248" stroke-width="1.5"/>')
    e.append('<line x1="640" y1="120" x2="640" y2="671" stroke="#3e4248" '
             'stroke-width="1" opacity=".45"/>')

    # Minimal subframes and rear hardware; values remain the visual priority.
    e.append('<path d="M548 205 Q640 184 732 205 L717 240 Q640 254 563 240 Z" '
             'fill="#111419" stroke="#aeb4ba" stroke-width="2"/>')
    e.append('<rect x="574" y="199" width="132" height="16" rx="8" '
             'fill="#111419" stroke="#aeb4ba" stroke-width="2"/>')
    e.append('<path d="M524 187 L583 207 L560 238 M756 187 L697 207 L720 238" '
             'fill="none" stroke="#9da3aa" stroke-width="3"/>')
    e.append('<path d="M548 525 Q640 509 732 525 L714 568 Q640 581 566 568 Z" '
             'fill="#111419" stroke="#aeb4ba" stroke-width="2"/>')
    e.append('<path d="M524 547 L574 526 L594 568 M524 547 L578 566 L618 523 '
             'M756 547 L706 526 L686 568 M756 547 L702 566 L662 523" '
             'fill="none" stroke="#8b9198" stroke-width="3"/>')

    for cx in (524, 756):
        e.append(f'<rect x="{cx-20}" y="492" width="40" height="110" rx="12" '
                 f'fill="#111419" stroke="{WHITE_DIM}" stroke-width="2"/>')
        e.append(f'<path d="M {cx-18} 520 H {cx+18} M {cx-18} 547 H {cx+18} '
                 f'M {cx-18} 574 H {cx+18}" stroke="#40444a" stroke-width="1"/>')

    # Red damper accents read instantly without over-drawing the suspension.
    for x, y in ((558, 143), (722, 143), (565, 504), (715, 504)):
        e.append(f'<path d="M{x} {y} V {y+74}" stroke="#aeb4ba" stroke-width="3"/>')
        e.append(f'<path d="M{x-9} {y+12} C{x+12} {y+17} {x+12} {y+24} {x-9} {y+29} '
                 f'C{x+12} {y+34} {x+12} {y+41} {x-9} {y+46} C{x+12} {y+51} '
                 f'{x+12} {y+58} {x-9} {y+63}" fill="none" stroke="{NEEDLE}" stroke-width="3"/>')

    e.append('</g>')

    # Corner leaders are drawn OUTSIDE the group, at the scaled axle heights,
    # so they run from the car's flank all the way to the value columns
    # instead of stopping short of them.
    front_y = PAGE3_CAR_TOP + (187 - PAGE3_CAR_TOP) * PAGE3_CAR_SCALE
    rear_y = PAGE3_CAR_TOP + (547 - PAGE3_CAR_TOP) * PAGE3_CAR_SCALE
    left_x = 640 - (640 - 526) * PAGE3_CAR_SCALE
    right_x = 640 + (754 - 640) * PAGE3_CAR_SCALE
    for axle_y, drop_y in ((front_y, 170), (rear_y, 426)):
        e.append(f'<path d="M {left_x:.1f} {axle_y:.1f} H 270 V {drop_y} '
                 f'M {right_x:.1f} {axle_y:.1f} H 1010 V {drop_y}" '
                 f'fill="none" stroke="#35393f" stroke-width="1.5"/>')
    labels = [(44,110,"DCC / FRONT LEFT"),(1018,110,"DCC / FRONT RIGHT"),
              (44,366,"DCC / REAR LEFT"),(1018,366,"DCC / REAR RIGHT")]
    for x, y, label in labels:
        e.append(text(x, y, label, 12, WHITE_DIM, weight=400, anchor="start", spacing=1.2))
    for key, (x, y, w, h) in PAGE3_BARS.items():
        e.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" fill="#202328"/>')
    e.append(text(44, 190, "RIDE HEIGHT FL", 12, WHITE_DIM, anchor="start", spacing=1.0))
    e.append(text(1018, 190, "RIDE HEIGHT FR", 12, WHITE_DIM, anchor="start", spacing=1.0))
    spx, spy, spw, sph = PAGE3_TEXT["speed"]
    e.append(text(spx + spw / 2, spy + sph + 16, "km/h", 11, WHITE_DIM,
                  weight=600, spacing=1.2))
    lx, ly, lw, lh = PAGE3_STEER_BARS["left"]
    rx, ry, rw, rh = PAGE3_STEER_BARS["right"]
    e.append(f'<rect x="{lx}" y="{ly}" width="{lw + rw}" height="{lh}" rx="2" '
             f'fill="#040506" stroke="{PANEL_LINE}" stroke-width="1"/>')
    e.append(f'<line x1="{rx}" y1="{ly - 4}" x2="{rx}" y2="{ly + lh + 4}" '
             f'stroke="{WHITE_DIM}" stroke-width="1.5"/>')
    e.append(text(lx - 10, ly + lh, "L", 11, WHITE_DIM, weight=600,
                  anchor="end", opacity=.7))
    e.append(text(rx + rw + 10, ry + rh, "R", 11, WHITE_DIM, weight=600,
                  anchor="start", opacity=.7))
    e.append(text(640, 590, "REAR RIDE HEIGHT", 12, WHITE_DIM, spacing=1.0))
    e.append("</svg>")
    write_svg("bg_page3", "".join(e), W, H)


def background_page4_v2():
    W, H = CANVAS
    e = background_base("TRIP, DIAGNOSTICS & SYSTEM HEALTH")
    for title, (px, py, pw, ph), rows in PAGE4_GROUPS:
        e.append(f'<rect x="{px}" y="{py}" width="{pw}" height="{ph}" rx="7" '
                 f'fill="{PANEL_BG}" stroke="{PANEL_LINE}" stroke-width="1.5"/>')
        e.append(text(px+18, py+34, title, 15, WHITE, weight=600, anchor="start", spacing=2))
        pitch = min(74, (ph-86) / max(1, len(rows)))
        for i, (label, unit) in enumerate(rows):
            ry = py + 92 + i*pitch
            e.append(text(px+18, ry, label, 15, WHITE_DIM, anchor="start"))
            if unit:
                e.append(text(px+pw-18, ry, unit, 12, WHITE_DIM, anchor="end", opacity=.7))
            e.append(f'<line x1="{px+16}" y1="{ry+18:.1f}" x2="{px+pw-16}" y2="{ry+18:.1f}" '
                     f'stroke="#181b1f" stroke-width="1"/>')
    e.append(text(34, 692, "CLEAR CODES REQUIRES A 2-SECOND HOLD / COMMAND IS REFUSED WHILE MOVING",
                  10, WHITE_DIM, anchor="start", spacing=1.0, opacity=.65))
    e.append("</svg>")
    write_svg("bg_page4", "".join(e), W, H)


# ------------------------------------------------------------------ writing

# --- deck render-band compensation -------------------------------------------
# RealDash paints a page BACKGROUND across the whole window, but lays every
# GAUGE out inside a shorter band.  Measured on the deck 2026-08-29 with the
# Android navigation bar visible, a gauge authored at design y renders at
#
#     y_screen = 0.9136 * y + 30
#
# -- the 660-tall design squeezed into 603 px and centred, because RealDash
# subtracts the 60 px navigation bar twice.  Least-squares fit over the twelve
# dial-bezel edges on page 1; residuals under 3 px, and the dials measure
# 393x361 on screen instead of 396x396, which is the same squeeze seen
# directly.
#
# The background is a page property and is NOT subject to that transform, so
# artwork and gauges sit in two different spaces and drift apart towards the
# top and bottom of the screen.  That is exactly the "values float above their
# labels, worse further from the middle" defect: on page 4 the labels step 74 px
# and the values 68 px.  Painting the background through the same map puts both
# back into one space.
#
# Everything stays authored in plain 1280x660 design coordinates; only the
# finished page SVG is remapped, so layout_v2 remains the single source of
# truth and the gauge rectangles are never pre-distorted.
# DISABLED 2026-08-29 after a second measurement.  Reloading the file a
# second time produced gauges at a plain 1:1 scale (page-4 value rows
# stepped the authored 74 px, not 68), which means the squeeze above is a
# TRANSIENT state of RealDash's buggy load-time layout -- the same bug that
# starts the dashboard with its top pushed off screen -- and not a stable
# property of the deck.  Compensating for it bakes the distortion in
# backwards whenever RealDash lays out correctly, so the artwork is left
# 1:1 and the transform is kept only as a diagnostic.  If the squeeze ever
# becomes the steady state, set this True and re-measure with the
# dial-bezel fit rather than adjusting layout constants by eye.
GAUGE_BAND_COMPENSATION = False
GAUGE_BAND_TOP = 30.0
GAUGE_BAND_HEIGHT = 603.0


def to_gauge_band(svg_text):
    """Re-map a finished page background into RealDash's gauge band."""
    head, sep, rest = svg_text.partition(">")
    if not sep:
        raise ValueError("page SVG has no opening tag")
    close = rest.rindex("</svg>")
    band = '<g transform="translate(0 %s) scale(1 %s)">' % (
        fmt(GAUGE_BAND_TOP), fmt(GAUGE_BAND_HEIGHT / CANVAS[1]))
    return head + ">" + band + rest[:close] + "</g></svg>"


def write_svg(name, content, w, h):
    if GAUGE_BAND_COMPENSATION and name.startswith("bg_page"):
        content = to_gauge_band(content)
    os.makedirs(SVG_DIR, exist_ok=True)
    path = os.path.join(SVG_DIR, name + ".svg")
    old = None
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            old = f.read()
    if old != content:
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
    ASSETS.append((name, w, h))


def render_all():
    os.makedirs(PNG_DIR, exist_ok=True)
    for (name, w, h) in ASSETS:
        svg = os.path.join(SVG_DIR, name + ".svg")
        html = os.path.join(SVG_DIR, name + ".html")
        with open(svg, encoding="utf-8") as f:
            body = f.read()
        with open(html, "w", encoding="utf-8") as f:
            f.write("<!doctype html><html><head><style>"
                    "html,body{margin:0;padding:0;background:transparent;}"
                    "svg{display:block;}</style></head><body>"
                    + body + "</body></html>")
        out = os.path.join(PNG_DIR, name + ".png")
        if os.path.exists(out) and os.path.getmtime(out) >= os.path.getmtime(svg):
            print(f"current  {name}.png ({w}x{h})")
            continue
        # Sharp/librsvg preserves alpha and is deterministic even while the
        # interactive RealDash and Chromium apps are open.  Render to a
        # temporary file and atomically replace the asset only on success.
        with tempfile.TemporaryDirectory(prefix="realdash-render-") as scratch:
            temp_out = os.path.join(scratch, name + ".png")
            code = (
                f"import sharp from '{SHARP_MJS}'; "
                "await sharp(process.argv[1]).png().toFile(process.argv[2]);"
            )
            env = os.environ.copy()
            env["XDG_CACHE_HOME"] = scratch
            r = subprocess.run(
                [NODE, "--input-type=module", "-e", code, svg, temp_out],
                capture_output=True, text=True, timeout=60, env=env)
            if r.returncode == 0 and os.path.exists(temp_out):
                os.replace(temp_out, out)
        if not os.path.exists(out) or os.path.getmtime(out) < os.path.getmtime(svg):
            print(f"FAILED {name}: {r.stderr[-400:]}")
            sys.exit(1)
        print(f"rendered {name}.png ({w}x{h})")


def main():
    # One visual grammar across all six faces.  Assets render at exactly 2x
    # their final v2 display sizes.
    gauge_face("face_boost", 800, -1.0, 2.0, 0.5, 0.1,
               amber=(1.2, 1.6), redline=(1.6, 2.0),
               unit="bar", title="Turbo", decimals=1,
               label_scale=0.86)
    gauge_face("face_rpm", 800, 0, 7, 1, 0.25, redline=(6.3, 7.0),
               unit="× 1000", title="1/min")
    gauge_face("face_coolant", 400, 50, 130, 20, 5, redline=(110, 130),
               amber=(105, 110),
               unit="°C", title="Water", label_scale=0.80)
    gauge_face("face_oil", 400, 50, 150, 25, 5, redline=(130, 150),
               amber=(120, 130),
               unit="°C", title="Oil", label_scale=0.80)
    gauge_face("face_charge", 400, 0, 120, 20, 5,
               amber=(50, 60), redline=(60, 120),
               unit="°C", title="Charge", label_scale=0.80)
    gauge_face("face_volt", 400, 8, 16, 2, 0.5,
               redline=(8, 10.5), amber=(10.5, 11),
               unit="V", title="Volt", label_scale=0.80)

    needle("needle_main", 800, 0.775)
    needle("needle_small", 400, 0.740, width_frac=0.028)
    needle("needle_main_ghost", 800, 0.775, ghost=True)
    needle("needle_small_ghost", 400, 0.740, width_frac=0.028, ghost=True)
    needle_marker("needle_target", 800)
    spark_needle()
    front_wheel_asset("wheel_front_left")
    front_wheel_asset("wheel_front_right", mirrored=True)
    clear_codes_button_asset()
    # Transparent face for overlay-only RealDash needle gauges (ghost peaks,
    # spark timing, and the two schematic front wheels).
    write_svg("blank", svg_open(1, 1) + "</svg>", 1, 1)

    indicator_symbols = make_icons_v2()
    background_page1_v2(indicator_symbols)
    background_page2_v2()
    background_page3_v2()
    background_page4_v2()
    render_all()


if __name__ == "__main__":
    main()
