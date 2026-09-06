"""
boostfind.py -- identify the real boost channel by swing, not by guesswork.

The measuring-block scan found seven blocks on this MED17.5 carrying a
pressure field (formula 18, a*b*0.04 mbar). At idle they cluster into two
families: ~350 mbar (post-throttle manifold pressure, i.e. vacuum) and
~1000 mbar (pre-throttle charge pressure, i.e. ambient because the turbo is
not making boost yet). Both climb under load, so idle values alone cannot
tell you which field is the gauge signal.

So: poll the candidates continuously, track min/max for every pressure
field, and rank by swing. Rev the engine and the boost channel is whichever
field moves furthest above ambient. On a stock 2.0 TSI expect roughly:

    idle          ~1000 mbar absolute (no boost)
    full load     ~2000-2400 mbar absolute (about 1.0-1.4 bar of boost)

Gauge boost in bar = (absolute mbar - ambient mbar) / 1000.

READ ONLY: service 0x21 measuring-block reads only.
"""

import time
import tp20

# Ross-Tech documents groups 110-119 for load and boost on VAG engines:
#   115 = [charge pressure control, rpm, engine load, boost specified,
#          boost actual]
#   118 = [charge pressure control, rpm, intake air temp, N75 duty,
#          boost actual]
# Our decode of 118 at idle came back "800 rpm | 53.0 C | 2.0 % | 990 mbar",
# which matches that layout exactly and confirms the ~990 mbar fields are
# charge pressure sitting at atmospheric with the turbo off boost. 115 is
# listed first because it is the only block carrying BOTH specified and
# actual, which is the pair worth putting on the gauge.
CANDIDATES = (115, 118, 111, 119, 113, 117, 78)

SETTLE = 0.0        # no artificial delay; the ECU paces us


class Track:
    """min/max/last for one (block, field) pressure channel."""

    __slots__ = ("block", "idx", "lo", "hi", "last")

    def __init__(self, block, idx, v):
        self.block = block
        self.idx = idx
        self.lo = self.hi = self.last = v

    def update(self, v):
        self.last = v
        if v < self.lo:
            self.lo = v
        if v > self.hi:
            self.hi = v

    @property
    def swing(self):
        return self.hi - self.lo


def pressure_fields(data):
    """[(field_index, mbar), ...] for every formula-18 field in a block."""
    out = []
    for i in range(0, len(data) - 2, 3):
        if data[i] == 18:
            out.append((i // 3, data[i + 1] * data[i + 2] * 0.04))
    return out


def run(ch, seconds=45.0, blocks=CANDIDATES, verbose=True):
    """Poll `blocks` for `seconds`, tracking every pressure field.
    Returns the tracks sorted by swing, widest first."""
    tracks = {}
    t0 = time.monotonic()
    t_end = t0 + seconds
    last_print = 0.0
    cycles = 0

    while time.monotonic() < t_end:
        for n in blocks:
            try:
                if not ch.connected:
                    ch.connect()
                data = tp20.read_block(ch, n, timeout=0.4)
            except tp20.TP20Error as e:
                print("boostfind: block %d transport error (%s)" % (n, e))
                continue
            if data is None:
                continue
            for idx, mbar in pressure_fields(data):
                key = (n, idx)
                if key in tracks:
                    tracks[key].update(mbar)
                else:
                    tracks[key] = Track(n, idx, mbar)
        cycles += 1

        now = time.monotonic()
        if verbose and now - last_print >= 1.0:
            last_print = now
            live = sorted(tracks.values(), key=lambda t: (t.block, t.idx))
            print("t+%04.1f " % (now - t0)
                  + "  ".join("%d.%d=%.0f" % (t.block, t.idx, t.last) for t in live))

    ranked = sorted(tracks.values(), key=lambda t: t.swing, reverse=True)
    print("=" * 58)
    print("boostfind: %d cycles in %.1fs (%.0f ms/cycle)"
          % (cycles, time.monotonic() - t0,
             1000.0 * (time.monotonic() - t0) / max(1, cycles)))
    print("block.field     min      max    swing")
    for t in ranked:
        print("   %3d.%d   %7.0f  %7.0f  %7.0f mbar"
              % (t.block, t.idx, t.lo, t.hi, t.swing))
    print("=" * 58)
    if ranked and ranked[0].swing > 200:
        w = ranked[0]
        print("boost channel: block %d field %d, %.0f-%.0f mbar (%.2f bar gauge)"
              % (w.block, w.idx, w.lo, w.hi, w.swing / 1000.0))
    else:
        print("no field swung meaningfully -- was the engine revved?")
    return ranked
