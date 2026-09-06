# Engine measuring blocks — what is where, and how it was proven

ECU `06J906026AR 5271 MED17.5`, engine CAWB. TP 2.0 channel tx `0x740`,
rx `0x300`. **128 of 255 measuring blocks answer.** Full raw sweeps live in
`captures/` (gitignored); regenerate with `tools/enginescan.sh`.

## The bug that started this

Four channels never changed for a whole drive: `fuel_rail_pressure`,
`catalyst_temp`, `pedal_position`, `lambda_commanded`. They came from
`boost.EOBD_SNAPSHOT` — generic OBD mode 01, read **once** before the TP 2.0
channel opens, because this ECU tears TP 2.0 down on every mode 01 request
(measured: 16 Hz → 0.7 Hz).

The handoff called them "one reading, taken once and then held". That was
half right. The other half is *when* the reading is taken: the board boots
when the head unit powers its USB, which is **ignition on, engine not
running**. So every held value was an engine-off value.

Proven directly on 2026-08-24. Same car, same warm idle, two boots:

| | booted engine-OFF | booted engine-RUNNING |
|---|---|---|
| `fuel_rail_pressure` | 2.0 | 39.2 |
| `catalyst_temp` | 22.4 | 487.7 |
| `lambda_commanded` | 1.999 | 1.000 |
| `run_time` | 0 | 2068 |

`run_time = 0` is the tell — PID 0x1F is seconds since engine start.
`lambda 1.999` was never a reading at all: PID 0x44 answered `0xFFFF`
(unsupported), and 65535 × 2/65536 = 1.99997.

## What was identified, and by what evidence

Identification here means a value that is **provably the same quantity**, not
one that looks plausible. Two methods were used, in order of strength:

1. **Shared raw byte.** If a measuring-block field and an EOBD PID carry the
   identical byte at the same moment, they are the same signal. Nothing else
   explains it.
2. **SI cross-reference.** EOBD reports in defined units. Reading a PID and a
   block at the same steady idle makes the ratio between them the block's
   scale factor. This is how formula 27 was confirmed in an earlier session
   (block 003 field 4 = −1.5° and PID 0x0E = −1.5°).

| Channel | Source | Evidence | Δ |
|---|---|---|---|
| `catalyst_temp` | **blk 115 fld 5**, f5 | 486.0 vs PID 0x3C 488.3; 480.0 vs 488.3 on a second run | 0.47 % |
| `fuel_rail_pressure` | **blk 106 fld 1**, f83 | 39.09 vs PID 0x23 39.10 | 0.04 % |
| rail *specified* | **blk 106 fld 0**, f83 | pinned at 4000 while fld 1 wandered around it | — |
| `pedal_position` | **blk 62 fld 2**, f23 | raw byte `0x26`, PID 0x49 raw byte `0x26` | identical |
| throttle | **blk 62 fld 0**, f23 | raw byte `0x1F`, PID 0x11 raw byte `0x1F` | identical |

**Catalyst temperature costs nothing.** Block 115 is `BLOCK_MAIN`, already
read every acquisition cycle at ~17 Hz for rpm/load/boost. Field 5 was simply
never claimed.

### Formula 83 (0x53) — derived, not looked up

Absent from the public jazdw/vag-blocks table, which implements the common
ids and falls through to raw for the rest. Derived on the car:

    4007 counts x 0.01 = 40.07 bar absolute
    40.07 - 1.005 baro = 39.07 bar gauge   vs PID 0x23's 39.10

So **0.01 bar per count, reported ABSOLUTE**. Absolute is forced, not
assumed: the PID's 39.10 sat *below* field 1's entire oscillation range
(3964..4051), which two readings of the same quantity in the same units
cannot do. `Gauge.rail_bar` subtracts ambient, matching the convention this
codebase already uses for boost.

> **Careful with hex vs decimal.** jazdw's table is keyed in hex; `tp20`
> stores the raw byte as decimal. Their `0x31` is our `f49`, not our `f31`.
> Reading that table as decimal will silently mis-decode fields.
> Their `0x25` = our `f37` = binary — which is why `f37(00,00)` is
> everywhere: it is this ECU's padding for unused fields.

### Validated across a real drive

971 s, rpm 0–6560, boost 980–2550 mbar absolute (1.55 bar gauge):

| | IDLE | CRUISE | BOOST | max |
|---|---|---|---|---|
| rail actual | 41.8 | 55.9 | 147.8 | 151.0 bar |
| rail specified | 40.0 | 56.1 | 148.2 | 150.0 bar |
| catalyst | 495 | 699 | 756 | 906 °C |
| pedal | 14.8 | 27.0 | 61.5 | 80.1 % |

