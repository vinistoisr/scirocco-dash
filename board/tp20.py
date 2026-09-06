"""
tp20.py -- VW TP 2.0 transport + KWP2000 client for CircuitPython canio.

The Scirocco's engine ECU answers generic OBD-II on ISO-TP but ignores UDS:
its manufacturer diagnostics are KWP2000 carried over VW's proprietary TP 2.0
transport (the pre-UDS VAG scheme; what VCDS labels "CAN / KWP2000").

Protocol shape (full writeup: https://jazdw.net/tp20):

    channel setup   tester -> 0x200:      [dest, C0, rx_lo, rx_hi, tx_lo, tx_hi, app]
                    ecu    -> 0x200+dest: [00,   D0, rx_lo, rx_hi, tx_lo, tx_hi, app]
                    In the response, bytes 2-3 echo the ID the tester should
                    LISTEN on and bytes 4-5 give the ID the tester TRANSMITS
                    to. Byte 1 is D0 for accepted; D6/D7/D8 mean refused.

                    Measured on this car (2009 Scirocco, J533 gateway): both
                    ID fields of the REQUEST must carry a real, valid ID.
                    The widely-copied form that puts 0x0010 -- the "not
                    valid" marker, high nibble 1 -- in bytes 2-3 is ignored
                    with no reply at all. Sending 0x300 in both fields works:

                        tx 0x200: 01 C0 00 03 00 03 01
                        rx 0x201: 00 D0 00 03 40 07 01

                    so the tester listens on 0x300 and transmits to 0x740.
    parameters      A0 request / A1 response: block size + timing values
    data frames     [op<<4 | seq, up to 7 payload bytes]; the payload stream
                    starts with a 2-byte big-endian KWP message length
    ops             0/1 = more/last packet + ACK wanted
                    2/3 = more/last packet, no ACK
                    B   = ACK ready   9 = ACK not ready
                    A3  = channel test (keepalive)   A8 = close channel
    keepalive       either side may send A3; the reply is an A1. The ECU
                    drops the channel after roughly a second of silence.

READ ONLY at the KWP layer: only readEcuIdentification (1A), a session
request (10 89), and readDataByLocalIdentifier (21) are used. Service 21
block N is exactly a VCDS measuring block, which is where boost lives.
"""

import time
import canio

BROADCAST = 0x200
DEST_ENGINE = 0x01
APP_KWP = 0x01
PROPOSED_RX = 0x300

# Block size 15, 100 ms ack timeout. Byte 4 is T3, the minimum gap we ask
# the ECU to hold between ITS response frames, in 0.1 ms units. VCDS uses
# 0x32 (5 ms) -- but a 3-frame block response then carries 10 ms of
# self-inflicted idle, a fifth of the measured 50 ms read. 0x0A (1 ms) was
# the 2026-08-23 Hz review's biggest lever. The ECU is free to clamp or
# refuse it, so connect() retries fall back to the VCDS values: attempt 1
# asks fast, attempts 2+ ask safe, and a car that hates 1 ms costs one
# failed handshake instead of a broken gauge.
# EXPERIMENT RESULT (2026-08-23, on the car): T3=0x0A was ACCEPTED by the
# MED17.5 at handshake and then the channel degraded -- ~6 Hz with multi-
# second stalls and periodic channel drops, versus a stable 13+ at 5 ms.
# The failure mode the risk note predicted: accept-then-misbehave, which
# the handshake-level fallback cannot catch. So both proposals are back at
# the VCDS-safe 5 ms; the fast/safe plumbing stays for a future, gentler
# A/B (e.g. 0x1E = 3 ms) done with someone watching the car.
PARAMS_REQ = bytes((0xA0, 0x0F, 0x8A, 0xFF, 0x32, 0xFF))
PARAMS_REQ_SAFE = bytes((0xA0, 0x0F, 0x8A, 0xFF, 0x32, 0xFF))
PARAMS_RESP = bytes((0xA1, 0x0F, 0x8A, 0xFF, 0x4A, 0xFF))

