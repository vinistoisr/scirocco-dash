"""
scanalyze.py -- read an enginescan capture and name the useful fields.

Phase 1 answers "what blocks exist and what do they read at idle". Phase 2
answers the only question that actually identifies a channel: "what moved,
and did it move WITH something we already know".

The formulas are not reimplemented here. board/tp20.py is imported directly
so the analysis and the firmware can never drift apart -- a duplicated
formula table is how you end up confidently decoding a field two different
ways. tp20 imports canio (CircuitPython only), so that one name is stubbed.

    python tools/scanalyze.py captures/enginescan-*.log
    python tools/scanalyze.py --phase2 captures/enginescan-*.log
"""

import argparse
import os
import re
import sys
import types

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_REPO, "board"))


class _Any:
    """Stand-in for canio's classes: tp20 only touches them at call time."""

    def __init__(self, *a, **k):
        pass

    def __getattr__(self, name):
        return _Any()


_canio = types.ModuleType("canio")
_canio.__getattr__ = lambda name: _Any
sys.modules.setdefault("canio", _canio)

import tp20  # noqa: E402

BLK_RE = re.compile(r"^\s*BLK (\d+)\s+([0-9A-F]*)\s")
S_RE = re.compile(r"^S (\d+) T ([\d.]+)")
M_RE = re.compile(r"^M ([\d.]+) ([0-9A-F]+)")
B_RE = re.compile(r"^B (\d+) ([0-9A-F-]*)")


def fields(hexs):
    """[(formula_id, a, b, decoded_or_None), ...] for one raw block."""
    d = bytes.fromhex(hexs)
    out = []
    for i in range(0, len(d) - 2, 3):
        fid, a, b = d[i], d[i + 1], d[i + 2]
        out.append((fid, a, b, tp20.value_of(fid, a, b)))
    return out


# What each missing channel must look like on a warm, running 2.0 TSI.
# Deliberately WIDE: this stage is meant to shortlist, not to conclude.
# Nothing here is proof -- a field is only identified once it MOVES with
# the thing it claims to measure (phase 2).
WANT = (
    ("catalyst_temp", lambda v: 250.0 <= v <= 1000.0,
     "hundreds of degC once the cat is lit"),
    ("rail_pressure_bar", lambda v: 30.0 <= v <= 250.0,
     "GDI rail runs tens of bar at idle"),
    ("rail_pressure_kpa", lambda v: 3000.0 <= v <= 25000.0,
     "same thing if the block reports kPa/mbar"),
    ("lambda", lambda v: 0.75 <= v <= 1.30,
     "closed loop sits on 1.00 -- WEAK on its own, many fields do"),
    ("pedal_pct", lambda v: 0.0 <= v <= 3.0,
     "must be ~0 with your foot off it"),
)


def phase1(path):
    seen = {}
    for line in open(path, "r", errors="replace"):
        m = BLK_RE.match(line)
        if m and m.group(2):
            seen[int(m.group(1))] = m.group(2)

    print("PHASE 1 -- %d blocks answered\n" % len(seen))
    hits = {}
    for blk in sorted(seen):
        fs = fields(seen[blk])
        cells = []
        for i, (fid, a, b, v) in enumerate(fs):
            cells.append("f%-3d %02X%02X %s" % (
                fid, a, b, ("%10.3f" % v) if v is not None else "         ?"))
            if v is None:
                continue
            for name, test, _ in WANT:
                try:
                    ok = test(v)
                except Exception:
                    ok = False
                if ok:
                    hits.setdefault(name, []).append((blk, i, fid, v))
        print("  BLK %3d  %s" % (blk, "  |  ".join(cells)))

    print("\n" + "=" * 70)
    print("SHORTLIST -- plausible at warm idle. NOT identification.")
    print("=" * 70)
    for name, _, why in WANT:
        rows = hits.get(name, [])
        print("\n%s  (%s)" % (name, why))
        if not rows:
            print("    nothing in range")
        for blk, i, fid, v in rows[:14]:
            print("    block %3d field %d  formula %-3d  %.3f" % (blk, i, fid, v))
        if len(rows) > 14:
            print("    ... and %d more" % (len(rows) - 14))
    return seen