Specified tracks actual to within 0.4 bar at every state — that is the
closed-loop pair behaving as one. 14.9 % pedal with a foot off the pedal is
this sensor's real resting output, not an error; PID 0x49 agrees.

## Block 106 confirmed against outside sources

Independent VAG documentation describes MVB 106 on the 2.0 TSI as four
fields, and all four match what was derived blind from this car:

| Field | Documented | Measured here |
|---|---|---|
| 0 | Fuel Rail Pressure (specified) | 40.00 bar |
| 1 | Fuel Rail Pressure (actual) | 40.09 bar |
| 2 | Fuel/lift pump duty cycle | 49.8 % |
| 3 | Fuel temperature (calculated) | 89 °C |

Two more corroborations worth keeping:

* The spec "at least 50 bar at idle, around **150 bar** at wide open
  throttle" — this car reached **147.8 bar** on boost, which independently
  validates the 0.01 bar/count scaling of formula 83.
* Ross-Tech's wiki gives thresholds for the pump duty: **35–55 % normal at
  idle**, 55–60 % a low-pressure concern, **over 70 % a failing or
  restricted pump**. This car reads 49.8 %, mid-normal. That makes it a
  genuine early warning for the HPFP/lift pump, a known failure mode on
  these engines — so fields 2 and 3 are now wired as channels
  (`fuel_pump_duty`, `fuel_temp`) on new frame **0xC8F**, at no bus cost
  because block 106 was already being read.

Blocks 002 (rpm, load, injection ms, air mass) and 003 (rpm, air mass,
throttle, timing) were confirmed the same way, 4/4 each.

**Sources that do NOT apply.** Diesel (PD/TDI) measuring-block lists are
easy to find and use completely different group numbering — group 003 is
EGR there, not air mass. Do not map them onto this engine. Likewise the
claim that "MED17 maps measuring blocks to UDS DIDs via service 0x22" is
false for this ECU, which was tested directly and answers nothing on 0x22.

### Why blocks 030/031/034 look empty

They are not broken. VAG documentation notes that blocks **034, 036 and 046
are run in BASIC SETTINGS**, not in plain measuring-block reads — basic
settings actively drives the ECU into a test mode, which is a write and is
out of scope for this read-only project. Block 030 fields 1–2 are
three-digit binary status codes, which is exactly what this car returns
(`bits 0F/0E`). So the lambda groups behave as documented; there simply is
no live ratio among them.

## Lambda / AFR — NOT available on this ECU

Searched thoroughly and **not found**. Recording the negative so nobody
repeats it:

* All 128 responding blocks were swept and decoded. No field carries a
  lambda ratio.
* Blocks **030–034**, the documented VAG lambda groups, are nearly empty
  here. 032 holds the fuel-trim adaptations (additive/multiplicative) and
  the rest are padding.
* The **formula-66** fields (blocks 031, 033, 036, 037, 043, 086, 098)
  oscillate hard at idle, which looks promising until you see the raw bytes:
  `b` flips between 3–6 and 35–41, a **bimodal square wave**. That is a
  switching O2 sensor **voltage** (~0.06–0.82 V), not a ratio.
* PID 0x44 answered raw `8000` = exactly 1.000 with the engine running, so
  the value exists — over **EOBD only**, which cannot be polled live here.
* PIDs 0x34 and 0x24 (O2 equivalence ratio) are **not supported** — asked,
  not assumed, via the supported-PID bitmaps.
* **UDS is not available.** A VCDS screenshot of "Advanced Measuring Values"
  showed `IDE00558/00559` *broadband* oxygen sensor voltage and current and
  `IDE01912` "specified value" — exactly what is wanted. But those are UDS
  DataIdentifiers read with service 0x22, and this ECU **does not answer
  0x22 at all**: F190, F19E, F186, F444, F40C, F405, F443, 2000, 2001,
  1000, 0101 all returned no reply, while mode 01 answered normally. That
  screen belongs to a newer UDS controller. This one is KWP/TP 2.0, which is
  also why it has 128 KWP measuring blocks — a pure UDS ECU has none.

So AFR has three honest options, none free:

1. **Leave it on the boot snapshot.** Correct only if the board happens to
   boot with the engine running; garbage (`0xFFFF`) at ignition-on.
