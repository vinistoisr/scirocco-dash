"""make_testdata.py -- fake drive sessions in the R2 layout, for wrangler dev.

The deck tee (deck/tee.py) is being written in parallel, so nothing real
exists to point the dashboard at yet. This script fabricates two sessions
that follow the session-files contract in docs/PLAN-deck.md:

    drive.csv    t_unix first, then the 35 channel columns named after
                 board/scirocco_realdash.xml, then the four GPS columns
    bursts.csv   the same row with a leading `segment` column, at ~14 Hz
    meta.json    uploaded LAST -- the commit marker

Two sessions, because the dashboard has two interesting paths:
  2026-08-15_0812  quiet morning errand -- no pull ever trips the burst
                   trigger, bursts.csv.gz is header-only (has_bursts must
                   read false, the chart must not draw bands)
  2026-08-21_1743  spirited evening -- three full-throttle pulls, each one
                   tripping the burst trigger from the PLAN (ON: rpm>=3000
                   or load>=80% or boost>=+0.30 bar; OFF: all of rpm<2500,
                   load<60, boost<0.15 sustained 3 s; 5 s pre-roll)

The burst segmentation here is not hand-placed: the whole drive is simulated
at 14 Hz and the PLAN's actual trigger runs over it, so the segments land
exactly where the real tee's would. The physics is only coherent, not
correct -- gears, turbo lag, warm-up curves and knock events exist so the
charts look like a real log, nothing more.

Usage (from cloud/):
    python testdata/make_testdata.py
    # then load the printed `wrangler r2 object put --local` commands, or:
    #   powershell testdata/out/put_local.ps1     (Windows)
    #   sh testdata/out/put_local.sh              (POSIX)
    npx wrangler dev

Stdlib only, deterministic (fixed seeds), safe to re-run.
"""

import gzip
import json
import math
import os
import random
from datetime import datetime

DT = 1.0 / 14.0          # acquisition period; 14 steps == exactly 1 s
BUCKET = "scirocco-drives"

# Column layout per the PLAN contract: t_unix, 35 channels named from
# scirocco_realdash.xml (Fault 1-4 are not logged as columns -- fault_count
# on 0xC82 plus meta.json cover DTCs), gps columns last. Each entry is
# (name, decimals). bursts.csv prepends `segment`.
COLS = [
    ("t_unix", 3),
    ("rpm", 0), ("map_kpa", 1), ("load_pct", 1), ("iat_k", 1),
    ("boost_bar", 3), ("boost_target_bar", 3), ("boost_peak_bar", 3),
    ("n75_pct", 1),
    ("baro_kpa", 1), ("fault_count", 0), ("reconnects", 0), ("sample_hz", 1),
    ("coolant_c", 1), ("oil_c", 1), ("battery_v", 2), ("speed_kmh", 1),
    ("ambient_c", 1), ("maf_gs", 2), ("throttle_pct", 1), ("inject_ms", 2),
    ("trim_short_pct", 1), ("trim_long_pct", 1), ("charge_air_c", 1),
    ("timing_deg", 1), ("rail_bar", 1), ("lambda_cmd", 3), ("pedal_pct", 1),
    ("knock1_deg", 2), ("knock2_deg", 2), ("knock3_deg", 2), ("knock4_deg", 2),
    ("odometer_km", 0), ("cat_temp_c", 0), ("run_time_s", 0),
    ("dist_clear_km", 0),
    ("lat", 6), ("lon", 6), ("alt", 1), ("gps_speed", 1),
]
HEADER = [c[0] for c in COLS]

# rpm per km/h in each gear, close enough to a 6-speed Scirocco.
GEAR_RATIO = [95.0, 58.0, 40.0, 30.0, 24.0, 19.5]
IDLE_RPM = 820.0


