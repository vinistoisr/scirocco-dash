"""
boost.py -- live engine data feed for the Scirocco.

Reads the identified channels over TP 2.0 / KWP2000 and exposes them for
realdash.py to stream to the head unit. Also keeps a peak hold and, when the
filesystem is writable (boot.py CAR MODE), writes a drive log.

Channel map, established by scanning all 255 measuring blocks on this ECU
(06J906026AR, MED17.5) and cross-checking against Ross-Tech's published
group definitions:

    block 115  [rpm, engine load %, boost specified, boost actual]
    block 118  [rpm, intake air temp, N75 duty, boost actual]
    block 004  [rpm, battery volts, coolant temp, intake air temp,
                coolant, -, OIL TEMP, -]          <- group 004 is documented
                                                     as rpm/volts/coolant/IAT
    block 003  [rpm, MAF g/s, throttle %, ...]
    block 005  [rpm, load, road speed km/h, ...]
    block 007  [rpm, load, coolant, -, -, ambient temp, IAT, coolant]
    block 032  [fuel trim additive %, fuel trim multiplicative %]  <- lambda
    block 002  [rpm, load, injection time ms, MAF g/s]
    block 011  [rpm, coolant, IAT, -, charge air temp, MAF, speed, -]
    block 078  [..., barometric pressure at field 6]

Confidence note: rpm, volts, coolant and IAT in block 004 are confirmed
against Ross-Tech's group definition. The OIL TEMP field is inferred -- it is
a real temperature sensor reading ~83 C with coolant at 96 C at warm idle,
which is what oil does, and the same value appears in blocks 003 and 008.
To confirm it properly, watch it on a COLD START: coolant climbs quickly and
oil lags well behind. Ambient and charge air temp are inferred the same way.

All pressures are ABSOLUTE mbar. Gauge boost -- what a boost gauge shows --
is absolute minus ambient, so it sits at 0 with the engine off boost:

    gauge_bar = (actual_mbar - baro_mbar) / 1000

Idle sanity check: actual ~1000 mbar, baro ~1005 mbar, so the gauge reads
about zero. Under full load on a stock 2.0 TSI expect roughly 2000-2400
mbar absolute, i.e. 1.0-1.4 bar / 15-20 psi gauge.

Polling strategy: block 115 every cycle so boost stays fast (~17 Hz), plus
one slow block per EXTRA_EVERY cycles, rotating. Temperatures and voltage do
not need to be quick, and reading everything every cycle would drop boost to
about 2 Hz.

READ ONLY: service 0x21 measuring-block reads only.
"""

import time
import tp20

BLOCK_MAIN = 115        # rpm, load, boost specified, boost actual
# ...and catalyst temp in field 5.  That is ALL of it: fields 4 and 6 were
# probed live on 2026-08-29 with the engine idling and both read a constant
# 0, so there is nothing else in this block to claim.  Timing and injector
# pulse width cannot be had for free by riding the every-cycle read; they
# genuinely cost their own block reads, and those reads are not affordable
# (see the reverted priority-tier note further down).
BLOCK_BARO = 78         # barometric pressure
BARO_FIELD = 5          # 0-based field index within block 78

