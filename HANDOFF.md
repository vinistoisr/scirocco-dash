# Handoff: Scirocco boost gauge

Updated 2026-08-21, evening. Read `CLAUDE.md` first for the operational summary; this
file is the reasoning, the decisions, and the dead ends, so nobody re-treads them.

**Status: working.** Boost is being read live off the car at ~17.6 Hz and streamed to
the head unit as RealDash CAN frames over USB. What remains is bolting it into the car
tidily and confirming the numbers under real load on a drive.

## The goal

The owner wants a live boost gauge with a rolling trace, plus other engine parameters, on
the Dudu7 Android head unit in his 2009 Scirocco 2.0 TSI. The head unit already shows
RPM, coolant, and oil temp, but not boost.

## What the car actually turned out to be

The original plan assumed UDS. It is not UDS. Measured on the car:

| | |
|---|---|
| ECU | `06J906026AR 5271 MED17.5` |
| VIN | `WVWZZZ13Z9V0xxxxx` |
| OBD port, `0x7E0`/`0x7E8` | generic EOBD mode 01 only |
| UDS service `0x22` | **not supported**, no reply to `22 F190` |
| UDS service `0x10` | **not supported**, no reply to `10 01` |
| Manufacturer diagnostics | **KWP2000 over VW TP 2.0** |
| Engine channel | dest `0x01`, tester transmits `0x740`, listens `0x300` |
| Bus | 500 kbit/s, 11-bit, quiet until addressed |

Stored fault codes, read on the first successful connection:

- **P0234** (VAG 00564) - overboost condition
- **P2261** (VAG 08801) - turbo bypass / diverter valve, mechanical

Both are boost related and predate this project. Worth fixing before trusting any
absolute boost numbers; the diverter valve is a known weak point on this engine.

## The bug that cost the most time, and the fix

The TP 2.0 channel setup request is widely published, including in the ESP32 datalogger
this project cribbed from, as:

```
tx 0x200:  01 C0 00 10 00 03 01
```

Bytes 2-3 are `00 10`, where the `0x10` high nibble is the "this ID is not valid"
marker, meaning "ECU, you pick". **This car never answers that.** Not a refusal, no
reply at all, which reads exactly like a dead bus and sent the investigation off
chasing wiring, termination, and alternative UDS addressing for hours.

The gateway wants a real ID in **both** fields:

```
tx 0x200:  01 C0 00 03 00 03 01      <- 0x300 in bytes 2-3 AND 4-5
rx 0x201:  00 D0 00 03 40 07 01      <- listen on 0x300, transmit to 0x740
```

If TP 2.0 ever goes silent again, check that field before anything else. The way this
was found is worth repeating: stop trusting an assumed response ID, capture the **whole
bus with no filter**, and send each candidate setup variant in turn. `tpdiag.py` does
exactly that and is kept for the next mystery.

## TP 2.0 gotchas that will bite again

- **Stale channels.** A channel abandoned by a previous run (board reset, program parked
  without disconnecting) makes the module answer the next setup with `D7`, or accept the
  setup and then immediately send `A8` mid-handshake. Cure: send `A8` to the advertised
  tx ID, wait, retry. `Channel.connect()` does this automatically, including once before
  the first attempt.
- **Stale frames outlive the channel.** A leftover `A8` sitting in the RX FIFO reads as
  "the ECU just hung up" against a channel that is perfectly healthy. `_drain()` is
  called when a channel starts and after the params exchange. Do not remove it.
- **One listener only.** The SAME51 has very few hardware filter slots and rebuilding a
  `canio.Listener` per reconnect exhausts them: `ValueError: Filters too complex`. The
  channel now creates a single promiscuous listener once and filters by ID in software.
  The diagnostic bus carries no broadcast traffic, so this costs nothing. A filter
  scoped to `0x200-0x2FF` also silently hides the params reply, which arrives on `0x300`.
- **The ECU hangs up during e-ink refreshes.** A tri-color refresh blocks ~15 s, far
  longer than the channel timeout, so the next sample reconnects. Harmless, but it is
  why the log shows a reconnect roughly every screen update.

## Where boost lives

Scanning all 255 measuring blocks (service `0x21`) found 128 that answer, 7 carrying a
pressure-formula field. Cross-checked against Ross-Tech's published definitions for
groups 110-119, and block 118 matched their layout field for field, which is what
confirmed the mapping rather than guesswork:

| Block | Fields |
|---|---|
| **115** | rpm, engine load %, **boost specified**, **boost actual** |
| **118** | rpm, intake air temp, N75 wastegate duty, **boost actual** |
| 78 | ..., barometric pressure at field 6 |

All pressures are **absolute mbar**. Gauge boost is absolute minus ambient:

```
gauge_bar = (actual_mbar - baro_mbar) / 1000
```

Idle sanity check, measured: actual ~1000 mbar, baro ~1005 mbar, so the gauge sits at
about zero. Manifold pressure post-throttle reads ~350-370 mbar at idle; do not confuse
the two families of number. Under full load expect roughly 2000-2400 mbar absolute.

Measuring block field encoding is 3 bytes per field: formula ID, then bytes a and b.
Formula 18 is `a*b*0.04` mbar and is how the pressure fields were located in the first
place. `tp20.decode_fields()` covers the common formulas and prints anything else raw,
so nothing is silently hidden.

## Why revving in the driveway proved nothing

An idle-versus-blip diff was tried and every pressure field moved by at most 30 mbar.
That is expected: at idle in neutral there is no load, so there is no boost, and charge
pressure genuinely sits at atmospheric. The channel identification came from the
documented block layouts instead. **The real confirmation is a drive**, where the peak
hold and the CSV will show boost climbing toward +1 bar.

## Getting it onto the head unit

Rejected, with reasons:

- **WiFi via the AirLift ESP32 FeatherWing.** Would have been ideal, the Feather could
  serve its own page. Dead: the AirLift is missing, and the head unit's WiFi is already
  committed to wireless Android Auto.
- **USB serial into Termux.** Android will not hand `/dev/ttyACM0` to Termux without
  root, and SELinux blocks it even then. A custom Android app using the USB Host API
  would work but is a project in itself.

Chosen: **RealDash over USB.** RealDash implements the Android USB Host API itself, so a
plain cable works with no root, no app development, and no WiFi. It also supplies the
gauge UI, which was the other half of the job.

Wire format is the RealDash CAN "44" frame, fixed 16 bytes, little-endian:

```
bytes 0-3   tag 44 33 22 11
bytes 4-7   CAN frame id, uint32
bytes 8-15  payload, 8 bytes
```

Frames emitted, matching `scirocco_realdash.xml`:

| ID | Payload |
|---|---|
| `0xC80` | rpm, MAP absolute mbar, load %x10, IAT 0.1 K |
| `0xC81` | boost mbar, target mbar, peak mbar, N75 %x10 (signed) |
| `0xC82` | baro mbar, fault count, reconnects, sample Hz x10 |
| `0xC83` | coolant 0.1C, oil 0.1C, battery mV, speed 0.1 km/h (signed) |
| `0xC84` | ambient 0.1C, MAF 0.01 g/s, throttle 0.1%, injection 0.01 ms (signed) |
| `0xC85` | fuel trim add 0.1%, trim mult 0.1%, charge air 0.1C (signed) |

Confirmed on the wire at warm idle: coolant 96.0 C, oil 82.0 C, battery 13.348 V,
ambient 22.5 C, MAF 3.21 g/s, throttle 3.1 %, injection 1.53 ms, trims +2.1 / +2.5 %,
charge air 37.0 C. Adding the five extra blocks cost about 1 Hz, from 17.0 to 16.0.

RealDash for Windows accepts the identical setup over the same USB cable, which is a
far quicker way to build gauge layouts than doing it in the car.

Verified on the wire with `tools/readframes.ps1`, which decodes captured bytes back into
frames. Two's complement is confirmed working: vacuum shows as `65531` unsigned = `-5`.

### Serial port layout

`boot.py` enables a second CDC channel so binary frames stay clear of console text.
**This needs a physical replug to take effect** - Windows caches USB descriptors, and
even `microcontroller.reset()` did not make the second port appear. Until then the code
falls back to putting frames on the console port and silencing all text, which is
actually the most robust configuration for RealDash: one port, pure binary.

If RealDash grabs the wrong port once two exist, set `REALDASH_ON_CONSOLE = True` in
`code.py`.

## EOBD and TP 2.0 are mutually exclusive on this ECU

Worth recording because it looks like a performance bug and is not one.

Generic EOBD PIDs were added to the live polling rotation to pick up fuel rail
pressure, commanded lambda and catalyst temperature, which no measuring block exposes
cleanly. The gauge immediately fell from 16 Hz to **0.7 Hz** and reconnects went from 1
to 5.

`eobdmix.py` isolated it. The failure alternates perfectly:

```
blocks before EOBD   20/20 ok   50.0 ms each   20.0 Hz
  PID 23  0186        7.8 ms   channel up
  PID 3C  NO REPLY    2.4 ms   channel DOWN
  PID 49  26          7.8 ms   channel up
  PID 1F  NO REPLY    0.5 ms   channel DOWN
blocks after EOBD    19/20 ok  299.9 ms each    3.3 Hz
```