KWP_NRC = {
    0x10: "generalReject",
    0x11: "serviceNotSupported",
    0x12: "subFunctionNotSupported",
    0x21: "busyRepeatRequest",
    0x22: "conditionsNotCorrect",
    0x31: "requestOutOfRange",
    0x33: "securityAccessDenied",
    0x78: "responsePending",
    0x80: "notSupportedInActiveSession",
}


def _hex(b):
    return "".join("%02X" % x for x in b) if b else "none"


class TP20Error(Exception):
    pass


class Channel:
    """One TP 2.0 logical channel. Create with the canio.CAN object, then
    connect(). Any transport failure marks the channel dead; call connect()
    again to reopen it."""

    def __init__(self, can, dest=DEST_ENGINE, listener=None):
        self.can = can
        self.dest = dest
        self.tx_id = None
        self.rx_id = None
        self.tx_seq = 0
        self.connected = False
        self._stash = []        # data frames that arrived while waiting for an ACK
        self._want = None       # software filter: only accept this CAN id

        # ONE promiscuous listener for the channel's whole life, filtered in
        # software. The SAME51 has only a handful of hardware filter slots
        # and rebuilding a Listener per reconnect exhausts them ("Filters
        # too complex"). This bus is a gated diagnostic CAN and carries no
        # broadcast traffic, so accepting everything costs nothing.
        # canio allows exactly ONE all-matches listener at a time, so a tool
        # that walks several modules must SHARE one rather than building a
        # Listener per Channel -- doing the latter raises
        # "Already have all-matches listener" on the second module.
        # Pass `listener=` to share; leave it None to own one.
        self._own_listener = listener is None
        self.listener = listener if listener is not None             else can.listen(timeout=0.02)

    def _listen(self, can_id):
        self._want = can_id

    def _send_raw(self, can_id, data):
        self.can.send(canio.Message(id=can_id, data=bytes(data)))

    def _recv_msg(self, timeout):
        """Next frame of any id, or None."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            msg = self.listener.receive()
            if msg is None:
                continue
            if getattr(msg, "data", None):   # skip RemoteTransmissionRequest
                return msg
        return None

    def _drain(self):
        """Discard everything already buffered.

        Frames from a previous, failed channel outlive it in the RX FIFO. A
        leftover A8 in particular reads as "the ECU just hung up" against a
        channel that is actually healthy, so the buffer must be emptied
        whenever a new channel starts.
        """
        n = 0
        while True:
            w = self.listener.in_waiting
            w = w() if callable(w) else w
            if not w:
                return n
            for _ in range(w):
                self.listener.receive()
                n += 1

    def _recv_raw(self, timeout):
        """Next frame matching the current software filter, or None."""
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            msg = self._recv_msg(remaining)
            if msg is None:
                return None
            if self._want is None or msg.id == self._want:
                return msg.data

    # ---- channel management ----

    def connect(self, timeout=1.0, tries=5, attempts=4,
                assume_clean=False):
        """Open a channel, retrying the whole handshake.

        A channel abandoned by a previous run (the board was reset, or the
        program parked without disconnecting) leaves the module holding a
        half-open channel. It then answers a fresh setup with D7, or accepts
        the setup and immediately sends A8 during the parameter exchange.
        Both are cured the same way: tear the old channel down on the id the
        module just advertised, then handshake again.
        """
        # A channel left open by a previous run is the normal state after a
        # board reset, so clear it before the first attempt rather than
        # spending an attempt discovering it. assume_clean skips this: a
        # caller that closed its channels politely a moment ago (the aux
        # poller does, in a finally) would otherwise pay 200 ms of pure
        # sleep per connect -- 400 ms per aux visit, the bulk of the
        # visit's cost. The dirty case stays covered twice over: the
        # D7-refusal handler inside _connect_once cures a stale channel on
        # the id the module itself advertises, and any failed attempt
        # falls through to the full teardown below.
        if not assume_clean:
            self._flush_stale(0.2)

        last = None
        for attempt in range(attempts):
            try:
                # Attempt 1 proposes the fast T3 (1 ms inter-frame gap);
                # later attempts fall back to the VCDS-safe 5 ms in case
                # this module dislikes the faster timing.
                return self._connect_once(timeout=timeout, tries=tries,
                                          safe_params=attempt > 0)
            except TP20Error as e:
                last = e
                print("tp20: connect attempt %d failed (%s)" % (attempt + 1, e))
                self._flush_stale(0.4 * (attempt + 1))
        raise last

    def _flush_stale(self, settle_s):
        """Tear down whatever the module may still think is open.

        tx_id is set as soon as setup succeeds, so it is known even when a
        failure came later, in the A0/A1 exchange. 0x740 is the engine's
        granted id -- the address a board reset most plausibly left open.
        """
        for cid in (self.tx_id, 0x740):
            if cid is None:
                continue
            try:
                self._send_raw(cid, b"\xA8")
            except Exception:
                pass
        time.sleep(settle_s)
        self._drain()

    def _connect_once(self, timeout=1.0, tries=5, safe_params=False):
        self.connected = False
        self.tx_seq = 0
        self._stash = []
        # Keep the one promiscuous listener from __init__ and filter in
        # software. Do NOT build a hardware-filtered listener here: a filter
        # scoped to 0x200-0x2FF would hide the params reply, which arrives on
        # the granted rx id (0x300), and rebuilding one per retry exhausts
        # the SAME51's filter slots.
        self._want = None                          # accept any id during setup
        self._drain()                              # drop the old channel's frames
        lo, hi = PROPOSED_RX & 0xFF, (PROPOSED_RX >> 8) & 0x0F
        setup = bytes((self.dest, 0xC0, lo, hi, lo, hi, APP_KWP))
        d = None
        refused = None
        for attempt in range(tries):
            self._send_raw(BROADCAST, setup)
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline and d is None:
                msg = self._recv_msg(deadline - time.monotonic())
                if msg is None:
                    break
                md = msg.data
                if not md or len(md) < 2:
                    continue
                if msg.id != BROADCAST + self.dest:
                    continue                      # another module's reply
                if md[1] == 0xD0:
                    d = md
                elif 0xD0 < md[1] <= 0xDF:
                    refused = md                  # D6/D7/D8 = setup refused
            if d is not None:
                break
            # A refusal (typically D7) means the module still has a channel
            # open from an earlier, unclosed session. The advertised tx id is
            # still in the reply, so disconnect there and ask again.
            if refused is not None and len(refused) >= 6 and not (refused[5] & 0x10):
                stale = refused[4] | ((refused[5] & 0x0F) << 8)
                print("tp20: setup refused %02X, closing stale channel on 0x%03X"
                      % (refused[1], stale))
                try:
                    self._send_raw(stale, b"\xA8")
                except Exception:
                    pass
                refused = None
                time.sleep(0.3 * (attempt + 1))
        if d is None:
            if refused is not None:
                raise TP20Error("setup refused (%02X): %s" % (refused[1], _hex(refused)))
            raise TP20Error("no setup reply on 0x%03X" % (BROADCAST + self.dest))
        if len(d) < 7:
            raise TP20Error("short setup reply: %s" % _hex(d))
        if d[5] & 0x10:
            raise TP20Error("no tx id granted: %s" % _hex(d))
        self.tx_id = d[4] | ((d[5] & 0x0F) << 8)
        self.rx_id = PROPOSED_RX
        if not (d[3] & 0x10):
            self.rx_id = d[2] | ((d[3] & 0x0F) << 8)
        self._listen(self.rx_id)

        # Params exchange. The module may still be flushing an A8 for the
        # previous channel, so skip stale control frames rather than
        # treating the first non-A1 byte as failure.
        self._send_raw(self.tx_id,
                       PARAMS_REQ_SAFE if safe_params else PARAMS_REQ)
        deadline = time.monotonic() + timeout
        d = None
        while time.monotonic() < deadline:
            f = self._recv_raw(deadline - time.monotonic())
            if f is None:
                break
            if f[0] == 0xA1:
                d = f
                break
            if f[0] == 0xA8:
                raise TP20Error("ecu closed the channel during params")
            if f[0] == 0xA3:
                self._send_raw(self.tx_id, PARAMS_RESP)
        if d is None:
            raise TP20Error("no A1 params reply")
        self._drain()          # nothing before this belongs to this channel
        self.connected = True
        return (self.tx_id, self.rx_id)

    def close(self):
        """Disconnect and release the listener if this Channel owns it.

        Use this instead of disconnect() when moving on to another module:
        disconnect() only tears down the logical channel, and the Listener
        would keep the single all-matches slot occupied.
        """
        try:
            self.disconnect()
        except Exception:
            pass
        if self._own_listener:
            try:
                self.listener.deinit()
            except Exception:
                pass
            self._own_listener = False

    def disconnect(self):
        if self.connected and self.tx_id is not None:
            try:
                self._send_raw(self.tx_id, b"\xA8")
            except Exception:
                pass
        self.connected = False

    def keepalive(self):
        """Channel test. Call when idle; steady request traffic also counts."""
        self._send_raw(self.tx_id, b"\xA3")
        d = self._recv_raw(0.3)
        if d is not None and d[0] == 0xA3:
            self._send_raw(self.tx_id, PARAMS_RESP)   # crossed pings
            return
        if d is None or d[0] != 0xA1:
            self.connected = False
            raise TP20Error("keepalive failed: %s" % _hex(d))

    # ---- data transfer ----

    def _handle_control(self, d):
        """Consume A1/A3/A8 frames. Returns True if the frame was one of them."""
        if d[0] == 0xA3:
            self._send_raw(self.tx_id, PARAMS_RESP)   # ECU pinged us
            return True
        if d[0] == 0xA1:
            return True                                # stray keepalive reply
        if d[0] == 0xA8:
            self.connected = False
            raise TP20Error("ecu closed the channel")
        return False

    def _wait_ack(self, timeout=0.5):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            d = self._recv_raw(deadline - time.monotonic())
            if d is None:
                break
            if self._handle_control(d):
                continue
            hi = d[0] & 0xF0
            if hi == 0xB0:
                return
            if hi == 0x90:
                continue                       # ACK-not-ready: keep waiting
            if (d[0] >> 4) <= 0x3:
                self._stash.append(d)          # early response frame, keep it
        self.connected = False
        raise TP20Error("no ACK from ecu")

    def _send_message(self, kwp):
        n = len(kwp)
        payload = bytes(((n >> 8) & 0xFF, n & 0xFF)) + bytes(kwp)
        pos = 0
        while pos < len(payload):
            chunk = payload[pos:pos + 7]
            pos += len(chunk)
            last = pos >= len(payload)
            op = 0x10 if last else 0x20
            self._send_raw(self.tx_id, bytes((op | self.tx_seq,)) + chunk)
            self.tx_seq = (self.tx_seq + 1) & 0x0F
            if last:
                self._wait_ack()
            # Requests here are a few bytes; a message longer than one block
            # (15 frames) would additionally need a mid-message ACK pause.

    def _recv_message(self, timeout=1.0):
        payload = bytearray()
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._stash:
                d = self._stash.pop(0)
            else:
                d = self._recv_raw(deadline - time.monotonic())
            if d is None:
                break
            if self._handle_control(d):
                continue
            op = d[0] >> 4
            if op > 0x3:
                continue                       # ack meant for a sent request
            payload.extend(d[1:])
            if op in (0x0, 0x1):               # sender wants an ACK
                self._send_raw(
                    self.tx_id, bytes((0xB0 | (((d[0] & 0x0F) + 1) & 0x0F),))
                )
            if op in (0x1, 0x3):               # last frame of the message
                if len(payload) < 2:
                    break
                n = (payload[0] << 8) | payload[1]
                return bytes(payload[2:2 + n])
        self.connected = False
        raise TP20Error("timeout waiting for kwp reply")

    # ---- generic EOBD alongside the channel ----

    def obd_read(self, pid, timeout=0.3):
        """Read a generic OBD-II mode 01 PID on 0x7E0/0x7E8 while the TP 2.0
        channel stays open.

        The two protocols use different CAN IDs on the same bus, and this
        object already listens promiscuously, so no extra hardware filter is
        needed. The care required is that TP 2.0 control frames arriving
        while we wait must still be answered -- dropping a channel-test A3
        would make the ECU hang up mid-drive. So handle those in the loop
        rather than discarding everything that is not 0x7E8.

        Returns the data bytes following [0x41, pid], or None.
        """
        frame = bytearray(8)
        for i in range(8):
            frame[i] = 0x55
        frame[0:3] = bytes((0x02, 0x01, pid))
        self.can.send(canio.Message(id=0x7E0, data=bytes(frame)))

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            msg = self._recv_msg(deadline - time.monotonic())
            if msg is None:
                return None
            d = msg.data
            if msg.id == self.rx_id:
                try:
                    self._handle_control(d)      # keep the channel alive
                except TP20Error:
                    return None
                continue
            if msg.id != 0x7E8 or len(d) < 3:
                continue
            if (d[0] >> 4) != 0:                 # single frame only
                continue
            n = d[0] & 0x0F
            if n < 2 or d[1] != 0x41 or d[2] != pid:
                continue
            return bytes(d[3:1 + n])
        return None

    def request(self, kwp, timeout=1.0):
        """Send one KWP2000 request, return the raw KWP response bytes.
        Transparently waits through 7F xx 78 (responsePending)."""
        self._send_message(kwp)
        while True:
            resp = self._recv_message(timeout)
            if len(resp) >= 3 and resp[0] == 0x7F and resp[2] == 0x78:
                continue
            return resp


# ---- recon helpers ----------------------------------------------------------

def sniff(can, seconds=1.0):
    """Count frames per CAN ID with no filter. Distinguishes the quiet
    diagnostic CAN behind the gateway (nothing) from a live vehicle bus
    (steady broadcast traffic)."""
    listener = can.listen(timeout=0.05)
    counts = {}
    t_end = time.monotonic() + seconds
    while time.monotonic() < t_end:
        msg = listener.receive()
        if msg is not None:
            counts[msg.id] = counts.get(msg.id, 0) + 1
    listener.deinit()
    return counts


# The VAG diagnostic addresses actually worth probing on a PQ35 car, with
# the names VCDS uses. Sweeping only these keeps a scan to seconds instead of
# minutes, and the labels make a scan result readable without a lookup table.
VAG_ADDRESSES = (0x01, 0x02, 0x03, 0x08, 0x09, 0x0F, 0x10, 0x14, 0x15, 0x16,
                 0x17, 0x19, 0x25, 0x2E, 0x37, 0x42, 0x44, 0x46, 0x47, 0x4C,
                 0x52, 0x55, 0x56, 0x5F, 0x6C, 0x77)

ADDRESS_NAMES = {
    0x01: "Engine", 0x02: "Transmission", 0x03: "ABS/ESP",
    0x08: "Climatronic", 0x09: "Central Electrics", 0x0F: "Radio digital",
    0x10: "Parking Aid", 0x14: "Suspension", 0x15: "Airbag",
    0x16: "Steering Wheel", 0x17: "Instruments", 0x19: "Gateway",
    0x25: "Immobiliser", 0x2E: "Media Player", 0x37: "Navigation",
    0x42: "Door Driver", 0x44: "Steering Assist", 0x46: "Central Convenience",
    0x47: "Sound System", 0x4C: "Tyre Pressure", 0x52: "Door Passenger",
    0x55: "Headlight Range", 0x56: "Radio", 0x5F: "Information Electronics",
    0x6C: "Rear Camera", 0x77: "Telephone",
}


def probe_dests(can, dests=None, wait=0.12):
    """Send a channel setup to every TP 2.0 logical address and report which
    ones answer. Channels that open are closed again immediately: read-only
    recon to learn whether anything at all speaks TP 2.0 through this port."""
    if dests is None:
        # NOT range(0x00, 0x40): on PQ35 every module that owns body state
        # except 09 and 17 sits ABOVE 0x3F -- 0x42 door driver, 0x46 comfort
        # (doors/hood/tailgate), 0x52 door passenger, 0x55 headlight aim,
        # 0x56 radio. A 0x00-0x3F sweep therefore looks convincingly complete
        # while missing exactly the modules worth having.
        dests = VAG_ADDRESSES
    listener = can.listen(matches=[canio.Match(0x200, mask=0x700)], timeout=0.02)
    hits = []
    lo, hi = PROPOSED_RX & 0xFF, (PROPOSED_RX >> 8) & 0x0F
    for dest in dests:
        setup = bytes((dest, 0xC0, lo, hi, lo, hi, APP_KWP))
        can.send(canio.Message(id=BROADCAST, data=setup))
        t_end = time.monotonic() + wait
        while time.monotonic() < t_end:
            msg = listener.receive()
            if msg is None:
                continue
            d = getattr(msg, "data", None)
            if not d or len(d) < 2:
                continue
            print("tp20: dest %02X -> 0x%03X %s" % (dest, msg.id, _hex(d)))
            # D0 accepted, D6/D7/D8 refused. A refusal still proves the
            # module is there and speaking TP2.0, so count it as a hit and
            # close the channel it named either way.
            if 0xD0 <= d[1] <= 0xDF and len(d) >= 6 and not (d[5] & 0x10):
                tx = d[4] | ((d[5] & 0x0F) << 8)
                hits.append((dest, tx))
                can.send(canio.Message(id=tx, data=b"\xA8"))   # close it again
    listener.deinit()
    return hits


# ---- KWP2000 services -------------------------------------------------------

def ecu_ident(ch):
    """readEcuIdentification. Tries VAG ident option 9B, then 91.
    Returns a printable string or None."""
    for opt in (0x9B, 0x91):
        r = ch.request(bytes((0x1A, opt)))
        if r[:1] == b"\x5A":
            return "".join(chr(b) if 32 <= b < 127 else " " for b in r[2:]).strip()
    return None


def start_session(ch, mode=0x89):
    """startDiagnosticSession. 0x89 is the plain VAG diagnostic session.
    Not required by every ECU; measuring blocks usually work without it."""
    r = ch.request(bytes((0x10, mode)))
    return r[:1] == b"\x50"


def _ascii(b):
    return "".join(chr(x) if 32 <= x < 127 else " " for x in b).strip()


def _looks_like_vin(tok):
    # CircuitPython's str has no isalnum(), so test the characters directly.
    if len(tok) != 17:
        return False
    for c in tok:
        if not (("0" <= c <= "9") or ("A" <= c <= "Z")):
            return False
    return True


def read_vin(ch):
    """VIN via readEcuIdentification. Option 0x90 is the ISO-standard
    vehicleIdentificationNumber; if the ECU does not carry it, fall back to
    picking a 17-character run out of the VAG ident blocks."""
    for opt in (0x90, 0x9B, 0x91):
        try:
            r = ch.request(bytes((0x1A, opt)))
        except TP20Error:
            return None
        if r[:1] != b"\x5A":
            continue
        text = _ascii(r[2:])
        for token in text.split():
            if _looks_like_vin(token):
                return token
        if opt == 0x90 and len(text) >= 17:
            return text[:17]
    return None


def ident_dump(ch, options=(0x86, 0x87, 0x88, 0x89, 0x8A, 0x90, 0x91,
                            0x92, 0x94, 0x95, 0x96, 0x97, 0x9A, 0x9B)):
    """Every identification block the ECU will hand over. Diagnostic aid:
    run it once and read the serial log to see what this ECU actually
    carries, since VAG's option numbering varies by module."""
    out = {}
    for opt in options:
        try:
            r = ch.request(bytes((0x1A, opt)))
        except TP20Error as e:
            print("ident %02X: transport error (%s)" % (opt, e))
            continue
        if r[:1] == b"\x5A":
            out[opt] = _ascii(r[2:])
            print("ident %02X: %s | %s" % (opt, out[opt], _hex(r[2:])))
        elif r[:1] == b"\x7F":
            print("ident %02X: rejected %s"
                  % (opt, KWP_NRC.get(r[2] if len(r) > 2 else 0, "?")))
    return out