# Slow channels, polled one per EXTRA_EVERY cycles, round robin.
#
#   ("mb",  block, ((attribute, 0-based field index), ...))
#   ("pid", pid,   ((attribute, scale callable), ...))
#
# Odometer: block 075 field 8 is a plain 16-bit counter reading 18588 while
# the car's dash showed 185885 km, so it counts in units of 10 km. The 5 km
# discrepancy is simply its resolution.
#
# Timing: block 003 field 4 uses formula 27 and agreed exactly with EOBD PID
# 0x0E at -1.5 degrees, so it is ignition advance and needs no scaling here.
EXTRA_BLOCKS = (
    ("mb", 118, (("iat", 1), ("n75", 2))),
    ("mb", 4,   (("volts", 1), ("coolant", 2), ("oil", 6))),
    ("mb", 3,   (("maf", 1), ("throttle", 2), ("timing", 3))),
    ("mb", 5,   (("speed", 2),)),
    ("mb", 32,  (("trim_add", 0), ("trim_mult", 1))),
    ("mb", 7,   (("ambient", 5),)),
    ("mb", 2,   (("inj_ms", 2),)),
    ("mb", 11,  (("charge_air", 4),)),
    ("mb", 75,  (("odo_raw", 7),)),
    # *** THESE ARE NOT VERIFIED AS KNOCK RETARD. ***
    # Group 020's four fields use formula 34, which the jazdw/vag-blocks
    # reference decodes as (b-128)*0.01*a in kW -- NOT degrees. Worse, this
    # project did not implement formula 34 until 2026-08-24, so field_at
    # returned None, the attribute kept its 0.0 initialiser, and a whole
    # 27-minute drive logged a convincing "zero knock" that was really
    # "never decoded". They now decode, but what they MEAN is still open:
    # b sits at the 128 midpoint at idle, so the sign convention and the
    # true quantity need checking against VCDS on the same blocks before
    # anyone reads them as knock. Treat as raw deviation until then.
    ("mb", 20,  (("knock1", 0), ("knock2", 1),
                 ("knock3", 2), ("knock4", 3))),
    # Rail pressure, LIVE. Identified 2026-08-24: field 0 sat pinned at
    # 4000 counts while field 1 wandered 3964..4051 around it -- a setpoint
    # and its regulated actual. Confirmed against EOBD PID 0x23 at the same
    # idle to within 0.03 bar; see tp20.value_of formula 83.
    # Block 106 is the fuel supply group, and all FOUR of its fields are
    # documented for the 2.0 TSI: rail pressure specified, rail pressure
    # actual, pump duty cycle, fuel temperature. Fields 2 and 3 are free --
    # the block is already being read for rail pressure.
    #
    # Pump duty is worth having on its own account. Ross-Tech's wiki gives
    # thresholds for it: 35-55% normal at idle, 55-60% a low-pressure
    # concern, over 70% a failing or restricted pump. This car reads 49.8%,
    # mid-normal. That makes it an early warning for the HPFP/lift pump,
    # which is a known failure mode on these engines.
    ("mb", 106, (("rail_spec_abs", 0), ("rail_abs", 1),
                 ("fuel_pump_duty", 2), ("fuel_temp", 3))),
    # Pedal, LIVE. Block 62 field 2 carries raw byte 0x26 and EOBD PID 0x49
    # answered the very same 0x26 at the same moment -- not a lookalike
    # value, the identical byte. Field 0 is throttle by the same test
    # (0x1F in both). 14.9% with a foot OFF the pedal is this sensor's real
    # resting output, not an error.
    ("mb", 62,  (("pedal", 2),)),
)

# Generic EOBD PIDs, read ONCE at startup before the TP 2.0 channel opens.
#
# These are NOT in the rotation above, and must not be: this ECU will not
# run both protocols at once. Measured, every mode 01 request on 0x7E0 makes
# it tear the TP 2.0 channel down with an A8. Interleaving them dropped the
# gauge from 16 Hz to 0.7 Hz and left block reads taking 300 ms instead of
# 50 ms, with a reconnect after every other PID. So they are a startup
# snapshot: still useful for the ones that barely move, and honest about
# the ones that do.
# Rail pressure, catalyst temperature and pedal USED to be read here and
# are now live TP 2.0 channels, so they are gone from this table. They were
# not merely stale: the snapshot is taken when the board boots, which is
# when the head unit powers its USB -- ignition on, engine NOT running. Every
# held value was therefore an engine-off value. Measured 2026-08-24 at warm
# idle with the engine off vs running:
#
#     rail   2.0 bar  (residual, pump stopped)   ->  39.2 bar
#     cat    22.4 C   (ambient, nothing burnt)   ->  487.7 C
#     lambda 1.999    (PID 0x44 = 0xFFFF, i.e.   ->  1.000
#                      UNSUPPORTED, never a reading)
#
# lambda_cmd stays here because no measuring block on this ECU was found to
# carry it: blocks 030-034 are nearly empty, and the oscillating formula-66
# fields in 031/033 are a switching O2 sensor VOLTAGE (bimodal 0.06-0.82 V),
# not a lambda ratio. So AFR remains a boot-time value, and at ignition-on
# it is 0xFFFF. See docs/NEXT-SESSION.md.
EOBD_SNAPSHOT = (
    (0x1F, "run_time",   lambda d: float((d[0] << 8) | d[1])),
    (0x31, "dist_clear", lambda d: float((d[0] << 8) | d[1])),
    # ISO 15031 defines PID 0x44 as equivalence ratio scaled 2/65536; it
    # read exactly 1.000 at closed-loop idle.
    (0x44, "lambda_cmd", lambda d: ((d[0] << 8) | d[1]) * 2.0 / 65536.0),
    (0x14, "o2_volt",    lambda d: d[0] * 0.005),
)


