"""
sendcmd.py -- send a command frame to the board, without RealDash.

The tee accepts exactly what RealDash sends: 17-byte "set value" frames on
its TCP port. So anything that can open a socket can drive the board -- which
means firmware reloads, peak resets and code clears can be done from the
laptop over adb instead of by tapping the head unit.

    python3 sendcmd.py reload        # restart code.py (picks up new files)
    python3 sendcmd.py peak          # reset the boost peak hold
    python3 sendcmd.py refresh       # re-read stored fault codes
    python3 sendcmd.py clear         # CLEAR fault codes -- writes to the ECU
    python3 sendcmd.py raw 0 5       # word 0 = 5

WHY RELOAD EXISTS: writing to CIRCUITPY from the head unit does NOT trigger
CircuitPython's auto-reload (verified 2026-08-23 -- realdash.py on the drive
had new code while the board kept running the old copy until a power cycle).
`reload` closes that gap and makes remote deploys real.

The board triggers on "value changed AND non-zero", so this sends a value
that differs from last time: a counter derived from the clock, wrapped into
1..9998. Zero is reserved for idle.
"""

import argparse
import socket
import struct
import sys
import time

TAG = b"\x44\x33\x22\x11"
CMD_FRAME = 0xC90

# word index within frame 0xC90, mirroring board/scirocco_realdash.xml
WORDS = {
    "clear": 0,       # WRITES TO THE ECU
    "peak": 1,
    "refresh": 2,
    "reload": 3,
}


def build(word_index, value):
    """A RealDash 'set value' frame: tag, id, 8 data bytes, checksum."""
    words = [0, 0, 0, 0]
    words[word_index] = value & 0xFFFF
    body = TAG + struct.pack("<I", CMD_FRAME) + struct.pack("<HHHH", *words)
    return body + bytes((sum(body) & 0xFF,))


def send(host, port, frame, hold_s=2.0):
    """Connect, send, and hold the socket briefly.

    The tee forwards commands straight through, but the board only polls its
    command reader once per acquisition cycle (~70 ms), and RealDash-style
    clients normally keep the value asserted. Holding the connection open for
    a moment means the frame cannot be missed by a cycle boundary.
    """
    s = socket.create_connection((host, port), timeout=5)
    try:
        s.sendall(frame)
        time.sleep(hold_s)
    finally:
        s.close()


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("command", choices=sorted(WORDS) + ["raw"])
    ap.add_argument("args", nargs="*", type=int,
                    help="for 'raw': <word-index> <value>")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=35000)
    ap.add_argument("--value", type=int, default=None,
                    help="override the auto counter")
    a = ap.parse_args(argv)

    if a.command == "raw":
        if len(a.args) != 2:
            ap.error("raw needs <word-index> <value>")
        idx, val = a.args
    else:
        idx = WORDS[a.command]
        # any non-zero value that differs from the previous one triggers
        val = a.value if a.value is not None else (int(time.time()) % 9998) + 1

    if a.command == "clear":
        print("NOTE: clearing fault codes WRITES to the ECU and resets the "
              "emissions readiness monitors.", file=sys.stderr)

    send(a.host, a.port, build(idx, val))
    print("sent %s: frame 0x%03X word %d = %d" % (a.command, CMD_FRAME, idx, val))


if __name__ == "__main__":
    main()
