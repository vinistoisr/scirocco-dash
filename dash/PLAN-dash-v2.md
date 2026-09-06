# Plan: dashboard revision v2

Updated 2026-08-25 after reviewing the repository, the actual dashboard
binary, current RealDash documentation, and five recent production datalogs
from the `scirocco-drives` R2 bucket.

This replaces the original issue list with an execution-ready design. The
current deck dashboard remains untouched until the new file passes the bench
checks at the end of this plan.

---

## Outcome and decisions already made

The v2 dashboard will:

* remove the top wordmark, `SCIROCCO` text, and red header stripe;
* use one coherent gauge-face style across all six round gauges;
* enlarge the four small gauges and give the two main gauges equal visual
  weight;
* add a dedicated live numeric readout inside every round gauge;
* use separate, translucent peak-hold ghost needles on **boost and RPM**;
* retain the boost-target needle as a third, visually distinct pointer;
* rebuild the telltales as a coherent VW/Audi-cluster-style icon family and
  leave them barely visible when inactive;
* make the fuel-trim bars grow outward from their shared zero line;
* fix steering angle so left turns display as negative values;
* repurpose the empty `Steering raw` slot as DCC data age/freshness;
* turn the spark-advance concept into a genuinely live, layered gauge; and
* split the dense data page into three legible pages, leaving connection and
  link-health information on the final page.

All work happens on the laptop. Build a new versioned file,
`dash/scirocco-v2.rd`; do not overwrite `dash/scirocco.rd`, which is the
rollback copy currently running in the car.

### Execution checkpoint — 2026-08-27

Offline design and transport work is complete: the four backgrounds, gauge
faces, needles, VW/Audi-style telltales, steering-wheel assets and guarded
clear-codes button render cleanly; the logger schema self-check passes with 65
channels; and the simulator exercises both steering signs plus fresh, stale
and unavailable DCC states. The original `dash/scirocco.rd` remains byte-for-
byte unchanged at SHA-256
`577758832FFB43B5D314EDF5F085A140FA357616BDE69779968B45D65CF7EC00`.

The structural build is also complete. The working copy has four pages and
98 controls with the intended page ownership. `rd_transform_v2.py` now
rebuilds the embedded asset table, assigns all page/gauge/button assets, and
repairs the native button label; `rd_manifest.py --apply-v2-layout` writes and
verifies every rectangle from the shared manifest. The resulting 1920×1080
file has 33 embedded PNG entries and has loaded successfully across all four
pages in RealDash.

Page 2 has also completed a screenshot-driven composition revision. The
Spark mechanism is now compacted around the native needle pivot, its tick
labels no longer overlap the valvetrain, and the AFR sensor/lambda assembly
has a smaller left-biased footprint with a separate unavailable-input card.
Both compositions retain clear negative space above the lower data panels.

The fixed-width configuration pass is now deterministic too.
`rd_config_v2.py` binds the new controls, writes all six-float range/threshold
blocks, fixes the signed steering ranges, sets the Spark and schematic-wheel
sweeps, and suppresses the duplicate built-in needle scale text. Page 1 also
bakes the matching telltales into the background at 11% opacity, guaranteeing
the requested barely-visible off state under the full-bright live icons.

The remaining work is a bounded RealDash editor pass for genuinely
variable-length/editor-owned fields: 70% text sizing and per-level colours,
MAX ghost history plus toggle visibility, outward trim direction confirmation,
DCC visibility conditions, and the Clear Codes hold action. The exact
remaining checklist and copy-back procedure are documented in
`V2-EDITOR-RUNBOOK.md`.

---

## Evidence that changes the original plan

### The dashboard and channel counts

The repository has moved since the first plan was written:

| item | verified state |
|---|---:|
| `.rd` canvas | 1920×1080, 16:9 |
| `.rd` size | 1,185,993 bytes |
| gauges in the file | **80** |
| gauge types | 12 image, 7 needle, 8 bar, 53 text |
| page 1 | 34 gauges |
| page 2 | **46** text gauges |
| current logger schema | **65** telemetry channels |

Page 2 is not 42 fields in six seven-row panels. The generator and
`page2_layout.txt` contain `8 + 8 + 8 + 8 + 7 + 7 = 46` fields. The final
semantic regrouping keeps 20 existing engine values on page 2 and moves 26
to pages 3 and 4. The empty `Steering raw` gauge is repurposed as DCC data
age rather than treated as a second angle. The original estimate of moving
28 gauges was therefore wrong.

The latest uploaded CSVs contain 69 columns because the deployed deck build
carried 63 real telemetry channels plus the six `Show Ghost *` scratch values.
The current local `frame_schema.py` correctly excludes frame `0xC92` from
logging and adds `dcc_age_s` plus `dcc_status`, exposing 65 real channels.
Treat 65 as the dashboard coverage contract; the six ghost toggles are
RealDash-local UI state, not vehicle data.

### What the recent car logs prove

Read-only production sessions examined:

* `2026-08-25_2006` — 4,494 baseline rows, 10,634 burst rows, 27 bursts,
  69-channel deployed schema; the primary evidence set;
* `2026-08-25_2124` and `2026-08-25_0003` — same-day short checks;
* `2026-08-24_1906` and `2026-08-23_2326` — longer drives with high-rate
  bursts and useful operating extremes.

