#!/usr/bin/env python3
"""
tee.py -- the deck tee daemon: owns the board's data serial port, serves the
frames to RealDash over TCP, and logs every drive without being asked.

Architecture (PLAN-deck.md): the Feather streams 16-byte RealDash '44' frames
on its data CDC port at ~14 Hz; RealDash used to own that USB port directly.
This daemon takes the port instead, RealDash connects as a TCP client on
127.0.0.1:35000 (RealDash CAN -> WiFi/LAN; RealDash is ALWAYS the client),
and everything the board says is forwarded verbatim AND decoded into
per-drive session files. RealDash's 17-byte set-value command frames flow
back to the board verbatim -- the board's own realdash.py CommandReader
parses them, so the tee only validates the checksum and passes them on.

Run on the deck (Termux, via su so /dev/ttyACM1 is accessible):
    python tee.py --serial /dev/ttyACM1
Bench test on the laptop against the simulator:
    python deck/sim_feather.py --fast          # terminal 1
    python deck/tee.py --sim                   # terminal 2

Constraints this file is built around:
  * RealDash times out and reconnects if no valid frame arrives within a
    couple of seconds, so a synthetic 0xC82 status frame goes out right on
    accept and once per second whenever the board is silent.
  * The deck sleeps on ignition off and re-enumerates USB on resume, so the
    serial port is opened in a retry loop with backoff and a session must
    survive any number of reopens.
  * A cold boot mid-drive must lose at most ~1 s of data: raw.bin is
    fsync'd every FLUSH_INTERVAL_S; the CSVs are flushed (they can be
    regenerated from raw.bin if the worst happens).
  * GPS, a stalled RealDash client, or junk on either stream must never
    stall the serial path.

Stdlib only; pyserial is imported lazily and only for --serial mode.
"""

import argparse
import gzip
import json
import os
import shutil
import signal
import socket
import struct
import subprocess
import sys
import threading
import time
from collections import deque

import config as cfg
import frame_schema

TAG = b"\x44\x33\x22\x11"
FRAME_LEN = 16                  # board -> app: tag + u32 id + 8 payload bytes
CMD_LEN = 17                    # app -> board: same + 1 checksum byte

FRAME_ID_CYCLE = 0xC80          # first frame of every acquisition burst
FRAME_ID_STATUS = 0xC82         # baro / faults / reconnects / rate
FRAME_ID_DTC = 0xC86


# ---------------------------------------------------------------------------
# stream parsers

class FrameParser:
    """Board -> app: resync on the 44 33 22 11 tag, yield (id, payload).

    The board also emits '55' text frames (variable length, null-terminated);
    they simply never match the tag and are skipped here -- but they DO reach
    RealDash, because forwarding is done on the raw byte stream, not on
    parsed frames."""

    def __init__(self, maxbuf=4096):
        self.buf = bytearray()
        self.maxbuf = maxbuf

    def feed(self, data):
        self.buf.extend(data)
        if len(self.buf) > self.maxbuf:
            del self.buf[:len(self.buf) - self.maxbuf]
        out = []
        while True:
            i = self.buf.find(TAG)
            if i < 0:
                # keep a possible partial tag at the tail, drop the rest
                if len(self.buf) > 3:
                    del self.buf[:len(self.buf) - 3]
                return out
            if len(self.buf) - i < FRAME_LEN:
                del self.buf[:i]
                return out
            f = bytes(self.buf[i:i + FRAME_LEN])
            del self.buf[:i + FRAME_LEN]
            out.append((struct.unpack_from("<I", f, 4)[0], f[8:16]))


class CommandParser:
    """RealDash -> board: validate 17-byte set-value frames, drop the rest.

    The checksum byte is (sum of the first 16 bytes) & 0xFF. RealDash may
    lead with 0x67-header config frames (10 and 9 bytes, CRC32-terminated)
    right after connecting; resyncing on the 44-tag discards them along with
    any other junk. Valid frames are returned VERBATIM -- the board parses
    them itself."""

    def __init__(self, maxbuf=1024):
        self.buf = bytearray()
        self.maxbuf = maxbuf
        self.rejected = 0       # frames that matched the tag but not the sum

    def feed(self, data):
        self.buf.extend(data)
        if len(self.buf) > self.maxbuf:
            del self.buf[:len(self.buf) - self.maxbuf]
        out = []
        while True:
            i = self.buf.find(TAG)
            if i < 0:
                if len(self.buf) > 3:
                    del self.buf[:len(self.buf) - 3]
                return out
            if len(self.buf) - i < CMD_LEN:
                del self.buf[:i]
                return out
            f = bytes(self.buf[i:i + CMD_LEN])
            if sum(f[:16]) & 0xFF == f[16]:
                del self.buf[:i + CMD_LEN]
                out.append(f)
            else:
                # Not a command frame after all (could be a '44' tag inside
                # other data). Skip just the tag and resync.
                del self.buf[:i + 1]
                self.rejected += 1


# ---------------------------------------------------------------------------
# serial sources

class SerialSource:
    """Byte pipe to the board with automatic reopen.

    read_chunk() returns b"" when idle or offline and never blocks for more
    than ~the backoff step, so the reader thread stays responsive. write()
    drops silently when offline: RealDash re-sends button frames every
    writeInterval (500 ms), so a lost command self-heals."""

    name = "?"

    def __init__(self):
        self.reconnects = 0     # successful reopens after the first open
        self._ever_open = False
        self._backoff = cfg.SERIAL_BACKOFF_MIN_S

    def _opened(self):
        if self._ever_open:
            self.reconnects += 1
        self._ever_open = True
        self._backoff = cfg.SERIAL_BACKOFF_MIN_S
        print("[tee] %s connected" % self.name, flush=True)

    def _sleep_backoff(self):
        time.sleep(self._backoff)
        self._backoff = min(self._backoff * 2, cfg.SERIAL_BACKOFF_MAX_S)

    def read_chunk(self):
        raise NotImplementedError

    def write(self, data):
        raise NotImplementedError