Every mode 01 request on `0x7E0` makes the ECU close the TP 2.0 channel with an `A8`.
The request that triggered it still gets its answer, so it looks fine; the *next* call
reads the stale `A8`, concludes the channel died, and reconnects. Hence one working PID,
one failure, repeating.

The fix is not to interleave them. EOBD is now a **startup snapshot** taken before the
channel opens, and the live loop is pure TP 2.0. Anything needed live has to come from a
measuring block. Rate went straight back to 14 Hz with one reconnect.

## Tuning channels

The log is aimed at what a 2.0 TSI tuner wants to see, boost specified against boost
actual first. Confirmed channels: rpm, boost specified and actual, ignition timing,
fuel trims, MAF, load, throttle, N75 duty, injection time, coolant, IAT, charge air,
ambient, oil, battery, road speed, odometer.

Ignition timing deserves a note because it was nearly guessed wrong. No measuring block
on this ECU uses formula 4, the BTDC/ATDC formula, so timing was not where it should
have been. Rather than pick a plausible unknown formula, EOBD PID `0x0E` was read: it
returned **-1.5 degrees**, and block 003 field 4 carried `f27(4B,7E)`, which under
`a*(b-128)*0.01` is exactly **-1.5**. Two independent sources agreeing is what promoted
formula 27 from a guess to a fact.

Still unverified, and flagged as such in the code: **knock retard scale** (blocks read 0
at idle, which is right, but nothing knocks at idle to calibrate the degrees), **oil
temp** (see the cold-start check), ambient and charge air temp.

Not yet found live: rail pressure and lambda actual. Both exist in the EOBD snapshot but
cannot be polled live, and no measuring block has been matched to them yet. The quickest
way to finish this is a side-by-side VCDS log from the tuner: match his named channels
against the raw block dump in `mb_scan.csv` and the remaining formulas fall out.

## Performance

Measured on the car, `ratetest.py`:

```
tp20 read only     avg 50.0 ms   -> 20.0 Hz
serial write only  avg  0.1 ms
read + write       avg 50.6 ms   -> 19.8 Hz
```

Steady-state in the gauge: **17.6 Hz**. Plenty for a boost gauge.

An early reading of 1.9 Hz was an artifact of only decoding the first few frames, which
covered the startup reconnect. When measuring rate, look at the **tail** of a capture.
Initialising the e-ink display was tested as a suspect and costs nothing.

## Why this hardware

The Feather M4 CAN was already on the shelf and is better than any ELM327 dongle: raw
frames, no AT-command round trips, and standalone operation. Cost of that: writing
ISO-TP and then TP 2.0 by hand, both of which are now done.

Adapters considered and rejected: OBDLink SX (~$55 CAD), vLinker FS (~$53 CAD), generic
ELM327 CH340 (~$15), Waveshare USB-CAN-A (~$25).

Dead ends from the original investigation, still true:

- **The head unit's CAN decoder box** sits on the 100 kbit/s infotainment CAN and only
  sees what the gateway forwards. Boost is not in its dictionary.
- **The HP Tuners RTD.** Proprietary, cloud-authenticated, EULA forbids reverse
  engineering, breaks on every firmware push.
- **The Ross-Tech HEX-V2 clone.** No dumb-mode serial pass-through. Still useful for
  reading measuring blocks by name in VCDS, which is how the block mapping was
  cross-checked.

## The e-ink saga, so it is never repeated

The panel is a **2.13" 250x122 tri-color SSD1680**, not the older 212x104 IL0373. The
board shipped with **CircuitPython 7.1.0-beta.3, a December 2021 beta**, which predated
the FeatherWing entirely:

- The IL0373 driver constructed cleanly, reported "ready", and drove nothing.
- `rotation` was accepted and stored but never applied to the frame geometry.
- `colstart` did not exist, so passing it threw `TypeError`.
- Backporting the modern driver was impossible: CP 7.1 lacks `address_little_endian`
  and `two_byte_sequence_length`.

Fixed by flashing **CircuitPython 10.2.1** and installing only the current libraries.

Lesson: check the firmware date against the hardware date before debugging a driver.

## Termination

The Feather M4 CAN ships with its 120 ohm terminator **enabled**, jumper `Trm`.

Measure across OBD pins 6 and 14 first, ignition off and key out:

- ~60 ohm: bus properly terminated by the car. Cut `Trm`.
- ~120 ohm: only one terminator. Leave `Trm` alone.
- Open circuit: unterminated stub. Leave `Trm` alone.

Three terminators gives 40 ohm, below the 45-65 ohm ISO 11898-2 expects. It half-works,
which is worse than failing, because it looks like a software bug.

Keep the stub from the OBD connector under about 30 cm.

## Wiring