def dtc_str(hi, lo):
    """2-byte DTC -> SAE J2012 code (P0299) and the VAG 5-digit number."""
    letter = "PCBU"[(hi >> 6) & 0x03]
    return "%s%d%X%02X" % (letter, (hi >> 4) & 0x03, hi & 0x0F, lo)


def read_dtcs(ch):
    """readDiagnosticTroubleCodesByStatus (service 0x18), all groups.

    Returns a list of (code_string, raw_hi, raw_lo, status) or None if the
    ECU refused the request. READ ONLY -- this never clears anything.
    """
    for req in (bytes((0x18, 0x02, 0xFF, 0x00)),   # status: identified
                bytes((0x18, 0x00, 0xFF, 0x00))):  # status: any
        try:
            r = ch.request(req, timeout=1.5)
        except TP20Error as e:
            print("dtc: transport error (%s)" % e)
            return None
        if r[:1] != b"\x58":
            continue
        count = r[1] if len(r) > 1 else 0
        out = []
        body = r[2:]
        for i in range(0, min(len(body) - 2, count * 3), 3):
            out.append((dtc_str(body[i], body[i + 1]),
                        body[i], body[i + 1], body[i + 2]))
        return out
    return None


def clear_dtcs(ch, timeout=3.0):
    """KWP2000 clearDiagnosticInformation (service 0x14), all groups.

    *** THIS IS THE ONLY CALL IN THE PROJECT THAT WRITES TO THE ECU. ***

    It is a standard diagnostic operation and does not touch coding,
    adaptations or the tune, but it is not free: clearing also discards
    freeze-frame data and resets the emissions readiness monitors, which
    then need a full drive cycle to re-run. Never call it speculatively --
    read the codes first, and only clear when someone has actually asked.

    Returns True on a positive 0x54 response.
    """
    for req in (bytes((0x14, 0xFF, 0x00)), bytes((0x14, 0xFF, 0xFF))):
        try:
            r = ch.request(req, timeout=timeout)
        except TP20Error as e:
            print("dtc clear: transport error (%s)" % e)
            return False
        if r[:1] == b"\x54":
            return True
        if r[:1] == b"\x7F":
            print("dtc clear: rejected %s"
                  % KWP_NRC.get(r[2] if len(r) > 2 else 0, "?"))
    return False


