# Scirocco v2 RealDash editor runbook

This is the execution checklist for `scirocco-v2.rd`. The semantic authority
is `v2_manifest.py`; this file exists to keep the GUI pass short, ordered and
auditable. Preserve `scirocco.rd` unchanged.

## Preferred workflow: direct transform first, short editor pass second

The page structure and all 98 gauge records now exist. Do not rebuild the
dashboard through repeated mouse placement. Regenerate the deterministic
parts from the recovery snapshot, then use RealDash only for settings the
binary parser does not yet own:

```powershell
python dash/generate_assets.py
python dash/rd_transform_v2.py `
  dash/scirocco-v2.pre-direct-20260829.rd dash/scirocco-v2.rd
python dash/rd_manifest.py dash/scirocco-v2.rd `
  --apply-v2-layout dash/scirocco-v2.rd
python dash/rd_config_v2.py dash/scirocco-v2.rd dash/scirocco-v2.rd
python dash/rd_manifest.py dash/scirocco-v2.rd
```

`rd_transform_v2.py` atomically rebuilds the embedded PNG library, replaces
same-name assets in place, adds the missing v2 assets, assigns page 3/4
backgrounds, gives Image Gauges 7–12 their unique telltales, assigns the five
new needle assets, and repairs the native Clear Codes button name/artwork.
`rd_manifest.py --apply-v2-layout` then writes and verifies the 98 rectangles
from `layout_v2.py`. The transformed file has been loaded successfully in the
Microsoft Store build of RealDash.

`rd_config_v2.py` then applies only fixed-width fields proven by controlled
editor comparisons: bindings, six-float threshold/range blocks, needle
start/sweep radians and hidden built-in scale text. It fixes signed steering,
binds the new Page 1/3 controls, gives Spark its 285°/150° sweep, and sets the
trim/DCC ranges without changing any variable-length record.

The recovery snapshot is intentional input: the transform is not an
in-place, repeatedly applied migration. It expects the pre-transform button
and asset references and emits a fresh working copy each time.

### UWP staging and copy-back

RealDash's Windows file picker may report that a valid workspace path does
not exist because the file is owned by the sandbox account. Stage the file in
a user-readable location, edit that copy, then copy the saved result back:

```powershell
Copy-Item dash/scirocco-v2.rd `
  $env:USERPROFILE/Pictures/RealDash-v2/scirocco-v2.rd -Force

# After File > Save in RealDash:
Copy-Item $env:USERPROFILE/Pictures/RealDash-v2/scirocco-v2.rd `
  dash/scirocco-v2.rd -Force
```

Hash the two copies before opening and after copy-back. RealDash can update
serialized runtime/display state during a save, so a changed working-copy
hash is not by itself a failure; inventory, assets, page ownership, geometry,
and loadability are the acceptance checks. The rollback file's SHA-256 must
remain `577758832FFB43B5D314EDF5F085A140FA357616BDE69779968B45D65CF7EC00`.

### Runtime appearance triage

The direct proof render established that the background and gauge rectangles
match the approved previews. These conspicuous defects are editor defaults,
not geometry-patcher scaling errors:

- `The Text` in the first proof meant an unbound new Text Gauge with 100%
  auto-scaled text; the direct configuration stage now supplies its input.
- The first page-2 diagonal white line and grey zeroes were Needle Gauge 10's
  defaults; its range, sweep and scale-text colours are now patched directly.
- The Page 3 front wheels now share the signed steering input and use the
  narrow 328°/64° schematic sweep.
- Page 1 bakes the same telltale silhouettes into the background at 11%
  opacity, so inactive locations remain legible while the live Image Gauges
  provide the full-brightness state.

Fix the channel/range/style first, compare the run-mode result with
`preview/page*_v2.png`, and only then change `layout_v2.py` if a true placement
problem remains.

## Safety gates

1. Open only `scirocco-v2.rd` and confirm the editor canvas is 1920×1080.
2. Confirm the RealDash CAN connection has loaded
   `board/scirocco_realdash.xml` and that an ECU-specific channel such as
   `dcc_status` is searchable before touching bindings.
3. Select gauges through the exact-name filter and verify `1 GAUGE SELECTED`.
4. After each coherent group, save and inspect with `rd_manifest.py`.

## Page construction and moves

Add two pages to the right of existing page 2. Cut/paste these records; do not
recreate them, because their bindings are already useful.

Page 2 receives from page 1:

- `Text Gauge 43` — spark-advance numeric value.

Page 3 receives from old page 2:

- `Text Gauge 18`, `Text Gauge 15`, `Text Gauge 16` — steering, gear, speed.
- `Text Gauge 22`, `23`, `24`, `25` — dampers FL, FR, RL, RR.
- `Text Gauge 26`, `27`, `28` — ride height FL, FR, rear.
- `Text Gauge 19` — rebind from legacy steering raw to `dcc_age_s`.

Page 4 receives from old page 2:

- `Text Gauge 29`, `21`, `30` — odometer, run time, distance since clear.
- `Text Gauge 31`, `32`, `33`, `34`, `35`, `42` — count, Fault 1–4, MIL.
- `Text Gauge 36`, `37`, `38`, `39`, `40`, `41` — sample rate,
  reconnects, aux visits/failures, gear age, CAN-box age.

## New-gauge order

Create in this exact order so RealDash assigns the names in the manifest:

1. Page 1: two needle gauges (`Needle Gauge 8`, `9`), five text gauges
   (`Text Gauge 54`–`58`), then two bar gauges (`Bar Gauge 9`, `10`).
2. Page 2: one needle gauge (`Needle Gauge 10`).
3. Page 3: one text gauge (`Text Gauge 59`), two needle gauges
   (`Needle Gauge 11`, `12`), then four bar gauges (`Bar Gauge 11`–`14`).
4. Page 4: one native button gauge (`Clear Codes (hold 2 s)`) in the reserved bottom
   area of the Diagnostics panel.

Expected inventory after creation: 98 total — page 1: 42, page 2: 22,
page 3: 18, page 4: 16.

## Assets

Replace in place: `bg_page1`, `bg_page2`, all six `face_*` images,
`needle_main`, `needle_small`, and `needle_target`. Reuse and replace an
existing `needle_main_ghost` tile if present; import it only if absent. Import
each genuinely new asset once: `bg_page3`, `bg_page4`, `needle_spark`, both
`wheel_front_*` images, and the six `ind_*` telltales. Assign each telltale its
own asset; the six gauges may not keep sharing RealDash's `_indicators` sheet.
Import `btn_clear_codes` once and assign it to `Clear Codes (hold 2 s)` if the button
image picker supports it; otherwise reproduce the same restrained colours and
text with the native button controls.

Set image and needle blends to `FFFFFFFF` at all levels unless the manifest
specifies a deliberate state treatment. Press Enter after every hex edit.

## Functional pass

Channel, range, angle, decimal, smoothing and history values are now all
direct-configured from `v2_manifest.py` by `rd_config_v2.py`; the editor no
longer owns any of them. The editor still owns visibility conditions and
button actions. Special checks:

- Live RPM/boost history mode off; separate Gauges 8/9 use MAX for 5 seconds.
- Boost target remains `boost_target`, separate from live and ghost boost.
- Steering text range is −600…+600 and both wheel gauges use −540…+540 over
  a schematic 64° sweep.
- DCC values/bars are visible for status 1–2 only. `Text Gauge 59` always
  displays the `dcc_status` enum; `Text Gauge 19` displays `dcc_age_s`.
- The prominent AFR face remains unbound and says `WIDEBAND INPUT REQUIRED`.
- Telltales are 10–12% graphite when off and full-colour when active.
- `Clear Codes (hold 2 s)`: `Initial Action Delay = 2000 ms`, enable `Actions When
  Pressed Down`, and add `Hold Value` for the XML input `Cmd Clear Codes`. A short
  tap must do nothing; the value must return to zero on release. Do not use
  RealDash's generic OBD Clear Error Codes action—the board command contains
  the project's speed interlock.

### Residual editor-owned checklist

The direct transform already owns assets, page backgrounds, button labels and
all geometry. The remaining editor pass is bounded to:

- Page 1: set Text Gauges 54–58 to 70% text-area height and apply their
  Normal/Warning/Critical colours — both now direct-configured. Needle Gauges
  8/9 MAX history and the five-second show time are direct-configured too
  (EDITOR-NOTES section 18), as is turning the same peak-hold OFF on the six
  live needles, which is what caused the hanging-needle report. Only the
  independent Show Ghost toggles and confirming that Bar Gauges 9/10 grow
  outward from the shared zero line remain.
- Page 2: visually verify the configured Spark needle at `-20 / 0 / +25` and
  apply the matching numeric typography/level colours to Text Gauge 43.
- Page 3: apply the `dcc_status` visibility condition to the corner values and
  bars, then verify unavailable/fresh/stale enum presentation. Bindings,
  signed ranges and wheel sweeps are already direct-configured.
- Page 4: configure the native Clear Codes button's 2000 ms hold action.

For the six round-gauge numeric readouts use centered text at 70% text-area
height as the initial scale. Normal text is `F2F4F5FF`, Warning is
`F5A000FF`, and Critical is `FF2D26FF`. Keep Editing Level explicit when
setting the three states; `ALL` is appropriate for geometry/typography, not
for different per-level colours.

## Exact geometry and verification

After the editor owns the correct pages, bindings, assets and 98 records,
save and close the file, then apply only the verified rectangle fields:

```powershell
python dash/rd_manifest.py dash/scirocco-v2.rd `
  --apply-v2-layout dash/scirocco-v2.rd
```

Reopen and save once in RealDash, then run `rd_manifest.py` again. Test against
`deck/sim_feather.py --fast` in normal, `--dcc-stale`, and
`--dcc-unavailable` modes. Keep `scirocco.rd` byte-identical throughout.