2. **Poll PID 0x44 occasionally**, e.g. every 30 s, accepting one TP 2.0
   teardown and reconnect each time. Rejected by the owner: no EOBD in the
   live path.
3. **Wire the wideband sensor directly** to a spare analog input. The only
   route to a true live AFR on this car.

## Supported EOBD PIDs (asked, not assumed)

    bitmap 00: BE1FB013   bitmap 20: A005A011   bitmap 40: FED00000
    01 03 04 05 06 07 0C 0D 0E 0F 10 11 13 14 1C 1F 20 21 23 2E 30 31 33
    3C 40 41 42 43 44 45 46 47 49 4A 4C

Not supported: 0x22, 0x24, 0x34, 0x3D, 0x3E.

## Block rotation as it now stands

    BLOCK_MAIN 115   every cycle   rpm, load, boost spec/actual, CATALYST
    FAST_TIER        118, 3, 5, 2, 11, 20, 106, 62
    SLOW_TIER        4, 7, 75, 32     (>= 10 s escape via SLOW_MAX_AGE_S)

Rail pressure and pedal joined the fast tier because both are read *during*
a pull, which is this tier's stated rule. Fuel trims (block 32) moved the
other way to pay for a slot: they are adaptation values that drift over
minutes and were dead flat across 46 idle sweeps. Net cost is one extra fast
block, 7 → 8, so ~2.9 s per channel under sustained load instead of ~2.5 s.

## Still unidentified

Fields that clearly moved during the drive but have no published formula and
no known-unit reference to check against. `a` is a fixed scale byte and `b`
carries the data in most of them. **Do not guess these** — a smooth-looking
sweep already got frame `2e 14` labelled "steering angle" once, wrongly.

| field | raw range | tracks | r |
|---|---|---|---|
| blk 14 fld 5 f99 | 157–65426 | load | 0.85 |
| blk 13 fld 5 f91 | 4192–4221 (a=16) | load | −0.78 |
| blk 89 fld 4 f97 | 2573–2771 (a=10) | rpm | 0.75 |
| blk 122/120 fld 2 f52 | 39474–39585 (a=154) | boost | 0.72 |
| blk 13 fld 4 f91 | 2192–16016 | boost | 0.71 |
| blk 41/91 fld 0 f80 | 18–36 (a=0) | rpm | 0.73 |
| blk 60/62 fld 1 f23 | 87.5 % → 32 % | boost | −0.85 |
| blk 104 fld 7 f21 | 3.76 V → 1.22 V | boost | −0.84 |

Two caveats on that table. First, per-field sampling was thin under boost —
a 128-block sweep takes ~10 s and a pull lasts ~5, so each field got only
~3 on-boost samples out of ~99. Second, correlation is not identity.

To resolve them, re-run with a **short** `live` list (10–15 blocks) so a
sweep takes ~1 s, and drive with sustained load rather than brief pulls.


## Acquisition rate: what the protocol will and will not give (2026-08-29)

Measured on the car, and settled, so nobody re-derives it:

**One TP 2.0 block read costs ~50 ms round trip.** That is the whole budget:
this ECU answers about **20 reads per second** and no scheduling changes it.
`BLOCK_MAIN` (115) takes one every cycle, which is what gives rpm and boost
their rate; everything else competes for the remainder.

| priority slot for blocks 3 + 2 | rpm / boost | timing | injection |
|---|---|---|---|
| none — shipping config | **17.1 Hz** | 0.34 Hz | 0.34 Hz |
| one per cycle | 8.6 Hz | ~4.3 Hz | ~4.3 Hz |

The second row was built, deployed and driven. It works exactly as modelled,
and it was **reverted**: 8.6 Hz is visibly choppy on the tach and boost
needle, which are the instruments this project exists for. 10 Hz on the
tuning channels is arithmetically impossible — two channels at 10 Hz is the
entire 20-reads-per-second budget with nothing left for rpm and boost.

**Block 115 has nothing spare.** It is at least six fields long and only
0/1/2/3/5 were ever identified, so fields 4 and 6 were probed live at idle:
both read a constant 0 across 83 log rows. There is no way to get timing or
injector pulse width out of the read that already happens every cycle.

**What survives.** Frame `0xC93` ships spark advance and injector pulse width
on every cycle instead of every fifth, so whatever the rotation last acquired
reaches RealDash without also waiting on the slow block. It costs no ECU
reads, so it is pure gain and stays in.

The real fix is the planned hardware revision: reading the ECU's own CAN bus
instead of KWP2000 over TP 2.0 removes the 50 ms round trip and with it this
entire trade-off.
