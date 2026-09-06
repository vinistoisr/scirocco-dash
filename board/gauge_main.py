"""
code.py -- Scirocco live boost gauge.

Opens a TP 2.0 / KWP2000 channel to the engine ECU and streams boost as JSON
on USB serial for the head unit, with a peak-hold summary on the e-ink panel.

Verify the channel on the first drive: boost_bar should sit near 0.00 at idle
and cruise, and climb toward +1.0 bar or so under hard acceleration. The
e-ink peak-hold makes that readable after the fact without watching serial.

Set QUIET = True to emit only JSON (what the head unit wants). Leave it False
while checking things by eye; the human line is easier to read.

READ ONLY. Nothing here writes to the ECU.
"""

import time
import board
import digitalio
import displayio
import terminalio
from adafruit_display_text import label

import uds
import tp20
import boost
import realdash

# auxmods is the newest module on the board and must never cost the gauge.
# A MemoryError while COMPILING it on-device is a real possibility on the
# SAME51 -- and with the console disabled, a bare import failure is a dead
# board with nothing to say why. So: collect first, import guarded, and
# report the outcome down the data port at startup (see below).
import gc
gc.collect()
try:
    import auxmods
    _aux_err = None
except Exception as _e:
    auxmods = None
    _aux_err = "%s: %s" % (type(_e).__name__, _e)

# The shift light is a nice-to-have bolted onto a program the car depends on
# for its gauges, so a broken/absent module must never take the gauges down
# with it. shiftlight.init() already swallows hardware faults; this covers
# the import itself (missing file, missing neopixel lib, syntax error).
try:
    import shiftlight
except Exception as _e:      # noqa: keep the gauges alive regardless
    print("shiftlight: unavailable (%s)" % _e)

    class shiftlight(object):        # minimal stand-in, same call shape
        @staticmethod
        def init():
            pass

        @staticmethod
        def render(g, faults=0):
            pass

        @staticmethod
        def off():
            pass

try:
    from fourwire import FourWire
except ImportError:
    FourWire = displayio.FourWire

# Where the RealDash frames go. boot.py enables a second CDC channel so the
# binary stream stays clear of console text; if RealDash on the head unit
# grabs the wrong port, set this True to move the frames onto the console
# port instead (which also silences all text output).
REALDASH_ON_CONSOLE = False

_console_absent = False
try:
    import usb_cdc
    _console_absent = usb_cdc.console is None
    if REALDASH_ON_CONSOLE or usb_cdc.data is None:
        rd_port = usb_cdc.console
        RD_ON_CONSOLE = True
    else:
        rd_port = usb_cdc.data
        RD_ON_CONSOLE = False
except Exception as e:
    rd_port = None

# A CDC port with no write timeout BLOCKS FOREVER the moment the deck stops
# draining it -- which is precisely what happened on 2026-08-23: the bridge
# app's serial reader died when a client disconnected, the TX buffer
# filled, and the board froze mid-send_all looking exactly like a crash.
# With a timeout, frames are dropped instead; RealDash resyncs and the
# board keeps sampling, which is what "auto-work, always reconnect" means.
if rd_port is not None:
    try:
        rd_port.write_timeout = 0.5
    except Exception:
        pass
    RD_ON_CONSOLE = False

# Text and binary cannot share one port: if the frames are on the console,
# everything human-readable has to stop. And when boot.py has disabled the
# console outright (the normal car layout, so RealDash's port-0 claim lands
# on the data stream) there is nowhere for text to go at all -- skip the
# formatting work rather than writing into a void.
QUIET = RD_ON_CONSOLE or _console_absent
ROTATION = 270
SECONDS_PER_FRAME = 25

# A tri-color refresh takes ~15 s and blocks, which is longer than the TP 2.0
# channel timeout, so the ECU hangs up during every screen update and the next
# sample transparently reconnects. That costs a ~0.3 s hole in the log, so
# refresh sparingly: the panel is for at-a-glance status, not the live gauge.
SCREEN_EVERY = 60.0
INFO_HOLD = 45.0           # keep the startup info page up this long


def set_root_group(display, group):
    try:
        display.root_group = group
    except AttributeError:
        display.show(group)


# ---------------------------------------------------------------- display

def make_display():
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
        d = adafruit_ssd1680.SSD1680(
            bus, width=250, height=122, rotation=ROTATION, busy_pin=None,
            highlight_color=0xFF0000, colstart=8,
            seconds_per_frame=SECONDS_PER_FRAME,
        )
        d.rotation = ROTATION
        return d
    except Exception as e:
        print("e-ink: unavailable (%s)" % e)
        return None