def phase2(path):
    """Time series per (block, field), and how each one tracks rpm.

    Correlation against rpm is the discriminator. Pedal and rail pressure
    track it hard on a rev; catalyst temperature does not track it at all
    but drifts on its own timescale; lambda swings the WRONG way on
    overrun fuel cut, which is itself a signature.
    """
    series = {}
    rpms = []
    t = 0.0
    rpm = None
    load = None
    boost = None
    for line in open(path, "r", errors="replace"):
        m = M_RE.match(line)
        if m:
            # The context line. Every B line after this one, until the next
            # M, was sampled at THIS rpm -- not at the sweep's start.
            t = float(m.group(1))
            fs = fields(m.group(2))
            rpm = fs[0][3] if len(fs) > 0 else None
            load = fs[1][3] if len(fs) > 1 else None
            boost = fs[3][3] if len(fs) > 3 else None
            if rpm is not None:
                rpms.append((t, rpm))
            continue
        if S_RE.match(line):
            continue
        m = B_RE.match(line)
        if not m or rpm is None:
            continue
        blk, hx = int(m.group(1)), m.group(2)
        if not hx or hx == "--":
            continue
        for i, (fid, a, b, v) in enumerate(fields(hx)):
            if v is None:
                continue
            series.setdefault((blk, i, fid), []).append((t, rpm, v, load, boost))

    if not series:
        print("no phase 2 samples in this capture")
        return

    print("PHASE 2 -- %d sweeps, rpm %.0f..%.0f\n"
          % (len(rpms), min(r for _, r in rpms), max(r for _, r in rpms)))

    rows = []
    for key, pts in series.items():
        vs = [p[2] for p in pts]
        lo, hi = min(vs), max(vs)
        if hi == lo:
            rows.append((key, lo, hi, 0.0, len(set(vs))))
            continue
        rs = [p[1] for p in pts]
        ls = [p[3] for p in pts if p[3] is not None]
        # Correlate against LOAD as well as rpm: rail pressure and lambda
        # track load, not engine speed. A field can be flat against rpm and
        # still be exactly what we are looking for.
        cl = corr([p[1] for p in pts], vs)
        if len(ls) == len(pts):
            cl2 = corr(ls, vs)
            if abs(cl2) > abs(cl):
                cl = cl2
        rows.append((key, lo, hi, cl, len(set(vs))))

    rows.sort(key=lambda r: -abs(r[3]))
    print("%-22s %12s %12s %8s %7s" % ("block/field/formula", "min", "max",
                                       "r(best)", "levels"))
    for (blk, i, fid), lo, hi, c, n in rows:
        if hi == lo:
            continue
        print("%-22s %12.3f %12.3f %8.3f %7d"
              % ("blk %d fld %d f%d" % (blk, i, fid), lo, hi, c, n))

    dead = [k for k, lo, hi, c, n in rows if hi == lo]
    print("\n%d fields never changed (constants at this throttle): %s"
          % (len(dead), ", ".join("%d/%d" % (b, i) for b, i, _ in dead[:25])))


def corr(x, y):
    n = len(x)
    mx, my = sum(x) / n, sum(y) / n
    sxy = sum((a - mx) * (b - my) for a, b in zip(x, y))
    sxx = sum((a - mx) ** 2 for a in x)
    syy = sum((b - my) ** 2 for b in y)
    if sxx <= 0 or syy <= 0:
        return 0.0
    return sxy / (sxx * syy) ** 0.5


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("log")
    ap.add_argument("--phase2", action="store_true")
    a = ap.parse_args()
    if a.phase2:
        phase2(a.log)
    else:
        phase1(a.log)
