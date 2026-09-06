# The real module map for this car (2026-08-23)

Derived by asking every address from `0x00` to `0x7F` for its own ident
string, rather than trusting the Ross-Tech label-file table. That table is
**wrong for this gateway** and following it cost a whole session: a curated
26-address sweep concluded the instrument cluster and body modules were "not
fitted" when they were simply at other addresses.

## What answered

| TP2.0 addr | Ident | What it is | Blocks |
|---|---|---|---|
| `0x01` | `06J906026AR 5271 MED17.5` | Engine | 40 |
| `0x02` | `02E300051R 1971 LGSG DSG AG6` | **DSG transmission** | 17 |
| `0x03` | *(no ident)* | ABS/ESP — opens a channel, no blocks 1..40 | 0 |
| `0x05` | `1K0909605AE ... AIRBAG VW8` | Airbag | 5 |
| `0x07` | `1K8920870F 2444 KOMBIINSTRUMENT` | Instrument cluster | 8 |
| `0x09` | `1K0909144E 2501 EPS_ZFLS` | Electric power steering | 6 |
| `0x0C` | *(no ident)* | **DCC adaptive chassis** | 6 |
| `0x14` | `1K8920870F 444 IMMO` | Immobiliser (lives in the cluster) | 4 |
| `0x1D` | *(no ident)* | Probably parking aid | 8 |
| `0x1F` | `1K0907530S 0210 J533__Gateway` | Gateway | 13 |
| `0x28` | *(no ident)* | Channel opens, no blocks | 0 |
| `0x2A` | `1K0953549CD 111 J0527` | Steering column electronics | not scanned |
| `0x2C` | `1K0907044DA 1213 ClimatronicPQ35` | Climate control | not scanned |

`0x2A` and `0x2C` identified in phase 1 but failed to reopen for the block
scan — they need a re-run on their own.

## The addresses are NOT the VCDS addresses

| Module | VCDS shows | Answers at |
|---|---|---|
| Engine | 01 | `0x01` |
| Transmission | 02 | `0x02` |
| Climatronic | 08 | `0x2C` |
| Airbag | 15 | `0x05` |
| Steering column J527 | 16 | `0x2A` |
| Instruments | 17 | `0x07` |
| Gateway | 19 | `0x1F` |
| Immobiliser | 25 | `0x14` |
| Steering assist | 44 | `0x09` |

Only engine and transmission land on their VCDS numbers. There is no
arithmetic offset (15->05, 17->07, 44->09, 25->14, 16->2A), so this looks
like a routing table inside the gateway rather than an encoding bug on our
side. **Never assume an address; ask for the ident.**

## DCC / adaptive chassis, address `0x0C`

Volkswagen's own description of the system: *"four electrically adjustable
dampers, three level sensors, three acceleration sensors, a control unit and
a switch"*. The blocks line up with that exactly, which is what identifies
this module despite it returning no ident string.

```
0C/001  f37(00,31) | 12.285 V | f158(00,00) | 2.0
0C/003  f23(80,AC) | f23(80,64) | f23(80,44)
0C/009  2.475 V | 2.500 V | 2.500 V | 4.975 V     <- 3 level sensors + 1 at rail
0C/011  f24(0C,00) x4                             <- 4 damper channels, equal at rest
0C/013  f24(0C,00) | f23(80,00) | f24(0C,00) | f23(80,00)
0C/015  1.0 | f37(02,1C) x3
```

Block 009 is the giveaway: three channels sitting at ~2.5 V is a position
sensor at mid-travel against a 5 V reference, and the fourth at 4.975 V is
an unused input pulled to the rail. Block 011 being four IDENTICAL values
with the car stationary is what four damper valves commanded alike look
like. Both still need confirming by watching them move.

## Gear, address `0x02`

```
02/003  " P" | " P" | ...        formula 17 = two ASCII chars
02/020  " R" | " 2" | " "
```

Formula 17 renders as `"%c%c" % (a, b)`, so the selected gear is a literal
**letter** in byte b, not a number. This is the real fix for RealDash showing
"N" while the car sits in P: RealDash was using its CALCULATED gear
(`<map key="200" value="25"/>` in the settings export), which is derived from
speed / rpm / ratio and is meaningless stationary.

## Capture rule, learned the hard way

Probe output must NOT be captured through the tee. The tee only keeps a
session open while it believes the engine is running, and it decides that
from the gauge frames -- which a probe does not emit. On 2026-08-23 a shifter
sweep was performed in the car and **every byte was discarded**: the session
closed at 18:15 and the sweep landed in the gap before the next opened at
18:21. Zero markers survived in any of the twelve session files.

Use `deck/probecap.py`, which reads the bridge directly and has no notion of
sessions:

```
adb shell "run-as com.termux sh -c 'pkill -f \"[w]atchdog.sh\"; pkill -f \"[t]ee.py\"'"
adb forward tcp:12323 tcp:2323
python deck/probecap.py --port 12323 --seconds 900 --out sweep.log
```

Note the `[w]` bracket trick: a plain `pkill -f watchdog.sh` matches its own
command line and fails with "Operation not permitted".

The watchdog lives in `files/home/scirocco/`, not `files/home/`. Restart it
with `cd files/home/scirocco && nohup sh watchdog.sh &` or the tee stays down
and nothing is logged.

## Sweep cost

`Channel.connect()` defaults to FOUR handshake attempts, so a dead address
cost 0.2 s + 4 x 0.6 s, twice over -- about 9 s each, and ~20 minutes for the
128-address space. Probing now passes `attempts=1` (a module that is present
answers first time), which brings a full sweep to roughly 2 minutes. The
block-scan phase keeps the full retry budget, because recovering a half-open
channel genuinely needs it.

## DCC confirmed by a bounce test (2026-08-23)

Ignition on, engine off, each corner pushed down in turn with pauses
between. 30 samples of block 009 captured through `probecap.py`.

| Field | Range over the test | Verdict |
|---|---|---|
| 1 | 2.375 – 2.600 V | level sensor, **front** |
| 2 | 2.400 – 2.575 V | level sensor, **front** |
| 3 | 2.375 – 2.575 V | level sensor, **rear** |
| 4 | 4.975 V, span **0.000 V** | not a sensor — input tied to the 5 V rail |

Field 4 never moved by a single count across 30 samples while the other
three swung, which settles it: this car has **three** level sensors, not
four. That matches Volkswagen's own description of DCC and contradicts the
forum claim of two.

Front/rear separation is clean in the time series: during the first bounce
group fields 1 and 2 moved while field 3 sat still at 2.500 V; during the
later group field 3 swung 2.375–2.575 V while 1 and 2 barely moved.

**Left/right is NOT separable from this test.** Pushing one front corner
moves both front sensors -- the anti-roll bar and body tie them together --
so which of fields 1 and 2 is which side needs a one-sided test (jack one
front wheel, or load one seat) before either is labelled.

Scale: formula 6 with a = 0x19 (25), so one count is 25 mV. A hard push on
a cold, stiff car produced ~9 counts. Real load transfer on track will be
far larger, so the resolution is usable -- but it is coarse for detecting
small road inputs.

### Damper channels, block 011

```
at rest      [ 0,  0,  0,  0]
moving       [45, 44, 45, 45] ... [45, 46, 45, 46]
transient    one channel briefly 83
```

All four channels sit at zero with the car still and rise together to ~45
while it moves, with brief excursions to 83. That is four independently
reported damper channels responding to suspension movement, which is what
The owner remembered being able to read. Formula 24 is not one this decoder
knows, so the unit is still unidentified -- the numbers above are the raw
`b` byte.
