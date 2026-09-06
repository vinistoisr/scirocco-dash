"""
discover2.py -- find spark advance and odometer, without guessing.

Two questions the engine-block scan could not answer:

  1. SPARK ADVANCE. No measuring block on this ECU uses formula 4, the
     BTDC/ATDC ignition formula, so timing is not sitting in an obvious
     field. Rather than pick a plausible-looking unknown formula and put a
     fabricated number on the dashboard, ask generic EOBD: PID 0x0E is
     "timing advance for cylinder 1", defined by ISO 15031 as A/2 - 64
     degrees. This car does answer EOBD on 0x7E0/0x7E8. So: enumerate the
     supported PID bitmasks and read everything useful they claim.

  2. ODOMETER. The engine ECU does not have it; distance travelled lives in
     the instrument cluster, a separate module at TP 2.0 address 0x17. So
     open a channel there and scan its measuring blocks.

Read only throughout.
"""

import time
import uds
import tp20

# ---------------------------------------------------------------- EOBD

PID_NAMES = {
    0x04: "calc load %", 0x05: "coolant C", 0x06: "STFT b1 %",
    0x07: "LTFT b1 %", 0x0B: "MAP kPa", 0x0C: "rpm", 0x0D: "speed km/h",
    0x0E: "TIMING ADVANCE deg", 0x0F: "intake air C", 0x10: "MAF g/s",
    0x11: "throttle %", 0x1F: "run time s", 0x21: "MIL distance km",
    0x2F: "fuel level %", 0x31: "dist since clear km", 0x33: "baro kPa",
    0x42: "module volts", 0x43: "abs load %", 0x44: "cmd equiv ratio",
    0x45: "rel throttle %", 0x46: "ambient C", 0x47: "abs throttle B %",
    0x4C: "cmd throttle %", 0x5C: "oil temp C", 0xA6: "ODOMETER km",
}


def decode_pid(pid, d):
    a = d[0] if len(d) > 0 else 0
    b = d[1] if len(d) > 1 else 0
    if pid == 0x0E:
        return a / 2.0 - 64.0
    if pid in (0x04, 0x11, 0x2F, 0x43, 0x45, 0x47, 0x4C):
        return a * 100.0 / 255.0
    if pid in (0x06, 0x07):
        return (a - 128) * 100.0 / 128.0
    if pid in (0x05, 0x0F, 0x46, 0x5C):
        return a - 40.0
    if pid in (0x0B, 0x33):
        return float(a)
    if pid == 0x0C:
        return ((a << 8) | b) / 4.0
    if pid == 0x0D:
        return float(a)
    if pid == 0x10:
        return ((a << 8) | b) / 100.0
    if pid in (0x1F, 0x21, 0x31):
        return float((a << 8) | b)
    if pid == 0x42:
        return ((a << 8) | b) / 1000.0
    if pid == 0xA6:
        return (((d[0] << 24) | (d[1] << 16) | (d[2] << 8) | d[3]) / 10.0
                if len(d) >= 4 else None)
    return None


def eobd_scan(bus):
    print("=" * 60)
    print("PHASE 1 -- generic EOBD on 0x7E0")
    print("=" * 60)
    supported = []
    for base in (0x00, 0x20, 0x40, 0x60, 0x80, 0xA0):
        r = bus.request(bytes([0x01, base]), timeout=0.6)
        if r is None or len(r) < 6 or r[0] != 0x41:
            print("support block %02X: no reply" % base)
            break
        bits = (r[2] << 24) | (r[3] << 16) | (r[4] << 8) | r[5]
        for i in range(1, 33):
            if bits & (1 << (32 - i)):
                supported.append(base + i)
        if not (bits & 0x01):
            break
    print("supported PIDs (%d): %s"
          % (len(supported), " ".join("%02X" % p for p in supported)))
    print("-" * 60)
    for pid in supported:
        r = bus.request(bytes([0x01, pid]), timeout=0.5)
        if r is None or len(r) < 2 or r[0] != 0x41:
            continue
        d = r[2:]
        v = decode_pid(pid, d)
        name = PID_NAMES.get(pid, "")
        raw = "".join("%02X" % x for x in d)
        if v is None:
            print("  PID %02X  %-22s raw %s" % (pid, name, raw))
        else:
            print("  PID %02X  %-22s %10.2f   raw %s" % (pid, name, v, raw))
    return supported


# ---------------------------------------------------------------- cluster

def cluster_scan(can, dest=0x17, last=40):
    print("")
    print("=" * 60)
    print("PHASE 2 -- instrument cluster, TP 2.0 address 0x%02X" % dest)
    print("=" * 60)
    ch = tp20.Channel(can, dest=dest)
    try:
        tx, rx = ch.connect()
    except tp20.TP20Error as e:
        print("cluster: no channel (%s)" % e)
        return None
    print("cluster: channel open, tx 0x%03X rx 0x%03X" % (tx, rx))
    try:
        print("cluster ident:", tp20.ecu_ident(ch))
    except tp20.TP20Error as e:
        print("cluster: ident failed (%s)" % e)
    if not ch.connected:
        try:
            ch.connect()
        except tp20.TP20Error:
            return ch

    for n in range(1, last + 1):
        if not ch.connected:
            try:
                ch.connect()
            except tp20.TP20Error as e:
                print("cluster: reconnect failed at %03d (%s)" % (n, e))
                break
        try:
            d = tp20.read_block(ch, n, timeout=0.5)
        except tp20.TP20Error as e:
            print("cluster %03d: transport error (%s)" % (n, e))
            continue
        if not d:
            continue
        raw = "".join("%02X" % x for x in d)
        print("CL %03d,%s,%s" % (n, raw, " | ".join(tp20.decode_fields(d))))
    return ch


# ---------------------------------------------------------------- main

bus = uds.Bus()
eobd_scan(bus)
bus.listener.deinit()

ch = cluster_scan(bus.can)
if ch is not None:
    ch.disconnect()

print("")
print("=" * 60)
print("DISCOVERY DONE")
print("=" * 60)
while True:
    time.sleep(1)
