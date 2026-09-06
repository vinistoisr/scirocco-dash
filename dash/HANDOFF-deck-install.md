# Legacy handoff: installing the original dashboard on the deck

Written 2026-08-25. Everything below is done at the car, with the deck
powered (ignition on or ACC) and the laptop on the same WiFi.

> **V2 note (2026-08-26):** this document is retained for the preserved
> original only. It is not the v2 deployment procedure. The original file now
> inventories as 80 gauges and predates the two new DCC status channels. Use
> `dash/V2-EDITOR-RUNBOOK.md` and `dash/README.md` for the four-page rebuild.

What this legacy procedure installs: `dash/scirocco.rd`, a two-page RealDash
dashboard styled after the car's own cluster, built against the 63-channel
schema that included the fuel-system channels added
2026-08-24 (`fuel_pump_duty`, `fuel_temp`, `rail_spec_bar_abs`,
`rail_actual_bar_abs`, frame 0xC8F).

---

## 0. Before you leave the house

Copy these two files somewhere you can reach from the deck. They must go
together — the `.rd` embeds its artwork, but the XML is what makes the
channels exist:

    dash/scirocco.rd                    the dashboard
    board/scirocco_realdash.xml         the channel description

Confirm they are the current ones:

```bash
python -c "import sys;d=open(r'dash/scirocco.rd','rb').read();import struct;print(len(d),'bytes',struct.unpack_from('<ii',d,0x34))"
```

Expect roughly 1.18 MB and `(1920, 1080)`.

---

## 1. Get both files onto the deck

Deck IP has been <deck-ip>; confirm on the deck under WiFi settings if
ADB refuses.

```bash
adb connect <deck-ip>:5555
adb push dash/scirocco.rd /sdcard/
adb push board/scirocco_realdash.xml /sdcard/
```

If `adb connect` fails, the deck's Developer Options / USB debugging has
been switched off again — Settings, tap System 4x, then Developer Options.
(The 3368 PIN in the old bootstrap notes does NOT work on DuduOS 3.7; the
settings screen opens directly via
`adb shell am start -a android.settings.APPLICATION_DEVELOPMENT_SETTINGS`
only once ADB is already up, so this one is a hands-on-the-screen step.)

---

## 2. Point RealDash at the tee, with the XML

**This must be done before loading the dashboard.** Without a RealDash CAN
connection carrying our XML, the custom channels do not exist, and the
dashboard's gauges bind to nothing. (That failure mode cost a whole session:
gauges silently show "The Text".)

In RealDash on the deck:

1. **Garage** → click the **car's cabin** → click the **dash screen** in the
   3-D scene. That is where the connections list lives; it is NOT under
   Settings.
2. **ADD** → **Adapters (CAN/LIN)** → next → **RealDash CAN** → next →
   **WIFI/LAN** → next.
3. IP `127.0.0.1`, port `35000`, next.
4. **CUSTOM CHANNEL DESCRIPTION FILE** → browse to
   `/sdcard/scirocco_realdash.xml`.
5. **DONE**.

Loopback works on Android between Termux and a normal app — the Windows
Store-app restriction that blocks this on the laptop does not apply here.

---

## 3. Load the dashboard

Enter edit mode (tap the screen to raise the top menu → **EDIT**), then
**FILE → LOAD…** → `/sdcard/scirocco.rd`.

Swipe left/right between the two pages:

* **Page 1** — driving view: tach, boost, water/oil/charge/volt, odometer
  and trip, gear and speed, knock retard per cylinder, ignition, N75 duty,
  engine load, ST/LT trims, lambda, boost target, and six telltales along
  the top.
* **Page 2** — data page: six panels covering the remaining channels
  (air path, fuel system, mixture & driveline, chassis/DCC, trip & faults,
  link health).

---

## 4. Check the aspect ratio

The dashboard was authored at **1920x1080 (16:9)**; the deck is
**1280x720**, also 16:9, so it scales cleanly. RealDash preserves the saved
aspect and letterboxes rather than stretching.

**If the gauges look squashed or letterboxed**, the deck is not reporting
16:9. Do not "fix" it by dragging gauges — re-save from a 16:9 editor
instead, or the whole layout drifts.

---

## 5. What "working" looks like

Start the tee (or let it autostart) and confirm on the deck:

* the tee log shows a **client connected**, not `client -`;
* page 1 tach and boost needles move with the engine;
* page 2 **Sample rate** shows roughly **14 Hz** — that one field is the
  quickest health check of the whole chain;
* **Fuel temp**, **Pump duty**, **Rail spec** and **Rail actual** show
  plausible numbers. These are the newest channels and the least exercised —
  if they read zero while everything else moves, suspect the 0xC8F frame
  rather than the dashboard.

---

## 6. Things to verify on the first real drive

Nothing below is broken; these are the values that have never been seen
against a running engine.

* **Knock retard bars** — scale is unverified. The bars are ranged 0 → -12°,
  empty at rest, growing as timing is pulled. Treat any movement as a
  lift-and-log event, not a calibrated number, until it is checked against
  a VCDS or tuner log under load.
* **Oil temp** — inferred, not documented. Confirm on a COLD START: coolant
  should climb fast while oil lags.
* **Boost zones** — amber 1.2, red 1.6 bar. Adjust in the gauge's
  Input & Values if the car's real ceiling differs.
* **Rail spec vs rail actual** — both in bar absolute. Worth watching that
  actual tracks spec under load; divergence is the interesting signal.

---

## 7. If something looks wrong

| symptom | cause | fix |
|---|---|---|
| Gauges show "The Text" | connection missing or XML not loaded | redo section 2; the channels must appear as "ECU SPECIFIC: …" in the input picker |
| Everything reads 0, needles dead | tee not running or RealDash not connected | check the tee log for a client; confirm IP/port 127.0.0.1:35000 |
| Whole dash tinted pink/green | gauge blend colour | Look'n Feel → Colors, EDITING LEVEL = **ALL**, Image/Needle/Bar blend colour to `FFFFFFFF`, press **ENTER** (TAB does not commit) |
| Page background missing, giant white icons | page background got set to the `_indicators` sprite | Look'n Feel → Dash Page → Select Image → pick `bg_page1` / `bg_page2` |
| Telltales never light | they are correct when dark — they show only when their flag goes non-zero | force one via the sim, or check 0xC87 in the tee log |

More detail on every RealDash quirk encountered: **`dash/EDITOR-NOTES.md`**.
Layout coordinates and channel bindings: **`dash/README.md`**.

---

## 8. Rebuilding the artwork later

The gauge faces, backgrounds and icons are generated, not hand-drawn:

```bash
python dash/generate_assets.py && python dash/make_preview.py
```

To push regenerated artwork into the existing dashboard, do NOT re-import —
open any image slot's asset picker and use the **↻ icon on the tile**, which
replaces the bitmap in place and keeps every gauge reference intact.

Gauge positions can be patched directly in the `.rd` (four normalised
float32 per rect) far more reliably than through the editor's coordinate
boxes — see EDITOR-NOTES.md §10-11 for the offsets. Input bindings cannot;
those must go through the UI.
