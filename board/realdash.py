"""
realdash.py -- RealDash CAN protocol output over USB serial.

Why this and not a web page: the Dudu7's WiFi is already committed to
wireless Android Auto, and Android will not hand /dev/ttyACM0 to Termux
without root and a fight with SELinux. RealDash implements the Android USB
Host API itself, so a plain USB cable from the Feather to the head unit
works with no root, no app development, and no WiFi.

Wire format, the '44' frame -- fixed 16 bytes, little-endian throughout:

    bytes 0-3    tag 0x44 0x33 0x22 0x11
    bytes 4-7    CAN frame id, uint32
    bytes 8-15   payload, always 8 bytes, zero padded

RealDash reads a channel description XML to interpret the payload; ours is
scirocco_realdash.xml, which must be copied to the head unit and selected
when creating the connection (Garage -> Connections -> RealDash CAN ->
Serial). Frame ids and byte offsets here and in that XML must agree.

Frame map. Everything is sent as a scaled integer; the XML does the final
division, so nothing here loses precision to rounding:

    0xC80  rpm         | MAP abs mbar  | load %x10      | IAT 0.1 K
    0xC81  boost mbar  | target mbar   | peak mbar      | N75 %x10      (signed)
    0xC82  baro mbar   | fault count   | reconnects     | sample Hz x10
    0xC83  coolant 0.1C| oil 0.1C      | battery mV     | speed 0.1km/h (signed)
    0xC84  ambient 0.1C| MAF 0.01 g/s  | throttle 0.1%  | inject 0.01ms (signed)
    0xC85  trim add 0.1%| trim mult 0.1%| charge air 0.1C| spare        (signed)
    0xC8B  gear code   | steer counts   | height1 mV     | height2 mV    (signed)
    0xC8C  damper1     | damper2        | damper3        | damper4
    0xC8D  height3 mV  | aux visits     | aux fails      | gear age s
    0xC91  DCC age s   | DCC status     | spare          | spare
    0xC93  timing x10  | inj ms x100    | spare          | spare  (FAST)
    0xC94  ST neg x10  | LT neg x10     | steer left deg | spare

Temperatures on the later frames are tenths of a degree CELSIUS, signed, so
sub-zero ambient reads correctly instead of wrapping to 6500-something.
"""

import struct

TAG = b"\x44\x33\x22\x11"

FRAME_ENGINE = 0xC80
FRAME_BOOST = 0xC81
FRAME_STATUS = 0xC82
FRAME_TEMPS = 0xC83
FRAME_AIR = 0xC84
FRAME_TRIM = 0xC85
FRAME_TUNE = 0xC88         # timing, rail pressure, lambda, pedal
FRAME_KNOCK = 0xC89        # knock retard, cylinders 1-4
FRAME_TRIP = 0xC8A         # odometer, catalyst temp, run time, trip
FRAME_DTC = 0xC86          # up to four stored fault codes, raw 16-bit
FRAME_WARN = 0xC87         # warning lamps (MIL etc.)
FRAME_FUEL = 0xC8F         # pump duty, fuel temp, rail spec/actual (abs)
FRAME_CHASSIS = 0xC8B      # gear, steering angle, ride height (DCC)
FRAME_DAMPER = 0xC8C       # the four damper channels
FRAME_AUX = 0xC8D          # rear ride height + aux poller health
FRAME_CMD = 0xC90          # RealDash -> board: buttons
FRAME_DCC_STATUS = 0xC91   # DCC snapshot age + availability state
# Spark advance and injector pulse width repeated on the FAST cadence.  Both
# are already read in boost.py's FAST_TIER, but they used to ship only in the
# SLOW_EVERY block, so a fresh sample could sit for up to five cycles before
# RealDash saw it.  They are the two numbers a tuner watches during a pull,
# so they get a frame of their own that goes out every cycle.  0xC88 and
# 0xC84 still carry the same words on the wire; the XML simply declares each
# channel once, here, so the log keeps one column per channel.
FRAME_SPARK_FUEL = 0xC93
# Outward halves of the two-way trim and steering bars, clamped HERE because
# RealDash cannot clamp them.  A bar that fills right-to-left draws the
# magnitude of its input rather than clamping at zero, so a half anchored on
# a shared zero line lights for both signs; and it cannot be hidden by level
# colour or by a transparent fill.  Sending max(0, -x) makes the half simply
# read zero for the wrong sign, which is the whole trick.
FRAME_SPLIT = 0xC94

