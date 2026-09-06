# Handoff — start here (rewritten 2026-08-24, after the reliability round)

Then read `docs/RELIABILITY.md` (why things broke and what was done about
it), `docs/RUNBOOK.md` (how the system is wired), `docs/MODULES.md` (what
this car's modules actually are) and `docs/CANBOX.md` (the head unit's
comfort-bus CAN box).

## State of the world

The chain is: Feather M4 CAN --USB--> bridge app --TCP 2323--> `tee.py` in
Termux --> RealDash + per-drive CSV --> `upload.py` --> R2 --> dashboard.

**Verified working on the car, 2026-08-24:**

* **Cold boot needs no human.** Deck rebooted with nothing touched
  afterwards: watchdog up at 22:20:12, daemons started, bridge started by
  the watchdog at 22:20:54, RealDash connected, 15.8 Hz, reconnects 0. Then
  the bridge was force-stopped twice more and recovered itself both times
  within 60 s.
* **The START tap is gone.** See RELIABILITY.md — the watchdog's poke was
  crashing the app with a null-action NPE. It now passes the app's own
  `need_to_start` action, twice.
* 15.6–16.0 Hz sustained, GPS fixing continuously.
* Termux wake lock held and self-healing (the tee re-asserts every 60 s), so
  Android 13's cached-app freezer cannot suspend the logger.
* Uploads: 14 drives recovered and uploaded during the session, including 11
  that had been stranded since 08-23 with no meta.json.

**Deck is at a static DHCP reservation.** `tools/deck-env.sh` resolves it
anyway (env, then cache, then default, then a subnet sweep verified by
model string).

* **Sleep → resume needs no human either.** Verified 2026-08-24 by actually
  sleeping the deck for five minutes and waking it. Three things are worth
  knowing from that test:
  * Termux **survived the sleep** — the watchdog (pid 5068) and tee
    (pid 5112) show unbroken elapsed time across it. The wake lock is doing
    its job; nothing was frozen or restarted.
  * The bridge **did** go down on resume, exactly as it does after a boot.
  * The watchdog brought it back on its own: `22:25:04 bridge TCP down with
    board attached -- starting com.clusterrr...`, and RealDash reconnected
    without a touch.

  So the fix is load-bearing here, not incidental — sleep was failing for
  the same reason boot was. **Sleep is now the better choice than
  power-off**: it recovers automatically and skips the slow boot.

## NOT yet verified — check on the next drive

1. **The burst-trigger rpm gate** (`TRIGGER_LOAD_MIN_RPM`). Replayed against
   the 08-23 drive but never seen on a live one.
2. **The GPS reaper and adaptive provider order.** Deployed, but no GPS
   wedge has happened since, so the recovery path is untested in the field.
3. **The deploy rollback path.** The health gate has caught a real broken
   deploy; the rollback itself has never had to run.

## DONE 2026-08-24: the frozen channels

Rail pressure, catalyst temperature and pedal position are now LIVE TP 2.0
channels, identified against EOBD cross-references and validated over a
971 s drive (rpm 0-6560, boost to 1.55 bar gauge). Working:
**`docs/ENGINE-CHANNELS.md`**.

    catalyst_temp       blk 115 fld 5   FREE -- 115 is already read every cycle
    fuel_rail_pressure  blk 106 fld 1   f83 = u16 * 0.01 bar ABSOLUTE (new formula)
    rail specified      blk 106 fld 0
    pedal_position      blk 62  fld 2   identical raw byte to PID 0x49

Drive result: rail 41.8 idle -> 147.8 bar on boost; catalyst 495 -> 756 C;
pedal 14.8 -> 80.1 %.

Root cause was worse than "stale": the EOBD snapshot is taken when the board
boots, which is when the deck powers its USB -- **ignition on, engine not
running** -- so every held value was an engine-off value. Proven by booting
twice at the same warm idle: rail 2.0 vs 39.2, cat 22.4 vs 487.7, run_time
0 vs 2068.

