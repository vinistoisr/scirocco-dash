"""
udsprobe.py -- does this ECU speak UDS, and can UDS coexist with TP 2.0?

WHY
    A VCDS screenshot showed "Advanced Measuring Values" with IDE numbers --
    IDE00558 "Voltage of oxygen sensor bank 1 sensor 1 (broadband)",
    IDE00559 "Current of ... (broadband)", IDE01912 "Oxygen sensor bank 1
    sensor 1: specified value". Broadband means the WIDEBAND sensor, and a
    specified value is lambda. That is exactly what is missing.

    But those are UDS DataIdentifiers read with service 0x22, not KWP
    measuring blocks read with 0x21. They are a different protocol on a
    different address, and Ross-Tech only shows that screen for UDS
    controllers. This car answered 128 KWP measuring blocks, which is what a
    TP 2.0 / KWP2000 ECU does and what a pure UDS ECU does NOT have -- so
    the screenshot is quite possibly a newer car. Worth ten seconds to find
    out rather than assuming either way.

TWO QUESTIONS, IN ORDER
    1. Does service 0x22 answer at all? uds.Bus.probe() asks for DID F190
       (VIN); a 0x62 reply proves 0x22 works, an NRC proves the ECU
       understood and refused, and silence proves nothing is there.
    2. If it does -- and this is the one that decides whether it is USABLE
       -- does a UDS read cost the TP 2.0 channel? Generic EOBD mode 01 does:
       measured, every mode 01 request makes this ECU tear the channel down
       with an A8, dropping the gauge from 16 Hz to 0.7. The owner's
       constraint is explicit: nothing that slows the TP 2.0 stream. So the
       test opens TP 2.0, times a block read, issues ONE UDS read, and times
       the same block read again. If the second read is slow or the channel
       is gone, UDS is unusable here no matter what data it holds.

Also tries the F4xx range: UDS mirrors OBD PID nn at DID F4nn, so F444 is
PID 0x44 (commanded lambda). If only that range answers, UDS buys nothing
over EOBD -- same data, same teardown.

*** READ ONLY. *** Services 0x22 and 0x21 only.
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


for _left in range(40, 0, -5):
    print("  starting in %d s (attach the capture now)" % _left)
    time.sleep(5)

print(">>>UDSPROBE-BEGIN<<<")
bus = uds.Bus()

print("")
print("=" * 68)
print("Q1 -- does service 0x22 answer on the 11-bit pair 7E0/7E8?")
print("=" * 68)
p = bus.probe()
print("  obd2(mode 01) : %s" % p["obd2"])
print("  uds (0x22)    : %s" % p["uds"])
print("  vin           : %s" % p["vin"])
print("  detail        : %s" % p["detail"])

print("")
print("  a few DIDs, raw:")
for did in (0xF190, 0xF19E, 0xF186, 0xF444, 0xF40C, 0xF405, 0xF443,
            0x2000, 0x2001, 0x1000, 0x0101):
    pump()
    st, val = bus.read_did(did, timeout=0.4)
    if st == "ok":
        show = "".join("%02X" % b for b in val[:12])
        print("    %04X ok   %s" % (did, show))
    elif st == "nrc":
        print("    %04X NRC  %02X %s" % (did, val, uds.NRC.get(val, "?")))
    else:
        print("    %04X --   no reply" % did)

print("")
print("=" * 68)
print("Q2 -- if UDS works, does using it COST the TP 2.0 channel?")
print("(this is what decides usability -- EOBD mode 01 costs it entirely)")
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
    print("  NO TP 2.0 CHANNEL -- cannot run the coexistence test")
else:
    def timed_block(n):
        t = time.monotonic()
        try:
            d = tp20.read_block(ch, n, timeout=0.4)
        except tp20.TP20Error as e:
            return None, (time.monotonic() - t), str(e)
        return d, (time.monotonic() - t), ""

    d, dt, err = timed_block(115)
    print("  baseline block 115 : %.1f ms  %s" % (dt * 1000, "ok" if d else err))
    base = []
    for _ in range(10):
        pump()
        _d, _dt, _ = timed_block(115)
        base.append(_dt)
    print("  10 more reads      : mean %.1f ms" % (sum(base) / len(base) * 1000))

    if p["uds"]:
        print("  ...issuing ONE UDS read (F190) now...")
        # tp20 owns the all-matches listener; the Bus needs its own view of
        # 7E8 back for this one request, then it must give it up again.
        bus.listener = bus.can.listen(
            matches=[uds.canio.Match(bus.resp_id)], timeout=0.02)
        st, val = bus.read_did(0xF190, timeout=0.6)
        bus.listener.deinit()
        print("  uds read: %s" % st)
        after = []
        for _ in range(10):
            pump()
            _d, _dt, _e = timed_block(115)
            after.append(_dt)
            if _e:
                print("    block read FAILED after UDS: %s" % _e)
        print("  10 reads after UDS : mean %.1f ms" % (sum(after) / len(after) * 1000))
        print("  channel connected  : %s" % ch.connected)
        print("")
        print("  VERDICT: %s" % (
            "UDS is FREE here -- block timing unchanged"
            if sum(after) / len(after) < sum(base) / len(base) * 1.5
            else "UDS COSTS the channel -- same trap as EOBD mode 01"))
    else:
        print("  service 0x22 did not answer, so there is nothing to")
        print("  coexist with. The wideband values in that VCDS screenshot")
        print("  belong to a UDS controller; this one is KWP/TP 2.0.")

print(">>>UDSPROBE-END<<<")
while True:
    pump()
    time.sleep(0.2)
