"""
shiftlight.py -- WS2812B shift / warning strip on D11.

Implements the "NeoPixel shift / warning light" section of
docs/PLAN-deck.md: dark at cruise, progressive green->amber fill from
4500 to 6200 rpm, all-red flash at the shift point, plus warning
overrides that outrank the tach because each one is a reason to lift,
not to keep pulling. Priority, highest first:

    knock retard      fast red flash      the ECU is pulling timing to
                                          save the engine -- lift NOW
    boost error       blue/red alternate  wastegate/N75/leak fault;
                                          acting beats reading a gauge
    coolant/oil hot   amber pulse         cool-down lap
    stored DTC        steady red end px   overlaid on all of the above;
                                          informational nag, not urgent

Hardware per the plan: 8-16 px WS2812B, data on D11 (D5/D6/D9/D10 belong
to the e-ink wing, A0 is the mode jumper) through a 300-470 ohm resistor,
fed from the USB 5V pin with >= 470 uF across the strip supply.

Cost per render() call is deliberately trivial: every frame the strip can
show is described by a small (mode, phase, dtc) key, colour tables are
precomputed at import, and pixels are rewritten (auto_write=False, one
show()) only when the key changes. Flash phases derive from
time.monotonic() -- no sleeps, so the ~14 Hz TP 2.0 acquisition rate is
untouched.

Any init failure (no neopixel module, pin taken) disables the module
after one log line and never raises: the gauge outranks the light show.
Note WS2812 data is write-only, so init CANNOT detect a missing strip --
it succeeds and the writes just go nowhere, which is harmless.
"""

import time

STRIP_PIXELS = 55      # 36in / 91 cm of 60-per-metre strip across the dash
                       # (see docs/PLAN-deck.md BOM; gradient scales with this)
BRIGHTNESS = 0.15       # plan caps software brightness at 0.2 (worst case
                        # ~200 mA from the USB 5V pin); 0.15 leaves margin

try:
    import board
    STRIP_PIN = board.D11   # the only free data-capable pin, per the plan
except Exception:
    STRIP_PIN = None        # not on CircuitPython; init() will say so

# --- tach thresholds (plan: dark at cruise, fill 4500-6200, flash 6300) ---
# 4500 is where holding the gear starts paying on the 2.0 TSI; 6200 ends
# the fill just short of the shift call so a full bar reads "almost";
# 6300 is "shift now", comfortably before the ~6500 limiter.
FILL_START_RPM = 4500.0
FILL_END_RPM = 6200.0
SHIFT_RPM = 6300.0

# --- knock (plan: retard past threshold -> fast red) ---
# 1.0 degree on any cylinder is a real intervention, not noise: block 020
# reads exactly 0.0 with no knock (verified at idle, see boost.EXTRA_BLOCKS).
KNOCK_RETARD_DEG = 1.0

# --- boost vs specified (plan: -300 / +250 mbar under load) ---
# Underboost only counts with load > 60%: off throttle, actual sits far
# below spec legitimately. Overboost is never legitimate, so no load gate.
UNDERBOOST_MBAR = 300.0
UNDERBOOST_MIN_LOAD = 60.0
OVERBOOST_MBAR = 250.0

# --- temperatures (plan: coolant > 105 C or oil > 130 C -> amber pulse) ---
# 105 is above the pressurised system's normal 90-100 band; 130 oil is
# where the film starts giving up. Both are "back off", not "stop engine".
COOLANT_MAX_C = 105.0
OIL_MAX_C = 130.0

# --- flash timing (toggle/step intervals in seconds) ---
# Knock toggles twice as fast as the shift flash so the two red states
# cannot be confused at a glance.
SHIFT_FLASH_S = 0.10    # 5 Hz blink
KNOCK_FLASH_S = 0.05    # 10 Hz blink
ALT_S = 0.25            # blue/red alternation
PULSE_S = 1.2           # amber pulse full period

# --- colours (pre-brightness; NeoPixel scales by BRIGHTNESS) ---
OFF = (0, 0, 0)
RED = (255, 0, 0)
BLUE = (0, 0, 255)
AMBER = (255, 140, 0)
GREEN = (0, 255, 0)


def _blend(c0, c1, t):
    return (int(c0[0] + (c1[0] - c0[0]) * t),
            int(c0[1] + (c1[1] - c0[1]) * t),
            int(c0[2] + (c1[2] - c0[2]) * t))


# Per-pixel gradient for the tach fill, green at the bottom to amber at
# the top, computed once so render() only indexes.
FILL_COLORS = tuple(_blend(GREEN, AMBER, i / max(1, STRIP_PIXELS - 1))
                    for i in range(STRIP_PIXELS))

