"""
clearcodes.py -- one-shot: clear the ECU's stored fault codes, then report.

*** THIS WRITES TO THE ECU. *** It is the only tool in the project that
does. The owner asked for it explicitly (2026-08-23) to see which codes
come back on the next drive -- P0234 (overboost) and P2261 (diverter valve)
were both stored, and whether they return says whether the fault is live or
historical.

What clearing costs, so nobody is surprised: it also discards freeze-frame
data and resets the emissions readiness monitors, which then need a full
drive cycle before an emissions test will pass.

Deployed as code.py. It does NOT hand over to the gauge program afterwards
-- it parks on a result screen so the outcome is readable, and the normal
firmware gets redeployed over it (tools/deploy-board.ps1). That is
deliberate: it makes the clear a single, witnessed event rather than
something that quietly repeats on every power-up.

The ECU is only reachable with the ignition on. This makes ONE pass: it
retries the channel briefly (WAIT_MINUTES) in case the bus is mid-handshake,
then reports either way rather than sitting there waiting for a key event.

Everything is reported on the e-ink, because boot.py disables the USB
console in the car (RealDash needs the data port at interface 0) and the
head unit is not rooted, so there is no serial output to read.
"""

import time
import board
import digitalio
import displayio
import terminalio
from adafruit_display_text import label

import uds
import tp20

try:
    from fourwire import FourWire
except ImportError:
    FourWire = displayio.FourWire

WAIT_MINUTES = 2           # brief: one attempt now, not a vigil
ROTATION = 270
RETRY_S = 3.0

try:
    import neopixel
    _px = neopixel.NeoPixel(board.NEOPIXEL, 1, brightness=0.2, auto_write=True)
except Exception:
    _px = None


def pixel(c):
    if _px is not None:
        try:
            _px.fill(c)
        except Exception:
            pass


def make_display():
    """Same bring-up as the gauge program; None if the panel is absent."""
    for pin in (board.D5, board.D6):
        try:
            cs = digitalio.DigitalInOut(pin)
            cs.switch_to_output(True)
        except Exception:
            pass
    try:
        displayio.release_displays()
        bus = FourWire(board.SPI(), command=board.D10, chip_select=board.D9,
                       reset=None, baudrate=1000000)
        time.sleep(1)
        import adafruit_ssd1680
        d = adafruit_ssd1680.SSD1680(bus, width=250, height=122,
                                     rotation=ROTATION, busy_pin=None,
                                     highlight_color=0xFF0000, colstart=8,
                                     seconds_per_frame=1)
        return d
    except Exception:
        return None


class Screen:
    def __init__(self):
        self.d = make_display()
        self.last = None
        if self.d is None:
            return
        w, h = self.d.width, self.d.height
        self.g = displayio.Group()
        bg = displayio.Bitmap(w, h, 1)
        pal = displayio.Palette(1)
        pal[0] = 0xFFFFFF
        self.g.append(displayio.TileGrid(bg, pixel_shader=pal))
        self.title = label.Label(terminalio.FONT, text="", color=0xFF0000,
                                 scale=2, x=4, y=14)
        self.g.append(self.title)
        self.lines = []
        y = 36
        while y < h - 6 and len(self.lines) < 6:
            lb = label.Label(terminalio.FONT, text="", color=0x000000, x=4, y=y)
            self.g.append(lb)
            self.lines.append(lb)
            y += 13
        try:
            self.d.root_group = self.g
        except AttributeError:
            self.d.show(self.g)

    def show(self, title, *rows):
        if self.d is None or (title, rows) == self.last:
            return
        self.title.text = title[:20]
        for i, lb in enumerate(self.lines):
            lb.text = rows[i][:40] if i < len(rows) else ""
        try:
            while self.d.time_to_refresh > 0:
                time.sleep(0.2)
            self.d.refresh()
            self.last = (title, rows)
        except Exception:
            pass


def dtc_names(dtcs):
    if not dtcs:
        return "none"
    return " ".join(d[0] for d in dtcs[:4])


screen = Screen()
screen.show("CLEAR CODES", "starting...", "ignition ON needed")
pixel((0, 0, 255))

bus = uds.Bus()
bus.listener.deinit()
ch = tp20.Channel(bus.can)

deadline = time.monotonic() + WAIT_MINUTES * 60
before = None
opened = False

while time.monotonic() < deadline and not opened:
    try:
        ch.connect()
        before = tp20.read_dtcs(ch)
        opened = True
    except Exception:
        pixel((255, 140, 0))
        screen.show("WAITING", "turn the ignition ON",
                    "looking for the ecu...",
                    "%d min left" % int((deadline - time.monotonic()) / 60))
        time.sleep(RETRY_S)

if not opened:
    pixel((255, 0, 0))
    screen.show("NO ECU", "never answered", "ignition was off?",
                "redeploy + retry")
    while True:
        time.sleep(1)

screen.show("CLEARING", "found: %s" % dtc_names(before),
            "%d code(s)" % len(before or ()), "sending 14 FF 00...")

ok = False
try:
    ok = tp20.clear_dtcs(ch)
except Exception:
    ok = False

time.sleep(1.0)
after = None
try:
    if not ch.connected:
        ch.connect()
    after = tp20.read_dtcs(ch)
except Exception:
    after = None

if ok and not after:
    pixel((0, 255, 0))
    screen.show("CLEARED", "was: %s" % dtc_names(before),
                "now: none", "drive it, then re-read",
                "to see what returns")
elif ok:
    pixel((160, 0, 255))
    screen.show("PARTIAL", "was: %s" % dtc_names(before),
                "now: %s" % dtc_names(after),
                "some faults are live",
                "and came straight back")
else:
    pixel((255, 0, 0))
    screen.show("REFUSED", "ecu rejected the",
                "clear request",
                "still: %s" % dtc_names(after if after else before))

try:
    ch.disconnect()
except Exception:
    pass

while True:
    time.sleep(1)