**AFR/lambda as a RATIO is not available on this ECU** -- settled, not
pending. See ENGINE-CHANNELS.md for the full negative result, including that
UDS service 0x22 does not answer at all here.

**But a live O2 signal WAS found**: formula-66 fields in blocks 031/033/036/
037/043/086/098. Block 86 field 5 is the clearest -- it dithers (b 3..41) in
closed loop and pegs at b 45..46 under boost, i.e. open-loop rich. Scale
looks like b*0.02 V. Not yet wired to a channel; that needs a frame slot,
tee schema entry and XML mapping.

## What is still actually wrong

### 1. Fuel economy and fuel level in RealDash

RealDash still thinks the car is "My Supercar", 6000 cc, 8 cylinders, 840 cc
injectors, 60 L tank, 1650 kg (`deck/realdash/scirocco-settings-export.xml`).
Real: 1984 cc, 4 cyl, ~55 L, ~1400 kg. Fix by hand in the RealDash garage.

Fuel level is never sent, and the settings contain
`<map key="170" value="223"/>` redirecting the input to a computed value. A
real level would come from the instrument cluster at TP 2.0 address `0x07`
(NOT 0x17 on this car — see MODULES.md).

### 2. Gear is estimated, not real

The real gear WAS decoded: `0x02` (DSG) block 003 field 1, formula 17, an
ASCII letter. Two independent blockers:

* `AUX_ENABLED = False` in `board/gauge_main.py` — the aux poller hard-reset
  the board during a visit. Prime suspect is `assume_clean=True` in
  `tp20.Channel.connect`; bisect that first.
* RealDash overrides it anyway: `<map key="200" value="25"/>` points the gear
  input at RealDash's own calculated gear. Remove that mapping.

### 3. Knock channels are NOT knock

`knock_cyl_1..4` come from engine group 020 via VAG formula 34, which is
`(b-128)*0.01*a` in **kW, not degrees**. The channels are plausible and
unverified. Compare against VCDS group 020 before tuning on them. Note the
drive shows timing dipping to −8.2° above 1.2 bar, which is what knock
correction looks like.

### 4. AFR -- unavailable as a ratio; an O2 proxy exists

`lambda_commanded` still comes from the boot EOBD snapshot: 0xFFFF (-> 1.999)
at ignition-on, a real 1.000 only if the board happens to boot with the
engine running. Options: leave it; poll PID 0x44 occasionally at the cost of
a TP 2.0 teardown each time (owner has ruled out EOBD in the live path);
wire the wideband sensor to an analog input; or wire the formula-66 O2
voltage above as a rich/lean indicator.

### 5. The two January-filed drives

Two real drives sit in the bucket under `drives/2026/01/` because the deck's
RTC was unsynced when they started. New drives cannot land there any more
(the tee corrects mid-session jumps and the uploader cross-checks the id
against file mtimes), but these two were uploaded before that existed and
would have to be moved in R2 by hand.

## Deploying

    bash tools/deploy-deck.sh deck/tee.py deck/upload.py
    bash tools/deploy-deck.sh --no-restart deck/watchdog.sh
    bash tools/deploy-board.sh board/gauge_main.py         # lands as code.py

`deploy-deck.sh` refuses `config.py` without `--allow-config` (HOME_SSID is
per-device), checksums every transfer, stages then commits, and after
restarting proves the tee is producing frames — rolling back from the
snapshot if not.

**The watchdog is the exception:** pushing `watchdog.sh` does not restart it,
because it is a long-lived shell. It picks up new code on the next deck
reboot, or run `rwd` in a real Termux session (a helper at
`$PREFIX/bin/rwd` that kills and restarts it).

## Traps — hard-won, do not relearn these

* **`engine_load` reads 100% at idle AND engine-off.** It has caused two
  separate bugs. Never gate anything on load alone.
* **`iat` is logged in KELVIN.** 285–308 in the CSV is 12–35 °C. The
  dashboard sniffs and converts — do not "fix" the raw column.