def read_block(ch, n, timeout=1.0):
    """readDataByLocalIdentifier: VCDS measuring block n.
    Returns the raw field bytes, or None if the block does not exist."""
    r = ch.request(bytes((0x21, n)), timeout)
    if r[:1] == b"\x61":
        return r[2:] if len(r) >= 2 and r[1] == n else r[1:]
    return None


# ---- VCDS measuring block field decoding ------------------------------------
#
# Each field is 3 bytes: formula id, then bytes a and b. Only the common
# formulas are decoded; anything else prints raw so nothing is hidden.
# Formula 18 (a*b*0.04 mbar) is the pressure formula: any block containing
# it is a boost candidate.

# Formula id -> (callable, unit label). VAG measuring-block fields are three
# bytes: formula id, then a and b. Only formulas actually seen on this ECU
# are here; anything else prints raw so nothing is silently invented.
#
# Formulas 20, 22 and 25 were identified from this car's own data rather
# than a table, by checking the decoded value against what the sensor must
# read at warm idle:
#   20  block 032 is the documented lambda/fuel-trim group, and a*(b-128)*0.01
#       yields +1.7% and +2.5% there
#   22  a*b*0.001 gives 1.275 ms injection time, right for idle
#   25  b*1.421+a/182 gives 3.3 g/s airflow, right for idle on a 2.0
#   27  a*(b-128)*0.01 degrees of ignition advance. Confirmed, not guessed:
#       block 003 field 4 reads f27(4B,7E) = 75*(126-128)*0.01 = -1.5, and
#       generic EOBD PID 0x0E (timing advance, A/2-64) independently
#       reported -1.5 at the same moment.
#   36  a*256+b, a plain 16-bit counter. Block 075 field 8 holds 18588,
#       and the car's odometer reads 185885 km, so it is tenths of the
#       odometer -- units of 10 km.
_UNITS = {
    1: "rpm", 2: "%", 3: "deg", 5: "C", 6: "V", 7: "km/h",
    15: "ms", 18: "mbar", 20: "%", 21: "V", 22: "ms", 25: "g/s",
    27: "deg", 33: "%", 83: "bar",
}