class Screen:
    def __init__(self):
        self.display = make_display()
        if self.display is None:
            return
        w, h = self.display.width, self.display.height
        self.group = displayio.Group()
        bg = displayio.Bitmap(w, h, 1)
        pal = displayio.Palette(1)
        pal[0] = 0xFFFFFF
        self.group.append(displayio.TileGrid(bg, pixel_shader=pal))
        self.head = label.Label(terminalio.FONT, text="", color=0xFF0000,
                                scale=2, x=4, y=14)
        self.group.append(self.head)
        self.lines = []
        y = 36
        while y < h - 6 and len(self.lines) < 6:
            lbl = label.Label(terminalio.FONT, text="", color=0x000000, x=4, y=y)
            self.group.append(lbl)
            self.lines.append(lbl)
            y += 13
        self.cols = max(8, (w - 8) // 6)
        set_root_group(self.display, self.group)

    def show(self, head, *rows, **kw):
        if not QUIET:
            print("[%s] %s" % (head, "  |  ".join(rows)))
        if self.display is None:
            return False
        if self.display.time_to_refresh > 0:
            if not kw.get("force"):
                return False
            while self.display.time_to_refresh > 0:
                time.sleep(0.2)
        self.head.text = head[: self.cols // 2]
        for i, lbl in enumerate(self.lines):
            lbl.text = rows[i][: self.cols] if i < len(rows) else ""
        try:
            self.display.refresh()
        except RuntimeError:
            return False
        return True


# ---------------------------------------------------------------- main

try:
    import neopixel
    _px = neopixel.NeoPixel(board.NEOPIXEL, 1, brightness=0.2, auto_write=True)
except Exception:
    _px = None


_px_last = None


def pixel(c):
    """Status NeoPixel, written only on change: fill() on an auto_write
    pixel costs a strip refresh every call, and the color only actually
    changes when boost crosses a threshold."""
    global _px_last
    if _px is not None and c != _px_last:
        _px_last = c
        _px.fill(c)


pixel((0, 0, 255))
screen = Screen()
screen.show("BOOST", "opening tp2.0", "engine ecu")

bus = uds.Bus()

# Take the generic-EOBD snapshot FIRST. This ECU tears down the TP 2.0
# channel on every mode 01 request, so these can only be read while no
# channel is open. See boost.EOBD_SNAPSHOT.
#
# An empty snapshot almost always means the IGNITION IS OFF -- the normal
# state when the deck (which powers this board) comes up before the car
# does. Dying here used to park the board until the next file write, so:
# wait politely for a while, redoing the snapshot so the one-shot values
# (rail pressure, catalyst temp, runtime) are not lost to boot order. If
# EOBD stays silent past that, fall through and let the TP 2.0 loop below
# keep trying forever -- EOBD-dead-but-TP2.0-alive then still recovers.
snapshot = boost.eobd_snapshot(bus)
_w = 0
while not snapshot and _w < 10:
    _w += 1
    pixel((255, 140, 0))
    screen.show("NO ECU", "ignition off?",
                "waiting for the car", "(check %d)" % _w)
    if rd_port is not None and rd_port.in_waiting:
        rd_port.read(rd_port.in_waiting)     # keep the bridge's writes alive
    time.sleep(3)
    snapshot = boost.eobd_snapshot(bus)
print("eobd snapshot: %s" % ", ".join(
    "%s=%.2f" % (k, v) for k, v in snapshot.items()) or "(none)")

bus.listener.deinit()          # tp20 filters in software on one listener
ch = tp20.Channel(bus.can)
# Same principle at the TP 2.0 layer, but forever: the gauge's job is to
# be running when the ignition comes on, however long that takes.
_w = 0
while True:
    try:
        ch.connect()
        break
    except tp20.TP20Error as e:
        _w += 1
        pixel((255, 140, 0))
        screen.show("NO ECU", "tp2.0 not answering",
                    "ignition off?", "retry %d" % _w)
        if rd_port is not None and rd_port.in_waiting:
            rd_port.read(rd_port.in_waiting)
        time.sleep(3)
print("tp20: channel open, tx 0x%03X rx 0x%03X" % (ch.tx_id, ch.rx_id))




def safely(fn, what, default=None):
    """Interrogation is informational: never let it cost us the gauge."""
    try:
        v = fn()
        if not ch.connected:
            ch.connect()
        return v
    except tp20.TP20Error as e:
        print("tp20: %s skipped (%s)" % (what, e))
        try:
            ch.connect()
        except tp20.TP20Error:
            pass
        return default


ident = safely(lambda: tp20.ecu_ident(ch), "ident")
vin = safely(lambda: tp20.read_vin(ch), "vin")
dtcs = safely(lambda: tp20.read_dtcs(ch), "dtc read")

print("realdash: frames on %s port"
      % ("console" if RD_ON_CONSOLE else "data (second CDC)"))
print("ecu:  %s" % ident)
print("vin:  %s" % vin)
n_faults = 0
if dtcs is None:
    dtc_line = "faults: n/a"
    print("dtc:  could not read")
elif not dtcs:
    dtc_line = "faults: none"
    print("dtc:  no fault codes stored")
else:
    n_faults = len(dtcs)
    dtc_line = "faults: %d" % len(dtcs)
    for code, hi, lo, status in dtcs:
        print("dtc:  %s  (VAG %05d, raw %02X%02X, status %02X)"
              % (code, (hi << 8) | lo, hi, lo, status))

g = boost.Gauge(ch, initial=snapshot)
shiftlight.init()          # never raises; disabled if the strip is absent
shift_errs = 0             # 3 consecutive render failures turn it off for good
log = boost.open_log()
print("logging to %s" % ("/boost_log.csv" if log else "serial only"))

# Commands only work on the data channel. A command payload can contain the
# byte 0x03, and on the console channel that is Ctrl-C: it would kill the
# program mid-drive. Never read commands from the console.
cmd_reader = realdash.CommandReader(rd_port if not RD_ON_CONSOLE else None)
cmd_prev = {}
if RD_ON_CONSOLE:
    print("commands: DISABLED (frames are on the console port)")
    print("commands: replug the board so boot.py can create the data port")
else:
    print("commands: listening on the data port")


def read_faults():
    """Re-read stored codes and refresh the derived display values."""
    global dtcs, n_faults, dtc_line
    dtcs = safely(lambda: tp20.read_dtcs(ch), "dtc read")
    if dtcs is None:
        n_faults, dtc_line = 0, "faults: n/a"
    elif not dtcs:
        n_faults, dtc_line = 0, "faults: none"
    else:
        n_faults, dtc_line = len(dtcs), "faults: %d" % len(dtcs)
    for entry in (dtcs or ()):
        print("dtc:  %s  (VAG %05d, status %02X)"
              % (entry[0], (entry[1] << 8) | entry[2], entry[3]))
    return dtcs


def _fired(word, prev_word):
    """True when a command word represents a NEW press.

    RealDash's "Change Value" action CLAMPS at max rather than wrapping, so a
    button configured 0..1 latches at 1 forever and a strict 0->1 edge would
    fire exactly once per app restart. Treating "changed, and now non-zero"
    as the trigger works for both shapes: a 0/1 toggle, and a counter that
    increments 1,2,3... on every press. Zero is always idle, so the value can
    still be parked to disarm.
    """
    return word != prev_word and word != 0


def handle_commands():
    """Act on RealDash button presses, edge triggered."""
    for cid, words in cmd_reader.poll().items():
        if cid != realdash.FRAME_CMD:
            continue
        prev = cmd_prev.get(cid, (0, 0, 0, 0))
        cmd_prev[cid] = words

        if _fired(words[0], prev[0]):
            # Writing to the ECU. Refuse while moving: clearing also wipes
            # freeze frames and resets readiness monitors, and doing that
            # by accident at speed is not something to make easy.
            if g.speed > 0:
                print("clear codes: REFUSED, vehicle is moving (%.0f km/h)"
                      % g.speed)
            else:
                print("clear codes: requested from RealDash, clearing...")
                ok = safely(lambda: tp20.clear_dtcs(ch), "dtc clear", False)
                print("clear codes: %s" % ("OK" if ok else "FAILED"))
                read_faults()

        if _fired(words[1], prev[1]):
            g.peak_bar = 0.0
            print("peak hold: reset")

        if _fired(words[2], prev[2]):
            print("fault codes: refresh requested")
            read_faults()

        if _fired(words[3], prev[3]):
            # Remote firmware reload.
            #
            # Writing to CIRCUITPY from the head unit does NOT trigger
            # CircuitPython's auto-reload -- verified on 2026-08-23: realdash.py
            # on the drive contained warn_frame and the board kept running the
            # previous copy until a power cycle. So every deploy silently did
            # nothing until the ignition was cycled.
            #
            # supervisor.reload() restarts code.py in place, which re-imports
            # every module from the (already updated) filesystem. It does NOT
            # re-run boot.py -- USB layout changes still need a real reset.
            print("reload requested from RealDash")
            try:
                import supervisor
                supervisor.reload()
            except Exception as e:
                print("reload failed: %s" % e)

# Startup status page: what it is connected to and whether the car is happy.
# Model number first -- it is the line worth photographing for parts lookups.
screen.show(
    "SCIROCCO",
    (ident or "MED17.5")[:34],
    "vin %s" % (vin or "unavailable"),
    "link OK  0x%03X/0x%03X" % (ch.tx_id, ch.rx_id),
    dtc_line,
    "baro %.0f mbar" % g.baro,
    force=True,
)
pixel((0, 255, 0))

# Gear, steering angle and the DCC chassis values live in OTHER modules,
# and a second TP 2.0 channel cannot be held open alongside this one -- it
# drops the engine stream to 4% of baseline (measured 2026-08-23). So they
# are visited one at a time, briefly, a few times a minute. See auxmods.py.
# KILL SWITCH (2026-08-23 23:00): with the ENGINE RUNNING, the board hard-
# resets during the first aux visit -- fresh-boot sessions stream ~16 Hz
# for exactly VISIT_EVERY_S seconds and die (sessions 2245/2250, err=1,
# visits=0). Engine-off soak passed 66 visits, so the failure needs the
# running engine's bus. Disabled until diagnosed with reset_reason
# instrumentation; a gauge that drops rpm every 20 s is worse than one
# with no gear display.
AUX_ENABLED = False

aux = None
if AUX_ENABLED and auxmods is not None:
    try:
        aux = auxmods.AuxReader(bus.can, ch)
    except Exception as e:
        _aux_err = "%s: %s" % (type(e).__name__, e)
gc.collect()
# One '55' text frame at startup, whatever happened: it lands in the tee's
# raw.bin, which is the only readable channel now that the console is off.
if rd_port is not None:
    try:
        try:
            import microcontroller
            _rr = str(microcontroller.cpu.reset_reason).split(".")[-1]
        except Exception:
            _rr = "?"
        rd_port.write(realdash.text_frame(
            0xC91,
            "aux=%s mem_free=%d reset=%s"
            % ("ok" if aux is not None else "OFF(%s)" % _aux_err,
               gc.mem_free(), _rr)))
    except Exception:
        pass

t_screen = time.monotonic() + INFO_HOLD - SCREEN_EVERY
t_rate = time.monotonic()
n_rate = 0
hz = 0.0

while True:
    if not g.sample():
        # Even with no ECU, the command port MUST be drained: RealDash
        # streams button frames ~2/s, and a full CDC OUT buffer stalls the
        # BRIDGE's serial writes -- which drops the tee's connection and
        # looks like "the telnet server is flaky". It was this, all along.
        try:
            handle_commands()
        except Exception:
            pass
        time.sleep(0.2)
        continue

    if rd_port is not None:
        try:
            realdash.send_all(rd_port, g, faults=n_faults, hz=hz,
                              dtcs=dtcs, aux=aux)
        except Exception as e:
            if not QUIET:
                print("realdash: write failed (%s)" % e)
    # One aux visit at most, and only when due. It closes and reopens the
    # engine channel around itself, so it must never run mid-frame.
    try:
        if aux is not None and aux.due():
            got = aux.visit()
            if got is not None and not QUIET:
                print("aux: visited %02X (gear=%s h=%.3f/%.3f/%.3f)"
                      % (got, aux.gear or "?",
                         aux.height[0], aux.height[1], aux.height[2]))
    except Exception as e:
        if not QUIET:
            print("aux: visit failed (%s)" % e)

    try:
        handle_commands()
    except Exception as e:
        if not QUIET:
            print("commands: %s" % e)

    if shift_errs < 3:
        try:
            shiftlight.render(g, n_faults)
            shift_errs = 0
        except Exception as e:
            shift_errs += 1
            if not QUIET:
                print("shiftlight: render failed (%s)" % e)
            if shift_errs >= 3:
                shiftlight.off()   # never raises; leave no stale warning lit

    if not QUIET:
        print(g.human())
    if log:
        boost.log_row(log, g)
        if g.n % 50 == 0:
            log.flush()

    n_rate += 1
    now = time.monotonic()
    if now - t_rate >= 1.0:
        hz = n_rate / (now - t_rate)
        n_rate = 0
        t_rate = now

    # Green off boost, amber on boost, red near the stock ceiling.
    b = g.bar
    pixel((0, 255, 0) if b < 0.15 else
          (255, 140, 0) if b < 0.9 else (255, 0, 0))

    if now - t_screen >= SCREEN_EVERY:
        if screen.show("BOOST",
                       "now %+.2f bar peak %+.2f" % (b, g.peak_bar),
                       "%.0f rpm  load %.0f%%  %.0fkm/h"
                       % (g.rpm, g.load, g.speed),
                       "cool %.0fC oil %.0fC iat %.0fC"
                       % (g.coolant, g.oil, g.iat),
                       "%.1fV  %.0fg/s  n75 %.0f%%"
                       % (g.volts, g.maf, g.n75),
                       "%s   %.1f Hz  rc %d" % (dtc_line, hz, g.errors),
                       "vin %s" % (vin or "n/a")):
            t_screen = now