class Sim:
    """One drive, stepped at 14 Hz off a (t_end, target_kmh, aggr) schedule."""

    def __init__(self, schedule, ambient_c, odo_km, seed, lat0, lon0):
        self.schedule = schedule
        self.ambient = ambient_c
        self.odo = odo_km
        self.rng = random.Random(seed)
        self.t = 0.0
        self.v = 0.0                    # km/h
        self.gear = 1
        self.shift_timer = 0.0
        self.boost = -0.65
        self.peak = 0.0
        self.lam = 1.0
        self.charge_air = ambient_c + 4.0
        self.iat = ambient_c + 3.0
        self.knock = [0.0, 0.0, 0.0, 0.0]
        self.trim_long = 2.4
        self.trim_short = 1.0
        self.heading = self.rng.uniform(0.0, 2 * math.pi)
        self.lat = lat0
        self.lon = lon0
        # EOBD channels are startup snapshots on the real board (the ECU
        # closes TP2.0 on every EOBD request), so they are constants here too.
        self.snap = {"rail_bar": 62.4, "pedal_pct": 9.8, "cat_temp_c": 372,
                     "run_time_s": 23, "dist_clear_km": 1284}

    def _target(self):
        for t_end, target, aggr in self.schedule:
            if self.t < t_end:
                return target, aggr
        return 0.0, 0

    def step(self):
        rng = self.rng
        target, aggr = self._target()

        # Speed chases the schedule target under an accel/brake cap.
        gap = target - self.v
        if aggr and gap > 0:
            a = max(1.2, 3.6 - self.v / 40.0)          # m/s^2, tapering
        elif gap > 0:
            a = 1.6
        else:
            a = 2.4                                     # braking
        dv = max(-a, min(a, gap / 3.0)) * 3.6 * DT
        self.v = max(0.0, self.v + dv)

        # Gearbox. Aggressive demand kicks down and holds to the redline.
        demand = aggr and gap > 10
        rpm = max(IDLE_RPM, self.v * GEAR_RATIO[self.gear - 1])
        if demand and rpm < 3300 and self.gear > 1:
            self.gear -= 1
            self.shift_timer = 0.35
        elif rpm > (6400 if demand else 2700) and self.gear < 6:
            self.gear += 1
            self.shift_timer = 0.35
        elif rpm < 1250 and self.gear > 1:
            self.gear -= 1
        if self.v < 2.0:
            self.gear = 1
            rpm = IDLE_RPM + rng.gauss(0, 12)
        else:
            rpm = max(IDLE_RPM, self.v * GEAR_RATIO[self.gear - 1])

        # Load: idle floor, speed-dependent cruise, pinned during a pull,
        # cut during a shift.
        if demand:
            load = 96.0 + rng.gauss(0, 1.5)
        elif gap > 3:
            # Gentle driving stays clear of the 80 % burst trigger; only the
            # aggr branch above is allowed to trip it.
            load = min(72.0, 45.0 + gap * 1.2) + rng.gauss(0, 2)
        elif self.v < 2.0:
            load = 18.0 + rng.gauss(0, 1)
        else:
            load = 24.0 + self.v * 0.25 + rng.gauss(0, 2)
        if self.shift_timer > 0:
            self.shift_timer -= DT
            load *= 0.35
        load = max(5.0, min(100.0, load))

        # Turbo: target from load/rpm, actual first-order lags it with a
        # spool overshoot; vacuum everywhere else.
        if load > 60 and rpm > 2200:
            b_tgt = min(1.10, (load - 60) / 40.0 * 1.15)
            b_tgt *= min(1.0, (rpm - 2200) / 1200.0)
        else:
            b_tgt = -(0.70 - load / 100.0 * 0.45)       # vacuum
        self.boost += (b_tgt - self.boost) * (DT / 0.55)
        if b_tgt > 0.3 and self.boost > b_tgt * 0.85:
            self.boost += rng.uniform(0, 0.010)          # spool flutter
        self.peak = max(self.peak, self.boost)

        # Mixture: open-loop enrichment past ~80 % load, trims freeze there.
        lam_tgt = 0.86 if load > 80 else 1.0
        self.lam += (lam_tgt - self.lam) * (DT / 0.8)
        self.trim_long += rng.gauss(0, 0.01)
        self.trim_long = max(1.5, min(3.5, self.trim_long))
        if self.lam < 0.95:
            trim_short = 0.0
        else:
            self.trim_short += rng.gauss(0, 0.15)
            self.trim_short = max(-4.0, min(4.0, self.trim_short))
            trim_short = self.trim_short

        # Knock: only worth simulating where knock happens; retard decays
        # back to zero at ~1.2 deg/s.
        for i in range(4):
            if load > 85 and rpm > 3500 and rng.random() < DT * 0.15:
                self.knock[i] = -rng.choice([1.5, 2.25, 3.0, 3.75])
            elif self.knock[i] < 0:
                self.knock[i] = min(0.0, self.knock[i] + 1.2 * DT)

        # Thermals.
        coolant = min(90.0, 20.0 + 72.0 * (1 - math.exp(-self.t / 300.0)))
        if coolant >= 89.5:
            coolant += 1.2 * math.sin(self.t / 45.0)     # thermostat cycling
        oil = self.ambient + 2 + (104.0 - self.ambient) * \
            (1 - math.exp(-self.t / 700.0))
        ca_tgt = self.ambient + 8 + max(0.0, self.boost) * 28
        self.charge_air += (ca_tgt - self.charge_air) * (DT / 6.0)
        iat_tgt = self.ambient + 3 + max(0.0, self.boost) * 10
        self.iat += (iat_tgt - self.iat) * (DT / 4.0)

        timing = (-1.5 + rng.gauss(0, 0.4)) if self.v < 2.0 else \
            (30.0 - load * 0.18 - max(0.0, self.boost) * 8 + rng.gauss(0, 0.6)
             + min(self.knock))

        # GPS: dead-reckon a wandering route.
        self.heading += rng.gauss(0, 0.015) * (1 if self.v > 2 else 0)
        dist = self.v / 3.6 * DT                        # metres
        self.lat += dist * math.cos(self.heading) / 111320.0
        self.lon += dist * math.sin(self.heading) / \
            (111320.0 * math.cos(math.radians(self.lat)))

        baro = 99.2 + rng.gauss(0, 0.03)
        row = {
            "rpm": rpm + rng.gauss(0, 6),
            "map_kpa": baro + self.boost * 100.0,
            "load_pct": load,
            "iat_k": self.iat + 273.15,
            "boost_bar": self.boost,
            "boost_target_bar": b_tgt if b_tgt > 0 else 0.0,
            "boost_peak_bar": self.peak,
            "n75_pct": (55 + load * 0.25) if self.boost > 0.05 else 4.0,
            "baro_kpa": baro,
            "fault_count": 0,
            "reconnects": 0,
            "sample_hz": 13.9 + rng.gauss(0, 0.15),
            "coolant_c": coolant + rng.gauss(0, 0.1),
            "oil_c": oil + rng.gauss(0, 0.1),
            "battery_v": (13.9 if self.v < 2 else 14.1) + rng.gauss(0, 0.03),
            "speed_kmh": self.v,
            "ambient_c": self.ambient + rng.gauss(0, 0.05),
            "maf_gs": max(2.0, rpm / 1000.0 * (2 + load / 100.0 * 20)),
            "throttle_pct": min(100.0, 98.0 if demand else load * 0.9 + 5),
            "inject_ms": 1.5 + load * 0.12 + rng.gauss(0, 0.05),
            "trim_short_pct": trim_short,
            "trim_long_pct": self.trim_long,
            "charge_air_c": self.charge_air,
            "timing_deg": timing,
            "lambda_cmd": self.lam,
            "knock1_deg": self.knock[0], "knock2_deg": self.knock[1],
            "knock3_deg": self.knock[2], "knock4_deg": self.knock[3],
            "odometer_km": self.odo,     # 10 km granularity: flat all drive
        }
        row.update(self.snap)
        self.t += DT
        return row