# Amber pulse as discrete brightness steps. Quantising the pulse means
# consecutive render() calls usually produce the same key and skip the
# pixel write entirely; 8 steps still reads as a smooth breathe.
_PULSE_LEVELS = (0.15, 0.4, 0.7, 1.0, 1.0, 0.7, 0.4, 0.15)
PULSE_COLORS = tuple((int(AMBER[0] * v), int(AMBER[1] * v), int(AMBER[2] * v))
                     for v in _PULSE_LEVELS)

_pixels = None
_last_key = None


def init():
    """Create the strip; on any failure log once and stay disabled.

    Never raises -- a bad solder joint on the light must not take the
    gauge down. Returns True if the strip object exists.
    """
    global _pixels, _last_key
    _pixels = None
    _last_key = None
    if STRIP_PIN is None:
        print("shiftlight: disabled (no board.D11)")
        return False
    try:
        import neopixel
        _pixels = neopixel.NeoPixel(STRIP_PIN, STRIP_PIXELS,
                                    brightness=BRIGHTNESS, auto_write=False)
        _pixels.fill(OFF)
        _pixels.show()
        print("shiftlight: %d px on D11" % STRIP_PIXELS)
        return True
    except Exception as e:
        print("shiftlight: disabled (%s)" % e)
        _pixels = None
        return False


def off():
    """Blank the strip. gauge_main calls this when it gives up on
    render() so a stale warning frame does not stay lit. Never raises."""
    global _last_key
    _last_key = None
    if _pixels is None:
        return
    try:
        _pixels.fill(OFF)
        _pixels.show()
    except Exception:
        pass


def _knocking(g):
    # Sign convention: boost.py decodes block 020 through tp20.value_of
    # with whatever formula id the ECU reports, and only the ZERO is
    # verified (exactly 0.0 at idle) -- the formula under real knock is
    # not. Formula 27 would yield NEGATIVE degrees for retard, formulas
    # 3/4 a positive magnitude. The plan's threshold is written as
    # "<= -1.0 deg"; abs() honours that if the decode is signed AND still
    # fires on a positive-magnitude decode, and the confirmed 0.0 at
    # no-knock means abs() cannot false-trigger either way.
    return (abs(g.knock1) >= KNOCK_RETARD_DEG
            or abs(g.knock2) >= KNOCK_RETARD_DEG
            or abs(g.knock3) >= KNOCK_RETARD_DEG
            or abs(g.knock4) >= KNOCK_RETARD_DEG)


def render(g, n_faults=0):
    """Update the strip from a boost.Gauge. Call once per main-loop pass.

    Computes the frame key first; in the common case (same key as last
    time) the cost is a few float compares and no pixel I/O.
    """
    global _last_key
    if _pixels is None:
        return
    now = time.monotonic()

    # g.spec and g.actual are ABSOLUTE mbar straight from block 115.
    if _knocking(g):
        key = ("knock", int(now / KNOCK_FLASH_S) & 1)
    elif ((g.load > UNDERBOOST_MIN_LOAD
           and g.actual < g.spec - UNDERBOOST_MBAR)
          or g.actual > g.spec + OVERBOOST_MBAR):
        key = ("boost", int(now / ALT_S) & 1)
    elif g.coolant > COOLANT_MAX_C or g.oil > OIL_MAX_C:
        key = ("temp",
               int(now * len(PULSE_COLORS) / PULSE_S) % len(PULSE_COLORS))
    elif g.rpm >= SHIFT_RPM:
        key = ("shift", int(now / SHIFT_FLASH_S) & 1)
    else:
        # Progressive fill; below FILL_START_RPM this is 0 = dark at
        # cruise. RPM decodes in ~0.2 rpm steps but the ~106 rpm per
        # pixel quantisation gives natural hysteresis against jitter.
        n_lit = int((g.rpm - FILL_START_RPM) * STRIP_PIXELS
                    / (FILL_END_RPM - FILL_START_RPM))
        if n_lit < 0:
            n_lit = 0
        elif n_lit > STRIP_PIXELS:
            n_lit = STRIP_PIXELS
        key = ("fill", n_lit)

    key = (key[0], key[1], 1 if n_faults else 0)
    if key == _last_key:
        return
    _last_key = key

    mode, phase, dtc = key
    px = _pixels
    if mode == "fill":
        for i in range(STRIP_PIXELS):
            px[i] = FILL_COLORS[i] if i < phase else OFF
    elif mode == "temp":
        px.fill(PULSE_COLORS[phase])
    elif mode == "boost":
        px.fill(BLUE if phase else RED)
    else:                                   # knock / shift: red blink
        px.fill(RED if phase else OFF)
    if dtc:
        # Steady red end pixel overlaid on every mode, including the dark
        # cruise strip: a stored code should nag until cleared. (It hides
        # inside all-red phases; the plan accepts that -- it is the
        # lowest-priority indication.)
        px[STRIP_PIXELS - 1] = RED
    px.show()
