"""
eobdmix.py -- can generic EOBD share the bus with an open TP 2.0 channel?

Adding EOBD PID reads to the gauge loop dropped it from 16 Hz to 0.7 Hz and
pushed channel reconnects from 1 to 5, so something about interleaving the
two protocols is expensive. Find out which part: measure block reads before
EOBD, then the EOBD reads themselves, then block reads after, and watch
whether the channel survives.
"""

import time
import uds
import tp20

PIDS = (0x23, 0x3C, 0x49, 0x1F, 0x31, 0x44, 0x14)

bus = uds.Bus()
bus.listener.deinit()
ch = tp20.Channel(bus.can)
ch.connect()
print("channel open, tx 0x%03X rx 0x%03X" % (ch.tx_id, ch.rx_id))

reconnects = [0]


def blocks(n, label):
    t0 = time.monotonic()
    ok = 0
    for _ in range(n):
        try:
            if not ch.connected:
                ch.connect()
                reconnects[0] += 1
            if tp20.read_block(ch, 115, timeout=0.4):
                ok += 1
        except tp20.TP20Error:
            pass
    dt = time.monotonic() - t0
    print("%-22s %2d/%2d ok   %6.1f ms each   %5.1f Hz"
          % (label, ok, n, dt * 1000 / n, n / dt))


blocks(20, "blocks before EOBD")

print("-" * 58)
t0 = time.monotonic()
for pid in PIDS:
    t1 = time.monotonic()
    try:
        d = ch.obd_read(pid, timeout=0.3)
    except tp20.TP20Error as e:
        d = None
        print("  PID %02X transport error (%s)" % (pid, e))
    dt = (time.monotonic() - t1) * 1000
    print("  PID %02X  %-10s %6.1f ms   channel %s"
          % (pid,
             ("".join("%02X" % x for x in d)) if d else "NO REPLY",
             dt,
             "up" if ch.connected else "DOWN"))
    if not ch.connected:
        try:
            ch.connect()
            reconnects[0] += 1
        except tp20.TP20Error:
            pass
print("EOBD total %.0f ms for %d pids" % ((time.monotonic() - t0) * 1000,
                                          len(PIDS)))
print("-" * 58)

blocks(20, "blocks after EOBD")
print("reconnects during test: %d" % reconnects[0])
print("=" * 58)
ch.disconnect()
while True:
    time.sleep(1)
