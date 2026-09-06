"""
switchtest.py -- is CHANNEL SWITCHING cheaper than holding two channels?

twochan.py established that holding the engine channel and the DSG channel
open at the same time collapses the engine stream from 19.9 Hz to 0.9 Hz --
4% of baseline, recovering the moment the second channel closes. So the
"just open another channel" design is dead.

This measures the alternative: keep exactly ONE channel open at a time and
pay a switch cost to visit an auxiliary module.

    engine ... engine | close | open aux | read | close | reopen engine ...

What matters is the cost of one visit. If a round trip to an aux module
costs ~200 ms, visiting one every 2 s costs 10% of the engine stream and
chassis logging is practical. If it costs 2 s, only slow-changing values
like gear are worth having and track-rate suspension data is not.

*** READ ONLY. *** KWP 0x21 only.
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


ENGINE_ADDR = 0x01
ENGINE_BLOCK = 115
# one visit reads everything we want from that module, so the switch cost is
# amortised over several blocks rather than paid per value
AUX = (
    (0x0C, (9, 11)),      # ride height x3, damper x4
    (0x09, (7,)),         # steering angle
    (0x02, (3,)),         # gear
)
BASELINE_S = 10.0
CYCLES = 3


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


d = make_display()
bus = uds.Bus()
bus.listener.deinit()
shared = bus.can.listen(timeout=0.02)

print(">>>SWITCH-BEGIN<<<")
print("=" * 64)
print("Is switching channels cheaper than holding two open?")
print("=" * 64)

eng = tp20.Channel(bus.can, dest=ENGINE_ADDR, listener=shared)
eng.connect()


def hammer(seconds):
    n = e = 0
    t0 = time.monotonic()
    while time.monotonic() - t0 < seconds:
        try:
            if not eng.connected:
                eng.connect()
            if tp20.read_block(eng, ENGINE_BLOCK, timeout=0.4):
                n += 1
            else:
                e += 1
        except tp20.TP20Error:
            e += 1
    return n / (time.monotonic() - t0), n, e


base, n, e = hammer(BASELINE_S)
print("baseline engine-only: %.1f Hz (%d ok, %d err)" % (base, n, e))

for addr, blocks in AUX:
    costs = []
    for c in range(CYCLES):
        t0 = time.monotonic()
        try:
            eng.disconnect()
        except Exception:
            pass
        t_close = time.monotonic() - t0

        t1 = time.monotonic()
        aux = tp20.Channel(bus.can, dest=addr, listener=shared)
        got = {}
        try:
            aux.connect(timeout=0.8, tries=1, attempts=2)
            t_open = time.monotonic() - t1
            t2 = time.monotonic()
            for b in blocks:
                try:
                    dd = tp20.read_block(aux, b, timeout=0.4)
                    if dd:
                        got[b] = "".join("%02X" % x for x in dd[:12])
                except tp20.TP20Error:
                    pass
            t_read = time.monotonic() - t2
        except tp20.TP20Error as ex:
            t_open = time.monotonic() - t1
            t_read = 0.0
            print("  %02X: connect failed: %s" % (addr, ex))
        try:
            aux.disconnect()
        except Exception:
            pass

        t3 = time.monotonic()
        try:
            eng.connect()
        except tp20.TP20Error:
            pass
        t_back = time.monotonic() - t3
        total = time.monotonic() - t0
        costs.append(total)
        if c == 0:
            for b in sorted(got):
                print("  %02X/%03d = %s" % (addr, b, got[b]))
    avg = sum(costs) / len(costs)
    print("  %02X visit cost: %.0f ms avg  (%s)"
          % (addr, avg * 1000,
             " ".join("%.0f" % (x * 1000) for x in costs)))
    # what that does to the engine stream at a few visit rates
    for per in (1.0, 2.0, 5.0):
        loss = 100.0 * avg / per
        print("       one visit every %.0f s -> %.0f%% of engine time lost "
              "(~%.1f Hz)" % (per, loss, base * (1 - min(loss, 99) / 100.0)))

after, n, e = hammer(BASELINE_S)
print("engine after all switching: %.1f Hz (%d ok, %d err)" % (after, n, e))
print(">>>SWITCH-END<<<")

while True:
    time.sleep(1)
