"""
probecap.py -- capture a probe's text output straight from the bridge.

WHY THIS EXISTS
    Probe firmware (bodyscan.py and friends) prints diagnostics down
    usb_cdc.data. That stream normally goes to tee.py, which files it into
    the current drive session's raw.bin -- but the tee only keeps a session
    open while the ENGINE looks like it is running, and it decides that from
    the gauge frames. A probe emits no gauge frames, so the tee concludes
    the drive ended, closes the session, and every subsequent byte is
    DISCARDED.

    That is not theoretical: on 2026-08-23 a shifter sweep was performed in
    the car and the output was lost exactly this way -- the session closed
    at 18:15 and the sweep landed in the gap before the next one opened at
    18:21. Zero CHANGE markers survived in any session file.

    So probe output does not go through the tee at all. This connects to the
    bridge app itself and writes every byte to a file, with no filtering and
    no notion of sessions. Stop the watchdog and the tee first, or they will
    hold the bridge and fight this for it.

USAGE (on the deck, under Termux)
    python3 probecap.py --seconds 300 --out /sdcard/probe.log

The bridge (USB Serial Telnet Server v2.0) is a TELNET server, not a raw
socket, so IAC sequences are stripped and doubled 0xFF bytes are collapsed
-- the same handling tee.py does, for the same reason.
"""

import argparse
import socket
import sys
import time

IAC = 0xFF


def telnet_decode(data, tail):
    """Strip IAC commands, un-double escaped 0xFF. Returns (clean, tail).

    `tail` carries a partial IAC sequence across read boundaries, because a
    3-byte command can straddle any buffer size.
    """
    data = tail + data
    out = bytearray()
    i, n = 0, len(data)
    while i < n:
        b = data[i]
        if b != IAC:
            out.append(b)
            i += 1
            continue
        if i + 1 >= n:
            return bytes(out), data[i:]         # dangling IAC
        c = data[i + 1]
        if c == IAC:                            # IAC IAC -> literal 0xFF
            out.append(IAC)
            i += 2
        elif c in (251, 252, 253, 254):         # WILL/WONT/DO/DONT + option
            if i + 2 >= n:
                return bytes(out), data[i:]
            i += 3
        else:
            i += 2
    return bytes(out), b""


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=2323)
    ap.add_argument("--seconds", type=float, default=300.0)
    ap.add_argument("--out", default="/sdcard/probe.log")
    ap.add_argument("--raw", action="store_true",
                    help="skip telnet decoding (bridge in raw mode)")
    a = ap.parse_args(argv)

    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(5.0)
    s.connect((a.host, a.port))
    s.settimeout(0.5)
    s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)

    deadline = time.time() + a.seconds
    tail = b""
    total = 0
    # line-buffered append: a capture that is killed early must still leave
    # everything it had already received on disk
    with open(a.out, "wb", buffering=0) as f:
        while time.time() < deadline:
            try:
                chunk = s.recv(4096)
            except socket.timeout:
                continue
            except OSError:
                break
            if not chunk:
                break
            if not a.raw:
                chunk, tail = telnet_decode(chunk, tail)
            if chunk:
                f.write(chunk)
                total += len(chunk)
    try:
        s.close()
    except Exception:
        pass
    print("captured %d bytes to %s" % (total, a.out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