def eobd_snapshot(bus):
    """Read the EOBD PIDs over plain ISO-TP. Call this BEFORE opening a
    TP 2.0 channel; returns {attribute: value}."""
    out = {}
    for pid, name, fn in EOBD_SNAPSHOT:
        r = bus.request(bytes([0x01, pid]), timeout=0.5)
        if r is None or len(r) < 3 or r[0] != 0x41 or r[1] != pid:
            continue
        try:
            out[name] = fn(r[2:])
        except Exception:
            pass
    return out

EXTRA_EVERY = 6         # cycles between slow-channel reads. 4 capped the
                        # gauge at 16 Hz (50 + 50/4 ms of ECU time); 6
                        # lifts the ceiling to 17.1 Hz, and the two-tier
                        # rotation below keeps the channels a tuner needs
                        # under load FRESHER than the old flat rotation.
BARO_EVERY = 500        # ambient pressure moves slower still

# Which extra blocks matter while the engine is working: knock, timing,
# trims and IAT are what a tuner reads during a pull; coolant and oil move
# on thermal timescales and ambient/odometer barely move at all. Under
# load the rotation reads ONLY the fast tier (each block refreshes ~1.4 s
# instead of the flat rotation's ~2.9 s); at cruise it alternates tiers so
# everything stays alive.
# TIER MEMBERSHIP IS A TUNING DECISION, not a convenience one: the slow
# tier pauses under load, so anything a tuner reads DURING a pull has to be
# fast. The 2026-08-23 drive proved the cost of getting this wrong --
# charge_air_temp logged 1751 samples above 3000 rpm with exactly ONE
# distinct value (frozen at 57 C), and inj_ms managed 8. Intercooler outlet
# temp is precisely what you look at for heat soak, and it was a flat line.
#
# Now fast: IAT+N75 (wastegate duty), MAF/throttle/timing, speed (a frozen
# speedo mid-pull is a visible regression), fuel trims, injection duration,
# charge air temp, and group 020. Slow: volts/coolant/oil, ambient, odo --
# genuinely thermal or near-static, and the SLOW_MAX_AGE_S escape still
# refreshes them every 10 s even under sustained load.
#
# Cost: 7 fast blocks instead of 5 means ~2.5 s per channel under load
# rather than 1.8 s. Two real samples per pull beats a frozen number.
# Rail pressure and pedal join the fast tier: both are things a tuner reads
# DURING a pull, which is the stated rule for this tier. Fuel trims (block
# 32) move the other way -- they are ADAPTATION values that drift over
# minutes, they were measured dead flat across 46 sweeps at idle, and the
# SLOW_MAX_AGE_S escape still refreshes them every 10 s. So they pay for one
# of the two new slots and the fast tier grows by one, 7 blocks to 8
# (~2.9 s per channel under sustained load rather than ~2.5 s).
# A priority tier was tried here on 2026-08-29 and REVERTED. The numbers
# stay, because the next hardware revision -- reading the ECU's own CAN bus
# instead of KWP2000 over TP 2.0 -- removes this constraint entirely, and
# whoever does that will want to know what was already established:
#
#   One TP 2.0 block read costs ~50 ms round trip, so this ECU answers about
#   20 reads per second and no scheduling changes that. The main block takes
#   one every cycle, which is what gives rpm and boost their rate. Everything
#   else competes for what is left:
#
#     priority slot   rpm/boost   timing    injection
#     none (this)       17.1 Hz     0.34 Hz   0.34 Hz
#     every cycle        8.6 Hz    ~4.3 Hz   ~4.3 Hz     <- measured, reverted
#
#   So ~4.3 Hz on the tuning channels costs HALF the boost gauge's rate, and
#   10 Hz on them is arithmetically impossible: two channels at 10 Hz is the
#   entire budget with nothing left for rpm and boost. Driven back to back,
#   8.6 Hz was visibly choppy on the tach and boost needle -- the two
#   instruments this project exists for -- so it went back.
#
#   Block 115's unclaimed fields are empty (see BLOCK_MAIN above), so there
#   is no way to get these two out of the read that already happens.
#
#   What was KEPT is the free half: frame 0xC93 in realdash.py ships spark
#   advance and injector pulse width EVERY cycle instead of every fifth, so
#   whatever the rotation last acquired reaches RealDash without waiting on
#   the slow block. That costs no ECU reads at all.
FAST_TIER = (0, 2, 3, 6, 7, 9, 10, 11)  # 118, 3, 5, 2, 11, 20, 106, 62
SLOW_TIER = (1, 5, 8, 4)                # 4, 7, 75, 32
SLOW_MAX_AGE_S = 10.0             # a slow block may go this long unread