TEXT_TAG = b"\x55\x33\x22\x11"

BAUD = 115200          # RealDash default for serial connections


def frame(can_id, *words):
    """One 16-byte RealDash CAN frame from up to four 16-bit words.

    Words are clamped into range rather than allowed to wrap, because a
    wrapped value renders as a wild gauge spike that looks like real data.
    Signed inputs are encoded two's complement so the XML can read them
    back with signed="true".
    """
    vals = []
    for w in words[:4]:
        w = int(w)
        if w < 0:
            w = max(w, -32768) & 0xFFFF
        else:
            w = min(w, 65535)
        vals.append(w)
    while len(vals) < 4:
        vals.append(0)
    return TAG + struct.pack("<IHHHH", can_id, *vals)


def engine_frame(rpm, map_abs_mbar, load_pct, iat_c):
    # IAT as tenths of a kelvin: RealDash targetId 27 expects K.
    return frame(FRAME_ENGINE,
                 rpm,
                 map_abs_mbar,
                 load_pct * 10.0,
                 (iat_c + 273.15) * 10.0)


def boost_frame(boost_mbar, target_mbar, peak_mbar, n75_pct):
    return frame(FRAME_BOOST,
                 boost_mbar, target_mbar, peak_mbar, n75_pct * 10.0)


def status_frame(baro_mbar, faults, reconnects, hz):
    return frame(FRAME_STATUS, baro_mbar, faults, reconnects, hz * 10.0)


def tune_frame(timing_deg, rail_bar, lambda_cmd, pedal_pct):
    return frame(FRAME_TUNE,
                 timing_deg * 10.0, rail_bar * 10.0,
                 lambda_cmd * 1000.0, pedal_pct * 10.0)


def fuel_frame(pump_duty_pct, fuel_temp_c, rail_spec_abs, rail_abs):
    """Fuel supply, all from measuring block 106.

    Rail pressure appears here as ABSOLUTE, specified alongside actual,
    which is what shows whether the pump is keeping up. FRAME_TUNE keeps
    carrying the GAUGE value for the gauge itself.
    """
    return frame(FRAME_FUEL,
                 pump_duty_pct * 10.0, fuel_temp_c * 10.0,
                 rail_spec_abs * 10.0, rail_abs * 10.0)


def knock_frame(k1, k2, k3, k4):
    return frame(FRAME_KNOCK, k1 * 10.0, k2 * 10.0, k3 * 10.0, k4 * 10.0)


def trip_frame(odo_raw, cat_temp_c, run_time_s, dist_clear_km):
    # odo_raw is the block-075 counter, 10 km per count; the XML multiplies
    # it back up, because 185880 km would not fit in a 16-bit field.
    return frame(FRAME_TRIP,
                 odo_raw, cat_temp_c * 10.0, run_time_s, dist_clear_km)


# Lamp thresholds. Deliberately conservative: a warning light that cries wolf
# gets ignored, which is worse than not having one.
COOLANT_WARN_C = 105.0
OIL_WARN_C = 130.0
KNOCK_WARN_DEG = 1.0        # any cylinder pulling this much timing
BOOST_UNDER_MBAR = 300.0    # actual this far under spec, under load
BOOST_OVER_MBAR = 250.0
# ~1 s at the 15-18 Hz the gauge actually runs at
BOOST_BAD_SAMPLES = 16
_boost_bad_run = 0     # actual this far over spec

# A DCC visit normally refreshes blocks 009 and 011 together every 20 s or
# more. Thirty seconds tolerates the normal scheduler cadence while still
# making a missed/failed visit visible promptly.
DCC_FRESH_S = 30.0


