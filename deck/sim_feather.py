#!/usr/bin/env python3
"""
sim_feather.py -- laptop stand-in for the Feather M4 CAN, for bench-testing
tee.py (and RealDash itself) with no car and no board.

TCP server on 127.0.0.1:35001; tee.py --sim connects as a client, exactly as
it would open the serial port. The frames are encoded by the board's OWN
realdash.py (imported from board/), so every byte -- ids, scaling, signed
negatives and frame burst order -- is identical to what the
car produces. The idle numbers are the values captured live on the car
(rpm 840, MAP 1000 mbar abs against baro 1005 -> boost -0.005 bar, load
19.5 %, IAT ~56 C, coolant 96, oil 82, battery 13.4).

Drive profile, looping: 60 s idle, a 12 s pull (rpm 2000 -> 6500, load 95,
boost ramping to +1.1 bar, knock on cylinder 2 pulling -3.0 deg mid-pull),
20 s cruise (2500 rpm, load 40), repeat. Steering sweeps through both signs,
turn lamps follow it, and a small chassis model exercises all DCC fields.
--fast compresses profile time 10x
(a full loop in 9.2 s) while still sending real frames at 14 Hz, so burst
triggers, pre-roll and the 3 s release can be exercised in seconds.

Incoming 17-byte RealDash command frames (forwarded by the tee) are checksum
-validated, printed decoded, and Reset Peak actually resets the peak so the
round trip is visible on a connected dashboard.

Stdlib only. Note the cruise rpm sits exactly ON the burst OFF threshold
(rpm < 2500 is never sustained), which is faithful to the spec as written:
expect each pull's segment to run through the cruise and end ~3 s into idle.
"""

import argparse
import math
import random
import select
import socket
import struct
import sys
import time
from pathlib import Path

# The whole point of the sim is byte-identical frames, so use the board's
# encoder rather than a copy of it. append (not insert) so board/code.py and
# friends can never shadow stdlib modules.
sys.path.append(str(Path(__file__).resolve().parent.parent / "board"))
import realdash

import config as cfg

IDLE_S, PULL_S, CRUISE_S = 60.0, 12.0, 20.0
CYCLE_S = IDLE_S + PULL_S + CRUISE_S

CMD_NAMES = {0: "Clear Codes", 1: "Reset Peak", 2: "Refresh Codes"}


class FakeGauge:
    """Attribute bag with everything realdash.send_all reads off boost.Gauge.

    The EOBD snapshot channels (rail_bar, lambda_cmd, pedal, cat_temp,
    run_time, dist_clear) HOLD their startup values on the real board -- the
    ECU closes TP 2.0 on every EOBD request -- so the sim holds them too."""

    def __init__(self):
        self.baro = 1005.0          # mbar; boost = actual - baro
        # fast block 115
        self.rpm = 840.0
        self.load = 19.5
        self.actual = 1000.0        # MAP absolute mbar (captured idle value)
        self.spec = 1000.0
        self.peak_bar = 0.0
        # slow blocks
        self.iat = 56.0             # (56 + 273.15) * 10 = the captured 3291
        self.n75 = 0.0
        self.volts = 13.4
        self.coolant = 96.0
        self.oil = 82.0
        self.maf = 3.2
        self.throttle = 3.9
        self.speed = 0.0
        self.trim_add = -0.8
        self.trim_mult = 2.3
        self.ambient = 21.5
        self.inj_ms = 2.4
        self.charge_air = 30.0
        self.timing = -2.2          # signed negative on the wire, like the car
        self.knock1 = self.knock2 = self.knock3 = self.knock4 = 0.0
        self.odo_raw = 18588        # x10 km counter = 185880 km
        # EOBD startup snapshot -- frozen for the session
        self.rail_bar = 52.0
        self.cat_temp = 382.0
        self.pedal = 14.5
        self.run_time = 95.0
        self.dist_clear = 1204.0
        self.lambda_cmd = 1.000
        # Measuring block 106 / FRAME_FUEL. These were added to the board
        # after the original simulator and must be present for the first
        # slow-frame burst to complete.
        self.fuel_pump_duty = 49.8
        self.fuel_temp = 41.0
        self.rail_spec_abs = 52.0
        self.rail_abs = 52.0
        self.errors = 0             # reconnect counter in the status frame


class FakeAux:
    """Bench-only DCC/gear source with the same attributes as AuxReader."""

    def __init__(self, stale=False):
        self.stale = stale
        self.gear = "P"
        self.steer_deg = 0.0         # legacy raw EPS channel, display-unused
        self.height = [2.62, 2.59, 2.43]
        self.damper = [18, 18, 14, 14]
        self.visits = 1
        self.fails = 0

    def age_of(self, name):
        if name in ("height", "damper"):
            return 45.0 if self.stale else 1.2
        return 1.2