# NOTE: there is deliberately no "are we under load?" test here any more.
# The first design alternated tiers 50/50 unless load was high -- which
# made the fast tier SLOWER at cruise (5.6 s per channel) than the
# thermals it was supposed to outrank (2.4 s), and needed a load reading
# that this ECU pins at 100% with the engine off. Deadline scheduling
# below needs neither: slow blocks are read only once genuinely stale, so
# the fast rotation gets every other slot at any driving state.
ODO_KM_PER_COUNT = 10   # block 075 field 8 counts in 10 km steps

MBAR_PER_PSI = 68.9476


class Gauge:
    def __init__(self, ch, initial=None):
        self.ch = ch
        self.baro = 1013.0
        self.peak_bar = 0.0
        # fast channels
        self.rpm = 0.0
        self.load = 0.0
        self.spec = 0.0
        self.actual = 0.0
        # slow channels
        self.iat = 0.0
        self.n75 = 0.0
        self.volts = 0.0
        self.coolant = 0.0
        self.oil = 0.0
        self.maf = 0.0
        self.throttle = 0.0
        self.speed = 0.0
        self.trim_add = 0.0
        self.trim_mult = 0.0
        self.ambient = 0.0
        self.inj_ms = 0.0
        self.charge_air = 0.0
        self.timing = 0.0
        self.odo_raw = 0.0
        self.rail_abs = 0.0        # bar ABSOLUTE, block 106 field 1
        self.rail_spec_abs = 0.0   # bar ABSOLUTE, block 106 field 0
        self.fuel_pump_duty = 0.0  # %, block 106 field 2
        self.fuel_temp = 0.0       # C, block 106 field 3
        self.cat_temp = 0.0
        self.pedal = 0.0
        self.run_time = 0.0
        self.dist_clear = 0.0
        self.knock1 = 0.0
        self.knock2 = 0.0
        self.knock3 = 0.0
        self.knock4 = 0.0
        self.lambda_cmd = 0.0
        self.o2_volt = 0.0

        self.n = 0
        self.errors = 0
        self._extra_i = 0
        self._fast_i = 0
        # per-slow-block last-read time; 0.0 means "never", so each one
        # gets an early first read after boot
        self._slow_t = [0.0] * len(SLOW_TIER)
        # Startup EOBD snapshot, if one was taken. These stay fixed for the
        # session -- see EOBD_SNAPSHOT for why they cannot be polled live.
        for name, value in (initial or {}).items():
            setattr(self, name, value)
        self.read_baro()

    # ---- acquisition ----

    def _read(self, block):
        if not self.ch.connected:
            self.ch.connect()
        return tp20.read_block(self.ch, block, timeout=0.4)

    def read_baro(self):
        try:
            d = self._read(BLOCK_BARO)
        except tp20.TP20Error:
            return
        if d:
            v = tp20.field_at(d, BARO_FIELD)
            if v:
                self.baro = v

    def _apply_block(self, idx):
        """Read one EXTRA_BLOCKS entry and store its fields.

        Shared by the priority tier and the rotation so both get the same
        per-field guards; there is exactly one place a block turns into
        attributes."""
        kind, ident, fields = EXTRA_BLOCKS[idx]
        try:
            d = self._read(ident)
        except tp20.TP20Error:
            return
        if not d:
            return
        for name, field in fields:
            v = tp20.field_at(d, field)
            if v is None:
                continue
            if name == "fuel_temp" and v <= -40.0:
                # Formula 26 is b - a with a pinned at 48, so an unpopulated
                # field (b = 0) decodes to exactly -48 C. That is not a cold
                # reading, it is arithmetic on a value the ECU did not
                # supply -- seen live with the ignition on and the engine
                # stopped, while the same field reads a correct 89 C once it
                # is running. Petrol is nowhere near liquid at -40, and no
                # fuel temperature sensor reports it, so treat it as absent
                # and keep the last real value rather than logging a number
                # that looks measured. Same reasoning as rail_bar below.
                continue
            setattr(self, name, v)

    def _read_extra(self):
        """One extra channel per call, scheduled by deadline.

        The fast rotation gets every slot until a slow block actually goes
        stale (SLOW_MAX_AGE_S), then the stalest one takes a single turn.
        No load test, no alternation: the tuning channels refresh at a
        steady ~2.8 s whatever the car is doing, and the thermals are
        guaranteed a read every 10 s even through a sustained pull.
        """
        now = time.monotonic()
        stalest = None
        for k in range(len(SLOW_TIER)):
            age = now - self._slow_t[k]
            if age >= SLOW_MAX_AGE_S and (stalest is None or age > stalest[0]):
                stalest = (age, k)
        if stalest is not None:
            k = stalest[1]
            self._slow_t[k] = now
            idx = SLOW_TIER[k]
        else:
            self._fast_i = (self._fast_i + 1) % len(FAST_TIER)
            idx = FAST_TIER[self._fast_i]
        self._apply_block(idx)

    @property
    def rail_bar(self):
        """Rail pressure as a GAUGE value, which is what the column has
        always meant (it came from PID 0x23, defined as gauge). Block 106
        reports absolute, so ambient comes off -- the same convention this
        file already uses for boost."""
        if self.rail_abs <= 0.0:
            # Engine off, or block 106 not read yet. Subtracting ambient
            # from the 0.0 initialiser would report -1.0 bar of rail
            # pressure, which is not a reading -- it is arithmetic on a
            # value that does not exist yet. Seen live 2026-08-24 with the
            # ignition on and the engine stopped.
            return 0.0
        return self.rail_abs - (self.baro / 1000.0 if self.baro else 0.0)

    @property
    def odometer(self):
        """Kilometres. Resolution is ODO_KM_PER_COUNT, so it steps."""
        return self.odo_raw * ODO_KM_PER_COUNT

    def sample(self):
        """One acquisition cycle. Returns True if fresh data landed."""
        try:
            d = self._read(BLOCK_MAIN)
        except tp20.TP20Error as e:
            self.errors += 1
            print('{"error":"tp20","detail":"%s"}' % e)
            return False
        if not d:
            return False
        self.rpm = tp20.field_at(d, 0) or 0.0
        self.load = tp20.field_at(d, 1) or 0.0
        # Block 115 field 2 is the setpoint (Ladedruck Sollwert) and field 3
        # the live reading (Istwert).  These were briefly swapped on
        # 2026-08-29 and swapped back the same day; do not "fix" them again
        # without re-reading this.
        #
        # The argument for swapping them was that field 2 reads 31 kPa
        # absolute at idle and climbs with throttle, "which is manifold
        # pressure and nothing else", while field 3 sat flat at ~atmospheric,
        # "which manifold pressure cannot do at part throttle".  The second
        # half of that is true and the conclusion drawn from it was wrong:
        # Ladedruck Istwert is measured BEFORE the throttle plate, so sitting
        # at ambient with the throttle shut is exactly what it should do.
        # Manifold pressure is measured after the plate.  They are not the
        # same signal, which is the question that started this.
        #
        # Confirmed on the drive log of 2026-08-29 (1272 rows):
        #   * field 3 never goes below -0.02 bar gauge; field 2 reaches -0.77.
        #     A pre-throttle sensor cannot read vacuum.  A setpoint can.
        #   * field 3 >= field 2 in 97.1% of rows, and the gap closes from
        #     0.61 bar at shut throttle to 0.33 at part throttle -- the ECU
        #     demanding less than it currently has while it backs off.
        #   * both reach the same 1.545 bar maximum, as a setpoint and its
        #     achieved value must.
        # And the driver's own report, which is the measurement that matters:
        # under throttle the field-2 needle jumps at once and field 3 ramps up
        # behind it.  Demand steps, turbos spool.
        self.spec = tp20.field_at(d, 2) or 0.0
        self.actual = tp20.field_at(d, 3) or 0.0
        # Catalyst temperature costs NOTHING: block 115 is the main block,
        # read every cycle at ~17 Hz, and field 5 was simply never claimed.
        # Confirmed against EOBD PID 0x3C at the same idle -- 480.0 C here
        # vs 488.3 C there, and 486.0 vs 488.3 on a second run.
        cat = tp20.field_at(d, 5)
        if cat is not None:
            self.cat_temp = cat

        b = self.bar
        if b > self.peak_bar:
            self.peak_bar = b

        self.n += 1
        if self.n % EXTRA_EVERY == 0:
            self._read_extra()
        elif self.n % BARO_EVERY == 0:
            # elif, deliberately: when both land on the same cycle the old
            # code did THREE ECU reads back to back -- a ~150 ms stutter
            # every 36 s. Baro just waits for the next multiple instead.
            self.read_baro()
        return True

    # ---- derived ----

    @property
    def bar(self):
        return (self.actual - self.baro) / 1000.0

    @property
    def psi(self):
        return (self.actual - self.baro) / MBAR_PER_PSI

    @property
    def spec_bar(self):
        return (self.spec - self.baro) / 1000.0

    def json(self):
        return (
            '{"rpm":%.0f,"load":%.1f,"boost_bar":%.3f,"boost_psi":%.2f,'
            '"spec_bar":%.3f,"abs_mbar":%.0f,"baro_mbar":%.0f,'
            '"iat":%.1f,"n75":%.1f,"peak_bar":%.3f,"volts":%.2f,'
            '"coolant":%.1f,"oil":%.1f,"maf":%.2f,"throttle":%.1f,'
            '"speed":%.0f,"trim_add":%.1f,"trim_mult":%.1f,"ambient":%.1f,'
            '"inj_ms":%.2f,"charge_air":%.1f,"t":%.2f}'
            % (self.rpm, self.load, self.bar, self.psi, self.spec_bar,
               self.actual, self.baro, self.iat, self.n75, self.peak_bar,
               self.volts, self.coolant, self.oil, self.maf, self.throttle,
               self.speed, self.trim_add, self.trim_mult, self.ambient,
               self.inj_ms, self.charge_air, time.monotonic())
        )

    def human(self):
        return ("%5.0f rpm %5.1f%% boost %+5.2f bar (%+5.1f psi) peak %+5.2f | "
                "cool %3.0fC oil %3.0fC iat %3.0fC amb %3.0fC | %4.1fV "
                "%5.1fg/s thr %4.1f%% %3.0fkm/h trim %+4.1f/%+4.1f n75 %3.0f%%"
                % (self.rpm, self.load, self.bar, self.psi, self.peak_bar,
                   self.coolant, self.oil, self.iat, self.ambient, self.volts,
                   self.maf, self.throttle, self.speed,
                   self.trim_add, self.trim_mult, self.n75))