def warn_frame(g, n_faults):
    """Warning lamps as clean 0/1 flags.

    The stored-fault COUNT cannot drive a lamp on its own: RealDash reads a
    lamp input's low bit, so a count of 2 would read as OFF. Hence an explicit
    MIL flag here, bound to RealDash's built-in Check Engine Light (65) so any
    downloaded dashboard lights up without configuration.
    """
    mil = 1 if n_faults else 0
    overtemp = 1 if (g.coolant > COOLANT_WARN_C or g.oil > OIL_WARN_C) else 0
    knock = 1 if max(abs(g.knock1), abs(g.knock2),
                     abs(g.knock3), abs(g.knock4)) >= KNOCK_WARN_DEG else 0
    # Only meaningful ON boost. At idle the ECU's "specified" charge pressure
    # is a closed-throttle manifold target (~300 mbar absolute) while actual
    # manifold pressure sits near atmospheric, so an ungated comparison reads
    # +700 mbar and screams overboost at every red light. Verified on the car
    # 2026-08-23: boost -0.01 bar, target -0.71 bar, load 16 %, 720 rpm.
    # Boost deviation must PERSIST to count. Measured instantaneously it
    # fired on 7.8% of the 2026-08-23 drive across 55 episodes, and the
    # samples show why: "boost +0.09 / target +0.77" is the turbo still
    # spooling, "boost +0.63 / target +0.03" is a throttle lift. Both are
    # normal transients where actual simply has not caught target yet. A
    # genuine overboost or a failing wastegate SUSTAINS, so requiring the
    # condition to hold for ~1 s keeps the real fault and drops the noise.
    global _boost_bad_run
    dev = g.actual - g.spec
    on_boost = g.load > 60 and g.rpm > 1500
    bad_now = on_boost and (dev < -BOOST_UNDER_MBAR or dev > BOOST_OVER_MBAR)
    _boost_bad_run = _boost_bad_run + 1 if bad_now else 0
    boost_bad = 1 if _boost_bad_run >= BOOST_BAD_SAMPLES else 0
    return frame(FRAME_WARN, mil, overtemp, knock, boost_bad)


def temps_frame(coolant_c, oil_c, volts, speed_kmh):
    return frame(FRAME_TEMPS,
                 coolant_c * 10.0, oil_c * 10.0,
                 volts * 1000.0, speed_kmh * 10.0)


def spark_fuel_frame(timing_deg, inject_ms):
    """The two tuning numbers that must not lag: ignition advance and
    injector pulse width.  Same scaling as their slow-frame counterparts
    (0xC88 word 0 and 0xC84 word 3) so nothing downstream has to special
    case them."""
    return frame(FRAME_SPARK_FUEL, timing_deg * 10.0, inject_ms * 100.0, 0, 0)


def air_frame(ambient_c, maf_gs, throttle_pct, inject_ms):
    return frame(FRAME_AIR,
                 ambient_c * 10.0, maf_gs * 100.0,
                 throttle_pct * 10.0, inject_ms * 100.0)


def _negative_part(value):
    """The magnitude of a value when it is negative, else zero."""
    return -value if value < 0 else 0.0


def split_frame(trim_short_pct, trim_long_pct, steer_deg):
    """Pre-clamped outward halves for the two-way bars on pages 1 and 3.

    The inward halves need no help: a left-to-right RealDash bar already
    clamps a negative input to zero, so they bind straight to the raw
    channel.  Only the outward halves need the sign split, and doing it here
    costs one frame on a cadence the source data already moves at."""
    return frame(FRAME_SPLIT,
                 _negative_part(trim_short_pct) * 10.0,
                 _negative_part(trim_long_pct) * 10.0,
                 _negative_part(steer_deg), 0)


def trim_frame(trim_add_pct, trim_mult_pct, charge_air_c):
    return frame(FRAME_TRIM,
                 trim_add_pct * 10.0, trim_mult_pct * 10.0,
                 charge_air_c * 10.0, 0)


def text_frame(can_id, text):
    """RealDash '55' frame: tag, id, then a null-terminated UTF-8 string."""
    return TEXT_TAG + struct.pack("<I", can_id) + text.encode("utf-8") + b"\x00"