def update_aux(aux, t, phase):
    """Animate chassis values enough to verify corner bindings visually."""
    if aux is None:
        return
    aux.gear = "P" if phase == "idle" else "4" if phase == "pull" else "6"
    roll = math.sin(t * 0.8)
    pitch = math.sin(t * 0.45)
    aux.height[0] = 2.62 + 0.08 * roll + 0.04 * pitch
    aux.height[1] = 2.59 - 0.08 * roll + 0.04 * pitch
    aux.height[2] = 2.43 - 0.06 * pitch
    base = 18 if phase == "idle" else 42 if phase == "pull" else 27
    aux.damper = [
        max(0, int(base + 8 * roll)),
        max(0, int(base - 8 * roll)),
        max(0, int(base + 5 * pitch)),
        max(0, int(base - 5 * pitch)),
    ]


def update(g, t, rng):
    """Advance the gauge to profile time t. Piecewise by phase, with a
    little noise so traces look alive; iat/charge_air chase targets so the
    thermal channels curve instead of stepping."""
    ph = t % CYCLE_S
    j = rng.uniform
    iat_target, charge_target = 56.0, 30.0

    if ph < IDLE_S:
        phase = "idle"
        g.rpm = 840.0 + 12.0 * math.sin(t * 1.3) + j(-6, 6)
        g.load = 19.5 + j(-1.0, 1.0)
        g.actual = 1000.0 + j(-4, 4)
        g.spec = 1000.0
        g.n75 = 0.0
        g.speed = 0.0
        g.maf = 3.2 + j(-0.3, 0.3)
        g.throttle = 3.9 + j(-0.3, 0.3)
        g.inj_ms = 2.4 + j(-0.1, 0.1)
        g.timing = -2.2 + j(-0.5, 0.5)
        g.knock2 = 0.0
    elif ph < IDLE_S + PULL_S:
        phase = "pull"
        p = (ph - IDLE_S) / PULL_S
        g.rpm = 2000.0 + p * 4500.0 + j(-30, 30)
        g.load = 95.0 + j(-1.5, 1.5)
        spool = min(1.0, p / 0.25)          # boost arrives in the first 3 s
        boost = 1.1 * spool - 0.06 * max(0.0, p - 0.3)   # then a slight taper
        g.actual = g.baro + boost * 1000.0 + j(-8, 8)
        g.spec = g.baro + 1120.0            # target held above actual
        g.n75 = 75.0 + j(-4, 4)
        g.speed = 50.0 + p * 80.0
        g.maf = 20.0 + p * 160.0 + j(-3, 3)
        g.throttle = 99.0 + j(-1, 1)
        g.inj_ms = 3.0 + p * 12.0
        knock = -3.0 if 0.45 <= p < 0.75 else 0.0        # cyl 2, mid-pull
        g.knock2 = knock
        g.timing = 8.0 + p * 10.0 + knock + j(-0.5, 0.5)
        iat_target, charge_target = 62.0, 46.0
    else:
        phase = "cruise"
        g.rpm = 2500.0 + 30.0 * math.sin(t * 0.9) + j(-15, 15)
        g.load = 40.0 + j(-3, 3)
        g.actual = g.baro - 150.0 + j(-10, 10)           # light vacuum
        g.spec = g.actual + j(-5, 5)
        g.n75 = 8.0 + j(-2, 2)
        g.speed = 100.0 + j(-2, 2)
        g.maf = 12.0 + j(-1, 1)
        g.throttle = 15.0 + j(-1, 1)
        g.inj_ms = 4.5 + j(-0.2, 0.2)
        g.timing = 25.0 + j(-1, 1)
        g.knock2 = 0.0
        iat_target, charge_target = 48.0, 34.0

    g.iat += (iat_target - g.iat) * 0.01
    g.charge_air += (charge_target - g.charge_air) * 0.02
    g.volts = 13.4 + j(-0.05, 0.05)
    g.peak_bar = max(g.peak_bar, (g.actual - g.baro) / 1000.0)
    return phase


class SockPort:
    """File-ish wrapper so realdash.send_all can write to the socket."""

    def __init__(self, sock):
        self.sock = sock

    def write(self, data):
        self.sock.sendall(data)


