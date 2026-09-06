"""
ratetest.py -- where is the sample loop spending its time?

The gauge measured 1.9 Hz while the earlier seven-block scan managed a block
every ~50 ms, which should give ~20 Hz on a single block. Something in the
gauge loop and not in the scan is costing ~450 ms per cycle. The two
candidates are the TP 2.0 read itself and the USB CDC writes carrying the
RealDash frames, so time them separately.

Results print at the end so the measurement is not itself distorted by
console output.
"""

import time
import board
import digitalio
import displayio
import usb_cdc
import uds
import tp20
import realdash

N = 60
BLOCK = 115

# The gauge differs from this test in exactly one way: it has an e-ink
# display initialised. displayio installs a background task that runs
# between bytecodes, so prove whether that is what costs the throughput.
WITH_DISPLAY = True

if WITH_DISPLAY:
    try:
        from fourwire import FourWire
    except ImportError:
        FourWire = displayio.FourWire
    for pin in (board.D5, board.D6):
        try:
            cs = digitalio.DigitalInOut(pin)
            cs.switch_to_output(True)
        except Exception:
            pass
    displayio.release_displays()
    _bus = FourWire(board.SPI(), command=board.D10, chip_select=board.D9,
                    reset=None, baudrate=1000000)
    time.sleep(1)
    import adafruit_ssd1680
    _d = adafruit_ssd1680.SSD1680(
        _bus, width=250, height=122, rotation=270, busy_pin=None,
        highlight_color=0xFF0000, colstart=8, seconds_per_frame=25)
    print("display initialised: %dx%d" % (_d.width, _d.height))

bus = uds.Bus()
bus.listener.deinit()
ch = tp20.Channel(bus.can)
ch.connect()

port = usb_cdc.data if usb_cdc.data is not None else usb_cdc.console


def timed(fn, n=N):
    t0 = time.monotonic()
    ok = 0
    worst = 0.0
    for _ in range(n):
        t1 = time.monotonic()
        if fn():
            ok += 1
        dt = time.monotonic() - t1
        if dt > worst:
            worst = dt
    total = time.monotonic() - t0
    return total / n, worst, ok


def read_only():
    try:
        return tp20.read_block(ch, BLOCK, timeout=0.4) is not None
    except tp20.TP20Error:
        try:
            ch.connect()
        except tp20.TP20Error:
            pass
        return False


blob = realdash.frame(0xC80, 1, 2, 3, 4) * 3


def write_only():
    port.write(blob)
    return True


def read_and_write():
    r = read_only()
    port.write(blob)
    return r


a_avg, a_worst, a_ok = timed(read_only)
b_avg, b_worst, b_ok = timed(write_only)
c_avg, c_worst, c_ok = timed(read_and_write)

# Only now is it safe to talk on the console.
time.sleep(0.5)
print("")
print("=" * 58)
print("RATE TEST  (%d iterations each, block %d)" % (N, BLOCK))
print("display: %s" % ("initialised" if WITH_DISPLAY else "not created"))
print("port: %s" % ("data" if usb_cdc.data is not None else "console"))
print("=" * 58)
print("tp20 read only     avg %6.1f ms   worst %6.1f ms   ok %d/%d"
      % (a_avg * 1000, a_worst * 1000, a_ok, N))
print("serial write only  avg %6.1f ms   worst %6.1f ms"
      % (b_avg * 1000, b_worst * 1000))
print("read + write       avg %6.1f ms   worst %6.1f ms   ok %d/%d"
      % (c_avg * 1000, c_worst * 1000, c_ok, N))
print("")
print("implied rates: read %.1f Hz, combined %.1f Hz"
      % (1.0 / a_avg if a_avg else 0, 1.0 / c_avg if c_avg else 0))
print("=" * 58)

ch.disconnect()
while True:
    time.sleep(1)