def dtc_frame(dtcs):
    """Stored fault codes as four raw 16-bit values.

    The raw value is also the VAG 5-digit code in decimal (0x0234 = 564 =
    VAG 00564 = P0234), so the XML can name known codes with an enum and
    still show anything unrecognised as a number.
    """
    words = []
    for entry in (dtcs or ())[:4]:
        words.append((entry[1] << 8) | entry[2])
    return frame(FRAME_DTC, *words)


class CommandReader:
    """Parses 'set value' frames sent by RealDash back to the board.

    Inbound frames are 17 bytes: the same 4-byte tag, 4-byte id and 8-byte
    payload as outbound, plus a trailing checksum byte that is the sum of
    the preceding 16, truncated to 8 bits.

    IMPORTANT: this must read from the usb_cdc DATA channel, never the
    console. A payload byte of 0x03 arriving on the console is interpreted
    as Ctrl-C and kills the running program, and command payloads are
    arbitrary binary. Pass None for the port to disable commands entirely.
    """

    def __init__(self, port, maxbuf=256):
        self.port = port
        self.buf = bytearray()
        self.maxbuf = maxbuf

    def poll(self):
        """Returns {can_id: (w0, w1, w2, w3)} for every complete valid frame
        received since the last call. Never blocks."""
        out = {}
        if self.port is None:
            return out
        n = self.port.in_waiting
        if n:
            self.buf.extend(self.port.read(n))
        # Resync rather than grow without bound if we are fed junk.
        if len(self.buf) > self.maxbuf:
            self.buf = self.buf[-self.maxbuf:]

        while True:
            i = self.buf.find(TAG)
            if i < 0:
                # keep a partial tag at the tail, drop the rest
                self.buf = self.buf[-3:] if len(self.buf) > 3 else self.buf
                return out
            if len(self.buf) - i < 17:
                del self.buf[:i]
                return out
            f = self.buf[i:i + 17]
            del self.buf[:i + 17]
            check = 0
            for k in range(16):
                check = (check + f[k]) & 0xFF
            if check != f[16]:
                continue                      # corrupt, resync on next tag
            can_id = struct.unpack("<I", f[4:8])[0]
            out[can_id] = struct.unpack("<HHHH", f[8:16])


# send_all's slow-group cadence. Only 0xC80/0xC81 carry values that can
# change between consecutive cycles: everything else updates at the extra-
# read rotation (>= 6 cycles) or the aux visit rate (5 s), so rebuilding
# and rewriting those 12 frames every cycle burned ~4 ms/cycle producing
# byte-identical output. Every SLOW_EVERY-th call sends the full set; at
# 17 Hz that is a ~300 ms worst-case staleness on channels whose sources
# refresh no faster than 350 ms anyway.
SLOW_EVERY = 5
_tick = 0


def send_all(port, g, faults=0, hz=0.0, dtcs=None, aux=None):
    """Push one update for a boost.Gauge to an open serial port.

    Frames are gathered into ONE buffer and written with a single call.
    Separate write()s meant repeated chances to eat the 0.5 s timeout when
    the deck stopped draining; one write caps that exposure per cycle.
    """
    global _tick
    buf = bytearray()
    buf += engine_frame(g.rpm, g.actual, g.load, g.iat)
    buf += boost_frame(g.actual - g.baro,
                       g.spec - g.baro,
                       g.peak_bar * 1000.0,
                       g.n75)
    buf += spark_fuel_frame(g.timing, g.inj_ms)
    _tick += 1
    if _tick >= SLOW_EVERY:
        _tick = 0
        buf += status_frame(g.baro, faults, g.errors, hz)
        buf += temps_frame(g.coolant, g.oil, g.volts, g.speed)
        buf += air_frame(g.ambient, g.maf, g.throttle, g.inj_ms)
        buf += trim_frame(g.trim_add, g.trim_mult, g.charge_air)
        # Steering only exists when the aux reader is running; without it the
        # left half of the steering bar simply stays at zero.
        buf += split_frame(g.trim_add, g.trim_mult,
                           aux.steer_deg if aux is not None else 0.0)
        buf += tune_frame(g.timing, g.rail_bar, g.lambda_cmd, g.pedal)
        buf += knock_frame(g.knock1, g.knock2, g.knock3, g.knock4)
        buf += trip_frame(g.odo_raw, g.cat_temp, g.run_time, g.dist_clear)
        buf += fuel_frame(g.fuel_pump_duty, g.fuel_temp,
                          g.rail_spec_abs, g.rail_abs)
        buf += dtc_frame(dtcs)
        buf += warn_frame(g, faults)
        # Always publish DCC status, even when the aux reader is disabled.
        # Otherwise an old RealDash value can look live indefinitely.
        buf += dcc_status_frame(aux)
        if aux is not None:
            # Held values, refreshed a few times a minute by AuxReader.
            # Sent on the slow cadence so dashboards see steady channels.
            buf += chassis_frame(aux.gear, aux.steer_deg,
                                 aux.height[0], aux.height[1])
            buf += damper_frame(aux.damper)
            buf += aux_frame(aux.height[2], aux.visits, aux.fails,
                             aux.age_of("gear"))
    port.write(bytes(buf))