```
OBD pin 6  (CAN-H)    -> Feather CANH
OBD pin 14 (CAN-L)    -> Feather CANL
OBD pin 4 or 5 (GND)  -> Feather GND
OBD pin 16 (+12V)     -> NOT CONNECTED
```

Power from USB. Once it lives in the car, that USB comes from the head unit, which also
means the board sleeps with the ignition. For a tidy permanent install use a
male-to-female OBD extension and take the three wires off the extension, not the car.
Fully reversible.

## What is left

1. **Drive it.** Confirm boost climbs to roughly +1 bar under load and that the peak
   hold and CSV agree. This is the only outstanding verification.
1b. **Confirm oil temp on a cold start.** It is the one inferred channel that matters.
   Coolant should climb fast while oil lags well behind; if the two track each other
   exactly, field 6 of block 004 is not oil and the label should be dropped.
2. **Install RealDash on the deck** and set up the connection, per `CLAUDE.md`.
3. **Replug the board** once so `boot.py` can create the second serial port, if
   separating debug text from frames is wanted.
4. **Consider the two fault codes.** P2261 in particular will affect real boost.
5. Optional: log a full drive in CAR MODE and check `boost_log.csv` for the peak.

## Useful notes

- On this ECU `22 F4 xx` does **not** work; there is no UDS at all. Ignore any advice
  about mode 22 with VAG DIDs, it was written for later cars.
- Standard PID `010B` (manifold pressure) does answer over EOBD, but it is post-throttle
  MAP, not charge pressure. Not the gauge signal.
- Service `0x21` block N is exactly the VCDS measuring block N, so anything documented
  for VCDS group N applies directly.
- Tri-color e-ink takes ~15 s per refresh and cannot do partial refresh. Fine for status
  and peak-hold summaries, useless as a live gauge.
- `Set-Content -Encoding utf8` in Windows PowerShell 5.1 writes a **BOM**, which
  CircuitPython rejects with `SyntaxError` on line 1. Use
  `[System.IO.File]::WriteAllText` with `UTF8Encoding($false)`.
- The Write and Edit tools cannot create files at the root of this FAT volume
  (`EPERM: mkdir 'D:\'`). Stage edits elsewhere and `Copy-Item` them across.

## Charge pressure is not manifold pressure (2026-08-29)

The single most expensive misunderstanding in this project, so it is written
down plainly:

* **Ladedruck** (charge pressure) is measured **before** the throttle plate.
  With the throttle shut it sits at roughly ambient, because the turbo is
  still connected to the airbox and nothing is drawing a vacuum on it.
* **Saugrohrdruck** (manifold pressure) is measured **after** the plate.  With
  the throttle shut it goes deep into vacuum, around -0.7 bar gauge.

Block 115 field 2 is the charge pressure **setpoint** and field 3 the
**actual** charge pressure.  That is the original labelling and it is
correct.  They were swapped on 2026-08-29 on the reasoning that a signal
sitting flat at atmospheric all drive "cannot be manifold pressure" -- true,
but it was never supposed to be manifold pressure, and the swap made the big
needle track pedal demand while the small target tick tracked real boost.
The driver spotted it from the seat before any log did.

Evidence, from the 1272-row drive log that day: field 3 never goes below
-0.02 bar while field 2 reaches -0.77 (a pre-throttle sensor cannot read
vacuum, a setpoint can); field 3 >= field 2 in 97.1 % of rows with the gap
closing as the throttle opens; both peak at the same 1.545 bar.  Verified
live at idle after the fix -- actual -0.005 bar, spec -0.635.

**We do not currently acquire true manifold pressure.**  The `map_kpa`
channel is fed from `g.actual` in `realdash.py`, so it is charge pressure
under another name and reads ~100 kPa at idle.  If a real MAP reading is
wanted, it needs its own source.

## Deploying board firmware to the car

`/storage/USB1` on the head unit **is** the CIRCUITPY drive, so a deploy is:

    adb push board/boost.py /storage/USB1/boost.py
    # then, from Termux on the deck:
    python3 sendcmd.py reload

Writing to CIRCUITPY from the head unit does NOT trigger CircuitPython's
auto-reload, which is why `reload` exists (see `deck/sendcmd.py`).

**`reload` restarts `code.py`, which drops the live TP 2.0 session.**  EOBD
and TP 2.0 are mutually exclusive on this ECU, so restarting while the engine
is running can leave the ECU holding a half-open session and the board
reports an EOBD error; the tee goes to 0.0 Hz and climbs its reconnect
counter instead of recovering.  A power cycle of the board clears it.

So: verify the pushed file by md5 against the laptop copy *before* reloading,
and **do not reload while the engine is running without saying so first** --
it takes the gauges down mid-drive.