class PySerialSource(SerialSource):
    """Real board on a serial device path (/dev/ttyACM1, COM7, ...)."""

    def __init__(self, device, baud=cfg.SERIAL_BAUD):
        super().__init__()
        # Lazy so that --sim mode works on machines without pyserial; failing
        # here (not on first read) makes a missing dependency obvious.
        try:
            import serial
        except ImportError:
            raise SystemExit(
                "pyserial is required for --serial mode: pip install pyserial")
        self._serial = serial
        self.device = device
        self.baud = baud
        self.name = "serial %s" % device
        self.port = None

    def _open(self):
        try:
            # timeout=0.05 bounds read_chunk; write_timeout so a wedged
            # device cannot hang the command path.
            self.port = self._serial.Serial(self.device, self.baud,
                                            timeout=0.05, write_timeout=1.0)
        except Exception:
            self.port = None
            self._sleep_backoff()
            return
        self._opened()

    def _close(self):
        p, self.port = self.port, None
        if p is not None:
            try:
                p.close()
            except Exception:
                pass
            print("[tee] %s lost, reopening" % self.name, flush=True)

    def read_chunk(self):
        if self.port is None:
            self._open()
            if self.port is None:
                return b""
        try:
            n = self.port.in_waiting
            return self.port.read(min(n, 4096) if n else 1) or b""
        except Exception:
            self._close()       # port vanished (deck sleep): reopen loop
            return b""

    def write(self, data):
        p = self.port
        if p is None:
            return
        try:
            p.write(data)
        except Exception:
            self._close()


# RFC 854 escape byte. Kept as a named constant so no source file has to
# carry a raw 0xFF literal (which encoding round-trips can mangle).
_IAC_B = bytes((0xFF,))


