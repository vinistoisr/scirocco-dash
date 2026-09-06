"""
modscan.py -- which modules answer TP 2.0, using the setup frame that works.

The earlier sweep in tpdiag.py used the old, broken setup frame, so its
results are not trustworthy. Redo it with the corrected frame, then open a
channel to every module that answers and ask for its identification, which
is what actually tells us where the odometer lives.

VAG addresses are hex bytes and match the numbers VCDS shows:
    01 engine   02 auto trans   03 ABS      08 HVAC     09 central elec
    15 airbag   16 steering     17 instruments          19 gateway
    25 immobiliser              46 comfort  56 radio

Read only.
"""

import time
import uds
import tp20

bus = uds.Bus()
bus.listener.deinit()
can = bus.can

print("=" * 60)
print("MODULE SWEEP -- corrected TP 2.0 setup frame")
print("=" * 60)

found = []
for dest in range(0x00, 0x60):
    ch = tp20.Channel(can, dest=dest)
    try:
        tx, rx = ch.connect(timeout=0.35, tries=1, attempts=1)
    except tp20.TP20Error:
        continue
    ident = None
    try:
        ident = tp20.ecu_ident(ch)
    except tp20.TP20Error:
        pass
    print("dest 0x%02X  tx 0x%03X  %s" % (dest, tx, ident or "(no ident)"))
    found.append(dest)
    try:
        ch.disconnect()
    except Exception:
        pass
    time.sleep(0.15)

print("-" * 60)
print("modules answering: %s" % " ".join("0x%02X" % d for d in found))
print("=" * 60)
while True:
    time.sleep(1)
