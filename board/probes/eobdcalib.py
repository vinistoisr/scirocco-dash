"""
eobdcalib.py -- pin down the unknown measuring-block formulas by reading the
SAME quantity two ways at the same moment.

THE PROBLEM
    Phase 1 of enginescan found strong candidates -- block 106 carries two
    near-identical 16-bit values under formula 83, which is what a
    specified/actual pair under closed-loop control looks like, and VCDS
    users read rail pressure in exactly this group. But "looks like" is not
    identification, and the SCALING is pure guesswork: 0x0FA0 = 4000 counts
    is 40.00 bar at 0.01 bar/count and 50.0 bar at 0.0125. Nothing in the
    raw bytes says which.

    Formulas 83 (0x53), 53 (0x35) and 31 (0x1F) are not in the public
    jazdw/vag-blocks table -- it implements the common ids and falls through
    to raw for the rest. So the table cannot answer this either.

THE METHOD
    Generic EOBD reports in DEFINED SI units. PID 0x23 is fuel rail gauge
    pressure in 10 kPa steps, 0x3C is catalyst temperature in 0.1 C steps
    with a -40 offset, 0x49 is pedal position as a percentage. Read those at
    warm idle, read the candidate blocks at the same warm idle, and the
    ratio between them IS the formula's scale factor. This is exactly how
    formula 27 was confirmed: block 003 field 4 read -1.5 deg and PID 0x0E
    independently reported -1.5 at the same moment.

    Crucially the EOBD read happens with the ENGINE RUNNING. The gauge's own
    snapshot (boost.EOBD_SNAPSHOT) is taken when the board boots, which is
    when the head unit powers its USB -- ignition on, engine not yet
    started. That is why the held values are nonsense: rail 2.0 bar is
    residual pressure with the pump stopped, cat 22.4 C is ambient, and
    lambda 1.999 is PID 0x44 answering 0xFFFF for "unsupported". Reading the
    same PIDs while the engine runs shows what they SHOULD have said, which
    both calibrates the blocks and demonstrates the root cause.

    The supported-PID bitmaps (0x00/0x20/0x40/0x60) are read first, so
    whether a live lambda PID exists at all is answered by the ECU rather
    than guessed.

ORDER MATTERS: EOBD first, then TP 2.0, and never interleaved. This ECU
tears the TP 2.0 channel down on every mode 01 request.

*** READ ONLY. *** Mode 01 and KWP 0x21 only.
"""

import time
import usb_cdc

import uds
import tp20
import realdash

_out = usb_cdc.data
if _out is not None:
    try:
        _out.write_timeout = 0.5
    except Exception:
        pass
_builtin_print = print


def print(*args):
    line = " ".join(str(a) for a in args) + "\r\n"
    if _out is None:
        _builtin_print(line, end="")
        return
    try:
        _out.write(line.encode("utf-8"))
    except Exception:
        pass


_cmd = realdash.CommandReader(_out)
_prev3 = 0


def pump():
    """Drain RealDash's writes; reload if word 3 fired. See enginescan.py."""
    global _prev3
    try:
        frames = _cmd.poll()
    except Exception:
        return
    for cid, words in frames.items():
        if cid != realdash.FRAME_CMD:
            continue
        w = words[3]
        fired = w != _prev3 and w != 0
        _prev3 = w
        if fired:
            print("reload requested -- handing back to code.py on disk")
            try:
                import supervisor
                supervisor.reload()
            except Exception as e:
                print("reload failed: %s" % e)


STARTUP_DELAY_S = 40.0

# The blocks phase 1 shortlisted, plus ones the gauge already polls, so the
# pairing can be sanity-checked against channels of known identity.
WATCH_BLOCKS = (106, 103, 115, 43, 46, 112, 121, 91, 92, 96, 109, 3, 20, 32)

