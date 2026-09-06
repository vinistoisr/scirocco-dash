"""
enginescan.py -- find LIVE equivalents for the channels that are stuck on
the EOBD startup snapshot.

THE PROBLEM THIS SOLVES
    lambda_commanded, fuel_rail_pressure, catalyst_temp and pedal_position
    are read once, over generic OBD mode 01, before the TP 2.0 channel
    opens -- and never again. They cannot be polled live, because this ECU
    tears the TP 2.0 channel down on every mode 01 request (measured: the
    gauge drops from 16 Hz to 0.7 Hz). See boost.EOBD_SNAPSHOT.

    Worse than "stale": the snapshot is taken when the BOARD boots, which
    is when the deck powers its USB -- ignition on, engine NOT running. So
    the held values are engine-off values. Observed live 2026-08-24 at warm
    idle: rail 2.0 bar (engine-off residual, not the ~50 bar a GDI rail
    runs), cat 22.4 C (ambient), lambda_cmd 1.999 (PID 0x44 returning
    0xFFFF = unsupported), run_time 0 (engine not running).

    So the fix is to find the same quantities in TP 2.0 measuring blocks,
    which cost nothing extra -- the gauge is already in a TP 2.0 session.

WHY TWO PHASES
    A block scan at idle tells you which blocks EXIST and what they read
    standing still. It cannot tell you what they MEAN: at warm idle a dozen
    fields sit at plausible-looking constants, and picking one because the
    number looks right is exactly how frame 2e 14 got labelled "steering
    angle" and was wrong.

    Identity comes from correlation. Phase 2 re-sweeps every responding
    block a few times a second while the engine is revved, so each field
    gets a time series that can be correlated against rpm offline. A field
    that tracks rpm through four revs and back is identified; a field that
    happens to read 1.00 is not.

    Phase 2 prints RAW HEX, not decoded values. The decode formula is part
    of what is being determined -- decoding on the board would bake in a
    guess and throw away the evidence.

*** READ ONLY. *** Service 0x21 (readDataByLocalIdentifier) and 0x1A
(identification) only. Nothing here can write, code, adapt or actuate.

CAPTURE (never through the tee -- it discards non-gauge bytes; see
deck/probecap.py):
    tools/enginescan.sh
"""

import time
import usb_cdc

import uds
import tp20
import realdash

# ----------------------------------------------------------------- output
# boot.py disables the USB console so RealDash owns IF0, which means the
# built-in print() writes to a port nobody can open. Redirect to
# usb_cdc.data -- the stream the head unit's bridge app already reads.
# write_timeout matters: an unread CDC buffer blocks write() forever and
# the scan hangs part-way through.
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


# ----------------------------------------------------------------- config

ENGINE = 0x01

BLOCK_FIRST = 1
BLOCK_LAST = 255
READ_TIMEOUT = 0.30

# Phase 2 re-reads every block that answered in phase 1. On this ECU a
# block read costs ~50 ms, so ~40 responders is ~2 s per sweep -- fast
# enough that a 5-second rev shows up in two or three consecutive sweeps.
WATCH_SECONDS = 3600.0
BLOCK_MAIN = 115          # rpm lives in field 0; printed every sweep so
                          # every other field can be correlated against it

# The capture cannot attach until tee.py has let go of the bridge, and
# tee.py cannot be stopped until the board has already been told to reload
# -- the reload command travels THROUGH the tee (deck/sendcmd.py). So the
# probe is alive and printing for a few seconds before anything is
# listening. Without this delay the first ~20 blocks of phase 1 land in
# that gap and are simply lost, which is the same class of mistake that
# lost the 2026-08-23 shifter sweep. Wait it out instead.
STARTUP_DELAY_S = 40.0


# --------------------------------------------------------------- recall
# This probe REPLACES code.py, so the gauge cannot come back on its own and
# `sendcmd reload` has nobody to answer it -- gauge_main.py is the thing
# that normally handles that frame. Without this the only way back would be
# power-cycling the board, i.e. rebooting the head unit that powers it.
# So the probe answers the same reload command, on the same frame, with the
# same edge-trigger rule as gauge_main.handle_commands().
#
# Polling this also drains the bridge's writes. RealDash keeps sending
# 0xC90 with words (0,0,0,0) whether or not anyone is listening, and an
# undrained CDC buffer is what makes write() block forever.
_cmd = realdash.CommandReader(_out)
_prev3 = 0