| signal | observed evidence | design consequence |
|---|---|---|
| Steering angle | **−487° to +452°** across recent drives | the transport is signed and healthy; RealDash is clamping at a zero minimum |
| Steering raw | no numeric samples in the examined files | it is not a second useful angle; reuse its channel/gauge slot for DCC age |
| DCC / ride height | no numeric samples in any of the five examined sessions | the page needs an explicit unavailable/stale state, and the aux reader must be restored before calling the values live |
| Turn left | 62 on-edges in the three driven baselines | the left input works |
| Turn right | 76 on-edges in the same baselines | the right input works |
| Ignition timing | **−21.7° to +40.5°** | the spark art's −30…+20 scale would clip normal advance |
| Lambda commanded | exactly **1.000 in every running sample** examined | it is a boot-time EOBD snapshot, not a live AFR source |
| Boost | up to 1.55 bar | 1.2 amber / 1.6 red remains a sensible display policy |
| RPM | up to 6,960 rpm | the 6,300 / 6,800 thresholds are exercised by real data |
| Coolant | up to 99°C | 105°C warning stays above observed normal operation |
| Oil | up to 86°C | 120/130°C thresholds have ample normal margin |
| Charge air | up to 93°C | the 50/60°C levels will deliberately flag heat soak |
| Running voltage | normally 13.35–14.01 V; brief lows to 11.37 V | retain wide low/high limits to avoid false alerts around starting |
| Knock warning | active in the newest long drive; retard down to −2.2° | keep a high-priority knock telltale; do not infer knock from total timing alone |

The steering bug is therefore not in `deck/canbox.py`, the `0xC8E` frame, or
the XML signedness. Those layers preserve negative values. It is the range
on the RealDash text gauge.

The timing channel is total ignition advance, confirmed in the repository
against both measuring block 003 and EOBD PID 0x0E. It is not knock retard.
Negative timing can occur during normal transients, so the spark gauge must
not declare a warning from timing alone; the dedicated per-cylinder retard
and `Knock Warning` channels own that job.

The current `lambda_commanded` value must not drive a prominent AFR gauge.
The ECU returns PID `0x44` only over EOBD, and polling EOBD tears down the
high-rate TP 2.0 connection. The repository's measuring-block survey found
only a switching oxygen-sensor voltage, not a wideband ratio. A trustworthy
live AFR gauge therefore requires the planned direct wideband analog input.

### RealDash mechanisms to use

The most efficient supported mechanisms are now known:

* [Normal, Warning and Critical levels](https://realdash.net/manuals/using_normal_warning_and_critical_levels.php)
  provide independent min/max windows and per-level colours.
* [RealDash's indicator tutorial](https://realdash.net/manuals/make_an_indicator.php)
  explicitly recommends lowering the **Normal** image opacity to keep an
  inactive indicator barely visible, then using full opacity when active.
  No duplicate background icon is required.
* [Value smoothing](https://forum.realdash.net/t/disable-text-interpolation/8443)
  is under `LOOK'N FEEL -> SPECIAL -> Value Smoothing (%)`; `0` disables it,
  the common default is `80`, and `100` is adaptive.
* [Gauge history mode](https://forum.realdash.net/t/peak-gauge-values-reset/1411)
  supports `MAX`, a show time, and the `Reset Gauge History Mode` action.
* [Cut/copy/paste between pages](https://forum.realdash.net/t/change-dashboard-with-a-trigger/525)
  is supported. The page split does not require recreating 30 bindings.
* [Bar direction can be right-to-left](https://forum.realdash.net/t/bar-graph-working-in-opposite-way/487),
  which is the missing mechanism for the negative trim halves.
* The RealDash developer's [hold-button procedure](https://forum.realdash.net/t/reset-distance-trip-a-by-hold-button/1015)
  confirms that a Button Gauge can use `Initial Action Delay` with `Actions
  When Pressed Down`; `Hold Value` returns the command to zero on release.
* The official [keyboard shortcut reference](https://realdash.net/manuals/keyboard_shortcuts.php)
  is the source of truth when a scripted editor action has an equivalent
  keyboard operation.

---

## Final page architecture

### Page 1 — driving view

Page 1 remains the glanceable driving page:

* six round gauges: RPM, boost, coolant, oil, charge air, battery;
* live and target/ghost pointers where applicable;
* a numeric value inside every round gauge;
* speed, gear, odometer/trip, knock bars, N75, load, trims and boost target
  in the bottom band; and
* the six telltales in the newly freed top band.

The existing small `IGN °BTDC` text gauge moves to page 2 and becomes the
numeric element of the spark gauge. This opens useful space in the page-1
bottom strip rather than displaying the same timing value twice.

The redundant page-1 Lambda text gauge is repurposed as one of the six round
numeric readouts. The clearly labelled snapshot on page 2 is the only place
the non-live lambda value remains.

### Page 2 — engine and tuning

Page 2 answers “what is the engine breathing, burning and doing with
timing?”

* Spark advance and AFR/Lambda are paired as the two visual anchors across
  the top half. The AFR face is present in v2 but explicitly reads
  `WIDEBAND INPUT REQUIRED`; it gets no live needle or plausible-looking
  number until a real source exists.
* `AIR & LOAD` contains MAP, barometric pressure, intake-air temperature,
  charge-air temperature, ambient temperature, MAF, throttle, pedal, engine
  load and boost peak.
* `FUEL DELIVERY` contains injection time, fuel-rail pressure, specified
  rail pressure, actual rail pressure, fuel-pump duty and fuel temperature.
* `MIXTURE & EXHAUST` contains short- and long-term trim, catalyst
  temperature, and the existing lambda snapshot only if it is clearly
  labelled `SNAPSHOT` rather than AFR.
* The existing ignition text gauge is cut from page 1 and pasted into the
  spark gauge; its binding and one-decimal precision are retained.

This is a deliberate regrouping, not a rearrangement of the old panels:
airflow, driver demand, fuel supply, mixture correction, exhaust temperature,
spark and the future wideband all belong to the engine/tuning question.

### Page 3 — driveline and chassis

This page is a top-down chassis schematic rather than another table. Use a
subtle Scirocco body outline as context, then draw the front subframe,
steering rack, four wheel/strut assemblies and rear multi-link geometry in a
restrained VW technical-illustration style.

Anchor every value where it physically belongs:

* Gear and Road speed sit quietly in the upper-right corner.
* The exact signed **Steering wheel angle** sits above the front axle.
* Two transparent front-wheel needle gauges rotate schematically with
  `steering_deg`; the number remains the authoritative steering-wheel angle,
  while the wheel graphics are explicitly illustrative rather than a claimed
  road-wheel measurement.
* Damper FL/FR/RL/RR appear beside their matching wheels as an existing
  numeric text gauge plus a thin new response bar.
* Ride height FL and FR sit at their front corners in volts.
* The single rear ride-height sensor sits centrally behind the rear axle and
  is visually linked to both rear corners.
* A small `DCC AGE` value shows how old the last chassis snapshot is.

The static body and suspension artwork is baked into `bg_page3.png`. The
four existing damper text gauges, three ride-height gauges, Gear, Road speed,
Steering angle and repurposed DCC-age text remain native RealDash elements.
Add four bar gauges for damper response and two needle gauges carrying
transparent front-wheel assets. Add one small status text gauge bound to
`dcc_status` so `DCC DATA UNAVAILABLE/FRESH/STALE` is explicit rather than
inferred from colour.

The DCC values are raw formula-24 bytes: documented as 0 at rest and roughly
45 while moving, but not yet decoded into force, current or percentage. Label
them `raw`; do not invent engineering units or warning thresholds. Use a
provisional 0…60 visual bar range only to show relative corner activity.

Catalyst temperature is removed from DCC; it was only a filler in the old
eight-row panel and has no semantic relationship to chassis.

#### Chassis data prerequisite

The current production logs contain signed steering but no damper or
ride-height samples. The aux reader was disabled after visits to the DCC
module hard-reset the board while the engine was running. Before this page is
called live, restore that source safely and validate all seven chassis values
in a new datalog.

Keep the legacy `steering_counts` channel in the logger schema even though the
CAN-box `steering_deg` feed has superseded it; CSV channel names are a permanent
compatibility contract. Add a dedicated `0xC91` status frame carrying
`dcc_age_s` and `dcc_status` (`unavailable`, `fresh`, or `stale`). Publish the
frame even while the auxiliary reader is disabled. When no valid DCC snapshot
exists, or its age exceeds 30 seconds, page 3 must show `--` and a subdued
`DCC DATA UNAVAILABLE/STALE` state rather than plausible zeros.

### Page 4 — trip, diagnostics and system health

* `TRIP` contains Odometer, Engine run time and Distance since clear.
* `DIAGNOSTICS` contains Fault count, Fault 1–4 and Check engine.
* A native `CLEAR CODES` button sits at the bottom of `DIAGNOSTICS`. It uses
  a 2-second initial action delay plus RealDash's `Hold Value` action on the
  existing `0xC90` command. A tap does nothing, releasing returns the command
  to zero, and the board still refuses clearing while road speed is non-zero.
* `SYSTEM HEALTH` contains Sample rate, Reconnects, Aux visits, Aux fails,
  Gear age and CAN-box age.
* Sample rate, reconnects, aux visits/failures and age fields remain here as
  requested, not on a primary driving/tuning page.

The 46 existing page-2 values therefore reconcile cleanly:

| destination | existing values | purpose |
|---|---:|---|
| Page 2 | 20 | air/load, fuel delivery, mixture/exhaust |
| Page 3 | 11 | driveline, adaptive chassis and DCC freshness |
| Page 4 | 16 | trip, diagnostics, guarded Clear Codes control and system health |
| **Total** | **46** | |

### Expected final gauge inventory

Starting from 80 gauges:

| change | delta |
|---|---:|
| five new round-gauge numeric readouts; repurpose page-1 Lambda for the sixth | +5 |
| separate boost and RPM ghost needle gauges | +2 |
| one extra negative half for each of two trim bars | +2 |
| spark-advance needle | +1 |
| four DCC corner-response bars | +4 |
| two schematic front-wheel steering gauges | +2 |
| repurpose the old `Steering raw` display gauge as DCC age | 0 |
| DCC availability/status text | +1 |
| guarded Clear Codes button | +1 |
| **expected final total** | **98** |

Moving gauges between pages and moving the ignition text gauge do not change
the total. The inactive AFR face is part of the generated page background and
does not add a fake data gauge. Adding a real wideband later adds an AFR
needle and numeric text gauge, taking the live-wideband version to 100.

---

## Page 1 visual specification

### Geometry

All coordinates below are proposals in the 1280×720 design grid. The source
`.rd` stays 1920×1080; rect patching scales these by 1.5.

| gauge | current `(x, y, w, h)` | v2 starting point |
|---|---|---|
| RPM | 30, 150, 340, 340 | **10, 80, 400, 400** |
| Boost | 440, 120, 400, 400 | **420, 80, 400, 400** |
| Coolant | 900, 125, 170, 170 | **855, 90, 200, 200** |
| Oil | 1090, 125, 170, 170 | **1070, 90, 200, 200** |
| Charge air | 900, 345, 170, 170 | **855, 320, 200, 200** |
| Battery | 1090, 345, 170, 170 | **1070, 320, 200, 200** |

Every face, tracer, live needle, target needle and ghost needle for a dial
must share the exact same rect. The values above are not final until the
four-page preview has been reviewed at native 1280×720 size.

### Gauge-face family

`gauge_face()` gets one shared visual grammar rather than hand-tuned density
per signal:

* the same chrome/black bezel construction;
* the same inner and outer hairline rings;
* a constant minor-tick count derived from sweep angle, not value span;
* the same major/minor tick proportions and label scale; and
* no brand text on any face.

For the tach, change minor spacing from 100 rpm to 250 rpm so its edge no
longer collapses into a white block. Render every final face at twice its
display size after the layout is locked.

### Numeric readouts

Add one transparent-background text gauge in the lower interior of every
round gauge, clear of the needle hub and sweep labels. Use a condensed
VW/Audi-like cluster numeral face (`Bahnschrift SemiCondensed` if RealDash
exposes it; otherwise the closest tested built-in condensed sans).

| gauge | decimals | range | warning boundary | critical boundary |
|---|---:|---|---|---|
| Boost | 1 | −1.0…2.0 bar | > 1.2 | > 1.6 |
| RPM | 0 | 0…7000 | > 6300 | > 6800 |
| Coolant | 0 | 50…130°C | > 105 | > 110 |
| Oil | 0 | 50…150°C | > 120 | > 130 |
| Charge air | 0 | 0…120°C | > 50 | > 60 |
| Battery | 1 | 8…16 V | < 11.0 or > 15.2 | < 10.5 or > 15.8 |

The charge-air face and needle range also change from 0…80 to **0…120°C**;
recent logs reached 93°C, so the old scale clips real data.

Set `LOOK'N FEEL -> COLORS -> TEXT COLOR` separately for each editing level:

| level | colour | purpose |
|---|---|---|
| Normal | `#F4F6F7` | cool OEM white |
| Warning | `#E0951F` | amber attention |
| Critical | `#E02A1C` | red lift/act now |

Do **not** use `EDITING LEVEL = ALL` while setting these colours; that would
overwrite the three states. Image and needle blend colours remain neutral
white unless a deliberate state tint is part of the design.

RealDash's levels are safe windows. For example, battery Warning is
`11.0…15.2` and Critical is `10.5…15.8`; values outside the Warning window
but inside the Critical window are amber, and values outside the Critical
window are red.

### Separate boost and RPM ghosts

Create two additional needle gauges, behind the target/live pointers:

| ghost | input | range | history | show time | image |
|---|---|---|---|---:|---|
| Boost peak | targetId 83 | −1.0…2.0 | MAX | 5 s | `needle_main_ghost.png` |
| RPM peak | targetId 37 | 0…7000 | MAX | 5 s | `needle_main_ghost.png` |

Both use the live signal, not invented `Ghost Boost` or `Ghost RPM` input
channels. Their visibility binds to the existing RealDash-local
`Show Ghost Boost` / `Show Ghost RPM` values, which default on. Tapping the
matching dial toggles its ghost.

Required z-order:

1. ghost;
2. boost target, on the boost dial only;
3. live pointer.

Before bulk assembly, prove on a throwaway copy that the separate history
gauge shows one held pointer and does not leave a second pointer at the live
value. Also prove that its custom ghost asset is preserved on Android. This
small spike prevents an undocumented history-rendering detail from becoming
a late rebuild.

### Telltales: OEM treatment and inactive state

Replace the built-in `_indicators` sprites with assets generated as SVG in
`generate_assets.py`. Do not use AI raster art for this icon family; clean
geometry, matched stroke widths and exact symmetry are the point.

The family should read as late-2000s VW/Audi instrumentation:

* thin, hollow, chamfered green turn arrows;
* an angular amber engine/MIL outline with recognizable manifold and exhaust
  geometry;
* the standard red thermometer over two waves;
* a red piston/combustion symbol with controlled detonation marks for knock,
  replacing the generic lightning bolt;
* an amber turbo/impeller plus deviation mark for boost tracking; and
* one shared optical size, stroke weight and corner language across all six.

Use 48×48 display rects at approximately `y=16`. Turn arrows remain near
the outer edges. Center the four warning icons as a compact group in the
new header-free band; starting positions `x=508, 580, 652, 724` are the
first preview proposal.

Configure each image gauge with range `0…1` and levels that put value 0 in
Normal and value 1 in an active level. Then set:

* Normal image blend opacity: **10–12%**, neutral graphite;
* Warning/Critical opacity: **100%**, original green/amber/red asset colour;
* optional active glow: restrained and localized, never a large soft halo.

This follows RealDash's documented indicator technique and uses one gauge
per icon. The inactive silhouette remains visible without baking a second
copy into the background.

The logs already prove both turn-signal channels pulse 0/1 in normal driving.
The final in-car check is visual timing and brightness, not signal discovery.

### Trim bars

Keep each existing trim bar as the positive half and add one negative half.
The existing trough is `x=833`, `w=218`, with centre `x=942`:

* negative half: `x=833`, `w=109`, input range `0…−25`, style
  **Right-to-Left**;
* positive half: `x=942`, `w=109`, input range `0…+25`, normal left-to-right.

Both halves share the same source channel. Verify `−10`, `0`, and `+10` in
the simulator; only the appropriate side may fill.

### Smoothing policy

Do not apply one smoothing value to every gauge.

| class | smoothing |
|---|---:|
| boost, boost target, RPM, tracers, ghosts | 0% |
| speed, timing, steering, knock and all telltales | 0% |
| coolant, oil, charge-air and battery needles/readouts | 80% initially |
| data-page text | 0% unless a specific low-rate field visibly steps |

Fast controls should display the newest sample, while thermal/electrical
needles can retain the calmer OEM motion. If a thermal numeric readout lags
its needle, set both elements for that signal to the same value.

---

## Steering fix

On the existing `Steering` text gauge:

* source: `steering_deg` (`0xC8E`, offset 4, signed);
* label: `Steering angle`;
* Range: **−600…+600**;
* decimals: 0;
* smoothing: 0%; and
* normal text colour: white.

`steering_counts` is the uncalibrated EPS auxiliary reading, not degrees, and
the recent CSVs contain no usable samples for it. The signed `steering_deg`
feed already covers the full useful display. Rebind and relabel the old
`Steering raw` text gauge as `DCC age` when the freshness channel described
for page 3 is implemented.

Acceptance test with live or replayed data: `−100`, `0`, and `+100` must all
display with their signs; the negative test may not clamp to zero.

---

## Spark-advance gauge

The attached concept is the visual reference, not a finished live asset. A
single flattened image cannot keep its pointer and number synchronized.
Rebuild it as three layers:

1. static engine/piston/spark-plug line art and coloured scale baked into the
   page-2 background;
2. one new transparent needle gauge bound to `timing_deg`; and
3. the existing page-1 ignition text gauge, cut/pasted into the design.

Use a data-driven range of **−30…+45°** with major labels at
`−30, −20, −10, 0, 10, 20, 30, 40`. Recent logs reached +40.5°, so the
concept's +20° end stop is not adequate. Generate tick positions and needle
rotation from the same linear mapping; do not place labels by eye.

Use a 150° upper sweep (`Start Angle 285`, `Sweep 150`) around the crankshaft
pivot rather than forcing the illustration into the page-1 gauges' 270° dial
geometry. The static layer includes a detailed cylinder head, angled valves,
central plug, spark burst, piston, pin, connecting rod and crank webs.

The concept image currently shows `25°` while the drawn pointer sits near
`−20°`. That mismatch must not survive implementation. Verify the composed
gauge at three injected values: `−20°`, `0°`, and `+25°`.

Keep the numeric value white in normal use. The negative arc may transition
from amber into red as a visual sign cue, but the number must not claim a
warning based only on total advance. `Knock Warning` and the cylinder-retard
bars remain the authoritative intervention signals.

### Companion AFR/Lambda gauge

Give AFR equal visual weight beside spark advance because mixture and timing
are the paired combustion variables a tuning page is meant to compare.

The inactive v2 face is still fully designed: it mirrors Spark's 150° upper
arc, complete 10/12/14/16/18 markings, restrained edge accents, a detailed
wideband sensor entering an exhaust-cell cross-section, and a central lambda
symbol. `WIDEBAND INPUT REQUIRED` is integrated into the instrument rather
than leaving an unstyled empty placeholder. It has no live pointer or large
numeric value until the source exists.

For v2, generate the full VW/Audi-style face and reserve the needle/value
rects, but render the centre state as `WIDEBAND INPUT REQUIRED`. Do not bind
the prominent gauge to `lambda_commanded`: recent logs prove it is a frozen
1.000 snapshot, and converting it to `14.7 AFR` would only make the stale
value look more convincing.

Activation requires a real wideband controller connected to a spare Feather
analog input, plus a new calibrated RealDash channel. When that exists:

* add one AFR needle and one numeric text gauge in the reserved rects;
* keep Lambda as the internal canonical value and convert only for display;
* support a user-selectable Lambda or gasoline-AFR label without changing the
  underlying measurement;
* use a useful gasoline display range such as 10.0…18.0 only after the
  controller's transfer function is known; and
* derive warning bands from operating mode/load, not one universal AFR
  threshold, because stoichiometric cruise and enriched boost targets are
  intentionally different.

Until then, the small legacy value may remain in `MIXTURE & EXHAUST` only as
`Lambda snapshot`; it is diagnostic context, not a tuning instrument.

---

## Computer-use operating discipline

RealDash will be driven directly on the laptop with `dash/tools_ui.ps1` at a
fixed 1920×1080 desktop resolution. The automation is deliberately atomic:

1. capture a full-screen PNG;
2. inspect the current application, page, selection header and open dialog;
3. perform **one** click, key sequence, short drag or committed field edit;
4. capture and inspect another screenshot before choosing the next action;
5. update the coordinate/state journal when a control is confirmed; and
6. save a checkpoint after each small coherent group of gauges.

Do not run long blind click scripts. Coordinates are reusable only while the
same panel and resolution are visibly unchanged. A transition, popup, page
change, scroll or asset picker invalidates assumptions until the next
screenshot.

For every gauge edit, select by exact name through the left filter and verify
that the header says `1 GAUGE SELECTED`. After entering a numeric or hex
field, press `ENTER`, screenshot the committed result, and only then leave the
dialog. This catches the two failure modes already documented in this repo:
missed selection and fields that silently ignore `TAB`.

Use the UI only where RealDash owns opaque state: bindings, ranges, levels,
history, smoothing, actions, images and page membership. Use the shared
manifest plus binary rect patching for geometry. That keeps screenshot-driven
computer use precise without spending hundreds of fragile clicks on X/Y/W/H.

---

## Implementation sequence

### Phase 0 — preserve and inventory

1. Copy `dash/scirocco.rd` to `dash/scirocco-v2.rd` and make all editor
   changes only in the v2 file.
2. Capture a machine-readable manifest of all 80 existing gauges: page,
   name, type and rect.
3. Record bindings/ranges from the RealDash editor for every gauge touched in
   this revision. Rects are binary-readable; bindings have **not** been proven
   reliably decodable from the proprietary variable-length records, so the
   original plan's proposed binary binding dump is not an accepted tool.
4. Derive the channel list from `frame_schema.load()` and store the 65-channel
   output with the verification notes.
5. Run the separate-ghost spike described above on a throwaway copy.
6. Treat DCC as a data-source prerequisite, not a dashboard-only assumption:
   reproduce the aux-reader reset safely, restore polling without destabilizing
   the board, expose DCC freshness as `dcc_age_s`, and capture a new datalog
   containing all four damper bytes and three ride-height voltages. Until that
   passes, page 3 must remain in its explicit unavailable state.

### Phase 1 — one shared layout model and four-page preview

The current preview only represents page 1 and hard-codes geometry separately
from the generator. Replace that duplication with one shared layout manifest
used by:

* `generate_assets.py`;
* `make_preview.py`;
* the `.rd` rect patcher; and
* the final verification dump.

Extend the preview to pages 1–4. Iterate geometry there first, including the
indicator group, numeric readouts, paired page-2 Spark/AFR composition, the
three semantically rebuilt engine panels, the page-3 top-down chassis
composition, and the larger page-3/page-4 typography.

The correct visual workflow is iterative: rough shared geometry, generated
art, preview, adjustment, then final 2× assets. It is not “finish art, then
discover its final box later.”

### Phase 2 — generate the final assets

Update `generate_assets.py` to:

* remove the shared header block and red line;
* remove the boost-face `SCIROCCO` brand;
* normalize all six gauge faces;
* change the charge-air face to 0…120°C;
* generate the VW/Audi-style telltale family;
* generate the paired page-2 Spark and reserved AFR background layers;
* generate the new `AIR & LOAD`, `FUEL DELIVERY`, and
  `MIXTURE & EXHAUST` panel artwork; and
* generate the page-3 body outline, exposed front/rear subframes, steering
  rack, struts/springs and rear-linkage artwork;
* generate transparent left/right front-wheel assets whose pivots and neutral
  angles are identical; and
* generate `bg_page1.png` through `bg_page4.png` from the shared manifest.

Existing assets are replaced with the tile's **↻** control so references are
preserved. New page-3/page-4 backgrounds and any telltale asset not yet in the
library must be imported once; “never re-import” applies only after an asset
exists in the dashboard.

### Phase 3 — structural editor pass

1. Add pages 3 and 4.
2. Keep all 20 engine-related values on page 2: the ten `AIR & LOAD`, six
   `FUEL DELIVERY`, and four `MIXTURE & EXHAUST` values listed above.
3. Cut/paste Gear, Road speed, Steering angle, the seven true DCC values and
   the old `Steering raw` gauge onto page 3. Relabel/rebind that last gauge as
   `DCC age` when the freshness channel is available.
4. Cut/paste Odometer, Engine run time, Distance since clear, Fault count,
   Fault 1–4, Check engine, Sample rate, Reconnects, Aux visits, Aux fails,
   Gear age and CAN-box age onto page 4.
5. Cut the page-1 ignition text gauge and paste it into the page-2 Spark
   gauge.
6. Repurpose the page-1 Lambda text gauge as one round-gauge numeric readout,
   then add the eighteen genuinely new gauges: five more numeric text, two
   ghost needles, two trim halves, one spark needle, four DCC response bars,
   two front-wheel steering gauges, one DCC status text gauge, and one native
   Clear Codes button gauge on page 4.

New gauges can initially sit in safe temporary boxes. Exact placement comes
from the rect patcher after all gauge names exist.

### Phase 4 — functional editor pass

Select gauges through the filtered gauge list and confirm the header reads
`1 GAUGE SELECTED` before every edit.

In one pass:

* verify/rebind boost live to targetId 83;
* verify boost target is targetId 270;
* verify RPM live is targetId 37;
* remove History Mode from the live boost/RPM gauges once the separate ghosts
  are working;
* bind/configure the two ghost gauges;
* bind/configure all six numeric readouts and their three colour levels;
* fix steering range/sign display;
* bind both front-wheel graphics to `steering_deg`, using a visual mapping of
  `-540…+540°` steering-wheel angle to an illustrative `-32…+32°` wheel sweep;
  keep the exact signed numeric angle authoritative;
* set odometer decimals to 0;
* configure trim directions/ranges;
* bind the spark needle and moved numeric text to `timing_deg`;
* bind the four DCC bars to their matching raw FL/FR/RL/RR channels with a
  provisional 0…60 range and no warning/critical colours;
* bind `DCC age`, verify the freshness threshold, and make absent/stale DCC
  values render as `--` plus the subdued unavailable message;
* configure the Clear Codes button for a 2-second hold using `Hold Value` on
  the `Cmd Clear Codes` XML input; verify a short tap sends nothing and release
  returns the command to zero;
* apply the per-class smoothing table; and
* replace/configure telltale images and inactive opacity.

Do not assume the three original needle symptoms prove crossed bindings. The
visual behavior is consistent with History Mode obscuring the live pointer,
but each binding must be verified in the editor before changing it.

### Phase 5 — asset replacement and exact geometry

1. Replace existing bitmaps in place and import the genuinely new ones once.
2. Patch all page rects from the shared manifest, including every face,
   tracer, target, live needle, ghost and numeric readout.
3. Verify the file header remains 1920×1080 / 16:9.
4. Re-open and save once in RealDash so the editor validates the resulting
   file structure.

### Phase 6 — verification

Verify from both the file and rendered behavior:

* inventory total is 98 and page ownership matches this plan;
* page 1 has no header, wordmark or red stripe;
* all six numeric readouts are legible and change white → amber → red at
  injected boundaries;
* boost/RPM live needles fall immediately while their separate ghosts hold
  for 5 seconds;
* the boost target remains independent of the live and ghost pointers;
* steering displays negative, zero and positive values;
* the two front-wheel graphics steer symmetrically left/right, return to
  neutral at zero and are clearly treated as schematic rather than measured
  road-wheel angle;
* DCC FL/FR/RL/RR bars and numbers stay mapped to the correct physical
  corners, use raw units and never imply force, percentage or current;
* a missing or stale chassis snapshot produces `--` values and the explicit
  unavailable/stale state rather than a believable field of zeros;
* FL/FR ride height remains corner-specific and the single rear sensor is
  shown centrally, not duplicated as two independent measurements;
* both trim halves pass `−10 / 0 / +10` direction tests;
* inactive telltales remain visible at 10–12%, and active telltales are full
  brightness;
* both turn arrows flash with their real 0/1 inputs;
* spark pointer and numeric agree at `−20 / 0 / +25`;
* the companion AFR face clearly says `WIDEBAND INPUT REQUIRED` and never
  presents the frozen lambda snapshot as live AFR;
* page 2 owns every air/load, fuel-delivery, mixture and exhaust value; page
  3 owns only driveline/DCC; page 4 owns only trip/diagnostic/system-health
  values;
* page swipes preserve all moved bindings; and
* the 65-channel display contract is covered, including the legacy
  `steering_counts` channel plus the new `dcc_age_s` and `dcc_status` fields.
* Clear Codes requires a continuous 2-second hold, produces one 0→1 command
  edge, re-arms on release, and remains refused by the board while moving.

Bench testing against RealDash for Windows requires the one-time UWP loopback
exemption documented in `EDITOR-NOTES.md`. Until the owner approves that
elevated Windows change, geometry/range verification can use the editor and
simulator, while the final live-input acceptance remains an in-car step.

---

## Transfer and rollback

Keep both files on the deck during acceptance:

```powershell
adb -s <deck-ip>:5555 push dash/scirocco-v2.rd /storage/emulated/0/Documents/RealDash/scirocco-v2.rd
```

In RealDash: top strip → `EDIT` → `FILE` → `LOAD…` → select
`scirocco-v2.rd` → `SELECT FILE`.

Do not replace or delete the existing `scirocco.rd` until v2 has passed one
real drive. Steering and both turn inputs are already healthy. The visual
dashboard work does not otherwise require an XML or firmware change, but
enabling the page-3 DCC data and freshness state does require the separately
verified aux-reader and schema work described in Phase 0.

---

## Documentation to update while implementing

When the implementation is complete:

* update `dash/README.md` with the four-page layout, 98-gauge inventory,
  correct ghost-input explanation and charge-air range;
* add the confirmed smoothing path and page cut/paste workflow to
  `dash/EDITOR-NOTES.md`;
* remove stale claims that the dashboard has 76 gauges / 42 page-2 fields or
  that ghost data comes from the tee; and
* regenerate every preview and layout table from the shared manifest so the
  docs cannot drift from the binary again.

---

# Handoff — state as of 2026-08-29 evening

Written for whoever picks this up next, Codex included.  The plan above is
substantially implemented and running in the car.  This section says what is
true *now*, so nobody re-derives it.

## Where things stand

`dash/scirocco-v2.rd` is built, deployed to the deck and verified live on a
running engine.  98 named gauges, four pages, canvas 1920x990.  The deck
copy is at `/storage/emulated/0/Documents/RealDash/scirocco-v2.rd`; push with
`adb push` and reload from RealDash's own File > Load.

**Design canvas is 1280x660, not 1280x720.**  The head unit keeps its Android
navigation bar (the owner switches apps with it), leaving RealDash a 1280x660
window.  RealDash fits to window *width* and bottom-anchors, so a 720-tall
design loses its top 60 px off the screen rather than letterboxing.
`layout_v2.CANVAS` and `EDITOR_CANVAS` are the single source of truth and
every stage reads them; do not hardcode either.

## The three traps that cost the most time

1. **The ellipse test, before anything else.**  RealDash has a load-time
   layout bug: it can render every *gauge* squeezed into a 603 px band
   (`y = 0.9136*y + 30`) while the page *background*, being a full-canvas page
   property, still fills all 660 px.  Artwork and gauges then drift apart the
   further they are from mid-screen, which reads exactly like a layout bug and
   is not one.  Measure the rpm dial: 396x396 means the layout is real,
   393x361 means RealDash is mid-bug.  Reload, or enter and leave the options
   screen, and re-capture.  **Do not compensate for the squeeze** -- it is
   transient, and compensating bakes it in backwards.  Full detail and the
   evidence in `EDITOR-NOTES.md` section 23.

2. **Boost must bind to a plain named channel, not targetId 83.**  RealDash
   converts a built-in pressure channel into its own configured pressure unit,
   so a true -0.695 bar displayed as -10.1 (that is psi) on a dial drawn in
   bar.  The XML declares an extra unitless `Boost Live` copy of the same word
   with `displayOnly="true"`, and the three boost gauges bind to its name
   hash.  `displayOnly` also keeps it out of the CSV header contract.

3. **Board-side field 2 is the setpoint (Sollwert), field 3 the live reading
   (Istwert)** -- the original labelling.  These were briefly swapped on
   2026-08-29 and swapped back the same day; the swap was wrong and the
   reasoning behind it is worth knowing so it is not repeated.

   The argument for swapping was that field 3 sat flat at ~atmospheric for an
   entire drive, "which manifold pressure cannot do at part throttle".  That
   is true, and the conclusion drawn from it was wrong: **Ladedruck Istwert is
   measured BEFORE the throttle plate**, so sitting at ambient with the
   throttle shut is exactly what it should do.  Manifold pressure is measured
   after the plate.  They are not the same signal -- which is the question
   that started the whole detour.

   Confirmed on the drive log of 2026-08-29 (1272 rows):
   * field 3 never goes below -0.02 bar gauge; field 2 reaches -0.77.  A
     pre-throttle sensor cannot read vacuum.  A setpoint can.
   * field 3 >= field 2 in 97.1 % of rows, the gap closing from 0.61 bar at
     shut throttle to 0.33 at part throttle.
   * both reach the same 1.545 bar maximum, as a setpoint and its achieved
     value must.
   * verified live at idle after the fix: actual -0.005 bar, spec -0.635.
     `CLAUDE.md` independently records "at idle boost sits at about 0.00 bar"
     from when the original mapping was in place.

   Bound the wrong way the big needle jumps instantly with pedal demand while
   the target tick ramps up behind it -- which is backwards, and is what the
   driver noticed from the seat before any log did.

   **Consequence, not a bug:** the boost dial reads ~0.00 at idle, not -0.7.
   The vacuum is the setpoint.  Page 2's MAP channel is fed from the same
   `actual` field, so it reads ~100 kPa at idle rather than 26.  Genuine
   manifold vacuum is a third signal and is not currently acquired.

## Measuring, not looking

Every alignment fix in this round came from a pixel measurement.  Eyeballing
screenshots produced two confident wrong diagnoses in a row.  Compare **pitch
before position**: equal pitch with a constant offset is a placement error
worth a few pixels, unequal pitch is a coordinate-space error and the offset
tells you nothing.  Recipe and thresholds in `EDITOR-NOTES.md` section 24.

## Pipeline, unchanged

Always rebuild from the snapshot, never from the previous output:

    generate_assets.py
    rd_transform_v2.py  <snapshot>.rd  scirocco-v2.rd     # assets
    rd_normalise_canvas.py                                 # only after editor saves
    rd_manifest.py --apply-v2-layout                       # geometry
    rd_config_v2.py                                        # bindings and fields

`rd_config_v2.patch()` is a **one-shot**: its colour stages consume RealDash's
default byte patterns, so re-running it on its own output fails by design.
`--fields-only` applies just the idempotent fields.  Delete
`dash/assets/png/bg_page*.png` before regenerating -- `render_all` skips any
PNG newer than its SVG, so an edited layout silently keeps the old artwork.

## Known bad, and not layout

`Rail pressure` reads 145.0 where the log said 52.  `Rail specified` and
`Rail actual` read 580.2 / 442.4 bar against a 40-150 spec.  `Engine load`
reads 100.0 at 8 % throttle.  All three are channel scaling in the
acquisition path and are the obvious next job.

Also outstanding: the Clear Codes hold action and the Show Ghost toggles are
still editor-owned; `Text Gauge 45` and four bar-gauge halves are parked
off-canvas rather than deleted; nothing here is committed to git.

## What not to change without asking

Spark advance and injection ride on a dedicated fast frame (0xC93) but the
ECU read budget is ~20 blocks/second total, so 10 Hz on tuning channels is
not reachable over KWP2000.  A priority-tier scheme was tried and reverted:
it made rpm and boost choppy, which the owner cares about more.  Leave the
fast rpm/boost rotation alone until the ECU CAN bus replaces block reads.