# ---- chassis / transmission / steering -----------------------------------
# These come from auxmods.py, which visits other modules a few times a
# minute rather than continuously -- see the measurements in that file. The
# values are therefore HELD between refreshes, not live at frame rate.

# RealDash's gear channel is numeric, but the DSG reports a LETTER (formula
# 17). This is the mapping RealDash's own gear display expects: negative for
# reverse, 0 for neutral, 1..n for the forward gears. P has no numeric
# equivalent, so it shares neutral's 0 and is told apart by the text channel.
GEAR_CODE = {
    "P": 0, "N": 0, "R": -1,
    "1": 1, "2": 2, "3": 3, "4": 4, "5": 5, "6": 6, "7": 7,
    "D": 8,          # D without a specific gear reported
}


def gear_code(letter):
    return GEAR_CODE.get((letter or "").upper()[:1], 0)


def chassis_frame(gear_letter, steer_counts, height1_v, height2_v):
    """Gear, steering angle and the two front ride-height sensors.

    steer_counts is the RAW signed count from EPS block 007 field 1. It is
    NOT degrees: the scale factor still needs a known-angle calibration, so
    sending raw counts is honest -- a made-up scale would silently produce
    wrong degrees on the dashboard.
    """
    return frame(FRAME_CHASSIS,
                 gear_code(gear_letter),
                 int(steer_counts),
                 int(height1_v * 1000.0),
                 int(height2_v * 1000.0))


def damper_frame(dampers):
    """The four DCC damper channels, raw. Formula 24 is not one this
    project decodes, so the unit is still unknown -- these are the raw `b`
    bytes, 0 at rest and ~45 while the car is moving."""
    d = list(dampers) + [0, 0, 0, 0]
    return frame(FRAME_DAMPER, int(d[0]), int(d[1]), int(d[2]), int(d[3]))


def aux_frame(height3_v, visits, fails, gear_age_s):
    """Rear ride height plus the aux poller's own health.

    The ages matter: these values are refreshed a few times a minute, not at
    frame rate, so a reader has to be able to tell a stale value from a
    fresh one. gear_age_s is capped at 600 so it cannot wrap the 16-bit
    word during a long ignition-on soak.
    """
    age = 0 if gear_age_s is None else min(int(gear_age_s), 600)
    return frame(FRAME_AUX, int(height3_v * 1000.0),
                 min(int(visits), 65535), min(int(fails), 65535), age)


def dcc_status_frame(aux):
    """DCC snapshot freshness for RealDash and the datalogger.

    Status values are 0=unavailable, 1=fresh and 2=stale. Both DCC blocks
    must have succeeded before the snapshot is considered available. The
    age word uses 65535 as an explicit unavailable sentinel; the XML renders
    that exact value as ``--`` rather than a plausible zero.
    """
    if aux is None:
        return frame(FRAME_DCC_STATUS, 65535, 0)

    height_age = aux.age_of("height")
    damper_age = aux.age_of("damper")
    if height_age is None or damper_age is None:
        return frame(FRAME_DCC_STATUS, 65535, 0)

    age = max(height_age, damper_age)
    status = 1 if age <= DCC_FRESH_S else 2
    return frame(FRAME_DCC_STATUS, min(int(age), 65534), status)
