"""
uds.py -- Minimal ISO-TP (ISO 15765-2) + UDS (ISO 14229) client for CircuitPython.

Target: Adafruit Feather M4 CAN Express (SAME51) on a VAG diagnostic CAN bus.
READ ONLY. This module only ever sends diagnostic read requests. It never
writes, codes, or flashes anything.

Wiring, OBD-II J1962 connector:
    pin 6   CAN-H   -> Feather CANH
    pin 14  CAN-L   -> Feather CANL
    pin 4/5 GND     -> Feather GND
    pin 16  +12V    -> LEAVE DISCONNECTED. Power the Feather from USB.

Check that the board's 120 ohm termination is NOT enabled. The vehicle bus is
already terminated at both ends; a third terminator drags it to 40 ohm and
produces error frames.

Addressing: defaults to the classic 11-bit 0x7E0/0x7E8 pair. Cars of the
2008-2010 VAG transition years serve only EOBD there and carry manufacturer
UDS on 29-bit IDs instead (0x17FC00nn/0x17FE00nn); pass req_id/resp_id and
extended=True for those, plus can= to reuse the already-built CAN object
(only one canio.CAN may exist per board).
"""

import time
import board
import digitalio
import canio

# ---- VAG diagnostic CAN addressing, 11-bit ----
REQ_ID = 0x7E0          # physical request, engine ECU
RESP_ID = 0x7E8         # engine ECU response
FUNCTIONAL_ID = 0x7DF   # functional broadcast, all emissions ECUs
BITRATE = 500_000       # diagnostic + powertrain CAN on PQ35
PAD = 0x55              # VAG convention for unused frame bytes

# ---- UDS service IDs ----
SID_SESSION = 0x10
SID_TESTER_PRESENT = 0x3E
SID_READ_DID = 0x22
NEGATIVE = 0x7F

NRC = {
    0x11: "serviceNotSupported",
    0x12: "subFunctionNotSupported",
    0x13: "badMessageLength",
    0x14: "responseTooLong",
    0x22: "conditionsNotCorrect",
    0x31: "requestOutOfRange",
    0x33: "securityAccessDenied",
    0x35: "invalidKey",
    0x78: "responsePending",
    0x7E: "subFuncNotInSession",
    0x7F: "serviceNotInSession",
}