# Column order is deliberate: the tuning-critical channels first, so the
# log opens usefully in a spreadsheet without reordering. boost_spec_bar
# next to boost_bar is the pair that matters most -- actual tracking
# specified is what says whether the tune is achieving its target.
LOG_HEADER = (
    "t,rpm,boost_bar,boost_spec_bar,boost_psi,abs_mbar,spec_mbar,baro_mbar,"
    "timing_deg,knock1,knock2,knock3,knock4,"
    "rail_bar,lambda_cmd,o2_volt,trim_add,trim_mult,"
    "load,maf,throttle,pedal,n75,inj_ms,"
    "iat,charge_air,coolant,oil,ambient,cat_temp,"
    "volts,speed,odometer_km,run_time_s,dist_since_clear_km\n")


def open_log(path="/boost_log.csv"):
    try:
        f = open(path, "a")
        f.write("# --- drive start ---\n")
        f.write(LOG_HEADER)
        f.flush()
        return f
    except OSError:
        return None


def log_row(f, g):
    f.write(
        "%.2f,%.0f,%.3f,%.3f,%.2f,%.0f,%.0f,%.0f,"
        "%.2f,%.2f,%.2f,%.2f,%.2f,"
        "%.1f,%.3f,%.3f,%.1f,%.1f,"
        "%.1f,%.2f,%.1f,%.1f,%.1f,%.2f,"
        "%.1f,%.1f,%.1f,%.1f,%.1f,%.1f,"
        "%.2f,%.0f,%.0f,%.0f,%.0f\n"
        % (time.monotonic(), g.rpm, g.bar, g.spec_bar, g.psi,
           g.actual, g.spec, g.baro,
           g.timing, g.knock1, g.knock2, g.knock3, g.knock4,
           g.rail_bar, g.lambda_cmd, g.o2_volt, g.trim_add, g.trim_mult,
           g.load, g.maf, g.throttle, g.pedal, g.n75, g.inj_ms,
           g.iat, g.charge_air, g.coolant, g.oil, g.ambient, g.cat_temp,
           g.volts, g.speed, g.odometer, g.run_time, g.dist_clear))
