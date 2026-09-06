"""Entry point: open the engine channel and rank pressure fields by swing."""

import time
import uds
import tp20
import boostfind

print("=" * 58)
print("BOOST FINDER -- read only")
print("=" * 58)

bus = uds.Bus()
bus.listener.deinit()

ch = tp20.Channel(bus.can)
ch.connect()
print("tp20: channel open, tx 0x%03X rx 0x%03X" % (ch.tx_id, ch.rx_id))

# Identification is a nice-to-have; never let it cost us the run.
try:
    print("tp20: ecu:", tp20.ecu_ident(ch))
except tp20.TP20Error as e:
    print("tp20: ident skipped (%s)" % e)
if not ch.connected:
    ch.connect()

print("")
print(">>> Gentle throttle blips over the next 45 seconds. <<<")
print(">>> Light taps to ~2000 rpm are enough -- manifold  <<<")
print(">>> pressure swings hundreds of mbar off idle, so    <<<")
print(">>> no need to make any real noise.                  <<<")
print("")
time.sleep(2)

boostfind.run(ch, seconds=45.0)

ch.disconnect()
while True:
    time.sleep(1)