class Bus:
    def __init__(self, bitrate=BITRATE, req_id=REQ_ID, resp_id=RESP_ID,
                 can=None, extended=False):
        self.req_id = req_id
        self.resp_id = resp_id
        self.extended = extended
        if can is None:
            self._enable_transceiver()
            can = canio.CAN(
                rx=board.CAN_RX, tx=board.CAN_TX, baudrate=bitrate,
                auto_restart=True,
            )
        self.can = can
        # Short listener timeout. We manage our own deadlines above this.
        self.listener = self.can.listen(
            matches=[canio.Match(resp_id, extended=extended)], timeout=0.02
        )

    @staticmethod
    def _enable_transceiver():
        # The Feather M4 CAN runs its transceiver off an onboard 5V boost
        # converter that is OFF at reset, and the transceiver comes up in
        # standby. Miss either and the bus is silent with no error at all.
        if hasattr(board, "CAN_STANDBY"):
            standby = digitalio.DigitalInOut(board.CAN_STANDBY)
            standby.switch_to_output(False)
        if hasattr(board, "BOOST_ENABLE"):
            boost = digitalio.DigitalInOut(board.BOOST_ENABLE)
            boost.switch_to_output(True)
        time.sleep(0.05)

    # ---- low level ----

    def _waiting(self):
        n = self.listener.in_waiting
        return n() if callable(n) else n

    def _drain(self):
        while self._waiting():
            self.listener.receive()

    def _send(self, data, arb_id=None):
        frame = bytearray(8)
        for i in range(8):
            frame[i] = PAD
        frame[0:len(data)] = bytes(data)
        self.can.send(
            canio.Message(
                id=self.req_id if arb_id is None else arb_id,
                data=bytes(frame),
                extended=self.extended if arb_id is None else False,
            )
        )

    # ---- ISO-TP ----

    def request(self, payload, timeout=0.15, functional=False):
        """Send a UDS request of up to 7 bytes, return the assembled response
        payload as bytes, or None on timeout.

        Handles single-frame and multi-frame responses, and transparently
        waits through responsePending (NRC 0x78) which the ECU uses to ask
        for more time.
        """
        if len(payload) > 7:
            raise ValueError("payload > 7 bytes would need multi-frame TX")

        self._drain()
        sf = bytearray([len(payload)])
        sf.extend(payload)
        self._send(sf, arb_id=FUNCTIONAL_ID if functional else None)

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            msg = self.listener.receive()
            if msg is None:
                continue
            d = getattr(msg, "data", None)   # skip RemoteTransmissionRequest
            if d is None or len(d) < 2:
                continue

            pci = d[0] >> 4
            if pci == 0:
                # single frame: low nibble is the payload length
                n = d[0] & 0x0F
                if n == 0 or n > 7:
                    continue
                resp = bytes(d[1:1 + n])

            elif pci == 1:
                # first frame of a multi-frame response
                total = ((d[0] & 0x0F) << 8) | d[1]
                buf = bytearray(d[2:8])
                # flow control: clear to send, block size 0, STmin 0
                self._send(bytes([0x30, 0x00, 0x00]))
                seq = 1
                while len(buf) < total and time.monotonic() < deadline:
                    m = self.listener.receive()
                    if m is None:
                        continue
                    c = getattr(m, "data", None)
                    if c is None or len(c) < 1 or (c[0] >> 4) != 2:
                        continue
                    if (c[0] & 0x0F) != (seq & 0x0F):
                        continue             # out of sequence, drop it
                    buf.extend(c[1:8])
                    seq += 1
                resp = bytes(buf[:total])

            else:
                continue                     # flow control frame or junk

            if len(resp) >= 3 and resp[0] == NEGATIVE and resp[2] == 0x78:
                deadline = time.monotonic() + timeout   # ECU wants more time
                continue
            return resp

        return None

    # ---- UDS services ----

    def read_did(self, did, timeout=0.12):
        """Service 0x22 ReadDataByIdentifier.

        Returns (status, value):
            ("ok", bytes)     positive response, value is the raw data
            ("nrc", int)      negative response, value is the NRC
            ("timeout", None) no reply at all
        """
        resp = self.request(
            bytes([SID_READ_DID, (did >> 8) & 0xFF, did & 0xFF]), timeout=timeout
        )
        if resp is None:
            return ("timeout", None)
        if resp[0] == SID_READ_DID + 0x40:          # 0x62 positive
            return ("ok", resp[3:])                 # strip 62 + DID echo
        if resp[0] == NEGATIVE:
            return ("nrc", resp[2] if len(resp) > 2 else 0)
        return ("nrc", 0)

    def start_extended_session(self, timeout=0.5):
        resp = self.request(bytes([SID_SESSION, 0x03]), timeout=timeout)
        return resp is not None and len(resp) > 0 and resp[0] == 0x50

    def tester_present(self):
        # 0x80 sets suppressPosRspMsgIndicationBit so the ECU stays quiet
        self._send(bytes([0x02, SID_TESTER_PRESENT, 0x80]))

    def probe(self):
        """Work out what the ECU actually speaks.

        Returns a dict: obd2 (bus alive + generic OBD-II works), uds (service
        0x22 works), vin, detail.
        """
        out = {"obd2": False, "uds": False, "vin": None, "detail": ""}

        # Generic OBD-II mode 01 PID 00. Answers on both UDS and older
        # KWP-over-CAN ECUs, so a reply here proves the wiring and bitrate
        # are right even if UDS turns out to be unsupported.
        r = self.request(bytes([0x01, 0x00]), timeout=0.6, functional=True)
        if r is not None and len(r) >= 2 and r[0] == 0x41:
            out["obd2"] = True

        # UDS: DID F190 is the VIN. A 0x62 back proves service 0x22 works.
        st, val = self.read_did(0xF190, timeout=0.6)
        if st == "ok":
            out["uds"] = True
            out["vin"] = "".join(chr(b) for b in val if 32 <= b < 127)
        elif st == "nrc":
            out["detail"] = "F190 NRC %02X %s" % (val, NRC.get(val, "?"))
        else:
            out["detail"] = "F190 no reply"

        return out

    @property
    def state(self):
        return self.can.state
