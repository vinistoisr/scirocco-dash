# RealDash editor: hard-won facts

Everything in this file was established by driving the RealDash for Windows
editor directly (2026-08-24/25) and verifying on screen. It is the stuff that
is NOT in RealDash's documentation and that cost real time to discover.

## 1. Image gauges are TINTED. This is the "why is my dashboard pink" bug.

RealDash multiplies a per-gauge **blend colour** over every image asset. A
brand-new gauge inherits a blend colour from the dashboard's asset set -- on
this dashboard it was `64FF82FF`, which rendered white artwork as magenta and
grey chrome as pink. The source PNGs were verified neutral (chrome 223,226,229;
brightest pixel 240,242,244), so nothing was wrong with the artwork.

**Fix, per gauge:**

    Edit -> select gauge -> LOOK'N FEEL -> COLORS
      -> (confirm EDITING LEVEL = ALL, bottom left)
      -> IMAGE BLEND COLOR   -> HEX (RGBA) = FFFFFFFF   -> press ENTER
      -> NEEDLE BLEND COLOR  -> HEX (RGBA) = FFFFFFFF   -> press ENTER   (needle gauges)

Notes that matter:

* **ENTER commits, TAB does not.** Typing the hex and tabbing away leaves the
  R/G/B fields at their old values and nothing changes. Press ENTER.
* **EDITING LEVEL must be ALL.** Each gauge stores separate colours for
  Normal / Warning / Critical and swaps them live as the value crosses the
  thresholds. With ALL selected, one edit writes all three. This is why the
  Volt gauge glowed red at idle -- battery was below its warning level, so the
  Warning blend colour was in play.
* The hex field is labelled RGBA but the stored value did not agree with the
  R/G/B boxes beside it. `FFFFFFFF` is unambiguous in any channel order, so
  set white via hex and ignore the discrepancy.
* Needle gauges have BOTH `IMAGE BLEND COLOR` (the face) and
  `NEEDLE BLEND COLOR` (the pointer). Fix both.

## 2. Assets can be replaced in place -- do NOT re-import

Every tile in the asset picker has a **↻ icon in its bottom-right corner**.
It opens a file dialog and **replaces that asset's bitmap**, keeping the asset
name and every gauge reference intact. This is how you push regenerated
artwork into an existing dashboard:

    LOOK'N FEEL -> IMAGES -> (any image slot) -> ↻ on the tile -> pick the new file

The X in a tile's top-right **deletes** the asset. Don't confuse them.

Re-importing with `+` instead creates a DUPLICATE asset with the same name.
The library currently holds duplicate `needle_main` / `needle_main_ghost`
entries from earlier import attempts; tiles marked `(UNUSED)` are safe to
delete, but check first -- "unused" is computed live.

## 3. Gauge geometry is stored normalised; the canvas aspect is baked in

From reverse-engineering `scirocco.rd`:

* Header `0x34` = canvas width (i32), `0x38` = height (i32), `0x3C` = aspect
  (f32). Currently `1920 x 1080`, `1.7778`.
* Each gauge stores its rect as **four float32 normalised edges (L, T, R, B)**,
  x divided by canvas width and y by canvas height.
* RealDash **preserves the saved aspect ratio and letterboxes** rather than
  stretching. The developer, forum thread "Aspect Ratio Kills Dash":
  "When dashboard is saved with certain aspect ratio and loaded with another,
  the original aspect ratio must be maintained to preserve the shape and
  relation of the gauges."

**Therefore: build at the deck's aspect.** The deck is 1280x720 (16:9). The
laptop was switched to 1920x1080 (also 16:9) with `tools/setres.ps1`-style
`ChangeDisplaySettings`, and the dashboard re-saved so the header records
1.7778. Design coordinates in `README.md` are 1280x720; multiply by **1.5**
for the 1920x1080 editor canvas.

