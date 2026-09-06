"""
canbox.py -- feed the head unit's CAN box into the logging chain.

The DUDU7 has a CAN decoder box on the car's COMFORT bus -- the bus the
OBD gateway refuses to route, which is why the M4 can never see turn
signals, and why steering angle over OBD costs 470 ms a visit while the
box gets it free at ~5 Hz. The box's traffic becomes readable the moment
the launcher's "Export CANBUS Log to .txt File" toggle is on: it appends
to /sdcard/canBusALog/log_YYYY-MM-DD_N.txt, plain text, no root needed.

This tails that file, decodes the two frames proven against a narrated
in-car test (docs/CANBOX.md, 2026-08-23), and injects one RealDash '44'
frame into the tee's side door (cfg.INJECT_PORT):

    2e 7d 02 01 <flags>        0x10 = LEFT indicator, 0x08 = RIGHT
    2e 7d 03 08 <s16 LE>       steering angle, ~1 count/degree, -ve = left

    -> frame 0xC8E: turn_left | turn_right | steering_deg | canbox_age

canbox_age is seconds since the box last said ANYTHING. It is the honesty
channel: when the launcher toggle is off (the app itself warns to disable
it -- the log grows ~2 MB/hour), age climbs and a reader can tell "no
signal" from "signal is zero".

The decode was proven by narration, not by eyeballing waveforms: an
earlier pass misidentified frame `2e 14` as steering because it swept
smoothly. Only "I am turning the wheel NOW" identifies a signal. Anything
this parser does not positively recognise, it ignores.

Run under Termux (the watchdog starts it). Termux needs raw /sdcard
access: uid-level NO_ISOLATED_STORAGE + READ_EXTERNAL_STORAGE, the same
fix RealDash needed (deck/fix-realdash-storage.sh explains why).
"""

import argparse
import glob
import os
import re
import socket
import struct
import sys
import time

try:
    import config as cfg
except ImportError:
    class cfg:
        INJECT_HOST = "127.0.0.1"
        INJECT_PORT = 35002

LOG_DIR = "/sdcard/canBusALog"
FRAME_ID = 0xC8E
TAG = b"\x44\x33\x22\x11"

HEARTBEAT_S = 1.0        # resend even without change, so age stays live
STALE_WARN_S = 30        # log a complaint when the box goes quiet

# One line of the launcher's log. The leading timestamp is wall clock;
# only the hex payload matters here.
_LINE = re.compile(r"data=((?:[0-9a-f]{2}\s)+)")


class State:
    def __init__(self):
        self.left = 0
        self.right = 0
        self.steer = 0
        self.last_rx = None      # monotonic of the last PARSED box frame

    def feed(self, line):
        """Returns True if a recognised signal changed."""
        m = _LINE.search(line)
        if not m:
            return False
        b = m.group(1).split()
        if len(b) < 3 or b[0] != "2e":
            return False
        # ANY box frame proves the box (and the launcher's log toggle) is
        # alive -- that is what canbox_age promises to measure. With the
        # ignition off the box still chatters climate frames, and age must
        # not read "stale" while they flow.
        self.last_rx = time.monotonic()
        if b[1] != "7d" or len(b) < 5:
            return False
        changed = False
        if b[2] == "02" and b[3] == "01":
            flags = int(b[4], 16)
            left, right = (1 if flags & 0x10 else 0), (1 if flags & 0x08 else 0)
            changed = (left, right) != (self.left, self.right)
            self.left, self.right = left, right
        elif b[2] == "03" and b[3] == "08" and len(b) >= 6:
            v = int(b[4], 16) | (int(b[5], 16) << 8)
            if v > 32767:
                v -= 65536
            changed = v != self.steer
            self.steer = v
        return changed

    def age_s(self):
        if self.last_rx is None:
            return 600
        return min(int(time.monotonic() - self.last_rx), 600)

    def frame(self):
        steer = max(-32768, min(32767, self.steer)) & 0xFFFF
        return TAG + struct.pack("<IHHHH", FRAME_ID,
                                 self.left, self.right, steer, self.age_s())


def newest_log():
    files = sorted(glob.glob(os.path.join(LOG_DIR, "log_*.txt")),
                   key=lambda p: (os.path.getmtime(p), p))
    return files[-1] if files else None


class Tail:
    """Follow the newest log across growth, truncation and rotation.

    First open seeks to the END: replaying an hour of history would blink
    the dashboard's indicators through everything the owner did today.
    """

    def __init__(self):
        self.path = None
        self.f = None
        self.buf = ""

    def _open(self, path, from_start):
        self._close()
        try:
            self.f = open(path, "r", encoding="utf-8", errors="replace")
        except OSError:
            self.f = None
            return
        self.path = path
        if not from_start:
            self.f.seek(0, os.SEEK_END)

    def _close(self):
        if self.f:
            try:
                self.f.close()
            except OSError:
                pass
        self.f = None

    def lines(self):
        """Yield complete new lines; returns when caught up."""
        latest = newest_log()
        if latest != self.path:
            # a rotation produces a NEW file whose content is all fresh
            self._open(latest, from_start=self.path is not None) \
                if latest else self._close()
        if self.f is None:
            return
        try:
            pos = self.f.tell()
            end = os.path.getsize(self.path)
            if end < pos:                     # truncated in place
                self.f.seek(0)
            chunk = self.f.read()
        except OSError:
            self._close()
            return
        if not chunk:
            return
        self.buf += chunk
        while "\n" in self.buf:
            line, self.buf = self.buf.split("\n", 1)
            yield line


def main(argv=None):
    global LOG_DIR
    ap = argparse.ArgumentParser(
        description="tail the CAN box log into the tee")
    ap.add_argument("--host", default=cfg.INJECT_HOST)
    ap.add_argument("--port", type=int, default=cfg.INJECT_PORT)
    ap.add_argument("--dir", default=LOG_DIR)
    a = ap.parse_args(argv)
    LOG_DIR = a.dir

    st = State()
    tail = Tail()
    sock = None
    next_beat = 0.0
    warned_dir = warned_stale = False

    print("[canbox] watching %s -> %s:%d" % (LOG_DIR, a.host, a.port),
          flush=True)
    while True:
        if not os.path.isdir(LOG_DIR):
            if not warned_dir:
                print("[canbox] %s missing -- is 'Export CANBUS Log' on?"
                      % LOG_DIR, flush=True)
                warned_dir = True
            time.sleep(5)
            continue
        warned_dir = False

        changed = False
        for line in tail.lines():
            if st.feed(line):
                changed = True

        now = time.monotonic()
        if changed or now >= next_beat:
            next_beat = now + HEARTBEAT_S
            if sock is None:
                try:
                    sock = socket.create_connection((a.host, a.port),
                                                    timeout=3)
                    sock.setsockopt(socket.IPPROTO_TCP,
                                    socket.TCP_NODELAY, 1)
                    print("[canbox] connected to the tee", flush=True)
                except OSError:
                    sock = None
            if sock is not None:
                try:
                    sock.sendall(st.frame())
                except OSError:
                    try:
                        sock.close()
                    except OSError:
                        pass
                    sock = None

        age = st.age_s()
        if age > STALE_WARN_S and not warned_stale and st.last_rx is not None:
            print("[canbox] box quiet for %ds (toggle off? car asleep?)"
                  % age, flush=True)
            warned_stale = True
        if age <= STALE_WARN_S:
            warned_stale = False
        time.sleep(0.2)


if __name__ == "__main__":
    sys.exit(main())
