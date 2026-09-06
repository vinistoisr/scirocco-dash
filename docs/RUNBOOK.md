# Runbook -- the system as built, 2026-08-23

## The chain

    Feather M4 CAN (OBD port, TP2.0 @ ~14 Hz)
      | USB, single CDC (boot.py disables the console so the DATA port is
      |                  interface 0 -- see "the CDC bug" below)
      v
    USB Serial Telnet Server app   claims USB, serves 127.0.0.1:2323
      v                            (Telnet-framed, not raw -- tee handles it)
    tee.py       logs every byte + decoded CSV, serves 127.0.0.1:35000
      v
    RealDash     RealDash CAN -> WiFi/LAN -> 127.0.0.1:35000
      +
    upload.py    closed sessions -> Cloudflare R2 -> scirocco.example.com

    watchdog.sh supervises tee.py + upload.py and restarts the bridge app
    if its TCP server dies. Termux:Boot starts the watchdog on cold boot.

## Verified working on the car (2026-08-23)

- 13.6-13.8 Hz live from the ECU, RealDash connected over TCP.
- 557 command frames forwarded to the board, 0 rejected -- the RealDash ->
  board command channel is live (so Clear Codes / Reset Peak will work once
  bound to a dashboard control; see "RealDash config").
- Burst segmentation triggering on real driving.
- Session 2026-08-23_1331 uploaded from the deck to R2 and visible on the
  dashboard: 213 s, 206 baseline rows, 28900 frames decoded.
- Watchdog recovery TESTED: killed tee.py, back in under 20 s by itself.
  RealDash showed "connecting" and re-attached on its own.

## The CDC bug (the thing that made nothing work)

CircuitPython numbers USB interfaces console-first. With console+data both
enabled, usb_cdc.data landed on interfaces 2/3 -- and RealDash on Android
always claims port index 0 with no picker, so it attached to the REPL and
sat reading Python text. Proven from the deck's sysfs (driver=usbfs on the
console pair, cdc_acm on the data pair). boot.py now disables the console,
so the data port IS interface 0. Jumper A1 -> GND at power-up to get the
REPL back on the bench.

## Operating notes

- Deck IP moves (.58 -> .61 seen). Set a DHCP reservation; a lot of
  "connection failed" noise was just this.
- adb over the deck's WiFi is unreliable and gets worse over a session:
  small pushes (<1 MB) are fine, multi-MB ones fail with "failed to read
  copy response: EOF". A deck reboot clears a wedged adbd.
- DuduOS silently rejects any adb push of a file named *.apk. Rename to
  *.dat, push, then `pm install /data/local/tmp/x.dat`.
- Termux must be the GitHub *debug* build (run-as works -> scriptable from
  the laptop). Launch it with `monkey`, not `am start`: Android 13's
  background-service rule kills the latter.
- Headless Termux shell:
  `adb shell run-as com.termux sh -c '<env>; <cmd>'` with PREFIX, HOME,
  PATH, LD_LIBRARY_PATH, TMPDIR all set.

## Checking on it

    adb connect <deck-ip>:5555
    adb shell run-as com.termux tail -3 files/home/tee.log
    adb shell run-as com.termux tail -5 files/home/upload.log
    adb shell run-as com.termux tail -5 files/home/watchdog.log

Healthy tee line looks like:
    [tee] 13.8 Hz | client 127.0.0.1:xxxxx | session ... | cmds N (rej 0)

## Cloud dashboard

https://scirocco.example.com -- rebuilt 2026-08-23 to show EVERY logged
channel, not a fixed five panels. Channel discovery is header-driven (the
union of drive.csv and bursts.csv headers), so a new tee channel appears by
itself; anything uncatalogued lands in an "Other" group rather than being
dropped. Grouped channel picker persisted in localStorage, a summary tile
strip, and a stats table with min/max/mean/last/n for every column
including ones not plotted.

Verified against the REAL uploaded drive (2026-08-23_1331), not just
fixtures: 39 channels in the stats table, values identical to the raw CSV
(timing -4.5/3.7 mean -1.7, coolant 66->77, rpm 720-760, knock 0.0), four
panels, no console errors.

## Known gaps

1. Sessions killed mid-write (e.g. by a forced tee restart) have no
   meta.json and are therefore never uploaded -- by design, meta.json is
   the commit marker. Three such orphans exist from testing; harmless, but
   nothing sweeps them up yet.
