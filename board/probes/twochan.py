"""
twochan.py -- can this gateway hold TWO TP 2.0 channels at once?

WHY IT MATTERS
    The gear selector lives in module 02 (the DSG), not in the engine ECU.
    To put gear on the dashboard the firmware has to read module 02 WITHOUT
    losing the ~14 Hz engine stream that everything else depends on. Three
    things could go wrong and they need telling apart:

      1. The gateway refuses a second channel outright.
      2. It accepts one, but opening it drops the engine channel.
      3. Both work, but they steal each other's frames.

    (3) is a real risk here rather than a hypothetical: canio allows only ONE
    all-matches listener, so both channels share it, and Channel._recv_raw
    DISCARDS any frame that does not match the id it is currently waiting
    for. A frame for the other channel that lands mid-read is dropped, not
    queued. Whether that matters in practice depends on timing, which is
    what this measures.

WHAT IT REPORTS
    Engine read rate and error count before, during and after the DSG
    channel is open -- so a rate collapse is attributed to the right cause
    -- plus the decoded gear letter, which is the payload we actually want.

*** READ ONLY. *** KWP 0x21 and 0x1A only.
"""

import time
import board
import digitalio
import displayio
import terminalio
from adafruit_display_text import label

import uds
import tp20
import usb_cdc

try:
    from fourwire import FourWire
except ImportError:
    FourWire = displayio.FourWire

# ----------------------------------------------------------------- output
_out = usb_cdc.data
if _out is not None:
    try:
        _out.write_timeout = 0.5
    except Exception:
        pass
_builtin_print = print


def print(*args, **kw):
    line = " ".join(str(a) for a in args) + "\r\n"
    if _out is None:
        _builtin_print(line, end="")
        return
    try:
        _out.write(line.encode("utf-8"))
    except Exception:
        pass


# ----------------------------------------------------------------- config

ENGINE_ADDR = 0x01
DSG_ADDR = 0x02
ENGINE_BLOCK = 115          # the fast block the gauge itself uses
DSG_BLOCK = 3               # field 1 came back as formula 17 = " P" in Park
DSG_EVERY_S = 0.5           # service the DSG this often (keeps it alive)
PHASE_S = 15.0              # seconds per phase


def make_display():
    for pin in (board.D5, board.D6):
        try:
            digitalio.DigitalInOut(pin).switch_to_output(True)
        except Exception:
            pass
    try:
        displayio.release_displays()
        bus = FourWire(board.SPI(), command=board.D10, chip_select=board.D9,
                       reset=None, baudrate=1000000)
        time.sleep(1)
        import adafruit_ssd1680
        return adafruit_ssd1680.SSD1680(
            bus, width=250, height=122, rotation=270, busy_pin=None,
            highlight_color=0xFF0000, colstart=8, seconds_per_frame=1)
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
            if self.d.time_to_refresh > 0:
                return
            self.d.refresh()
            self.last = (title, rows)
        except Exception:
            pass


def gear_of(d):
    """Field 1 of DSG block 3: formula 17 is two ASCII chars, and the gear
    is the second one. Returns the letter, or None."""
    if not d or len(d) < 3:
        return None
    fid, a, b = d[0], d[1], d[2]
    if fid != 17:
        return "?fid%d(%02X,%02X)" % (fid, a, b)
    try:
        return ("%c%c" % (a, b)).strip() or "(blank)"
    except Exception:
        return None


def run_phase(name, eng, dsg, seconds, screen):
    """Hammer the engine channel; service the DSG if one is given."""
    reads = errs = dsg_ok = dsg_err = 0
    gear = "-"
    t0 = time.monotonic()
    next_dsg = t0
    while time.monotonic() - t0 < seconds:
        try:
            if not eng.connected:
                eng.connect()
            d = tp20.read_block(eng, ENGINE_BLOCK, timeout=0.4)
            if d:
                reads += 1
            else:
                errs += 1
        except tp20.TP20Error:
            errs += 1
        if dsg is not None and time.monotonic() >= next_dsg:
            next_dsg = time.monotonic() + DSG_EVERY_S
            try:
                if not dsg.connected:
                    dsg.connect(timeout=0.6, tries=1, attempts=2)
                gd = tp20.read_block(dsg, DSG_BLOCK, timeout=0.4)
                if gd:
                    dsg_ok += 1
                    g = gear_of(gd)
                    if g and g != gear:
                        gear = g
                        print("    gear -> %s   (raw %s)"
                              % (g, "".join("%02X" % x for x in gd[:6])))
                else:
                    dsg_err += 1
            except tp20.TP20Error as e:
                dsg_err += 1
    el = time.monotonic() - t0
    print("  %-22s engine %5.1f Hz  (%d ok / %d err)   dsg %d ok / %d err   gear=%s"
          % (name, reads / el, reads, errs, dsg_ok, dsg_err, gear))
    screen.show("TWOCHAN", name[:34], "%.1f Hz" % (reads / el), "gear %s" % gear)
    return reads / el


screen = Screen()
screen.show("TWOCHAN", "two TP2.0 channels?", "read only")

bus = uds.Bus()
bus.listener.deinit()

print(">>>TWOCHAN-BEGIN<<<")
print("=" * 64)
print("Can this gateway hold an engine channel AND the DSG at once?")
print("=" * 64)

shared = bus.can.listen(timeout=0.02)
eng = tp20.Channel(bus.can, dest=ENGINE_ADDR, listener=shared)

try:
    eng.connect()
    print("engine channel: open")
except tp20.TP20Error as e:
    print("engine channel FAILED: %s" % e)
    raise SystemExit

base = run_phase("baseline (engine only)", eng, None, PHASE_S, screen)

dsg = tp20.Channel(bus.can, dest=DSG_ADDR, listener=shared)
opened = False
try:
    dsg.connect(timeout=0.8, tries=2, attempts=2)
    opened = True
    print("dsg channel: open  <-- the gateway ALLOWS a second channel")
except tp20.TP20Error as e:
    print("dsg channel REFUSED: %s" % e)

if opened:
    both = run_phase("both channels", eng, dsg, PHASE_S, screen)
    try:
        dsg.disconnect()
    except Exception:
        pass
    after = run_phase("after closing dsg", eng, None, PHASE_S, screen)
    print("")
    print("=" * 64)
    print("VERDICT")
    print("  engine alone   %.1f Hz" % base)
    print("  with dsg open  %.1f Hz  (%.0f%% of baseline)"
          % (both, 100.0 * both / base if base else 0))
    print("  after closing  %.1f Hz" % after)
    if base and both / base > 0.6:
        print("  -> usable: gear can ride alongside the engine stream")
    else:
        print("  -> too costly: read the DSG only when parked, or on demand")
    print("=" * 64)

print(">>>TWOCHAN-END<<<")
screen.show("TWOCHAN DONE", "see the capture")

while True:
    time.sleep(1)