class SimSource(SerialSource):
    """Read frames from a TCP server instead of a serial port.

    Two users, same mechanics:
      * bench:  sim_feather.py on the laptop (--sim)
      * car:    the USB-serial bridge app on the deck (--tcp HOST:PORT).
                The deck is unrooted, so /dev/ttyACM* is root-only and an
                Android app has to own the USB device; it re-exposes it as a
                local TCP socket, which needs no permissions at all.
    Writes flow back the same way, so RealDash commands still reach the board
    as long as the bridge is bidirectional.
    """

    def __init__(self, host=cfg.SIM_HOST, port=cfg.SIM_PORT, telnet=False):
        super().__init__()
        self.addr = (host, port)
        self.name = "sim %s:%d" % self.addr
        self.sock = None
        # The released USB Serial Telnet Server (v2.0) is a TELNET server, not
        # a raw socket: on connect it sends IAC negotiation, and in both
        # directions a literal 0xFF is doubled (RFC 854 escaping). Our frames
        # are binary and contain 0xFF regularly -- an unescaped stream would
        # silently corrupt values and desync the 16-byte framing. So when
        # talking to that app we speak Telnet back at it. Master has a raw
        # mode (PR #19) which makes this unnecessary; sim_feather.py is raw.
        self.telnet = telnet
        self._iac_tail = b""

    def _open(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(1.0)
        try:
            s.connect(self.addr)
        except OSError:
            s.close()
            self._sleep_backoff()
            return
        s.settimeout(0.1)
        s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self.sock = s
        self._opened()

    def _close(self):
        s, self.sock = self.sock, None
        if s is not None:
            try:
                s.close()
            except OSError:
                pass
            print("[tee] %s lost, reconnecting" % self.name, flush=True)

    def read_chunk(self):
        if self.sock is None:
            self._open()
            if self.sock is None:
                return b""
        try:
            data = self.sock.recv(4096)
        except socket.timeout:
            return b""
        except OSError:
            self._close()
            return b""
        if not data:            # orderly close by the sim
            self._close()
            return b""
        return self._telnet_decode(data) if self.telnet else data

    # ---- telnet framing (only when self.telnet) ----

    IAC = 0xFF

    def _telnet_decode(self, data):
        """Strip IAC commands and un-double escaped 0xFF bytes.

        Handles sequences split across reads via _iac_tail, which matters
        because a 3-byte IAC command can straddle a 4096-byte boundary.
        """
        buf = self._iac_tail + data
        self._iac_tail = b""
        out = bytearray()
        i, n = 0, len(buf)
        while i < n:
            b = buf[i]
            if b != self.IAC:
                out.append(b)
                i += 1
                continue
            if i + 1 >= n:                  # dangling IAC, wait for more
                self._iac_tail = buf[i:]
                break
            c = buf[i + 1]
            if c == self.IAC:               # IAC IAC -> literal 0xFF
                out.append(self.IAC)
                i += 2
            elif c in (251, 252, 253, 254):  # WILL/WONT/DO/DONT + option
                if i + 2 >= n:
                    self._iac_tail = buf[i:]
                    break
                i += 3
            else:                            # 2-byte command
                i += 2
        return bytes(out)

    def _telnet_encode(self, data):
        return data.replace(_IAC_B, _IAC_B + _IAC_B)

    def write(self, data):
        s = self.sock
        if s is None:
            return
        try:
            s.sendall(self._telnet_encode(data) if self.telnet else data)
        except OSError:
            self._close()


# ---------------------------------------------------------------------------
# GPS

def _mps_to_kmh(v):
    """Android Location.getSpeed() is METRES PER SECOND.

    Logged raw, it sat in a column called gps_speed right next to
    speed_kmh and read 3.6x low -- on the 2026-08-23 drive the road speed
    peaked at 119 while GPS peaked at 35, which looks like a broken sensor
    rather than a unit mismatch. Converted here, at the only place the
    value enters the system, so the column name means what it says.
    """
    try:
        return None if v is None else float(v) * 3.6
    except (TypeError, ValueError):
        return None


class WakeLock(threading.Thread):
    """Hold Termux's wake lock for as long as the tee is alive.

    This is not really about the CPU. Taking it is what makes TermuxService
    run as a FOREGROUND service; without it Termux is an ordinary cached app
    (oom_score_adj 945) and Android 13's cached-app freezer suspends the
    whole uid the moment RealDash takes the screen. On 2026-08-24 that froze
    the tee, canbox, the uploader AND the watchdog mid-instruction for ten
    minutes: RealDash went blank, nothing was logged, and the drive could
    not close or upload until the cgroup was thawed by hand.

    It lives HERE, not in watchdog.sh, for two reasons. The watchdog only
    took it once at startup, so a lock released later (Termux restarting its
    service, a stray termux-wake-unlock) was never noticed -- which is
    exactly what happened again the same evening. And the watchdog is a
    long-lived shell that keeps running old code after a deploy, while the
    tee is the process every deploy restarts. Re-asserting is idempotent.

    Runs on its own daemon thread so a hung termux-wake-lock can never
    stall the acquisition loop.
    """

    PERIOD_S = 60.0
    TIMEOUT_S = 10.0

    def __init__(self):
        super().__init__(daemon=True, name="wakelock")
        self.held = 0           # successful assertions, for the status line
        self.failed = 0

    @staticmethod
    def available():
        return shutil.which("termux-wake-lock") is not None

    def _assert_once(self):
        p = subprocess.Popen(["termux-wake-lock"],
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             start_new_session=True)
        try:
            p.communicate(timeout=self.TIMEOUT_S)
        except subprocess.TimeoutExpired:
            GpsPoller._reap(p)
            raise
        return p.returncode == 0

    def run(self):
        while True:
            try:
                if self._assert_once():
                    self.held += 1
                else:
                    self.failed += 1
            except Exception:
                self.failed += 1
            time.sleep(self.PERIOD_S)


class GpsPoller(threading.Thread):
    """Polls termux-location in its own thread; the data path only ever reads
    the last fix. Every failure mode -- missing binary, hang, junk output,
    permission denied -- is contained here and can only produce blank GPS
    columns, never a stalled tee."""

    def __init__(self):
        super().__init__(daemon=True, name="gps")
        self._fix = None        # (monotonic, lat, lon, alt, speed)
        self.fixes = 0
        self.provider = None    # which provider actually answered last
        self.accuracy = None    # metres, as reported by that provider
        self.unwedges = 0       # how often Termux:API had to be restarted

    @staticmethod
    def available():
        return shutil.which("termux-location") is not None

    # gps first (metres, real speed), network second (tens of metres, but it
    # answers in ~2 s and works before the receiver has locked, under cover,
    # and in a parkade). A coarse track beats no track, so take either --
    # which provider produced a fix is recorded so a log can be judged later.
    #
    # ORDER IS ADAPTIVE, and that matters more than it looks. The list used
    # to be walked from the top every single round, so whenever the gps
    # provider was hanging, every round paid its full GPS_TIMEOUT_S (15 s)
    # before even trying network. With GPS_MAX_AGE_S at 10 s, the fix was
    # already stale by the time the next one arrived and rows logged blank
    # GPS while a provider was answering perfectly well. A provider that
    # times out goes to the back; the one that answers comes to the front.
    PROVIDERS = ("gps", "network")

    def _try(self, provider):
        # start_new_session so a timeout can kill the whole PROCESS GROUP.
        # subprocess.run()'s timeout kills only the direct child -- the
        # `termux-location` shell wrapper -- and leaves the `termux-api`
        # helper it spawned running forever. On 2026-08-24 seven of those
        # piled up, wedged the Termux:API app, and the whole
        # highway drive home logged ZERO fixes: the track only resumed
        # once the car was already parked. Reaping the group prevents it.
        p = subprocess.Popen(
            ["termux-location", "-p", provider, "-r", "once"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            start_new_session=True)
        try:
            out, _ = p.communicate(timeout=cfg.GPS_TIMEOUT_S)
        except subprocess.TimeoutExpired:
            self._reap(p)
            raise
        d = json.loads(out or b"{}")
        lat, lon = d.get("latitude"), d.get("longitude")
        if lat is None or lon is None:
            return False
        self._fix = (time.monotonic(), lat, lon,
                     d.get("altitude"), _mps_to_kmh(d.get("speed")))
        self.fixes += 1
        self.provider = provider
        self.accuracy = d.get("accuracy")
        return True

    @staticmethod
    def _reap(p):
        """Kill the timed-out wrapper AND the termux-api helper under it."""
        try:
            os.killpg(os.getpgid(p.pid), signal.SIGKILL)
        except (OSError, AttributeError):
            try:
                p.kill()
            except OSError:
                pass
        try:
            p.communicate(timeout=2)
        except Exception:
            pass

    # Termux:API can still be wedged from an earlier leak or a binder stall,
    # and then every provider times out forever. com.termux, com.termux.api
    # and the helper binary share uid 10157, so we are permitted to kill
    # them and the app restarts on the next request. Cooldown-bounded, so a
    # genuinely GPS-less place (parkade, tunnel) does not thrash it.
    UNWEDGE_AFTER = 5           # consecutive rounds where no provider answered
    UNWEDGE_COOLDOWN_S = 120.0

    def _unwedge(self, dry_rounds):
        # pkill's exit code says whether anything was actually killed: 0 =
        # matched, 1 = no match. Reporting "restarted Termux:API" when
        # nothing matched is the kind of comforting lie that sent us looking
        # in the wrong place for an hour tonight.
        killed = 0
        for pat in ("[t]ermux-api", "[c]om.termux.api"):
            try:
                r = subprocess.run(["pkill", "-9", "-f", pat],
                                   timeout=10, capture_output=True)
                if r.returncode == 0:
                    killed += 1
            except (OSError, subprocess.TimeoutExpired) as e:
                print("[tee] gps: pkill %s failed: %r" % (pat, e), flush=True)
        self.unwedges += 1
        if killed:
            print("[tee] gps: %d rounds with no fix -- killed %d Termux:API "
                  "process group(s)" % (dry_rounds, killed), flush=True)
        else:
            print("[tee] gps: %d rounds with no fix, but nothing to kill -- "
                  "no signal rather than a wedged API?" % dry_rounds,
                  flush=True)

    def run(self):
        dry = 0
        last_unwedge = 0.0
        order = list(self.PROVIDERS)
        while True:
          # The whole body is guarded: this thread dying takes GPS with it
          # for the rest of the drive, silently, and nothing restarts it.
          try:
            got = False
            for provider in list(order):
                try:
                    if self._try(provider):
                        got = True
                        if order[0] != provider:      # promote the winner
                            order.remove(provider)
                            order.insert(0, provider)
                        break
                except subprocess.TimeoutExpired:
                    if len(order) > 1:                # demote the hang
                        order.remove(provider)
                        order.append(provider)
                    continue
                except Exception:
                    continue        # try the next provider, then sleep
            if got:
                dry = 0
            else:
                dry += 1
                now = time.monotonic()
                # The cooldown is measured from the LAST unwedge, but a hung
                # provider makes each round take GPS_TIMEOUT_S, so rounds are
                # slow exactly when unwedging matters. Counting dry rounds
                # (not elapsed time) is what actually gates it; the cooldown
                # only stops a genuinely signal-less place from thrashing.
                if (dry >= self.UNWEDGE_AFTER
                        and now - last_unwedge >= self.UNWEDGE_COOLDOWN_S):
                    last_unwedge = now
                    self._unwedge(dry)
                    dry = 0
                    order = list(self.PROVIDERS)      # start clean after
          except Exception as e:
            print("[tee] gps thread caught %r; continuing" % (e,), flush=True)
          time.sleep(cfg.GPS_POLL_S)

    def fix(self):
        f = self._fix
        if f is not None and time.monotonic() - f[0] <= cfg.GPS_MAX_AGE_S:
            return f[1:]
        return None


# ---------------------------------------------------------------------------
# session files

def _fmt(v):
    return "" if v is None else "%.10g" % v


ACTIVE_MARKER = ".active"       # holds the pid of the tee writing this session


class Session:
    """One drive: a directory of raw.bin + drive.csv + bursts.csv, finalized
    into gzipped CSVs plus meta.json (written last -- it is the uploader's
    commit marker that the directory is complete)."""

    def __init__(self, root, channels, t_start):
        name = time.strftime("%Y-%m-%d_%H%M", time.localtime(t_start))
        path = os.path.join(root, name)
        n = 2
        while os.path.exists(path):            # two drives in one minute
            path = os.path.join(root, "%s_%d" % (name, n))
            n += 1
        os.makedirs(path)
        self.dir = path
        self.name = os.path.basename(path)
        self.channels = channels
        self.t_start = t_start
        # Monotonic twin of t_start. The deck's RTC is often still wrong when
        # Termux:Boot starts the tee -- on 2026-08-24 two real drives were
        # named 2026-01-25_1944 and filed under drives/2026/01/ because the
        # clock had not synced yet. Comparing elapsed-wall against
        # elapsed-monotonic at close detects the NTP correction and renames
        # the session to the time it actually happened.
        self.mono_start = time.monotonic()
        self.mono_last_row = self.mono_start
        self.clock_offset = 0.0     # added to every t_unix if the RTC jumped
        self.rows_baseline = 0
        self.rows_burst = 0
        self.segments = 0

        # Liveness marker for the uploader. Without it, adoption has to guess
        # from file mtimes whether a session is dead -- and close() releases
        # every file descriptor BEFORE it gzips and writes meta.json, so
        # there is a window where a live session looks exactly like an
        # abandoned one. Tonight's ten-minute freeze made that window real:
        # the files were 600 s stale when close() finally ran.
        #
        # The pid is what makes this safe in the other direction too. A tee
        # that is killed leaves the marker behind, but a dead pid means the
        # session really is abandoned, so it can still be adopted. Only a
        # LIVE tee blocks adoption.
        try:
            with open(os.path.join(path, ACTIVE_MARKER), "w") as f:
                f.write("%d\n" % os.getpid())
        except OSError as e:
            print("[tee] could not write %s: %r" % (ACTIVE_MARKER, e), flush=True)

        self.raw = open(os.path.join(path, "raw.bin"), "wb")
        self.drive = open(os.path.join(path, "drive.csv"), "w")
        self.bursts = open(os.path.join(path, "bursts.csv"), "w")
        header = "t_unix," + ",".join(channels) + ",lat,lon,alt,gps_speed"
        self.drive.write(header + "\n")
        self.bursts.write(header + ",segment\n")

    # raw.bin record: '<BHQ' (dir 0=board->app 1=app->board, payload_len,
    # unix_ms) + payload, one record per read chunk. payload_len is u16;
    # chunks are capped at 4096 by the sources, the split is pure paranoia.
    def log_raw(self, direction, payload, t):
        ms = int(t * 1000)
        for i in range(0, len(payload), 0xFFFF):
            part = payload[i:i + 0xFFFF]
            self.raw.write(struct.pack("<BHQ", direction, len(part), ms))
            self.raw.write(part)

    def _row(self, t, values, gps):
        # Monotonic twin of the newest row's wall time. _fix_clock_jump needs
        # both clocks sampled at the SAME instant to measure drift; taking
        # elapsed at close() while t_end is the last frame compared two
        # different moments and folded the idle-timeout window into the
        # "drift".
        self.mono_last_row = time.monotonic()
        cells = ["%.3f" % t] + [_fmt(v) for v in values]
        cells += ["%.6f" % gps[0], "%.6f" % gps[1],
                  _fmt(gps[2]), _fmt(gps[3])] if gps else ["", "", "", ""]
        return ",".join(cells)

    def write_baseline(self, t, values, gps):
        self.drive.write(self._row(t, values, gps) + "\n")
        self.rows_baseline += 1

    def write_burst(self, t, values, gps, segment):
        self.bursts.write(self._row(t, values, gps) + ",%d" % segment)
        self.bursts.write("\n")
        self.rows_burst += 1

    def flush(self):
        self.drive.flush()
        self.bursts.flush()
        self.raw.flush()
        # fsync only raw.bin: it is the byte-exact record both CSVs can be
        # regenerated from, and one fsync/s of a few KB is cheap even on the
        # deck's flash. Losing <= FLUSH_INTERVAL_S on a cold boot is the spec.
        try:
            os.fsync(self.raw.fileno())
        except OSError:
            pass

    CLOCK_JUMP_S = 3600.0       # anything smaller is drift, not a resync

    def _fix_clock_jump(self, t_end):
        """Detect an RTC correction landing mid-session, and undo its damage.

        Monotonic time cannot jump, so elapsed-monotonic is the true length
        of the session; anything the wall clock disagrees with by more than
        an hour is the NTP correction landing. The real start is then
        t_end - elapsed.

        Both clocks are sampled at the SAME instant -- the last row written
        -- because t_end is the last decoded frame, not "now". Measuring
        elapsed at close() instead compared two different moments and folded
        the whole idle-timeout window into the apparent drift.

        Renaming the directory is only half the repair. Every row already in
        drive.csv and bursts.csv carries a t_unix stamped from the BOGUS
        clock, so a rename alone produces a drive filed under August whose
        rows all claim January -- internally inconsistent, and worse than
        leaving it alone. The offset is therefore recorded and applied to the
        rows as they are compressed.
        """
        elapsed = self.mono_last_row - self.mono_start
        drift = (t_end - self.t_start) - elapsed
        if abs(drift) < self.CLOCK_JUMP_S:
            return
        true_start = t_end - elapsed
        self.clock_offset = true_start - self.t_start
        name = time.strftime("%Y-%m-%d_%H%M", time.localtime(true_start))
        root = os.path.dirname(self.dir)
        # Uniqueness must consider uploaded/ too. A corrected name that
        # collides with an already-uploaded session id would land on the same
        # R2 prefix and overwrite a drive that is already safely stored.
        taken = set()
        for d in (root, os.path.join(root, "uploaded")):
            try:
                taken.update(os.listdir(d))
            except OSError:
                pass
        candidate = name
        n = 2
        while candidate in taken or os.path.exists(os.path.join(root, candidate)):
            candidate = "%s_%d" % (name, n)
            n += 1
        path = os.path.join(root, candidate)
        try:
            os.rename(self.dir, path)
        except OSError as e:
            print("[tee] clock jumped but rename failed (%r); session stays "
                  "as %s" % (e, self.name), flush=True)
            return
        print("[tee] clock resynced mid-session (%+.0f s): %s -> %s"
              % (self.clock_offset, self.name, candidate), flush=True)
        self.dir = path
        self.name = candidate
        self.t_start = true_start

    def _compress_rows(self, base, t_end):
        """Gzip one CSV, correcting t_unix if the clock jumped mid-session.

        The offset must NOT be applied to every row. NTP lands part-way
        through the session, so rows written before it carry the bogus stamp
        and rows written after it are already correct -- adding the offset to
        both would simply move the error onto the good half.

        Which half a row belongs to is decidable from the row itself: a
        correct stamp lies inside the session's true span, and a bogus one,
        being wrong by CLOCK_JUMP_S or more, cannot.
        """
        src = os.path.join(self.dir, base)
        off = self.clock_offset
        lo, hi = self.t_start - 1.0, t_end + 1.0
        fixed = 0
        with open(src, "r") as fin, gzip.open(src + ".gz", "wt") as fout:
            if not off:
                shutil.copyfileobj(fin, fout)
            else:
                fout.write(fin.readline())          # header, verbatim
                for line in fin:
                    stamp, sep, rest = line.partition(",")
                    try:
                        t = float(stamp)
                    except ValueError:
                        fout.write(line)            # never drop a row
                        continue
                    if not (lo <= t <= hi):
                        t += off
                        fixed += 1
                    fout.write("%.3f%s%s" % (t, sep, rest))
        if fixed:
            print("[tee] %s: corrected %d pre-resync row timestamps"
                  % (base, fixed), flush=True)
        os.remove(src)

    def close(self, t_end, meta_extra):
        for f in (self.raw, self.drive, self.bursts):
            f.close()
        self._fix_clock_jump(t_end)
        for base in ("drive.csv", "bursts.csv"):
            self._compress_rows(base, t_end)
        meta = {
            "session": self.name,
            # t_start is already corrected by _fix_clock_jump; t_end is the
            # last frame's wall time, which is AFTER the resync and so was
            # never wrong.
            "t_start_unix": round(self.t_start, 3),
            "t_end_unix": round(t_end, 3),
            "duration_s": round(t_end - self.t_start, 1),
            # raw.bin timestamps are NOT rewritten -- it is the byte-exact
            # record and rewriting it would defeat the point. Anything
            # replaying it must add this offset.
            "clock_offset_s": round(self.clock_offset, 3),
            "rows_baseline": self.rows_baseline,
            "rows_burst": self.rows_burst,
            "burst_segments": self.segments,
            "channels": list(self.channels),
            "files": {"drive": "drive.csv.gz", "bursts": "bursts.csv.gz",
                      "raw": "raw.bin"},
            "upload": {"state": "pending"},
        }
        meta.update(meta_extra)
        tmp = os.path.join(self.dir, "meta.json.tmp")
        with open(tmp, "w") as f:
            json.dump(meta, f, indent=2, sort_keys=True)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, os.path.join(self.dir, "meta.json"))
        # Strictly last: meta.json is the uploader's commit marker, so the
        # session must not stop looking live until it is on disk.
        try:
            os.remove(os.path.join(self.dir, ACTIVE_MARKER))
        except OSError:
            pass


# ---------------------------------------------------------------------------
# the tee

class Tee:
    def __init__(self, source, schema, bind, logdir,
                 idle_end_s=cfg.SESSION_IDLE_END_S):
        self.source = source
        self.schema = schema
        self.bind = bind
        self.logdir = logdir
        self.idle_end_s = idle_end_s

        self.lock = threading.RLock()   # session/latest/burst/client state
        self.tx_lock = threading.Lock() # serializes client sends (frames must
                                        # not interleave mid-frame on the wire)
        self.stop = threading.Event()
        self.parser = FrameParser()

        self.client = None
        self.client_addr = None
        self.client_connects = 0
        self.last_client_tx_m = 0.0
        self.heartbeats = 0
        self.cmds_forwarded = 0
        self.cmds_rejected = 0

        self.session = None
        self.latest = {}
        self.channels = schema.channels
        self.frames_decoded = 0
        self.cycles = 0
        self.dtc_last = None
        self.baro_word = 0              # raw mbar word off the last real 0xC82
        self.last_board_m = None             # monotonic of last serial bytes
        self._engine_off_since = None        # monotonic when the car went off
        self.last_frame_wall = 0.0

        self.cycle_open = False
        self.cycle_t = 0.0
        self.cycle_times = deque()      # monotonic stamps for the rate figure
        self.last_baseline_t = 0.0

        self.ring = deque()             # (t, values, gps) while burst is OFF
        self.burst_on = False
        self.segment = 0
        self.below_since = None

        # index of the trigger channels inside the row; the schema comes from
        # the XML, so guard against a rename breaking the trigger silently.
        try:
            self.i_rpm = self.channels.index(cfg.CHANNEL_RPM)
            self.i_load = self.channels.index(cfg.CHANNEL_LOAD)
            self.i_boost = self.channels.index(cfg.CHANNEL_BOOST_BAR)
        except ValueError as e:
            raise SystemExit("trigger channel missing from schema: %s" % e)

        self.gps = GpsPoller() if GpsPoller.available() else None
        self.wakelock = WakeLock() if WakeLock.available() else None

    # ---- board -> everything ----

    def _serial_loop(self):
        while not self.stop.is_set():
            chunk = self.source.read_chunk()
            if not chunk:
                continue
            now = time.time()
            self.last_board_m = time.monotonic()
            frames = self.parser.feed(chunk)
            with self.lock:
                if frames:
                    self.last_frame_wall = now
                    # Only log a running engine. Frames arrive whenever the
                    # head unit is awake, so without this the tee opens a
                    # session every time the key is turned to accessory --
                    # or straight after closing one, while the car sits
                    # parked with the ignition still on, which is exactly
                    # the junk session left behind on 2026-08-25.
                    if self.session is None and self._engine_running():
                        self._start_session(now)
                if self.session is not None:
                    self.session.log_raw(0, chunk, now)
                for fid, payload in frames:
                    self._on_frame(fid, payload, now)
            # Verbatim passthrough of the raw stream: '44' frames, '55' text
            # frames, whatever future firmware adds -- RealDash resyncs.
            self._send_to_client(chunk)

    def _on_frame(self, fid, payload, t):
        # lock held
        if fid == FRAME_ID_STATUS:
            self.baro_word = payload[0] | (payload[1] << 8)
        vals = self.schema.decode(fid, payload)
        if vals is None:
            return
        self.frames_decoded += 1
        if fid == FRAME_ID_CYCLE:
            # 0xC80 opens every burst the board sends, so its arrival means
            # the PREVIOUS cycle is complete: snapshot that one as a row.
            self._close_cycle()
            self.cycle_open = True
            self.cycle_t = t
            self.cycles += 1
            self.cycle_times.append(time.monotonic())
        self.latest.update(vals)
        if fid == FRAME_ID_DTC:
            self.dtc_last = [int(vals[k]) for k in
                             ("fault_1", "fault_2", "fault_3", "fault_4")
                             if k in vals]

    def _close_cycle(self):
        # lock held
        if not self.cycle_open or self.session is None:
            self.cycle_open = False
            return
        self.cycle_open = False
        t = self.cycle_t
        values = [self.latest.get(ch) for ch in self.channels]
        gps = self.gps.fix() if self.gps else None
        if t - self.last_baseline_t >= cfg.BASELINE_PERIOD_S:
            self.last_baseline_t = t
            self.session.write_baseline(t, values, gps)
        self._burst_step(t, values, gps)

    def _burst_step(self, t, values, gps):
        # lock held. Full-rate row bookkeeping per the burst spec.
        rpm = values[self.i_rpm] or 0.0
        load = values[self.i_load] or 0.0
        boost = values[self.i_boost] or 0.0
        if self.burst_on:
            self.session.write_burst(t, values, gps, self.segment)
            # load is only meaningful above idle on this ECU (it pins at
            # 100% at idle), so a low-rpm row counts as "below" regardless
            # of what load claims -- otherwise a burst never ends.
            load_hot = (load >= cfg.TRIGGER_OFF_LOAD
                        and rpm >= cfg.TRIGGER_LOAD_MIN_RPM)
            below = (rpm < cfg.TRIGGER_OFF_RPM
                     and not load_hot
                     and boost < cfg.TRIGGER_OFF_BOOST_BAR)
            if not below:
                self.below_since = None
            elif self.below_since is None:
                self.below_since = t
            elif t - self.below_since >= cfg.TRIGGER_OFF_SUSTAIN_S:
                self.burst_on = False
                self.below_since = None
                print("[tee] burst segment %d ended" % self.segment,
                      flush=True)
        else:
            self.ring.append((t, values, gps))
            while self.ring and t - self.ring[0][0] > cfg.RING_SECONDS:
                self.ring.popleft()
            if (rpm >= cfg.TRIGGER_ON_RPM
                    or (load >= cfg.TRIGGER_ON_LOAD
                        and rpm >= cfg.TRIGGER_LOAD_MIN_RPM)
                    or boost >= cfg.TRIGGER_ON_BOOST_BAR):
                self.burst_on = True
                self.segment += 1
                self.session.segments += 1
                for rt, rv, rg in self.ring:    # pre-roll, current row last
                    self.session.write_burst(rt, rv, rg, self.segment)
                self.ring.clear()
                self.below_since = None
                print("[tee] burst segment %d triggered "
                      "(rpm %.0f load %.0f boost %+.2f)"
                      % (self.segment, rpm, load, boost), flush=True)

    def _engine_running(self):
        """True only while the engine is actually turning.

        Two signals, and BOTH are needed -- each one alone gets a real case
        wrong, and both mistakes have already cost a drive.

        `sample_rate` (frame 0xC82) is how many TP 2.0 reads per second are
        succeeding, so it collapses to 0 the moment the ECU stops answering.
        That is what catches ignition-off: the board is powered from the head
        unit's USB, so it keeps streaming frames full of its last-known
        values, and rpm alone would sit at some stale non-zero number forever
        and never close the session at all.

        But sample_rate alone does not catch ignition-on-engine-off. Parked
        in the driveway with the key still in, the ECU answers perfectly
        happily at ~16 Hz while the engine is stopped, so the session stayed
        open -- that is what left the 2026-08-25 drive home unclosed on the
        deck until it was ended by hand. rpm > 0 is what closes that case.
        """
        rate = self.latest.get("sample_rate")
        rpm = self.latest.get("rpm")
        if rate is not None and rate <= 0.5:
            return False            # ECU has stopped answering: ignition off
        if rpm is not None:
            return rpm > 0          # ECU answering: believe rpm
        return rate is not None and rate > 0.5

    def _engine_off_for(self, now_m):
        """Seconds the car has been OFF, or 0 while it is running."""
        running = self._engine_running()
        if running:
            self._engine_off_since = None
            return 0.0
        if self._engine_off_since is None:
            self._engine_off_since = now_m
            return 0.0
        return now_m - self._engine_off_since

    # ---- sessions ----

    def _start_session(self, t):
        # lock held
        self.session = Session(self.logdir, self.channels, t)
        self.latest.clear()
        self.cycle_open = False
        self.ring.clear()
        self.burst_on = False
        self.below_since = None
        self._engine_off_since = None
        self.last_baseline_t = 0.0
        print("[tee] session %s started" % self.session.name, flush=True)

    def _end_session(self, why):
        # lock held
        s, self.session = self.session, None
        if s is None:
            return
        # reset per-drive state so the status line and the next session
        # start clean; _start_session resets again, belt and braces
        self.burst_on = False
        self.below_since = None
        self.ring.clear()
        self.cycle_open = False
        t_end = self.last_frame_wall or time.time()
        s.close(t_end, {
            "cycles": self.cycles,
            "frames_decoded": self.frames_decoded,
            "serial_reconnects": self.source.reconnects,
            "client_connects": self.client_connects,
            "commands_forwarded": self.cmds_forwarded,
            "gps_fixes": self.gps.fixes if self.gps else 0,
            "gps_provider": self.gps.provider if self.gps else None,
            "gps_accuracy_m": self.gps.accuracy if self.gps else None,
            "dtc_words_last": self.dtc_last,
            "end_reason": why,
        })
        print("[tee] session %s closed (%s): %d baseline rows, "
              "%d burst rows in %d segments -> %s"
              % (s.name, why, s.rows_baseline, s.rows_burst, s.segments,
                 s.dir), flush=True)

    # ---- RealDash client ----

    def _server_loop(self):
        srv = socket.create_server(self.bind, reuse_port=False)
        srv.settimeout(1.0)
        print("[tee] listening for RealDash on %s:%d" % self.bind, flush=True)
        while not self.stop.is_set():
            try:
                conn, addr = srv.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            conn.settimeout(cfg.CLIENT_SEND_TIMEOUT_S)
            with self.lock:
                old, self.client = self.client, conn
                self.client_addr = "%s:%d" % addr[:2]
                self.client_connects += 1
            if old is not None:             # new connection replaces old
                try:
                    old.close()
                except OSError:
                    pass
            print("[tee] client connected: %s" % self.client_addr, flush=True)
            # RealDash cycles the socket unless frames appear within ~1-2 s:
            # give it a status frame immediately, board or no board.
            self._send_to_client(self._heartbeat_frame())
            threading.Thread(target=self._client_loop, args=(conn,),
                             daemon=True, name="client").start()
        srv.close()

    def _client_loop(self, conn):
        parser = CommandParser()
        rejected_seen = 0
        try:
            while not self.stop.is_set():
                with self.lock:
                    if self.client is not conn:
                        return              # replaced: exit quietly
                try:
                    data = conn.recv(1024)
                except socket.timeout:
                    continue
                except OSError:
                    break
                if not data:
                    break
                now = time.time()
                with self.lock:
                    if self.session is not None:
                        # every byte both directions, junk included
                        self.session.log_raw(1, data, now)
                for f in parser.feed(data):
                    self.source.write(f)    # verbatim; board validates again
                    self.cmds_forwarded += 1
                    fid = struct.unpack_from("<I", f, 4)[0]
                    words = struct.unpack_from("<HHHH", f, 8)
                    print("[tee] cmd 0x%X words=%s -> board" % (fid, words),
                          flush=True)
                if parser.rejected != rejected_seen:
                    self.cmds_rejected += parser.rejected - rejected_seen
                    rejected_seen = parser.rejected
        finally:
            with self.lock:
                current = self.client is conn
                if current:
                    self.client = None
                    self.client_addr = None
            try:
                conn.close()
            except OSError:
                pass
            if current:
                print("[tee] client disconnected", flush=True)

    # ---- injected frames (canbox.py) ----

    def _inject_loop(self):
        """Local-only side door for deck-side readers.

        canbox.py tails the head unit's CAN box log (steering angle, turn
        signals -- signals the OBD gateway refuses to route) and pushes
        them here as ordinary RealDash '44' frames. They are decoded into
        the log row and forwarded to RealDash, but they NEVER go to the
        board, and they NEVER start a session: only the board's own frames
        mean "the engine is alive". Injected values simply ride along in
        self.latest and appear in whatever rows the board's cycles cut.
        """
        try:
            srv = socket.create_server(
                (cfg.INJECT_HOST, cfg.INJECT_PORT), reuse_port=False)
        except OSError as e:
            print("[tee] inject port unavailable: %s" % e, flush=True)
            return
        srv.settimeout(1.0)
        print("[tee] listening for injected frames on %s:%d"
              % (cfg.INJECT_HOST, cfg.INJECT_PORT), flush=True)
        conn = None
        parser = FrameParser()
        while not self.stop.is_set():
            if conn is None:
                try:
                    conn, addr = srv.accept()
                except socket.timeout:
                    continue
                except OSError:
                    break
                conn.settimeout(1.0)
                parser = FrameParser()      # never carry a torn frame over
                print("[tee] injector connected", flush=True)
            try:
                data = conn.recv(4096)
            except socket.timeout:
                continue
            except OSError:
                data = b""
            if not data:
                try:
                    conn.close()
                except OSError:
                    pass
                conn = None
                continue
            now = time.time()
            frames = parser.feed(data)
            if not frames:
                continue
            with self.lock:
                if self.session is not None:
                    # direction 2 = injected, alongside 0=board 1=client
                    self.session.log_raw(2, data, now)
                for fid, payload in frames:
                    self._on_frame(fid, payload, now)
            self._send_to_client(data)
        srv.close()

    def _send_to_client(self, data):
        with self.lock:
            c = self.client
        if c is None:
            return
        try:
            with self.tx_lock:
                c.sendall(data)
            self.last_client_tx_m = time.monotonic()
        except OSError:
            with self.lock:
                if self.client is c:
                    self.client = None
                    self.client_addr = None
            try:
                c.close()
            except OSError:
                pass
            print("[tee] client dropped (send failed)", flush=True)

    def _heartbeat_frame(self):
        """Synthetic 0xC82, encoded exactly like the board's status_frame:
        words = (baro mbar or 0, fault count 0, reconnect count, Hz x10).
        The XML divides word 0 by 10 into kPa and word 3 by 10 into Hz."""
        return TAG + struct.pack("<IHHHH", FRAME_ID_STATUS,
                                 self.baro_word & 0xFFFF, 0,
                                 self.source.reconnects & 0xFFFF,
                                 int(self._rate() * 10) & 0xFFFF)

    # ---- housekeeping ----

    def _rate(self):
        now = time.monotonic()
        while self.cycle_times and now - self.cycle_times[0] > 5.0:
            self.cycle_times.popleft()
        return len(self.cycle_times) / 5.0

    def _housekeeping(self):
        now_m = time.monotonic()
        with self.lock:
            need_hb = (self.client is not None
                       and now_m - self.last_client_tx_m
                       >= cfg.HEARTBEAT_PERIOD_S)
        if need_hb:
            self._send_to_client(self._heartbeat_frame())
            self.heartbeats += 1
        with self.lock:
            if self.session is not None:
                if now_m - self._last_flush_m >= cfg.FLUSH_INTERVAL_S:
                    self._last_flush_m = now_m
                    self.session.flush()
                if (self.last_board_m is not None
                        and now_m - self.last_board_m >= self.idle_end_s):
                    self._end_session("serial idle %.0fs" % self.idle_end_s)
                elif self._engine_off_for(now_m) >= cfg.SESSION_ENGINE_OFF_S:
                    self._end_session("engine off %.0fs"
                                      % cfg.SESSION_ENGINE_OFF_S)
        if now_m - self._last_status_m >= cfg.STATUS_PERIOD_S:
            self._last_status_m = now_m
            self._print_status()

    def _print_status(self):
        with self.lock:
            burst = ("seg %d ON" % self.segment) if self.burst_on else "off"
            session = self.session.name if self.session else "-"
            client = self.client_addr or "-"
        # GPS health belongs in the status line: on 2026-08-24 the poller
        # was dead for 42 minutes of a 54-minute drive and every status line
        # still read perfectly healthy, so the loss was only discovered from
        # the map afterwards. "gps -" means no usable fix RIGHT NOW.
        if self.gps is None:
            gps = "off"
        else:
            gps = "%d fixes" % self.gps.fixes
            if self.gps.fix() is None:
                gps = "NO FIX (%s)" % gps
            if self.gps.unwedges:
                gps += " unwedge %d" % self.gps.unwedges
        # A wake lock that stopped being taken is the freeze coming back, so
        # it is only worth printing when it is NOT simply working.
        wl = ""
        if self.wakelock is None:
            wl = " | wakelock UNAVAILABLE"
        elif self.wakelock.failed:
            wl = " | wakelock FAILING (%d ok, %d failed)" % (
                self.wakelock.held, self.wakelock.failed)
        print("[tee] %.1f Hz | client %s | session %s | burst %s | "
              "reconnects %d | cmds %d (rej %d) | heartbeats %d | gps %s%s"
              % (self._rate(), client, session, burst,
                 self.source.reconnects, self.cmds_forwarded,
                 self.cmds_rejected, self.heartbeats, gps, wl), flush=True)

    # ---- lifecycle ----

    def run(self):
        os.makedirs(self.logdir, exist_ok=True)
        print("[tee] source: %s | logdir: %s | gps: %s | wakelock: %s"
              % (self.source.name, self.logdir,
                 "termux-location" if self.gps else "disabled (not found)",
                 "held every %gs" % WakeLock.PERIOD_S if self.wakelock
                 else "UNAVAILABLE - Android may freeze this process"),
              flush=True)
        self._last_flush_m = time.monotonic()
        self._last_status_m = time.monotonic()
        if self.wakelock:
            self.wakelock.start()
        if self.gps:
            self.gps.start()
        threading.Thread(target=self._serial_loop, daemon=True,
                         name="serial").start()
        threading.Thread(target=self._server_loop, daemon=True,
                         name="server").start()
        threading.Thread(target=self._inject_loop, daemon=True,
                         name="inject").start()
        try:
            while True:
                self._housekeeping()
                time.sleep(0.2)
        except (KeyboardInterrupt, SystemExit):
            pass
        finally:
            self.stop.set()
            with self.lock:
                self._end_session("shutdown")
            print("[tee] stopped", flush=True)


# ---------------------------------------------------------------------------

def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Scirocco tee daemon: board serial <-> RealDash TCP, "
                    "with per-drive logging (see docs/PLAN-deck.md)")
    src = ap.add_mutually_exclusive_group()
    src.add_argument("--serial", metavar="DEV",
                     help="board data port (default %s)" % cfg.SERIAL_DEVICE)
    src.add_argument("--sim", action="store_true",
                     help="bench mode: read from sim_feather.py at %s:%d"
                          % (cfg.SIM_HOST, cfg.SIM_PORT))
    src.add_argument("--tcp", metavar="HOST:PORT",
                     help="read from a TCP server (the deck's USB-serial "
                          "bridge app), e.g. 127.0.0.1:8080")
    ap.add_argument("--telnet", action="store_true",
                    help="the TCP peer speaks Telnet (USB Serial Telnet "
                         "Server v2.0): un-double 0xFF and strip IAC")
    ap.add_argument("--bind", metavar="HOST:PORT",
                    default="%s:%d" % (cfg.REALDASH_BIND_HOST,
                                       cfg.REALDASH_BIND_PORT),
                    help="where RealDash connects (default %(default)s)")
    ap.add_argument("--logdir", default=cfg.LOG_DIR,
                    help="session directories go here (default %(default)s)")
    ap.add_argument("--xml", default=str(frame_schema.DEFAULT_XML),
                    help="RealDash channel XML (default: the repo's)")
    ap.add_argument("--idle-end", type=float, metavar="S",
                    default=cfg.SESSION_IDLE_END_S,
                    help="serial silence that ends a session "
                         "(default %(default)s; lower it for bench tests)")
    args = ap.parse_args(argv)

    # SIGTERM must unwind, not abort. The run() loop already closes the
    # session in its finally: block, but Python's DEFAULT SIGTERM handler
    # kills the process outright, so `pkill -f tee.py` -- how the watchdog
    # and every deploy restart it -- left the drive with no meta.json and
    # the uploader skipped it forever. Eleven drives from 2026-08-23 were
    # stranded on the deck that way.
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))

    host, _, port = args.bind.rpartition(":")
    bind = (host or cfg.REALDASH_BIND_HOST, int(port))
    schema = frame_schema.load(args.xml)
    if args.tcp:
        thost, _, tport = args.tcp.rpartition(":")
        source = SimSource(thost or "127.0.0.1", int(tport),
                           telnet=args.telnet)
    elif args.sim:
        source = SimSource()
    else:
        source = PySerialSource(args.serial or cfg.SERIAL_DEVICE)
    logdir = os.path.expanduser(args.logdir)
    Tee(source, schema, bind, logdir, idle_end_s=args.idle_end).run()


if __name__ == "__main__":
    main()