def value_of(fid, a, b):
    """Numeric value of one measuring-block field, or None if the formula
    is not one we know how to read."""
    try:
        if fid == 1:
            return a * b * 0.2                      # engine speed
        if fid == 2:
            return a * b * 0.002
        if fid == 3:
            return a * b * 0.002
        if fid == 4:
            return abs(b - 127) * 0.01 * a          # ignition angle
        if fid == 5:
            return a * (b - 100) * 0.1              # temperature
        if fid in (6, 21):
            return a * b * 0.001                    # voltage
        if fid == 7:
            return a * b * 0.01                     # road speed
        if fid == 8:
            return a * b * 0.1
        if fid == 15:
            return a * b * 0.01
        if fid == 18:
            return a * b * 0.04                     # pressure, mbar
        if fid == 20:
            return a * (b - 128) * 0.01             # fuel trim, signed
        if fid == 22:
            return a * b * 0.001                    # injection time
        if fid == 25:
            return b * 1.421 + a / 182.0            # mass air flow
        if fid == 27:
            return a * (b - 128) * 0.01             # ignition advance, deg
        if fid == 33:
            return 100.0 * b / a if a else 0.0
        if fid == 36:
            return a * 256 + b
        # --- added 2026-08-24 from the jazdw/vag-blocks reference table.
        # Undecoded formulas were NOT harmless: field_at returned None, the
        # caller kept the previous value, and the channel sat at its 0.0
        # initialiser forever. The knock channels did exactly that for a
        # whole drive and read a convincing, meaningless zero.
        if fid == 23:
            return b * a / 256.0                 # %
        if fid == 26:
            return float(b - a)                  # degC
        if fid == 34:
            return (b - 128) * 0.01 * a          # kW
        if fid == 54:
            return float(a * 256 + b)            # count
        if fid == 94:
            return a * (b / 50.0 - 1.0)          # Nm
        # --- formula 83 (0x53), added 2026-08-24. NOT from a public table:
        # it is absent from jazdw/vag-blocks, which implements the common
        # ids and falls through to raw for the rest. Derived on the car
        # instead, the same way formula 27 was.
        #
        # Block 106 fields 0 and 1 carry a 16-bit pair -- field 0 pinned at
        # 4000 while field 1 wandered 3964..4051 around it, which is a
        # setpoint and a regulated actual. At the same warm idle, EOBD PID
        # 0x23 (fuel rail GAUGE pressure, defined in 10 kPa units) read
        # 3910 kPa = 39.10 bar, and barometric was 1.005 bar:
        #
        #     4007 counts * 0.01 = 40.07 bar absolute
        #     40.07 - 1.005      = 39.07 bar gauge   vs 39.10 measured
        #
        # 0.03 bar apart, so the scale is 0.01 bar per count and the block
        # reports ABSOLUTE. Absolute is also what forces the reading: the
        # PID's 39.10 sat BELOW field 1's entire oscillation range, which a
        # same-units pair could not do.
        if fid == 83:
            return ((a << 8) | b) * 0.01         # bar ABSOLUTE
    except Exception:
        pass
    return None