def pump():
    """Drain RealDash's writes; reload if word 3 fired. Never blocks."""
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


def hexs(b):
    return "".join("%02X" % x for x in b)


def describe(data):
    out = []
    for i in range(0, len(data) - 2, 3):
        out.append(tp20._field(data[i], data[i + 1], data[i + 2]))
    return " | ".join(out)


def reconnect(ch):
    """Blocks that do not exist sometimes cost the channel. Re-open it
    rather than abandoning the rest of the sweep."""
    for _ in range(3):
        try:
            ch.connect(timeout=0.6, tries=1)
            return True
        except tp20.TP20Error:
            time.sleep(0.2)
    return False


def read(ch, n):
    if not ch.connected and not reconnect(ch):
        return None
    try:
        return tp20.read_block(ch, n, timeout=READ_TIMEOUT)
    except tp20.TP20Error:
        return None


# ----------------------------------------------------------------- run

# Countdown first, and print it, so a capture that attaches late can still
# tell "I was early" from "the board is dead".
for _left in range(int(STARTUP_DELAY_S), 0, -5):
    print("  starting phase 1 in %d s (attach the capture now)" % _left)
    time.sleep(5)

print(">>>ENGINESCAN-BEGIN<<<")

bus = uds.Bus()
bus.listener.deinit()          # tp20 filters in software on one listener
ch = tp20.Channel(bus.can)

opened = False
for attempt in range(20):
    try:
        ch.connect()
        opened = True
        break
    except tp20.TP20Error as e:
        print("  waiting for ECU (%d): %s" % (attempt + 1, e))
        time.sleep(3)

if not opened:
    print("  NO CHANNEL to engine -- ignition off?")
    print(">>>ENGINESCAN-END<<<")
    while True:
        pump()
        time.sleep(0.2)

print("tp20: channel open, tx 0x%03X rx 0x%03X" % (ch.tx_id, ch.rx_id))
try:
    print("ident: %s" % tp20.ecu_ident(ch))
except tp20.TP20Error:
    print("ident: (unavailable)")

# --- phase 1: what blocks exist, and what they read standing still -------
print("")
print("=" * 68)
print("PHASE 1 -- every measuring block on the engine, at idle")
print("=" * 68)

live = []
t0 = time.monotonic()
for n in range(BLOCK_FIRST, BLOCK_LAST + 1):
    pump()
    d = read(ch, n)
    if not d:
        continue
    live.append(n)
    print("  BLK %03d  %-24s  %s" % (n, hexs(d), describe(d)))

print("")
print("  -> %d blocks answered in %.0f s" % (len(live), time.monotonic() - t0))
print("  -> watching: %s" % ",".join(str(n) for n in live))

# --- phase 2: the same blocks over and over, while the engine works ------
print("")
print("=" * 68)
print("PHASE 2 -- re-sweeping. Rev the engine; press the pedal.")
print("Raw hex only, on purpose: the decode formula is what we are")
print("trying to determine, so nothing is decoded away here.")
print("=" * 68)
# A full sweep of ~128 blocks takes ~10 s, which is far slower than the
# things being hunted actually move -- a pull is over in five seconds. That
# does NOT ruin correlation, provided every sample carries its OWN context
# rather than the context of whenever the sweep happened to start. So the
# main block (rpm, load, boost) is re-read every MAIN_EVERY blocks and
# emitted as an M line; each following B line belongs to the most recent M.
# Cost is ~25% more reads for samples that are individually trustworthy.
MAIN_EVERY = 4

print("FORMAT: S <sweep> T <seconds>")
print("        M <seconds> <hex of block %d>   rpm/load/boost context"
      % BLOCK_MAIN)
print("        B <block> <hex>                 belongs to the M above it")

sweep = 0
t0 = time.monotonic()
while time.monotonic() - t0 < WATCH_SECONDS:
    sweep += 1
    pump()
    print("S %d T %.2f" % (sweep, time.monotonic() - t0))
    for j, n in enumerate(live):
        pump()
        if j % MAIN_EVERY == 0:
            main = read(ch, BLOCK_MAIN)
            print("M %.2f %s" % (time.monotonic() - t0,
                                 hexs(main) if main else "--"))
        if n == BLOCK_MAIN:
            continue
        d = read(ch, n)
        print("B %d %s" % (n, hexs(d) if d else "--"))

print(">>>ENGINESCAN-END<<<")
print("parked -- `sendcmd reload` returns the board to code.py")
while True:
    pump()
    time.sleep(0.2)