# PIDs worth asking for, with the ISO 15031 decode. The RAW bytes are
# printed too: a PID answering 0xFFFF is "unsupported", not a reading, and
# that distinction is the whole reason lambda_commanded shows 1.999.
PIDS = (
    (0x23, "rail_gauge_10kPa", lambda d: ((d[0] << 8) | d[1]) * 10.0),
    (0x22, "rail_rel_manifold", lambda d: ((d[0] << 8) | d[1]) * 0.079),
    (0x3C, "cat_temp_b1s1_C", lambda d: ((d[0] << 8) | d[1]) / 10.0 - 40.0),
    (0x3D, "cat_temp_b2s1_C", lambda d: ((d[0] << 8) | d[1]) / 10.0 - 40.0),
    (0x3E, "cat_temp_b1s2_C", lambda d: ((d[0] << 8) | d[1]) / 10.0 - 40.0),
    (0x49, "pedal_D_pct", lambda d: d[0] * 100.0 / 255.0),
    (0x4A, "pedal_E_pct", lambda d: d[0] * 100.0 / 255.0),
    (0x11, "throttle_pct", lambda d: d[0] * 100.0 / 255.0),
    (0x44, "lambda_cmd", lambda d: ((d[0] << 8) | d[1]) * 2.0 / 65536.0),
    (0x34, "o2s1_lambda", lambda d: ((d[0] << 8) | d[1]) * 2.0 / 65536.0),
    (0x24, "o2s1_eq_ratio", lambda d: ((d[0] << 8) | d[1]) * 2.0 / 65536.0),
    (0x0E, "timing_deg", lambda d: d[0] / 2.0 - 64.0),
    (0x1F, "run_time_s", lambda d: float((d[0] << 8) | d[1])),
    (0x0C, "rpm", lambda d: ((d[0] << 8) | d[1]) / 4.0),
    (0x05, "coolant_C", lambda d: float(d[0] - 40)),
    (0x04, "calc_load_pct", lambda d: d[0] * 100.0 / 255.0),
)


def hexs(b):
    return "".join("%02X" % x for x in b)


def pid_raw(bus, pid):
    r = bus.request(bytes([0x01, pid]), timeout=0.5)
    if r is None or len(r) < 3 or r[0] != 0x41 or r[1] != pid:
        return None
    return r[2:]


for _left in range(int(STARTUP_DELAY_S), 0, -5):
    print("  starting in %d s (attach the capture now)" % _left)
    time.sleep(5)

print(">>>EOBDCALIB-BEGIN<<<")
bus = uds.Bus()

# --- which PIDs does this ECU actually support --------------------------
print("")
print("=" * 68)
print("SUPPORTED PIDS -- asked, not assumed")
print("=" * 68)
supported = set()
for base in (0x00, 0x20, 0x40, 0x60):
    d = pid_raw(bus, base)
    if not d or len(d) < 4:
        print("  bitmap %02X: no answer" % base)
        continue
    mask = (d[0] << 24) | (d[1] << 16) | (d[2] << 8) | d[3]
    print("  bitmap %02X: %08X" % (base, mask))
    for i in range(32):
        if mask & (1 << (31 - i)):
            supported.add(base + i + 1)
print("  supported: %s" % " ".join("%02X" % p for p in sorted(supported)))
for _pid, _name, _fn in PIDS:
    if _pid not in supported:
        print("  NOT SUPPORTED: %02X %s" % (_pid, _name))

# --- read them, engine RUNNING ------------------------------------------
print("")
print("=" * 68)
print("EOBD AT WARM IDLE, ENGINE RUNNING")
print("(compare with the gauge's boot snapshot, taken engine-OFF)")
print("=" * 68)
for pid, name, fn in PIDS:
    d = pid_raw(bus, pid)
    if d is None:
        print("  %02X %-18s no answer" % (pid, name))
        continue
    try:
        v = fn(d)
    except Exception:
        v = None
    flag = ""
    if len(d) >= 2 and d[0] == 0xFF and d[1] == 0xFF:
        flag = "   <-- 0xFFFF = UNSUPPORTED, not a reading"
    print("  %02X %-18s raw %-10s %s%s"
          % (pid, name, hexs(d), ("%.3f" % v) if v is not None else "?", flag))

# --- now TP 2.0, same idle ----------------------------------------------
print("")
print("=" * 68)
print("TP 2.0 BLOCKS AT THE SAME IDLE -- pair these with the PIDs above")
print("=" * 68)
bus.listener.deinit()
ch = tp20.Channel(bus.can)
ok = False
for _a in range(10):
    try:
        ch.connect()
        ok = True
        break
    except tp20.TP20Error as e:
        print("  tp20 retry %d: %s" % (_a + 1, e))
        time.sleep(2)

if not ok:
    print("  NO TP 2.0 CHANNEL")
    print(">>>EOBDCALIB-END<<<")
    while True:
        pump()
        time.sleep(0.2)

t0 = time.monotonic()
sweep = 0
while True:
    sweep += 1
    pump()
    print("S %d T %.2f" % (sweep, time.monotonic() - t0))
    for n in WATCH_BLOCKS:
        pump()
        if not ch.connected:
            try:
                ch.connect(timeout=0.6, tries=1)
            except tp20.TP20Error:
                continue
        try:
            d = tp20.read_block(ch, n, timeout=0.3)
        except tp20.TP20Error:
            d = None
        print("B %d %s" % (n, hexs(d) if d else "--"))