def _field(fid, a, b):
    if fid == 16:
        return "bits %02X/%02X" % (a, b)
    if fid == 17:
        try:
            return "%c%c" % (a, b)
        except Exception:
            pass
    if fid == 4:
        v = value_of(fid, a, b)
        return "%.1f %s" % (v, "BTDC" if b < 128 else "ATDC")
    v = value_of(fid, a, b)
    if v is None:
        return "f%02d(%02X,%02X)" % (fid, a, b)
    unit = _UNITS.get(fid, "")
    return ("%.0f %s" % (v, unit)).strip() if unit in ("rpm", "mbar", "km/h") \
        else ("%.3f %s" % (v, unit)).strip() if unit == "V" \
        else ("%.1f %s" % (v, unit)).strip()


def field_at(data, index):
    """Numeric value of field `index` (0-based) in a raw block, or None."""
    i = index * 3
    if i + 2 >= len(data):
        return None
    return value_of(data[i], data[i + 1], data[i + 2])


def decode_fields(data):
    out = []
    for i in range(0, len(data) - 2, 3):
        out.append(_field(data[i], data[i + 1], data[i + 2]))
    return out


def pressure_value(data, which=0):
    """Value in mbar of the (which)th formula-18 field, or None."""
    seen = 0
    for i in range(0, len(data) - 2, 3):
        if data[i] == 18:
            if seen == which:
                return data[i + 1] * data[i + 2] * 0.04
            seen += 1
    return None


def has_pressure(data):
    return pressure_value(data) is not None
