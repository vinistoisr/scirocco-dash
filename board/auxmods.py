"""
auxmods.py -- visit the DSG and DCC modules without starving the engine.

Holding a second TP2.0 channel open collapses the engine stream to 4% of
baseline (measured 2026-08-23: 19.9 -> 0.9 Hz, full recovery on close), so
this opens ONE aux channel at a time, briefly, a few times a minute.
Visit costs, measured: 0x02 gear 481 ms; 0x0C chassis 990 ms, and 0x0C
refuses rapid reconnects, hence its long minimum gap.

Steering came OFF this poller: the head unit's CAN box provides it at
~5 Hz for free (docs/CANBOX.md) against 470 ms per OBD visit. The 0x09
decode branch below is kept as a fallback should the CAN box route die.

This docstring is deliberately short: it is compiled ON the SAME51, where
heap is the scarcest resource we have. Full notes: docs/MODULES.md.
*** READ ONLY: only KWP 0x21 is ever sent. ***
"""

import time
import tp20

# addr -> (blocks to read, minimum seconds between visits)
# 0x0C is throttled hard: it failed two of three rapid reconnects.
AUX_PLAN = (
    (0x02, (3,), 4.0),        # DSG: gear letter (block 003, formula 17)
    (0x0C, (9, 11), 20.0),    # DCC: 3 level sensors + 4 damper channels
)

VISIT_EVERY_S = 5.0           # how often ANY aux visit may happen at all


class AuxReader:
    """Round-robins the aux modules, one visit at a time, never holding a
    channel open alongside the engine's."""

    def __init__(self, can, engine_ch, listener=None):
        self.can = can
        self.eng = engine_ch
        self.listener = listener if listener is not None else engine_ch.listener
        self._i = 0
        self._next_visit = time.monotonic() + VISIT_EVERY_S
        self._last = {}                      # addr -> monotonic of last visit
        self.visits = 0
        self.fails = 0

        # the values, with the time each was last refreshed so the log can
        # tell "0 because it is really 0" from "0 because we have not looked"
        self.gear = ""                       # "P" "R" "N" "D" "1".."6"
        self.steer_deg = 0.0                 # raw counts until calibrated
        self.height = [0.0, 0.0, 0.0]        # volts
        self.damper = [0, 0, 0, 0]           # raw
        self.age = {}                        # name -> seconds since refresh
        self._stamp = {}

    # ---------------------------------------------------------------- api
    def due(self):
        return time.monotonic() >= self._next_visit

    def visit(self):
        """Do at most ONE aux visit. Returns the address visited, or None.

        The engine channel is closed first and reopened afterwards --
        ALWAYS, including on every error path, because leaving it shut
        would silently kill the gauge."""
        now = time.monotonic()
        if now < self._next_visit:
            return None
        self._next_visit = now + VISIT_EVERY_S

        pick = None
        for _ in range(len(AUX_PLAN)):
            addr, blocks, min_gap = AUX_PLAN[self._i]
            self._i = (self._i + 1) % len(AUX_PLAN)
            if now - self._last.get(addr, -1e9) >= min_gap:
                pick = (addr, blocks)
                break
        if pick is None:
            return None
        addr, blocks = pick
        self._last[addr] = now

        try:
            self.eng.disconnect()
        except Exception:
            pass

        aux = tp20.Channel(self.can, dest=addr, listener=self.listener)
        try:
            # assume_clean: this poller closed every channel politely a
            # moment ago, so the 200 ms precautionary teardown would be
            # pure waste. tries=2/attempts=1 puts the retry INSIDE the
            # handshake, where the D7-refusal handler cures a stale
            # channel inline, instead of paying the outer 0.4 s backoff.
            aux.connect(timeout=0.8, tries=2, attempts=1,
                        assume_clean=True)
            for b in blocks:
                try:
                    d = tp20.read_block(aux, b, timeout=0.4)
                except tp20.TP20Error:
                    continue
                if d:
                    self._store(addr, b, d)
            self.visits += 1
        except tp20.TP20Error:
            self.fails += 1
        finally:
            try:
                aux.disconnect()
            except Exception:
                pass
            # the engine stream matters more than any aux value
            try:
                if not self.eng.connected:
                    self.eng.connect(assume_clean=True)
            except tp20.TP20Error:
                pass
        return addr

    # ------------------------------------------------------------ decode
    def _store(self, addr, blk, d):
        now = time.monotonic()
        if addr == 0x02 and blk == 3:
            # formula 17 is two ASCII characters; the gear is the second
            if len(d) >= 3 and d[0] == 17:
                try:
                    g = ("%c" % d[2]).strip()
                except Exception:
                    g = ""
                self.gear = g
                self._stamp["gear"] = now
        elif addr == 0x09 and blk == 7:
            # field 1, formula 81: signed 16-bit (a<<8)|b. Proven by a
            # lock-to-lock sweep: +11771 / -10022 / ~0 at centre.
            if len(d) >= 3:
                raw = (d[1] << 8) | d[2]
                if raw > 32767:
                    raw -= 65536
                self.steer_deg = raw
                self._stamp["steer"] = now
        elif addr == 0x0C and blk == 9:
            # three level sensors, formula 6 (a*b*0.001 V). Field 4 is tied
            # to the 5 V rail and is NOT a sensor -- ignored deliberately.
            for i in range(3):
                o = i * 3
                if len(d) >= o + 3 and d[o] == 6:
                    self.height[i] = d[o + 1] * d[o + 2] * 0.001
            self._stamp["height"] = now
        elif addr == 0x0C and blk == 11:
            for i in range(4):
                o = i * 3
                if len(d) >= o + 3:
                    self.damper[i] = d[o + 2]
            self._stamp["damper"] = now

    def age_of(self, name):
        t = self._stamp.get(name)
        return None if t is None else time.monotonic() - t