Editing at 1920x1200 (16:10, the laptop's native mode) silently squashes every
gauge vertically -- that was the "gauges look stretched" symptom.

## 4. Coordinate fields in the editor

The X / Y / W / H boxes at the bottom of the editor:

* `Ctrl+A` does NOT select the contents -- it appends instead, producing values
  like `192660`. Use `{END}` then a run of `{BS}` to clear, then type.
* The **chain-link icon between W and H locks aspect**. Click it to unlink
  before entering a non-square rect, or H will follow W.

## 5. Navigation quirks

* Run mode -> editor: press **Space** to raise the top menu, wait for it to
  settle, then click **EDIT**. Clicking before the menu finishes animating
  does nothing. `Shift+6` (the documented shortcut) did not work here.
* The **Connections** editor is NOT in Settings. It is a hotspot inside the
  Garage 3-D scene: `Garage -> click the car's cabin -> click the dash screen`.
  Adding a RealDash CAN connection: ADD -> **Adapters (CAN/LIN)** -> next ->
  **RealDash CAN** -> next -> **WIFI/LAN** -> next -> IP/port -> next ->
  **CUSTOM CHANNEL DESCRIPTION FILE** -> pick `board/scirocco_realdash.xml`.
* Our XML channels appear in the input picker under **ECU SPECIFIC**; built-in
  targetIds appear under their own headings (e.g. RPM under ENGINE/ECU INPUTS).

## 6. UWP loopback: RealDash cannot reach 127.0.0.1 by default

RealDash from the Microsoft Store runs in an AppContainer, and Windows blocks
Store apps from connecting to loopback. `CheckNetIsolation LoopbackExempt -s`
showed no exemption for `Napko.RealDash_tsn2xah6q27qw`, and the tee logged
`client -` forever.

Grant it once, **from an elevated prompt** (this needs admin and cannot be done
from an unelevated automation session):

```bash
CheckNetIsolation.exe LoopbackExempt -a -n=Napko.RealDash_tsn2xah6q27qw
```

Until that is run, bench-testing against `127.0.0.1:35000` will not connect.
Workarounds: bind the tee to the LAN IP instead of loopback
(`python deck/tee.py --sim --bind 0.0.0.0:35000`) and point RealDash at the
machine's own LAN address, which is not subject to the loopback rule.

On the deck this does not apply -- Android permits loopback between Termux and
a normal app, which is what `docs/PLAN-deck.md` already relies on.

## 7. Native peak-hold ("ghost") needles

RealDash has this built in; the tee does not need to compute it (the earlier
tee-side implementation was removed at the owner's request).

    select gauge -> LOOK'N FEEL -> SPECIAL -> HISTORY MODE = MAX
                                           -> HISTORY SHOW TIME = 5

The peak renders as a second needle at reduced opacity and clears after the
show time. A button can carry the "Reset Gauge History Mode" action.

Per-gauge tap-toggling of the ghost uses frame `0xC92` in
`board/scirocco_realdash.xml`: RealDash-local scratch values that nothing ever
transmits, so a gauge's press action can flip `Show Ghost *` between 0 and 1
and the ghost gauge's visibility binds to it.

## 8. Scale angles

All faces in `assets/png/` are drawn with the scale starting at the 7:30
position and sweeping 270° clockwise. In the editor that is:

    LOOK'N FEEL -> SPECIAL -> ANGLES & OFFSETS
      START ANGLE 225,  SWEEP 270

Verified against live simulator data: with the sim at 0 bar the boost needle
sits exactly on the 0.0 tick.

## 9. Killing the built-in scale text

Needle gauges draw their own numeric scale labels on top of a custom face.
To hide them without touching the face:

    LOOK'N FEEL -> COLORS -> TEXT COLOR -> OPACITY = 0

## 10. Page 2 / bulk gauge work — what actually works (2026-08-25)

Adding a second page: bottom page toolbar (click the bottom edge strip) ->
CONTEXT MENU -> PAGE -> **ADD RIGHT**. Navigate with NEXT/PREVIOUS PAGE.
LOOK'N FEEL -> DASH PAGE only styles the *current* page.

### Selecting a gauge: the trap that cost the most time

**Clicking a gauge on the canvas does NOT select it.** The header keeps
saying "0 GAUGES SELECTED" and every subsequent panel edit silently applies
to whatever was selected before — so a scripted loop appears to run fine and
rebinds the same gauge 8 times.

Reliable selection: the gauge list on the left has a **filter box at its
top** (approx 219,252 at 1920x1080). Clear it, type the exact gauge name
("Text Gauge 13"), and the list narrows to one row; click that row. Verify
the header reads "1 GAUGES SELECTED" and the X/Y/W/H boxes populate.

### Verify from the file, not from screenshots

Gauge rects are 4 float32 (L,T,R,B, normalised) at **name_end + 16** bytes,
where name_end is the end of the UTF-16 gauge name. That is stable and
patchable:

    python  # locate 'Text Gauge N' utf-16le, take end of name, +16, pack 4 floats

Patching positions this way is far more reliable than the editor's X/Y
boxes, which drop values intermittently, and it lets you lay out 42 gauges
exactly. **Input bindings are NOT patchable the same way** — records are
variable length and no fixed relative offset holds (checked against 12
known targetIds; best candidate matched only 4/12).

### The connection is what supplies the custom channels

If the RealDash CAN connection is missing, the input picker contains only
RealDash built-ins, so every search for one of our XML channels
(`damper_1`, `Charge Air Temp`, `Fault 1`, `aux_visits`, ...) returns an
EMPTY list — and clicking the first-result position then binds nothing,
leaving the gauge showing "The Text". Built-in-backed channels (MAP, Baro,
Throttle, Gear, Odometer, Check Engine ...) still bind, which makes the
failure look random until you notice the pattern.

RealDash's first-run wizard re-ran after a forced kill and **wiped the
connection and the root folder**. Recreate it before any binding work, and
confirm our channels appear as "ECU SPECIFIC: ..." in the picker.

### UWP loopback, again

Pointing RealDash at the PC's own LAN IP does **not** dodge the
AppContainer loopback block — same-machine connections are loopback
whatever address is used. RealDash sits on "Connecting ...". The only fix
is the elevated exemption in section 6. Without it the gauges bind fine but
show 0/defaults instead of live simulator values.

## 11. Gauge types, defaults and the rect offset per type

* **ADD GAUGE -> INDICATORS** holds NEEDLE / ARC / AXIS / BAR GAUGE,
  INDICATOR BAR / CLUSTER / LIGHT. TEXT GAUGE and IMAGE GAUGE are on the
  first level of the ADD GAUGE menu.
* An **INDICATOR LIGHT is stored as an "Image Gauge"** — creating six of them
  turned Image Gauge 6 into Image Gauge 12, and pulls RealDash's built-in
  `_indicators` sprite sheet into the asset library.
* **Bar gauges default to the theme green** `64FF82FF`. Fix via
  LOOK'N FEEL -> COLORS -> **BAR COLOR** -> hex + ENTER (same ENTER-commits
  rule as image blend). Unlike the image-blend case the hex here reads as
  plain RGBA.
* **Rect offset varies by gauge type**, measured from the end of the UTF-16
  gauge name: **text and bar gauges = name_end + 16**, **image and needle
  gauges = the first quad with L,T > 0 in the following ~400 bytes**
  (image gauges carry an image-name string first). A gauge parked at the
  origin has L=T=0, so a "first positive quad" scan skips it — special-case
  it at name_end+16.
* Reading 8 bytes before the real rect yields a decoy `(0, 0, L, T)` quad.
  Do not mistake it for the rectangle.

## 12. Bar gauge ranges

Set min/max in INPUT & VALUES like any gauge. Useful inversions:

* knock retard: min `0`, max `-12` so the bar is empty at rest and grows
  as timing is pulled;
* fuel trims: `-25` … `+25` against a centre line baked into the background.

RealDash bar gauges always fill from one end — a genuine centre-out bar
needs two mirrored bars.

## 13. Two more that bit late in the build

**A new gauge belongs to whichever page is active.** ADD GAUGE drops the
gauge on the current page, not on whatever page you were last *thinking*
about. Four fields created while page 1 happened to be showing ended up
stranded there, invisible behind the boost dial, while page 2 showed blank
rows. Navigate to the target page FIRST (bottom toolbar -> NEXT/PREVIOUS
PAGE), confirm the canvas shows that page, then add.

The gauge-list filter box only lists gauges **on the current page**, which
is also the quickest way to tell which page a gauge really lives on: filter
for it on one page, then the other.

**Delete pops a confirmation.** `{DEL}` on a selected gauge raises a
"Delete selected gauge / Are you sure?" dialog. A scripted delete loop that
does not click the tick will delete exactly one gauge and then fire the
rest of its clicks into the dialog.

## 14. Re-derive the channel list; never trust a remembered count

`deck/frame_schema.py` derives the channel list from
`board/scirocco_realdash.xml` at run time, and the XML changes whenever new
blocks are decoded. Page 2 was laid out for 59 channels and was already
stale by the time it was built -- the XML had gained four fuel channels
(`fuel_pump_duty`, `fuel_temp`, `rail_spec_bar_abs`, `rail_actual_bar_abs`
on frame 0xC8F). Before touching dashboard coverage, run:

```bash
cd deck && python -c "import frame_schema; s=frame_schema.load(); print(len(s.channels)); print(s.channels)"
```

and diff that against what the dashboard actually carries.

## 15. V2 inventory and DCC availability (2026-08-26)

The current XML derives **65** logged channels. V2 intentionally keeps the
legacy `steering_counts` column for historical CSV compatibility and adds
`dcc_age_s` plus `dcc_status` on frame `0xC91`; do not rename/reuse the old
field. The board sends `0xC91` even when the auxiliary reader is disabled so
RealDash cannot leave a held chassis value looking live.

The final v2 dashboard contract is **98 gauges**: page 1 = 42, page 2 = 22,
page 3 = 18, page 4 = 16. `v2_manifest.py` is the exact semantic inventory and
`V2-EDITOR-RUNBOOK.md` gives the only safe add order for preserving RealDash's
generic auto-numbered gauge names.

The page-4 Clear Codes control is a native Button Gauge with `Initial Action
Delay = 2000 ms`, `Actions When Pressed Down` enabled, and a `Hold Value`
action targeting the custom XML `Clear Codes` input. This sends the project
command through `0xC90` and re-arms on release; do not substitute RealDash's
generic OBD clear-codes action because it bypasses the board-side speed check.

## 16. Windows asset imports and reliable page creation (2026-08-26)

In edit mode the bottom-edge strip exposes the page toolbar. From the last
existing page use `CONTEXT MENU -> PAGE -> ADD RIGHT`; the command adds a page
but leaves the current page active. Use `NEXT PAGE` to confirm the new blank
page. Two empty pages add 72 bytes to this dashboard (36 bytes each), which is
a useful saved-file checkpoint before any gauges are moved.

The Microsoft Store build's file picker runs as the signed-in Windows user.
Files created inside the Codex workspace can be owned by the sandbox account
and appear as "You don't have permission to open this file" even though the
path is valid. Stage generated PNGs in
`%LOCALAPPDATA%\Temp\RealDash-v2`; files created there inherit
read access for the normal Windows account and import successfully.

Inside `SELECT ASSET`, the cyan circular-arrow control on an unused asset tile
opens the Windows file picker and replaces that asset. Selecting a new file
also renames the asset to the file's base name. This is a dependable way to
introduce a new page background when the large plus tile is not visible.
`Escape` unwinds the nested asset/page/look-and-feel panels one level at a time.

For multi-page moves, avoid sending `Ctrl+X` or `Ctrl+V` from a fresh desktop
automation process while the gauge-list filter has focus; RealDash may route
the chord to the filter instead of the selected gauges. Keep selection and the
cut command in one focused automation transaction, or use
`CONTEXT MENU -> EDIT` explicitly. A broad filter such as `Text Gauge 2`
allows multiple visible rows to be Ctrl-clicked without changing the filter;
changing the filter clears the previous gauge selection.

## 17. Direct `.rd` transformation is the production workflow (2026-08-27)

The slow part of this build was treating RealDash as the primary construction
tool. Once the 98 records and four pages existed, the reliable approach was
to treat the editor as a residual semantic-settings tool and make everything
deterministic outside it.

`rd_transform_v2.py` parses the embedded asset table at offset `0x76`; the
asset count is the little-endian `u32` at `0x72`. Each entry is a `u32` flags
field, a length-prefixed UTF-16LE name, a little-endian `u64` PNG length, and
the PNG bytes. The transformer preserves flags and ordering for existing
entries, replaces every same-name generated bitmap, appends only missing v2
assets, rebuilds the table, and preserves the remaining dashboard suffix.

It also performs bounded record-local substitutions:

* page 3 and 4 receive their two serialized background references;
* Image Gauges 7–12 stop sharing `_indicators.png_` and receive unique icons;
* Needle Gauges 8–12 receive a transparent face plus the correct ghost,
  spark, or wheel needle asset;
* the accidentally concatenated button name is replaced by the same-length
  `Clear Codes (hold 2 s)` string in both serialized locations; and
* the native button receives `btn_clear_codes.png` and `CLEAR CODES` labels.

The output is written to a temporary file in the destination directory and
atomically replaced only after all transformations succeed. The asset table
is reparsed at the end and every required v2 asset is asserted present.

`rd_manifest.py --apply-v2-layout` is the second deterministic stage. Text
and bar rectangles live at `name_end + 16`; image/needle/button records use
their proven inline rectangle location. The command writes all 98 rectangles
from `v2_manifest.py`, rereads them, and fails unless every result matches.
The successful proof file is 1920×1080, has 98 controls and 33 embedded PNG
entries, and loads/runs across all four pages in RealDash.

`rd_config_v2.py` is the third deterministic stage. Controlled record dumps
showed that all gauge types share a fixed-width six-float threshold/range
block followed 0x7c bytes later by the input identifier, and that needle
start/sweep radians follow the unique integer pair `5, 6`. The script asserts
those signatures per record, patches the new bindings/ranges/angles, replaces
all duplicate built-in needle scale-text colour blocks with invisible colours,
and atomically re-reads every result. Custom input identifiers are Java
`String.hashCode()` plus 1000; this reproduces the editor's existing IDs for
`steering_deg`, `Charge Air Temp` and all four damper channels and yields the
new `dcc_age_s`/`dcc_status` IDs without guesswork.

### Visual proof and what remains editor-owned

The first run-mode proof showed that page backgrounds and rectangles match
the generated previews. Oversized `The Text` placeholders are unbound text
gauges using RealDash's default 100% auto-scale. The long diagonal spark line
and grey zeroes are the new spark needle's default value range/angles and
built-in scale labels. The front-wheel assets are correctly placed but still
need the signed steering range/sweep. These are functional/style defaults,
not a failure of the 16:9 layout scale.

The first page-2 proof still failed visual QA even after separating those
defaults from the baked artwork: the full-size combustion illustration ran
into the lower panels and its crank hub did not coincide with the native
needle pivot; the AFR sensor, lambda medallion and warning copy competed for
the same space. The accepted revision scales the combustion mechanism to 78%
around the real pivot, keeps the arc labels close to the sweep, reduces the
AFR assembly to 72%, shifts it left/up, and places the unavailable-wideband
message in a separate compact card below the right side of the sweep. Both
upper instruments now end above y=403, leaving a clear gap before the data
panels at y=420.

Input bindings, Normal/Warning/Critical level colours, history mode, angle
and smoothing fields, conditional opacity, and button actions remain owned by
the RealDash editor until their variable-length records are decoded with the
same confidence as rectangles. Keep that pass bounded to the checklist in
`V2-EDITOR-RUNBOOK.md`.

### Full-screen automation discipline

The Microsoft Store process is `RealDash_uwp`. In full-screen mode it reports
`MainWindowHandle = 0`, so title-based focus helpers cannot restore it. Bring
it forward from the taskbar, take one full-screen checkpoint, then use raw
coordinates only while it remains foreground. At 1920×1080 the gauge filter
is centered near `(219,252)` and its single result near `(210,321)`.

Use one screenshot per selected gauge or completed settings group, not one
per keystroke. Wait for sliding panels to settle before using coordinates;
mid-animation clicks can land in the outgoing panel. The exact-name filter
and the visible `1 GAUGES SELECTED` header remain mandatory. Explicitly use
`File > Save` after each page-level group, then copy the staged user-owned
file back into the workspace and verify it with `rd_manifest.py`.

RealDash cannot open sandbox-owned workspace files directly on this machine.
`%USERPROFILE%\Pictures\RealDash-v2\scirocco-v2.rd` is the current staging
path. Hash it against `dash/scirocco-v2.rd` before editing and after copy-back.

## 18. History, decimals, and the live-needle peak-hold bug (2026-08-28)

Three more fields are now deterministic. All three sit at a fixed distance
from the range block that `_binding_and_range_offsets` already locates, so
they need no new anchor of their own:

| field | offset | type |
|---|---|---|
| history show time (seconds) | `range + 0x30` | `f32` |
| guard word, always `FFFFFFFF` | `range + 0x6C` | `u32` |
| history mode (0 = off, 2 = MAX) | `range + 0x70` | `u32` |
| decimal count | `range + 0x84` | `u32` |

Two independent sources agree on the history layout, which is why it is
trusted. The v1 editor left Needle Gauges 1, 2 and 4-7 at mode 2 with a five
second show time; and the controlled 2026-08-28 editor pass that set Needle
Gauges 8/9 to MAX/5 s changed exactly those two fields and nothing else
structural. Decimals was confirmed against rendered output: speed, gear and
the fault flags read 0 and display as integers, while both odometers read 1
and display the reported `185880.0` overflow.

**The live needles were carrying their own peak-hold.** That is the whole of
the reported "needle hangs at whatever the max was for four or five seconds,
then drops back, and only tracks accurately when rising". It was never a
binding fault. Gauges 1, 2, 4, 5, 6 and 7 are now forced to mode 0 and the
hold belongs to the ghost gauges 8/9 alone. `v2_manifest.py` already
described this correctly; nothing wrote it until now.

`rd_config_v2.py` takes both settings straight from `v2_manifest.GAUGES`, so
the manifest stays the single source of truth. The one exception is
`LEGACY_DECIMALS`, which names the page-4 odometer because the manifest does
not describe the inherited page-4 trip block.

### `--fields-only`, and why it had to exist

`patch()` is a one-shot. Its colour and scale-text stages recognise
RealDash's *default* byte patterns and consume them, so a second run fails
with `expected one text-colour signature, found []`. That is correct
behaviour, not a bug — but it means configuration changes normally require
re-running the whole pipeline from `scirocco-v2.pre-direct-20260829.rd`.

That pipeline is currently blocked. `dash/assets/png` was written by a
process running as `<machine>\CodexSandboxUsers` and its ACL excludes the normal user account:
27 of 28 PNGs cannot be opened at all, so `rd_transform_v2.py` dies with
`PermissionError` on `bg_page1.png` before it writes anything. The SVG
sources next to them are fine. Until someone runs

```powershell
icacls <repo>\dash\assets\png /grant <user>:(OI)(CI)F /t
```

the asset stage cannot run. History and decimals do not consume their own
signatures, so they are idempotent and `--fields-only` applies just those to
an already-configured dashboard. Prefer the full pipeline once the ACL is
fixed.

### Value smoothing

Confirmed the same way, 2026-08-28. RealDash presents **Look'n Feel ->
Special -> Value smoothing (%)** as a percentage but serializes the per-frame
blend coefficient `(100 - percent) / 100`, in **three** copies at
`range + 0x1C`, `+ 0x38` and `+ 0x3C` -- one per editing level, all written
together when Editing Level is `ALL`. Setting Needle Gauge 4 to 33% between
two otherwise identical saves moved all three floats from `0.2` to `0.67`,
and the 80% it started at reads back as `0.2`.

Every needle and text gauge in the dashboard was sitting at RealDash's 80%
default; bar gauges default to 10% (`0.9`). That is the "some gauges respond
super fast, others are balls slow and barely move" report -- at 80% a needle
closes only a fifth of the remaining distance per frame. `rd_config_v2.py`
now takes the percentage from `v2_manifest.GAUGES`, so the plan's smoothing
policy (0% for boost/RPM/speed/timing, 80% for the thermal and electrical
needles and their readouts) is applied to 91 controls in one pass.

The signature guard asserts that all three copies agree and that the
coefficient is in `(0, 1]` before writing, so a future RealDash version that
reorders the block fails loudly instead of corrupting a record.

### The probe recipe

Any remaining editor-owned field can be decoded the same way, and it is
cheap -- about fifteen minutes:

1. Copy the working file to `Pictures\RealDash-v2\probe-<field>.rd`.
2. Load it in RealDash and **save immediately, unchanged**. That is probe A.
   The save absorbs the canvas re-normalisation (below) so it cancels out.
3. Change exactly one field on one gauge. Save. That is probe B.
4. `python dash/rd_compare.py probe-A.rd probe-B.rd "<Gauge Name>"`.

The changed runs are reported relative to the record, and the offsets that
matter are almost always a fixed distance from the range block that
`_binding_and_range_offsets` already locates.

Two harness notes. `tools_ui.ps1 click` re-focuses RealDash on every call,
which dismisses the run-mode menu; use `clickraw` inside a single foreground
transaction instead. And `Ctrl+A` does not select inside RealDash's numeric
fields -- `{END}{BACKSPACE 30}` then type, then `ENTER`, is what works.

### Saving rewrites the design resolution

Reproduced deliberately: with the window at 1920x1080, File > Save wrote the
canvas as **1904x1039** -- the client area, not the design size -- and
re-normalised all 98 rectangles against the new aspect. This is the same
drift found in the staged copy from the previous session, so it is
systematic, not an accident.

Treat any file saved by RealDash as geometry-tainted. Harvest field *values*
from it, then rebuild through the pipeline from
`scirocco-v2.pre-direct-20260829.rd`; never copy a RealDash save back over
the working file. `rd_config_v2._records` refuses anything that is not
1920x1080, which is the backstop.

### Text alignment: Font & Text -> Formatting

`TEXT ALIGN` is a click-to-cycle row under **Look'n Feel -> Font & Text ->
Formatting** (LEFT / CENTER / RIGHT). It is *not* in the Text Area block --
that block is `u32(20), u32(0), u32(0), f32(1.0), f32(1.0), f32(height)` and
all 60 text gauges are byte-identical there apart from the height, which is
why no offline scan could find it.

It serializes as **two single bytes** immediately before the Text Area
marker, at `marker - 7` and `marker - 3`; `_text_height_offset` already
locates that marker. 0 = LEFT (RealDash's default), 1 = CENTER, 2 = RIGHT.
Confirmed by switching Text Gauge 55 to CENTER between two saves: exactly
those two bytes moved 0 -> 1, across the 71 gauges that carry a text area.

Every readout was LEFT, so text rendered hard against the left edge of a
rectangle that was itself centred on the dial -- the numbers sat visibly
off-centre. The six round-gauge readouts are now CENTER; the page 2-4
label/value rows keep LEFT, which is what their column layout wants.

Also found while searching: `Text Gauge 59` was the only text gauge whose
text height exceeded its box -- 75.6px in a 36px box, 210%, the stale
100%-of-75.6px default. It had never been added to `TEXT_HEIGHT_PERCENT`.
Fixed at 70% (25.2px).

### Bar fill direction, and why it does not fix the trim bars

`STYLE` (LEFT TO RIGHT / RIGHT TO LEFT) lives under **Look'n Feel ->
Special -> Style** for bar gauges, where needles have Angles & Offsets. It
is one byte at `range + 0x1D4`, 0 = left-to-right, 1 = right-to-left.

Bar Gauges 9 and 10 -- the negative halves of the fuel-trim pair -- are now
right-to-left so they grow outward from the shared zero line rather than
inward. That is necessary but **not sufficient**, and the live simulator run
proved why:

With `trim_short = -0.8` and `trim_long = +2.3`, the LT row's *negative* half
rendered **completely full**. RealDash normalises a reversed range
(`0 .. -25`) by magnitude, so it computes `(2.3 - (-25)) / 25 = 1.09`, clamps
to 1, and fills the bar. Direction only chooses which end the fill grows
from; it does not change the fraction. A negative-half bar therefore
saturates whenever the trim is positive -- which is exactly the "all red
until they get to the reading" report.

The range mechanism cannot express `clamp(-value / 25, 0, 1)`. The fix has
to change the value the bar sees, not its direction:

* **Gauge Math** (Look'n Feel -> Special -> Gauge Math) on the two negative
  halves: multiply by -1 and set their range to `0 .. 25`. A positive trim
  then goes negative and clamps to empty; a negative trim fills outward from
  zero. Two gauges, editor-owned, and worth checking whether the expression
  is fixed-width before assuming it needs the GUI each time.
* Or publish derived `trim_*_neg` channels from the board and bind to those,
  which costs a firmware and XML change plus a deck deploy.

Gauge Math is the cheaper of the two and stays on the laptop. Untested as of
this note. Worth also cycling `STYLE` past RIGHT TO LEFT first: if RealDash
offers a centre-out style, one bar per trim with a `-25 .. +25` range would
replace the whole two-bar arrangement.

### A hard limit on the automation

Ruled out offline, so nobody repeats the search. The Text Area block is
`u32(20), u32(0), u32(0), f32(1.0), f32(1.0), f32(height)` and every one of
the 60 text gauges is byte-identical there apart from the height. There is no
variation anywhere in the file to correlate against, so horizontal alignment
either lives somewhere not yet mapped or is not a per-gauge setting at all.
It needs the probe recipe above; do not try to infer it from the file.

What that search did find: `Text Gauge 59` was the only text gauge whose text
height exceeded its box -- 75.6px of text in a 36px box, 210%, the same stale
100%-of-75.6px default described under `TEXT_HEIGHT_PERCENT`. It had simply
never been added to that table. It is there now at 70% (25.2px).

### A hard limit on the automation

`tools_ui.ps1 focus` reports success even when Windows refuses the foreground
change. If another application owns the foreground, `SetForegroundWindow` is
blocked, RealDash stays behind, and every subsequent `clickraw` lands in
whatever window *is* in front -- silently, because `shotclient` then captures
that window's pixels instead. Check the screenshot actually shows RealDash
before trusting any coordinate sequence, and do not run blind click batches
while another app is in use.

### Still editor-owned

Visibility conditions (the Show Ghost toggles and the DCC status gating) and
button actions remain undecoded. Both are plausibly variable-length rather
than fixed-width, so probe them before assuming the fixed-offset approach
transfers.


## 19. The binding audit, and what the simulator caught (2026-08-29)

Running `deck/sim_feather.py` + `deck/tee.py --sim` while reading the live
dashboard is the acceptance test this build needed much earlier. Comparing
every displayed number against the same channel in the tee's own `drive.csv`
found five bindings that pointed at target ids nothing publishes, so they
showed a RealDash default instead of their channel:

| gauge | was | is | symptom |
|---|---|---|---|
| Text Gauge 1 MAP | 10 | 31 | read 0.0 forever |
| Text Gauge 2 Barometric | 31 | 11 | was showing the MAP |
| Text Gauge 9 Pedal | 230 | `Pedal Position` | read 0.0 |
| Text Gauge 12 Lambda | 254 | `Lambda Commanded` | 1.00 by luck |
| Text Gauge 44 boost readout | `Lambda Commanded` | copy of Needle Gauge 2 | read 1.0 by luck |

Two of those are the reason to check against logged values rather than by
eye. The page 1 boost readout was bound to lambda, which sits at 1.00, so it
looked correct every time boost happened to be near 1 bar. And the long
standing "MAP 0.0 kPa while Baro reads 99" oddity, flagged as a possible
sensor fault back at install, was never a sensor: MAP pointed at an
unpublished id and the row labelled Barometric was displaying the MAP.

Reproduce the audit with `rd_compare.record` + `_binding_and_range_offsets`
over every gauge, resolving each id against the XML's `targetId`s and
`String.hashCode()+1000` of its `name`s. Note that built-in channels bind by
*display name* hash too -- `hash("Boost") = 0x03D6376B` is the live boost
needle -- so an unresolved custom id is not automatically a fault.

### The trim bars, resolved

Bar fill direction (section 18) was necessary but not sufficient. RealDash
normalises a reversed range by magnitude, so the fraction, not the
direction, was wrong. Gauge Math would fix it but is a **variable-length**
record field -- setting it grew Bar Gauge 9 from 1046 to 1052 bytes -- so
the direct patchers cannot own it and every pipeline rebuild would discard
it.

The scriptable fix lives in the XML instead: `0xC85` now re-reads the same
two trim words with `conversion="V*-0.1"` under the names `Trim Short
Negative` and `Trim Long Negative`, and the negative-half bars bind to those
with a positive `0 .. 25` range. Same bytes on the wire, read twice. They
carry `displayOnly="true"`, which `frame_schema.py` now honours, so they do
not become CSV columns -- the channel count is still 65.

### Open after this pass

* `Rail pressure` (Text Gauge 11, built-in 202) displays 10.0 where the log
  says 52.0. The id *is* published with `V*0.1`, so RealDash appears to
  apply its own scaling to that built-in. Needs a probe.
* The Clear Codes button renders its artwork heavily dimmed. Blanking the
  duplicate native label made the button illegible, so the label is back;
  fix the artwork brightness first (a button probably has the same
  six-colour blend block as an image gauge).
* Clear Codes hold action, Show Ghost toggles and DCC visibility conditions
  are still editor-owned and still undecoded.


## 20. Refreshing the snapshot, and a design pass (2026-08-29)

### Adding controls without losing the pipeline

The pipeline rebuilds from `scirocco-v2.pre-direct-*.rd` every time, so any
control added in the editor to the *built* file is destroyed on the next run,
and `rd_transform_v2` is not idempotent so the built file cannot become the
new snapshot. The way to add a control is to edit the **snapshot**:

1. Copy the current `scirocco-v2.pre-direct-*.rd` to the staging folder.
2. Open it, add the controls -- on the right page, because a gauge is born on
   whatever page is active. Arrow keys page in **run mode only**; in the
   editor they do nothing, so leave edit mode, page across, and come back.
3. File > Save, copy back, then
   `python dash/rd_normalise_canvas.py saved.rd scirocco-v2.pre-direct-<date>.rd`
   to undo RealDash's 1904x1039 canvas rewrite (section 19).
4. Update `layout_v2`, `v2_manifest` (including `EXPECTED_GAUGE_COUNT`) and
   `rd_config_v2`, then run the three stages as usual.

New controls take the next free number in their type: adding three bar gauges
produced Bar Gauge 15, 16 and 17 regardless of which page they were created
on. The snapshot is now 100 named gauges and the build is 101 -- the button's
name only becomes a countable gauge name after the transform repairs it.

### The peak-hold ghosts were never missing

Reported as "not showing up", and the settings all looked right: MAX history,
correct binding, correct rect, correct page, full-white blend, ghost asset
assigned. Forcing history OFF still showed a single needle, which ruled out
history and pointed at rendering -- and the asset picker gave it away. The
ghost was a **translucent red copy of the live needle**, on the same pivot at
the same length. When the values agree it hides underneath; when they differ
it reads as a second red needle rather than as a peak marker. Opacity was
never going to fix that. The ghosts are now **white**, which separates
instantly and leaves red meaning "now" and amber meaning "target".

Worth generalising: when a gauge seems absent, check whether it is drawing
something indistinguishable from its neighbour before assuming it is not
drawing at all.

### Text alignment: only LEFT and CENTER are usable

Following section 18, `RIGHT` (2) writes and reads back cleanly but RealDash
renders the gauge left-aligned anyway. Road speed therefore gets CENTER with
its unit as a caption underneath, rather than a right-aligned number parked
against a label.

### Images and labels on a native button

A button draws its image *and* its native label, so a caption baked into the
artwork is duplicated -- and the baked one loses, because a 392px asset in a
294px button renders at 75%. RealDash also does not scale button artwork the
way the geometry predicts: a 44px icon that should have come out 33px wide
rendered as an 8px sliver. Let the image be the frame and the label be the
text; do not bake type or fine detail into button artwork.

### Design pass

Judgements made while reviewing the four pages against live simulator data,
recorded so they are not silently reverted:

* The gauge bezels were halved to 0.035 R -- the OEM VW ring is much finer --
  and the tick band moved out with them so the face reads larger.
* Page 2's cylinder-head and oxygen-sensor illustrations are gone. They sat
  where the needle sweeps and where the reading has to be found.
* `AIR & LOAD` went from ten rows to seven. Intake air is airbox temperature,
  charge air is post-intercooler manifold temperature and the one that drives
  knock, so with ambient present intake air is the redundant third; pedal is
  snapshot-only on this ECU while throttle is live, so throttle is the
  diagnostic one. Both plus barometric moved to the reference panel.
* Injector pulse width was promoted out of the fuel table to a bar beside
  spark and AFR, with amber past 12 ms and red past 16 on a 20 ms scale --
  one cycle at 7000 rpm is about 17 ms, so that is real headroom, not
  decoration.
* Steering became a two-way bar growing from a centred zero, using the same
  negated-channel trick as the fuel trims.


## 21. Bars, ghosts and a second design pass (2026-08-29)

### How a RealDash bar treats its value

Established by colouring the four fuel-trim bars individually and measuring
where each one actually drew:

* A **left-to-right** bar clamps to `[0, max]`. A negative value draws
  nothing. This is what makes the *positive* half of a two-way pair correct
  for free.
* A **right-to-left** bar draws the **magnitude**. A negative value fills
  just as a positive one does, so the half anchored on a shared zero line
  lights for both signs.
* Bar fill colour does **not** follow the level. Two builds with opposite
  Normal/Critical assignments rendered identically, so the level trick that
  works for text gauges cannot hide a bar.

Consequence: a true two-way bar needs a channel that is already clamped --
`max(0, -trim)` computed on the board -- because neither the range, the
direction, nor the level colours can express it. Until that exists the
outward halves are set fully transparent and the positive halves carry the
display: a positive trim grows outward from the zero line, a negative one
leaves the row empty, and the signed number is on page 2 regardless. The
same applies to the steering bar's left half.

Also worth recording, because an earlier comment in `rd_config_v2` had it
backwards: for the six-float block the pairs are (critical window, warning
window, full range), and the full range is what scales the fill.

### Peak-hold ghosts: shape, not colour

The ghosts were reported missing twice. First they were a translucent red
copy of the live needle -- same pivot, same length -- so they hid underneath
it. Making them white separated them but read as a second highlight. The
shape was the real fix: a **short stub in the outer 30% of the needle
length**, in the needle red with the brightness pulled down. It cannot cover
the live pointer, and it reads as a mark left behind rather than a rival
needle.

### Boost target

Now a red notch drawn at exactly the tick band's radius, length and weight,
so it reads as a moving graduation the needle chases. The amber wedge it
replaced looked like an artefact: amber appears nowhere else on the face, and
it floated inboard of the scale. Its numeric panel is gone -- the notch is
the readout.

### Deleting controls, and one trap it exposed

Deletion works the same way as addition: edit the **snapshot**, select the
gauge, press Delete, confirm, save, then `rd_normalise_canvas.py`. Names are
never reused, so numbering stays stable.

Adding `Needle Gauge 13` exposed a latent bug in `rd_manifest._find_rect`:
`inline_needles` was hardcoded to needles 8-12, so the new one took the
no-inline-asset fast path and matched the `(0, 0, L, T)` decoy eight bytes
early. That decoy is only harmless when the real left and top are both zero.
Extend the range whenever a needle is added, and give the new gauge its
asset in `rd_transform_v2.needle_assets` so its record shape matches.

### Design pass

* Page 1's lower third was on three different baselines and three widths; it
  is now a two-row grid on the full 20..1260 measure in equal thirds.
* Page 2 carries three matched half-moon instruments -- spark, injector pulse
  width, AFR -- on one baseline, with the panels gaining 50px of height. The
  page title used to run straight through the first instrument heading.
* Page 3 lost "FRONT OF VEHICLE", the DCC status line, the wheel-direction
  footer, the gear repeat and the DCC age. The car is drawn inside a group
  scaled about its nose, which frees a band at each end so the steering bar
  and the rear ride height no longer sit on the bodywork.

### Still open

* `Text Gauge 45` (the old boost-target numeric) is parked off-canvas rather
  than deleted; fold it into the next snapshot edit.
* `Rail pressure` still displays 10.0 where the log says 52.0.
* Clear Codes hold action and the Show Ghost toggles remain editor-owned.


## 22. The fast tuning frame, and why the two-way bars were abandoned (2026-08-29)

### 0xC93: spark advance and injector pulse width, every cycle

Both are read in `boost.py`'s FAST_TIER, but they shipped only in the
`SLOW_EVERY` block, so a fresh sample could sit up to five cycles before
RealDash saw it. They now have their own frame that goes out every cycle,
alongside 0xC80 and 0xC81.

The scaling matches their slow-frame counterparts exactly (0xC88 word 0 and
0xC84 word 3), and the board still puts the same words in those frames -- the
XML simply declares each channel **once**, on 0xC93, because two declarations
would give the logger two columns for one channel. `deck/sim_feather.py`
imports `board/realdash.py` directly, so the simulator picked this up with no
change of its own.

Worth knowing what this does and does not buy: the acquisition rotation still
refreshes each block every ~2.9 s under load, so this removes transmission
lag, not acquisition lag. It is the difference between a fresh sample waiting
300 ms and waiting none.

### Two-way bars do not work in RealDash, and here is the evidence

Four experiments, each colouring the bars individually and measuring where
they actually drew:

1. A **left-to-right** bar clamps to `[0, max]`; a negative value draws
   nothing. A **right-to-left** bar draws the **magnitude**. So a pair of
   halves meeting at a shared zero lights on both sides for either sign.
2. Feeding the outward half a negated channel fixes its magnitude but not the
   problem: the same half still lights when the negated value goes negative.
3. Level colours cannot hide it. Two builds with opposite Normal/Critical
   assignments rendered identically, so a bar's fill colour does not follow
   its level.
4. A fully transparent fill cannot hide it either: `00000000` reads as
   "unset" and RealDash falls back to red. Painting the fill the track's own
   `#040506` at full alpha does hide a bar -- but by then there was no reason
   to keep the arrangement.

So both the fuel trims and the steering angle are now **one bar across the
whole track over the full signed range** (-25..+25 and -600..+600), with the
zero mark the background already drew at the midpoint. The fill edge is the
reading; the mark says where zero is. Always correct, no hidden halves, and
no firmware needed. The four outward halves are parked off-canvas until they
can be deleted from the snapshot.

### Design pass

* The four small dials went 200 -> 210 (+5%). 216 was tempting but left 2px
  between the boost dial's rim and the coolant dial.
* Page 2's faded subtitles are gone; each unit is now a caption under its
  reading, the same treatment GEAR and km/h get on page 1. The three arcs are
  centred on the panel columns beneath them (267/708/1081), so the top and
  bottom halves of the page share one set of vertical axes.
* Page 2's table values sit two pixels high of where the naive box put them:
  RealDash centres the glyph in the box, so a box centred on the label's
  baseline lands the value's baseline about two pixels low.
* Spark and injection readings are centred under their own arcs.

## 23. The gauge band: backgrounds and gauges live in two coordinate spaces

Measured on the deck 2026-08-29, RealDash running with the Android navigation
bar visible (window 1280x660 of a 1280x720 screen):

* A **page background** (set via `assign_page_background`, a page property)
  is painted across the **whole** 660 px window, 1:1 with the design.
* Every **gauge** is laid out in a shorter band: a gauge authored at design
  `y` renders at `y_screen = 0.9136 * y + 30`.  The 660-tall design is
  squeezed into 603 px and centred, i.e. RealDash subtracts the 60 px
  navigation bar twice.  Horizontal is untouched (1:1).

Evidence: least-squares fit over the twelve dial-bezel edges on page 1, all
residuals under 3 px; and the page-1 dials measure **393x361** on screen
instead of 396x396 -- visibly elliptical once you look for it.

The failure this causes is subtle and was misread twice as a stale build:
labels (artwork) and their values (gauges) drift apart the further they are
from mid-screen, because they scale about different centres.  On page 4 the
label rows stepped 74 px while the value rows stepped 68 px, so the top row's
value sat 23 px high of its label and the bottom row's 11 px low.  A
full-canvas background is *invariant* under the band transform, which is
exactly why the panels and page titles looked correct and only the readings
looked wrong.

**It is transient, and must NOT be compensated for.**  Reloading the file a
second time produced gauges at a plain 1:1 scale -- the page-4 value rows
stepped the authored 74 px, not 68 -- with everything aligned.  So the squeeze
is a state of RealDash's buggy load-time layout, the same bug that starts the
dashboard with its top pushed off screen, and not a property of the deck.
Compensating for it in the artwork bakes the distortion in backwards whenever
RealDash lays out correctly, which is what happened on the first attempt: the
labels came back at 68 px pitch against 74 px gauges, the same defect mirrored.

`generate_assets.to_gauge_band()` and its constants are kept behind
`GAUGE_BAND_COMPENSATION = False` as a diagnostic.  Leave it off.  If the
squeeze ever becomes the steady state, re-measure with the dial-bezel fit
rather than adjusting layout constants by eye -- and confirm across two
separate reloads before changing anything, which is the step that was missing
the first time.

The practical rule: when a page looks like its readings have drifted off their
labels, check whether the dials are circles or ellipses before touching any
layout constant.  Ellipses mean RealDash is mid-bug and the file is fine; a
reload, or entering and leaving the options screen, restores it.
Unrelated but adjacent: RealDash's own load-time layout is buggy here -- it
first draws with the top of the dashboard pushed off the screen, and
entering and leaving the options screen forces a correct re-layout.  All
measurements above are from the *post*-re-layout state.

## 24. The alignment round, and how to measure it (2026-08-29)

Four deck screenshots per pass, swiping between pages with
`adb shell input swipe 1000 300 300 300 250`, captured with `exec-out
screencap -p`.  Every fix below came from a pixel measurement, not from
looking at the image -- eyeballing produced two wrong diagnoses in a row
earlier in the same session.

**Always run the ellipse test first.**  Scan a column through the middle of
the rpm dial and a row through its centre:

    design (10, 56, 396, 396) -> expect roughly 11..404 across, 58..451 down

396x396 means RealDash has laid out correctly and what you see is real.
393x361 means it is mid-bug (section 23) and *nothing* on screen can be
trusted; reload, or enter and leave the options screen, and capture again.
This one check would have saved the whole detour.

Useful measurement shape: collect runs of lit rows in the label column and in
the value column separately, then compare **pitch** before comparing position.
Equal pitch with a constant offset is a placement error worth a few pixels;
unequal pitch is a coordinate-space error and the offset is a red herring.

    labels x 470..600 threshold 90     (dim grey captions)
    values x 655..910 threshold 200    (bright white readings)

What that turned up, all fixed:

* page 1 -- odometer and trip values stepped 32 px against 38 px labels, so
  the odometer sat 8 px low of its caption and the trip 3 px.  Re-pitched to
  38 (`PAGE1_TEXT` odometer y 484 -> 476, trip 516 -> 514).
* page 2 -- values sat a consistent 3-4 px below their label baselines; the
  lift in `page2_value_rects` went from `baseline - 18` to `baseline - 22`.
* page 2 -- `("Lambda snapshot", "snapshot")` said the same word twice, and
  the long unit reserved 86 px, pulling that one reading ~20 px left of the
  column the other six shared.  Unit dropped to `""`.
* page 3 -- steering angle and road speed were LEFT-aligned inside centred
  boxes, so each read ~70 px left of the thing it belonged to (its L/R bar,
  its own "km/h" caption).  Both added to `TEXT_ALIGN` as CENTER.
* page 4 -- `"P0234 overboost"` is 264 px wide and overran its 150 px box
  into the SYSTEM HEALTH panel, painting over "Reconnects".  Fault values are
  free-form text rather than numbers, so `page4_value_rects` now gives that
  one column a 260 px box and leaves the others at 150.
* page 4 -- Clear Codes sat on the Check engine row's rule; moved down 10 px.
* page 2 -- the two arc pointers were the only white needles in the
  dashboard.  `spark_needle` now fills with `NEEDLE`, the cluster red used by
  the six dials on page 1.  Their angles were always correct; only the colour
  and the reader's confidence were wrong.
* page 2 -- arcs down 10 px (`PAGE2_ARC_TOP` 66 -> 76) so the instrument
  titles clear the page title, and the value row up 8 px (284 -> 276) so its
  boxes stop short of the panel tops at 330, which had been clipping the
  wideband notice.
* page 3 -- ride-height readings moved under their own captions
  (`height_fl` x 170 -> 44, `height_fr` 1144 -> 1018) and the rear reading
  centred on the car's axis.

Still wrong, and **not** layout: `Rail pressure` reads 145.0 where the log
said 52, and `Rail specified` / `Rail actual` read 580.2 / 442.4 bar against
a 40-150 spec.  `Engine load` reads 100.0 at 8 % throttle.  All three are
channel scaling in the acquisition path.
