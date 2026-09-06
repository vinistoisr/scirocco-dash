"""
hunt.py -- find which CAN addressing carries manufacturer UDS.

The 2009 Scirocco sits in VAG's transition years: EOBD answers on 11-bit
0x7E0 but neither UDS-on-0x7E0 nor KWP/TP2.0 exists on the diagnostic port.
Cars of this era carry manufacturer UDS on other CAN IDs instead. This
module sprays a harmless default-session request (10 01) across the
candidate schemes and reports everything that answers:

    1. every 11-bit ID 0x700-0x7FF
    2. VAG 29-bit scheme: request 0x17FC00nn, response 0x17FE00nn
       (nn = the module's diagnostic address, engine = 01)
    3. ISO 15765-4 fixed addressing: request 0x18DAttF1, response 0x18DAF1tt

READ ONLY in effect: 10 01 asks a module for the session it is already in.
"""

import time
import canio

PAD = 0x55


def _frame():
    f = bytearray([PAD] * 8)
    f[0:3] = b"\x02\x10\x01"
    return bytes(f)


def sweep(can, dwell=0.02):
    """Returns a list of (req_id, resp_id, extended, resp_data) hits."""
    listener = can.listen(timeout=0.005)
    hits = []

    def poke(req_id, extended):
        try:
            can.send(canio.Message(id=req_id, data=_frame(), extended=extended))
        except Exception as e:
            print("hunt: send 0x%X failed (%s)" % (req_id, e))
            return
        t_end = time.monotonic() + dwell
        while time.monotonic() < t_end:
            msg = listener.receive()
            if msg is None:
                continue
            d = getattr(msg, "data", None)
            if not d:
                continue
            print("hunt: req 0x%X -> 0x%X %s"
                  % (req_id, msg.id, "".join("%02X" % x for x in d)))
            hits.append((req_id, msg.id, bool(extended), bytes(d)))

    print("hunt: phase 1, 11-bit 0x700-0x7FF")
    for i in range(0x700, 0x800):
        poke(i, False)
    print("hunt: phase 2, vag 29-bit 0x17FC00xx")
    for n in range(0x00, 0x80):
        poke(0x17FC0000 + n, True)
    print("hunt: phase 3, iso fixed 0x18DAttF1")
    for ta in range(0x00, 0x100):
        poke(0x18DA00F1 | (ta << 8), True)
    listener.deinit()
    print("hunt: done, %d replies" % len(hits))
    return hits