def simulate(schedule, seed, ambient_c, odo_km, lat0, lon0):
    duration = schedule[-1][0]
    sim = Sim(schedule, ambient_c, odo_km, seed, lat0, lon0)
    rows = []
    for i in range(int(duration * 14)):
        t = i / 14.0                    # exact at whole seconds
        row = sim.step()
        row["t"] = t
        row["lat"] = sim.lat
        row["lon"] = sim.lon
        row["alt"] = 42.0 + 8.0 * math.sin(t / 180.0)
        row["gps_speed"] = max(0.0, row["speed_kmh"] + sim.rng.gauss(0, 0.8))
        rows.append(row)
    return rows


def burst_segments(rows):
    """The PLAN's trigger, verbatim, over the 14 Hz rows.

    Returns [(i_start, i_end_exclusive)] index windows, pre-roll included.
    """
    segs = []
    on = False
    off_run = 0.0
    start = 0
    for i, r in enumerate(rows):
        trig = (r["rpm"] >= 3000 or r["load_pct"] >= 80
                or r["boost_bar"] >= 0.30)
        calm = (r["rpm"] < 2500 and r["load_pct"] < 60
                and r["boost_bar"] < 0.15)
        if not on and trig:
            on = True
            off_run = 0.0
            start = max(0, i - int(5 * 14))             # 5 s pre-roll
        elif on:
            off_run = off_run + DT if calm else 0.0
            if off_run >= 3.0:
                segs.append((start, i + 1))
                on = False
    if on:
        segs.append((start, len(rows)))
    return segs


def fmt_row(row, extra=None):
    cells = [] if extra is None else [str(extra)]
    for name, nd in COLS:
        v = row["t"] if name == "t_unix" else row[name]
        cells.append(f"{v:.{nd}f}" if nd else str(int(round(v))))
    return ",".join(cells)


def write_gz(path, text):
    # mtime=0 keeps re-runs byte-identical, which keeps `put --local` honest.
    with open(path, "wb") as f:
        f.write(gzip.compress(text.encode("utf-8"), mtime=0))


