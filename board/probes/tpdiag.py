"""
tpdiag.py -- ground-truth TP 2.0 diagnostic. Read only.

Answers the two questions the sweep could not, by capturing the WHOLE bus with
no ID filter instead of trusting an assumed response ID:

  1. Is this port on the live powertrain CAN (flooded with periodic broadcast
     while the engine runs) or a gated diagnostic CAN (quiet until addressed)?
     -> a promiscuous capture during a rev settles it.

  2. When we send a TP 2.0 channel setup, does ANYTHING answer, on ANY id?
     -> send each setup variant, then capture every frame for 0.8 s and print
     it, so a reply on an unexpected id is seen rather than filtered out.

canio.Match(0, mask=0) matches every frame (mask 0 = all bits don't care),
which is the reliable way to go promiscuous regardless of build quirks.
"""

import time
import canio
import uds

ALL = None   # built lazily; canio.Match(0, mask=0)


def _hx(d):
    return "".join("%02X" % x for x in d) if d else ""


def _cap(can, seconds, label, show=60):
    lis = can.listen(timeout=0.02)
    counts = {}
    shown = 0
    t_end = time.monotonic() + seconds
    while time.monotonic() < t_end:
        m = lis.receive()
        if m is None:
            continue
        d = getattr(m, "data", None)
        counts[m.id] = counts.get(m.id, 0) + 1
        if shown < show:
            print("  %s 0x%03X [%d] %s"
                  % (label, m.id, 0 if d is None else len(d), _hx(d)))
            shown += 1
    lis.deinit()
    print("  %s: %d distinct ids, %d frames"
          % (label, len(counts), sum(counts.values())))
    for i in sorted(counts):
        print("    id 0x%03X  x%d" % (i, counts[i]))
    return counts


def _send(can, cid, data, ext=False):
    can.send(canio.Message(id=cid, data=bytes(data), extended=ext))


def _setup_trial(can, name, cid, data, ext=False, window=0.8):
    print("--- setup %s: tx 0x%03X%s data %s ---"
          % (name, cid, " ext" if ext else "", _hx(data)))
    lis = can.listen(timeout=0.02)
    _send(can, cid, data, ext)
    t_end = time.monotonic() + window
    got = 0
    while time.monotonic() < t_end:
        m = lis.receive()
        if m is None:
            continue
        d = getattr(m, "data", None)
        print("    <- 0x%03X %s" % (m.id, _hx(d)))
        got += 1
    lis.deinit()
    if not got:
        print("    (no reply)")
    return got


def run():
    print("=" * 50)
    print("TP2.0 DIAG  --  read only")
    print("=" * 50)

    bus = uds.Bus()            # enables transceiver + boost, 500 kbit/s
    can = bus.can

    # Sanity: confirm TX + RX still work by asking mode 01 PID 0C (rpm).
    # Do this BEFORE dropping the 0x7E8 filter, since request() needs it.
    r = bus.request(bytes([0x01, 0x0C]), timeout=0.5)
    print("sanity mode01 rpm ->", _hx(r) if r else "no reply")
    bus.listener.deinit()      # now go promiscuous

    print("\n--- 4s promiscuous capture: REV THE ENGINE NOW ---")
    free = _cap(can, 4.0, "free")
    if not free:
        print("free-run bus is SILENT -> gated diagnostic CAN, gateway must route TP2.0")
    else:
        print("free-run bus has traffic -> live powertrain CAN")

    print("\n--- channel setup trials (watching ALL ids) ---")
    trials = (
        # name,                 tx id, data,                                    ext
        ("A 200 dest01 rxdef",  0x200, (0x01, 0xC0, 0x00, 0x10, 0x00, 0x03, 0x01), False),
        ("B 201 dest00 rxdef",  0x201, (0x00, 0xC0, 0x00, 0x10, 0x00, 0x03, 0x01), False),
        ("C 200 dest01 rx300",  0x200, (0x01, 0xC0, 0x00, 0x03, 0x00, 0x03, 0x01), False),
        ("D 200 dest01 rx740",  0x200, (0x01, 0xC0, 0x40, 0x07, 0x00, 0x03, 0x01), False),
        ("E 200 dest01 app00",  0x200, (0x01, 0xC0, 0x00, 0x10, 0x00, 0x03, 0x00), False),
    )
    for name, cid, data, ext in trials:
        _setup_trial(can, name, cid, data, ext)

    print("\n--- dest sweep 0x00-0x3F to 0x200, ALL ids, 60ms each ---")
    lis = can.listen(timeout=0.02)
    any_hit = False
    for dest in range(0x00, 0x40):
        _send(can, 0x200, (dest, 0xC0, 0x00, 0x10, 0x00, 0x03, 0x01))
        t_end = time.monotonic() + 0.06
        while time.monotonic() < t_end:
            m = lis.receive()
            if m is None:
                continue
            d = getattr(m, "data", None)
            print("    dest %02X -> 0x%03X %s" % (dest, m.id, _hx(d)))
            any_hit = True
    lis.deinit()
    if not any_hit:
        print("    (no reply to any dest)")

    print("\n--- same dest sweep, but tx to 0x200+dest, ALL ids ---")
    lis = can.listen(timeout=0.02)
    any_hit = False
    for dest in range(0x00, 0x40):
        _send(can, 0x200 + dest, (0x00, 0xC0, 0x00, 0x10, 0x00, 0x03, 0x01))
        t_end = time.monotonic() + 0.06
        while time.monotonic() < t_end:
            m = lis.receive()
            if m is None:
                continue
            d = getattr(m, "data", None)
            print("    dest %02X (tx 0x%03X) -> 0x%03X %s"
                  % (dest, 0x200 + dest, m.id, _hx(d)))
            any_hit = True
    lis.deinit()
    if not any_hit:
        print("    (no reply to any dest)")

    print("=" * 50)
    print("DIAG DONE")
    print("=" * 50)
    while True:
        time.sleep(1)