* **Never push `deck/config.py` without checking `HOME_SSID`.** Pushing the
  repo placeholder over the deck's value silently disabled uploads for a
  whole evening.
* **`gauge_main.py` is the ONLY board firmware source.** `board/code.py` was
  a stale duplicate and has been deleted; `deploy-board.ps1` (which pushed
  it) is retired. That stale copy already caused a review to wrongly
  conclude the car was running the safe build.
* **Never start Termux processes through `run-as`** — they inherit the
  `runas_app` SELinux context, cannot read `/sdcard`, and `am start` fails
  `assertPackageMatchesCallingUid`. Use a real Termux session.
* **Do not give Android's tools Termux's `LD_LIBRARY_PATH`.** It poisons
  `app_process`, which is why the watchdog's old `monkey` fallback could
  never run. `poke_bridge` unsets it in a subshell.
* **`am start` without `-a` CRASHES the bridge app** (null action into
  `switch(action)` is an NPE in `onNewIntent`). Always pass
  `-a need_to_start`. If the app is ever updated that internal action string
  could change — **pin the APK**.
* **The bridge app FANS OUT to multiple readers -- nothing needs killing.**
  Measured 2026-08-24: a second telnet client on 2323 got the full 15.6 Hz
  stream while tee.py independently held 15.2-15.8 Hz. A probe capture
  attaches ALONGSIDE the tee, so watchdog, tee and uploader all keep
  running. The old "stop the watchdog and the tee first" advice is more
  destructive than necessary. `tools/enginescan.sh` does it the new way.
* **A probe that replaces code.py must answer the reload command itself.**
  `sendcmd reload` is normally handled by gauge_main.py; without it the only
  way back is rebooting the deck. See `pump()` in enginescan.py.
* **`run-as` CAN start deck-side captures.** The "never start Termux
  processes through run-as" rule is about needing /sdcard or `am start` --
  probecap.py needs neither, and nohup'd under run-as it survived a
  16-minute drive out of WiFi range. That is what makes capturing during a
  drive possible.
* **Windows Python cannot open MSYS `/c/...` paths.** Broke `sha_local` in
  deploy-deck.sh, then deploy-board.sh's validation (good files looked like
  syntax errors when deployed by absolute path), then enginescan.sh. Route
  script paths through `cygpath -w`.
* **jazdw/vag-blocks formula ids are HEX; tp20 stores them DECIMAL.** Their
  `0x31` is our `f49`, not our `f31`. Their `0x25` is our `f37` = binary,
  which is why `f37(00,00)` is everywhere -- it is this ECU's padding.
* **This ECU does NOT speak UDS.** Service 0x22 returns nothing for any DID.
  Advice that MED17 maps measuring blocks to UDS DIDs does not apply here.
* **Probe output must NOT be captured through the tee.** The tee closes
  sessions when it thinks the engine is off; a shifter sweep was lost that
  way. Use `deck/probecap.py`.
* **T3 = 1 ms was tried and reverted.** The MED17.5 accepts the faster gap
  and then degrades the channel. If retried use 3 ms (`0x1E`) and watch it
  live.
* **RealDash ignores `input tap`** — it needs `input motionevent DOWN/UP`
  pairs. The bridge app, by contrast, responds to `input tap` fine.
* **Board short-writes are NOT a "nobody is reading" signal.** Measured: with
  the bridge app force-stopped for 150 s, the board's writes still drained
  completely. Any future link-health logic on the board must use something
  else — a heartbeat sent DOWN from the tee is the obvious candidate.

## Good news, for context

Boost control is tight: median overshoot above target **+0.03 bar**, peak
**1.54 bar** held flat across four pulls. Biggest pull: 3920 → 6960 rpm in
5.4 s, 37 → 117 km/h, MAF peaking 266 g/s. Fuel trims healthy (short ~3.1%,
long 0.0%). Thermals correct, oil lagging coolant as it should.

The engine side of this system has never lost a drive or told a lie. Every
failure has been Android lifecycle — and that is now the part with the most
armour.