def parse_commands(buf):
    """Pop complete, checksum-valid 17-byte command frames out of buf.
    Same resync logic as the board's CommandReader."""
    out = []
    while True:
        i = buf.find(realdash.TAG)
        if i < 0:
            if len(buf) > 3:
                del buf[:len(buf) - 3]
            return out
        if len(buf) - i < 17:
            del buf[:i]
            return out
        f = bytes(buf[i:i + 17])
        if sum(f[:16]) & 0xFF == f[16]:
            del buf[:i + 17]
            out.append((struct.unpack_from("<I", f, 4)[0],
                        struct.unpack_from("<HHHH", f, 8)))
        else:
            del buf[:i + 1]


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Feather M4 CAN simulator: serves board-identical "
                    "RealDash frames over TCP for tee.py --sim")
    ap.add_argument("--host", default=cfg.SIM_HOST)
    ap.add_argument("--port", type=int, default=cfg.SIM_PORT)
    ap.add_argument("--rate", type=float, default=cfg.SIM_RATE_HZ,
                    help="frame bursts per second (default %(default)s)")
    ap.add_argument("--fast", action="store_true",
                    help="10x time compression of the drive profile "
                         "(92 s loop -> 9.2 s); frame rate is unchanged")
    dcc = ap.add_mutually_exclusive_group()
    dcc.add_argument("--dcc-unavailable", action="store_true",
                     help="publish explicit unavailable DCC status")
    dcc.add_argument("--dcc-stale", action="store_true",
                     help="publish DCC data with a 45-second stale age")
    args = ap.parse_args(argv)

    warp = 10.0 if args.fast else 1.0
    period = 1.0 / args.rate
    rng = random.Random()
    g = FakeGauge()
    aux = None if args.dcc_unavailable else FakeAux(stale=args.dcc_stale)

    srv = socket.create_server((args.host, args.port))
    print("[sim] Feather sim on %s:%d, %g Hz, profile %gx "
          "(idle %gs / pull %gs / cruise %gs)"
          % (args.host, args.port, args.rate, warp,
             IDLE_S / warp, PULL_S / warp, CRUISE_S / warp), flush=True)

    client = None
    port = None
    cbuf = bytearray()
    t0 = time.monotonic()
    next_tick = time.monotonic()
    last_phase = None

    def drop(why):
        nonlocal client, port
        if client is not None:
            try:
                client.close()
            except OSError:
                pass
            print("[sim] client dropped (%s)" % why, flush=True)
        client, port = None, None
        cbuf.clear()

    while True:
        now = time.monotonic()
        if now >= next_tick:
            phase = update(g, (now - t0) * warp, rng)
            if phase != last_phase:
                last_phase = phase
                print("[sim] phase: %s (rpm %.0f, boost %+.2f bar)"
                      % (phase, g.rpm, (g.actual - g.baro) / 1000.0),
                      flush=True)
            profile_t = (now - t0) * warp
            update_aux(aux, profile_t, phase)
            if port is not None:
                try:
                    realdash.send_all(port, g, faults=0, hz=args.rate,
                                      aux=aux)
                    # In production 0xC8E is injected by deck/canbox.py;
                    # sending the identical frame here makes bench mode
                    # self-contained and proves both steering signs and
                    # the off/on turn-indicator artwork.
                    steer = int(420.0 * math.sin(profile_t * 0.5))
                    left = 1 if steer < -100 else 0
                    right = 1 if steer > 100 else 0
                    port.write(realdash.frame(0xC8E, left, right, steer, 0))
                except OSError:
                    drop("send failed")
            next_tick += period
            if next_tick < now:             # fell behind (debugger, stall)
                next_tick = now + period

        rlist = [srv] + ([client] if client else [])
        timeout = max(0.0, next_tick - time.monotonic())
        readable, _, _ = select.select(rlist, [], [], timeout)

        if srv in readable:
            conn, addr = srv.accept()
            drop("replaced")                # one client at a time, like a port
            conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            client, port = conn, SockPort(conn)
            print("[sim] client connected: %s:%d" % addr[:2], flush=True)
        if client is not None and client in readable:
            try:
                data = client.recv(1024)
            except OSError:
                drop("recv failed")
                continue
            if not data:
                drop("closed")
                continue
            cbuf.extend(data)
            for fid, words in parse_commands(cbuf):
                if fid == realdash.FRAME_CMD:
                    named = " ".join("%s=%d" % (CMD_NAMES.get(i, "w%d" % i),
                                                words[i])
                                     for i in range(3))
                    print("[sim] cmd frame 0x%X: %s" % (fid, named),
                          flush=True)
                    if words[1] == 1:       # Reset Peak: visible round trip
                        g.peak_bar = (g.actual - g.baro) / 1000.0
                        print("[sim] peak reset to %+.3f bar" % g.peak_bar,
                              flush=True)
                else:
                    print("[sim] cmd frame 0x%X: words=%s" % (fid, words),
                          flush=True)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[sim] stopped", flush=True)