def write_session(root, session_id, rows, start_unix):
    y, m = session_id[0:4], session_id[5:7]
    outdir = os.path.join(root, "drives", y, m, session_id)
    os.makedirs(outdir, exist_ok=True)
    for r in rows:
        r["t"] += start_unix

    # drive.csv: every 14th step lands exactly on the whole second.
    base = [",".join(HEADER)]
    base += [fmt_row(r) for i, r in enumerate(rows) if i % 14 == 0]
    write_gz(os.path.join(outdir, "drive.csv.gz"), "\n".join(base) + "\n")

    segs = burst_segments(rows)
    burst = [",".join(["segment"] + HEADER)]
    n_burst = 0
    for seg_id, (i0, i1) in enumerate(segs, start=1):
        for r in rows[i0:i1]:
            burst.append(fmt_row(r, extra=seg_id))
            n_burst += 1
    write_gz(os.path.join(outdir, "bursts.csv.gz"), "\n".join(burst) + "\n")

    meta = {
        "session": session_id,
        "start_unix": start_unix,
        "end_unix": rows[-1]["t"],
        "vin": "WVWZZZ13ZAV000000",
        "dtcs_at_start": [],
        "ident": {"ecu": "MED17.5", "note": "fabricated by make_testdata.py"},
        "counters": {
            "baseline_rows": len(base) - 1,
            "burst_rows": n_burst,
            "burst_segments": len(segs),
            "reconnects": 0,
        },
        "upload": {"state": "testdata"},
    }
    # meta.json LAST in the R2 layout; locally the order is only tradition.
    with open(os.path.join(outdir, "meta.json"), "w") as f:
        json.dump(meta, f, indent=1)
    return len(base) - 1, n_burst, len(segs)


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    root = os.path.join(here, "out")

    sessions = []

    # Quiet errand: nothing here reaches rpm 3000 / load 80 / +0.30 bar.
    quiet = [(30, 0, 0), (90, 45, 0), (150, 30, 0), (210, 0, 0),
             (270, 50, 0), (420, 55, 0), (480, 0, 0), (540, 40, 0),
             (780, 60, 0), (840, 20, 0), (900, 0, 0), (960, 45, 0),
             (1200, 50, 0), (1320, 30, 0), (1440, 55, 0), (1500, 0, 0)]
    sid = "2026-08-15_0812"
    rows = simulate(quiet, seed=1, ambient_c=17.5, odo_km=185880,
                    lat0=45.5010, lon0=-73.5710)
    t0 = datetime(2026, 8, 15, 8, 12).timestamp()
    sessions.append((sid, write_session(root, sid, rows, t0)))

    # Spirited evening: three on-ramp pulls, aggr flag floors it.
    spirited = [(60, 40, 0), (240, 60, 0), (300, 60, 0),
                (318, 130, 1), (420, 80, 0),
                (595, 80, 0), (612, 150, 1), (700, 70, 0),
                (890, 50, 0), (905, 120, 1), (1000, 60, 0), (1080, 0, 0)]
    sid = "2026-08-21_1743"
    rows = simulate(spirited, seed=2, ambient_c=24.0, odo_km=185890,
                    lat0=45.4890, lon0=-73.5620)
    t0 = datetime(2026, 8, 21, 17, 43).timestamp()
    sessions.append((sid, write_session(root, sid, rows, t0)))

    # Emit the exact `wrangler r2 object put --local` loaders. Run from
    # cloud/ so the objects land in the same .wrangler/state that
    # `wrangler dev` reads. meta.json goes last: the commit marker.
    cmds = []
    for sid, _ in sessions:
        y, m = sid[0:4], sid[5:7]
        prefix = f"drives/{y}/{m}/{sid}"
        for fname in ("drive.csv.gz", "bursts.csv.gz", "meta.json"):
            local = f"testdata/out/{prefix}/{fname}"
            cmds.append(f'npx wrangler r2 object put "{BUCKET}/{prefix}/{fname}"'
                        f' --file "{local}" --local')

    with open(os.path.join(root, "put_local.ps1"), "w") as f:
        f.write("# Run from cloud/ -- loads testdata into wrangler dev's local R2\n")
        f.write("\n".join(cmds) + "\n")
    with open(os.path.join(root, "put_local.sh"), "w") as f:
        f.write("#!/bin/sh\n# Run from cloud/ -- loads testdata into wrangler dev's local R2\nset -e\n")
        f.write("\n".join(cmds) + "\n")

    for sid, (n_base, n_burst, n_segs) in sessions:
        print(f"{sid}: {n_base} baseline rows, {n_burst} burst rows, "
              f"{n_segs} segments")
    print(f"\nwrote {root}")
    print("load into wrangler dev's local R2 (run from cloud/):\n")
    print("\n".join(cmds))


if __name__ == "__main__":
    main()