2. termux-api CLI is not installed (`pkg install termux-api`), so
   termux-wake-lock and the home-SSID check are no-ops. The SSID gate
   currently FAILS OPEN, i.e. it uploads on any network. Fine at home;
   install termux-api to enforce the gate and hold a wake lock.
3. Bridge-app auto-restart logic is written but has not been observed
   firing on a real failure.
4. GPS merge is inert (termux-location absent, same termux-api cause).
5. Test tones: 106 MB never transferred -- adb cannot move files that big
   over this link. Use a USB stick.
6. cloud/testdata/make_testdata.py is STALE relative to the shipped system:
   it emits the old column names (load_pct, iat_k, n75_pct, fault_count...),
   puts `segment` as the LEADING column, has no fault_1..4, and fills in
   GPS. deck/tee.py writes frame_schema names with a TRAILING segment and
   blank GPS. The dashboard tolerates both, but the generator should be
   regenerated from frame_schema before it is trusted again.
7. Two EOBD snapshot channels look wrong in the logs and are worth a second
   look: lambda_commanded pinned at 1.999 (PID 0x44 returning 0xFFFF, i.e.
   not supported at that moment, decoding to ~2.0) and fuel_rail_pressure
   at 5.9 bar in one session vs 38.7 bar in another. Both are read ONCE at
   startup, so they capture whatever the ECU said at that instant -- treat
   them as indicative, not live, and consider dropping lambda if 0xFFFF
   turns out to be its normal answer on this ECU.

## RealDash config -- DONE 2026-08-23

Configured by driving the deck's UI over adb (`screencap` + `input tap`),
saved as `newdash.rd`. It contains:
- a **Button** (top left) that clears fault codes, and
- a **text gauge** under it bound to `Fault Codes` (the stored-fault count).

Verified live: pressing the button produced `cmd 0xC90 words=(1,0,0,0)` in
the tee log, then `(2,...)` and `(3,...)` on later presses -- i.e. RealDash
-> tee -> board, every press.

Two things that are NOT obvious and cost real time:

1. RealDash's built-in "Read/Clear Error Codes" actions drive its own
   OBD2/ELM327 stack and can never work with a custom RealDash CAN feed.
   The working recipe is: Button -> INPUT & VALUES -> SELECT BUTTON ACTIONS
   -> NEW ACTION -> type **Change Value** -> input **Cmd Clear Codes**.
2. "Change Value" CLAMPS at MAX, it does not wrap. Configured 0..1 the
   value latches at 1 forever and a strict 0->1 edge fires exactly once per
   app restart. Fixed on BOTH sides: the action is MIN 0 / MAX 9999 /
   STEP 1 so every press yields a new number, and the firmware now triggers
   on "changed AND non-zero" (`_fired()` in gauge_main.py) rather than a
   0->1 edge. Zero still means idle, so a value can be parked to disarm.

To add the other two buttons, repeat with `Cmd Refresh Codes` and
`Cmd Reset Peak`. The menu is a TAP NEAR THE TOP of the screen (it
auto-hides after a couple of seconds), then EDIT.

The remaining fault inputs `Fault 1`..`Fault 4` are enum-mapped (564 shows
as P0234, 8801 as P2261) if a per-code readout is wanted later.

## The scoped-storage bug (why RealDash "refused to load the XML")

Symptom: RealDash's CAN description-file list opens **empty**, and picking a
file through the Files app appears to do nothing. Three different XML files
failed identically, including a 551-byte one-frame test file -- so the XML was
never the problem.

Cause, from logcat on the head unit:

```
D/NUTS: Exception in uri permission: No persistable permission grants found
        for UID 10153 and Uri content://com.android.externalstorage.documents/...
E/MediaProvider: Permission to access file: .../scirocco_realdash.xml is denied
W/NUTS: File::Open - failed ... reason: Permission denied
```

RealDash v2.6.8 targets SDK 37 (full scoped storage) but its native NUTS
engine still opens description files by **raw path**. It launches
`ACTION_GET_CONTENT`, whose URI grants are not persistable by design, so
`takePersistableUriPermission` throws; it then falls back to a raw path that
MediaProvider refuses.

The tell is the *pair* of errors. RealDash's own `newdash.rd` opens fine and a
missing file reports `No such file or directory`, but our pushed file reports
`Permission denied` -- RealDash could read only files it created itself.

Fix: `deck/fix-realdash-storage.sh`. It sets **`NO_ISOLATED_STORAGE: allow`**,
which stops MediaProvider mediating that uid's raw-path opens.

Two dead ends worth not repeating:

* `MANAGE_EXTERNAL_STORAGE` cannot be granted -- RealDash does not declare it
  in its manifest, so `appops set ... allow` silently reverts to `default`
  and `pm grant` fails with *"not a changeable permission type"*. Setting it
  also **force-kills RealDash** (`Killing ...: MANAGE_EXTERNAL_STORAGE changed`).
* The deck is a `user` build with no `su` and `adbd cannot run as root`, so
  RealDash's private config cannot be edited directly.

RealDash creates `camera/ datalogs/ dyno/ settings/ trackdays/ tripdiary/` at
install but **not** `descfiles/` -- create it by hand and put the XML there.

### Driving the head unit's screen over adb

RealDash is an OpenGL surface: `uiautomator dump` returns no text and there
are no accessibility nodes, so every step must be verified by screenshot.
It also **ignores `input tap`** -- a zero-duration synthetic tap is swallowed.
Use a real press/release pair:

```
input motionevent DOWN <x> <y>; sleep 0.12; input motionevent UP <x> <y>
```

Path to the description file, on a 1280x720 screen:
tap top edge (640,12) -> GARAGE (745,45) -> driver's door (760,330) ->
gauge cluster (160,175) -> the connection row (900,250) ->
CAN DESCRIPTION FILE (364,225) -> CUSTOM CHANNEL DESCRIPTION FILE (293,627) ->
pick the file -> SELECT FILE (869,632) -> DONE (150,55).

Note the top menu auto-hides after a few seconds, and `dumpsys window` may
report focus on display 2 -- that is the DUDU launcher's virtual split-screen
NAV panel, not a real second screen. Input still goes to display 0.

## Marker UX (revised 2026-08-23 after first real use)

The first cut painted each marker's name and per-panel readout as a stacked
plate on every chart. In practice that buried the trace it was annotating,
and with several markers close together the staggered rows collided so text
sat outside its own box. Now:

* The chart carries only the rule, a **very subtle** band (about 2% lift over
  the background -- measured, not eyeballed) and a small numbered chip.
* The hatch is kept ONLY for the selected marker, which is the one moment a
  reader needs to pick one band out of several.
* Name, timestamp, values and note all live in the **hover tooltip**, which
  clears on pointerleave. The list above the charts keeps names permanently
  visible, so nothing is lost.
* Hit priority is chip > rule > band, so a wide band cannot swallow the
  edges inside it.

Any chart, and the map, can be **expanded to fill the display** and collapsed
again (Esc also collapses). Only one thing owns the screen at a time. uPlot
draws to a fixed-size canvas, so the `.full` class only makes the room --
`setSize()` does the actual resizing.

Note: the page is served by the Workers asset layer with caching, so after a
deploy a hard refresh (Ctrl+Shift+R) may be needed to pick up changes.

## Hz optimization round (2026-08-23 late)

Target was 17 Hz smooth; measured result on the car (burst-row timestamps,
median cycle gap): **17.9-18.2 Hz steady, p90 100 ms, no stalls** -- up
from 13.6 Hz, and 11-12 with the aux poller.

What did it (all verified by a 33-agent adversarial review first):
* EXTRA_EVERY 4 -> 6 with a load-adaptive two-tier rotation: under load,
  knock/timing/trims/IAT/speed refresh ~1.8 s (better than the old flat
  2.9 s); thermals stretch to ~4 s.
* connect(assume_clean=True) for aux visits: skips the 2 x 200 ms
  precautionary teardown after polite closes. Visits ~100-200 ms now.
* send_all: one buffered write per cycle; the 12 slow frames go every 5th
  cycle (their sources refresh no faster anyway).
* Baro read moved to elif -- it collided with an extra read every 36 s
  for a ~150 ms stutter.
* Status NeoPixel written only on change.

Tried and REVERTED: T3 inter-frame gap 1 ms. The MED17.5 accepts the
params, then degrades the channel (~6 Hz, stalls, drops). If retried, use
3 ms (0x1E) and watch it live.

## The 'flaky telnet server' -- root cause (finally)

The board only drained its inbound command port after a successful ECU
read. RealDash streams ~2 button frames/s, so during any board wait (boot,
ECU asleep, aux visit) the CDC OUT buffer filled, the BRIDGE app's serial
writes threw, and it dropped the tee's connection -- reconnect churn that
looked exactly like a flaky server app. Every wait path drains now, and
reconnects have been flat since. If churn ever returns, check FIRST
whether the board is draining, not the bridge.

Boot order is also fixed: the board waits for the ignition (e-ink shows
"NO ECU / waiting for the car") instead of dying when the deck powers it
before the car is on.
