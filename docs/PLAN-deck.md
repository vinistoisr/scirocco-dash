# Plan: deck tee daemon, burst logging, cloud upload, GPS, shift light

2026-08-22. Research done (5-agent sweep; the raw research dump is not included in this repo). Decisions taken (see the Decisions section):
recon-first on root, all-Cloudflare cloud, home-WiFi uploads, burst as
specced.

## Goal

Move the serial link's ownership from RealDash to a small daemon on the head
unit, so every drive is logged without anyone thinking about it, and the
logs appear on a cloud dashboard by the time the owner walks from the driveway
to the front door.

## Architecture

    Feather M4 CAN (car OBD port, TP2.0 @ ~14 Hz)
      |  USB CDC composite: [ttyACM0 console: text/debug]
      |                     [ttyACM1 data: binary frames <->]
      v
    Dudu7 head unit (DuduAuto firmware, FYT/Unisoc family, Android 12/13)
      scirocco-tee (Python daemon, Termux, run via su)
        - owns the data CDC port (RealDash no longer touches USB)
        - TCP server on 127.0.0.1:35000; RealDash connects as client
          (RealDash CAN -> WiFi/LAN; RealDash is ALWAYS the client; no UDP)
        - must start streaming within ~2 s of accept or RealDash cycles
        - tolerates/discards RealDash 0x67 config frames; forwards 17-byte
          set-value command frames to the board verbatim
        - stamps frames with deck wall clock + GPS (termux-location ~1 Hz)
        - per-drive session files, 1 Hz baseline + burst segments
      uploader (same daemon)
        - on home SSID: gzip + push, retry queue, partial wake lock to hold
          the ~20 min driveway window
      v
    Cloudflare R2 (raw archive) + Worker/uPlot dashboard

    Laptop (this repo) --ADB over WiFi (network-only on FYT units)--> deck
    OTA firmware: deck automounts CIRCUITPY; root shell writes
    /mnt/media_rw/XXXX-XXXX/code.py -> board auto-reloads. Parked only,
    sync after copy (FAT corruption risk on ignition cut).

## RECON RESULT 2026-08-22/23: NO ROOT. Architecture revised.

The deck is a DUDU7 (UIS7870, Android 13, DuduOS 3.7, firmware
DUDU7870_2602101746_2601261147). Recon facts that change the plan:

- `su` = NO. Unrooted, and the owner has DECIDED NOT TO ROOT (2026-08-23):
  the deck is the car's stereo, backup camera and CAN integration, and a
  bricked Unisoc unit means SPD recovery on a daily driver. Correct call --
  root is NOT required, see the revised path below.
- SELinux is Permissive (so the old HANDOFF claim that SELinux blocks tty
  access is wrong -- plain Unix perms do: /dev/ttyACM1 is crw------- root
  root). Same conclusion, different reason.
- Network ADB on 5555 works with NO PIN and no gadget mode. Deck IP
  <deck-ip> on SSID "<home-ssid>". This is also a LAN security wart: anyone on
  the WiFi has a shell on the dash.
- Dev-options PIN is NOT 3368 on DuduOS 3.7 (3368/8888/123456/7890+hour all
  fail; bootstrap.md is wrong). Not needed:
  `adb shell am start -a android.settings.APPLICATION_DEVELOPMENT_SETTINGS`.
- CIRCUITPY automounts at /storage/usbdisk/ and IS WRITABLE by the adb
  shell user -> firmware OTA over WiFi works, but only in DEV MODE.
- adb shell CAN write global settings (verified reversibly), so the Android
  12/13 phantom-process killer can be disabled WITHOUT root.
- Internal storage has 99 GB free. CIRCUITPY has 1.7 MB free, which rules
  out board-side flash logging entirely (~10 MB/hour at full rate).
- RealDash is com.napko.RealDash v2.6.8, running, and holds the CDC
  interface (dmesg `cdc_acm probe ... error -16` = EBUSY, as designed).

### Revised no-root architecture (replaces "run tee.py under su")

The owner also decided (2026-08-22) NOT to use the CAR MODE jumper: the board
should never own its filesystem, so OTA always works and logging happens on
the deck. With no root, the deck-side chain becomes:

    Feather --USB--> [bridge app: usb-serial-for-android, owns the CDC
                      interface, persistent USB grant via device_filter.xml,
                      TCP server]
                        |  local TCP
                        v
                     [Termux: deck/tee.py, TCP source mode -- already
                      implemented as --sim] -> logs + serves :35000
                        |
                     RealDash (RealDash CAN -> WiFi/LAN -> 127.0.0.1:35000)

Why this works without root: an ordinary app may claim a USB device through
the Android USB Host API (RealDash does exactly this today), Termux needs
only TCP, and the phantom killer is disableable over adb. tee.py needs no
new transport -- its TCP source mode was written for the simulator and the
bridge app plays the same role. Two-way commands need the bridge to be
bidirectional; verify before committing to a specific app.

Costs/risks: two extra apps in the chain; the bridge must re-acquire USB on
cold boot; RealDash must be switched from USB to network. Fallback if the
bridge apps disappoint: write one purpose-built foreground-service app
(usb-serial + TCP + log + upload), which also sidesteps Termux entirely --
but that needs a JDK + Android SDK, neither installed on the laptop.

### Interim, zero-build path (in use first)

RealDash's own datalogging writes CSV to /sdcard, `adb pull` retrieves it
over WiFi with no root, and the laptop converts + uploads to R2. That
delivers real drive data on the existing dashboard with NO new software on
the deck at all. Limits: RealDash logs computed channels (not raw frames) at
one global rate, so burst becomes a post-processing step on the laptop --
acceptable, since 99 GB of internal storage makes full-rate-always cheap.

## Research verdicts that drive the design

1. RealDash network mode fits exactly: TCP client to a user-entered
   IP:port, same 0x44332211 frames, same XML file. Convention port 35000.
   Loopback 127.0.0.1 between Termux and a normal app is allowed on
   Android.
2. Non-root USB is not viable unattended: the Android USB permission
   dialog is per-attach-session (cleared on unplug and reboot), and
   Termux:API cannot hold a persistent grant, so a human would tap a
   dialog every ignition cycle. Root (Magisk) makes the daemon trivial:
   su + pyserial on /dev/ttyACM1, no SELinux fight (Magisk su domain is
   exempt). Fallback if root is refused/impossible: fork maks/UartBridge
   (MIT, usb-serial-for-android) as the USB owner with the Termux daemon
   behind it over local TCP.
3. Deck platform (FYT family): deep sleep on ignition off -- the daemon
   process SURVIVES normal off/on cycles; cold boots still happen (3-day
   timeout, forced power-off, 20-min MCU force-shutdown while
   wake-locked), so autostart + continuous flush are still required.
   Factory/dev password 3368 (Settings -> tap System 4x). ADB is
   network-only. Termux must come from F-Droid, not Play. Android 12/13
   phantom process killer WILL kill Termux children until disabled
   (settings + device_config flags; root makes the fix persistent).
4. RealDash's own datalog/cloud (paid) cannot log raw frames in the
   background, has one global log rate (no burst), and syncs on its own
   schedule -- the custom daemon is justified.
5. Nothing off-the-shelf does tee+log+upload; bridge apps exist but none
   log. Build the daemon; do not build a USB driver layer unless forced
   (that is what the root decision avoids).

## Burst logging spec (CONFIRMED 2026-08-22)

Acquisition already runs flat out (~14 Hz); burst is about what gets
RECORDED, plus an optional firmware assist later.

- Baseline: one full decoded row per second.
- Ring buffer: last 5 s of full-rate rows kept at all times.
- Trigger ON when any of:  rpm >= 3000, load >= 80 %, boost >= +0.30 bar.
- Trigger OFF when all of: rpm < 2500, load < 60 %, boost < +0.15 bar,
  sustained 3 s.
- On trigger: flush the 5 s pre-roll, then record every frame set until
  OFF. Segment ids tag each pull.
- WOT proxy is load (full rate), not pedal (startup snapshot only).
- Phase 5 firmware assist: same thresholds computed on-board; suppress the
  slow-block rotation and alternate blocks 115/020/005 -> boost/rpm/load
  ~14 Hz + knock ~7 Hz + road speed ~5 Hz during pulls (speed in the fast
  set gives acceleration metrics better than 1 Hz GPS ever could). Command
  frame to force it for testing.

## Session files

    ~/drive-logs/2026-08-22_0731/
      raw.bin        every byte both directions + timestamp records
      drive.csv      1 Hz baseline rows (today's 35 columns + lat/lon/alt/
                     gps_speed from deck GPS)
      bursts.csv     full-rate rows, segment-tagged
      meta.json      start/end, VIN, DTCs at start, ident, counters,
                     upload state

Decoder is generated from scirocco_realdash.xml -- one schema for board,
RealDash and logger. Flush continuously (a cold boot mid-drive must lose at
most ~1 s). gzip on close; move to uploaded/ only after the server
confirms.

## Cloud (DECIDED 2026-08-22: all-Cloudflare)

The owner chose R2 + a custom Worker dashboard over Grafana Cloud: one
vendor, no retention limit, full control.

- Storage: Cloudflare R2, one bucket, rclone S3 backend from Termux with a
  bucket-scoped Object R/W token that never expires, chmod 600 in Termux
  home. Roughly 4 years of drives inside the free 10 GB, zero egress fees.
- Layout: drives/YYYY/MM/2026-08-22_0731/{drive.csv.gz, bursts.csv.gz,
  raw.bin.gz, meta.json}. Uploaded verbatim; meta.json last, as the commit
  marker.
- Dashboard: one Worker with the R2 bucket binding. Static HTML page with
  inlined uPlot (~50 KB); two JSON routes (list drives via R2 list; fetch
  one object). Client-side gunzip via DecompressionStream. PROTECTED by
  Cloudflare Access since 2026-08-22: app "Scirocco Drives", policy allows
  only you@example.com (same convention as every other personal app
  on the account), 730 h session. The tuner gets a presigned R2 URL, or a
  temporary email added to the app policy. Workers free plan (100k req/day) is orders of magnitude
  beyond a one-viewer dashboard. Effort: 1-2 evenings for drive picker +
  multi-channel chart + burst-segment highlighting.
- Rejected: Grafana Cloud (14-day retention, second vendor -- can be added
  later on top of the same R2 data if ever wanted), InfluxDB Cloud
  Serverless (30-day cap, no dashboards in the v3 UI), Drive/Dropbox
  (rclone's shared Google client id retires during 2026).

## GPS

The Ultimate GPS FeatherWing would work (UART TX/RX free, adafruit_gps, no
pin conflicts, Doubler mount) but is third-best: the under-dash sky view is
poor, so it needs an external antenna -- duplicating the deck's existing
one. Preferred: (1) RealDash uses Android GPS natively for speed/track
gauges; (2) the tee merges termux-location fixes (~1 Hz) into every log
row. Note: termux-location and termux-wifi-connectioninfo need the Location
permission granted to Termux:API and system location ON, else the SSID
reads <unknown ssid> -- part of deck setup.

Timestamp alignment (why deck GPS does not hurt a performance logger): the
tee is the single clock -- it stamps every CAN frame on arrival (USB
latency single-digit ms) and logs each GPS fix in the same process,
recording BOTH arrival time and the fix's own satellite UTC (Android
Location.getTime). Engine rows and position share one timebase to within
tens of ms. A board-side GPS wing would not improve this; the board has no
wall clock, so the deck is the time authority either way. Deck GPS's real
limit is rate (~1 Hz fixes): fine for traces/maps, coarse for derived
acceleration -- which is covered instead by promoting ECU wheel speed into
the burst rotation (below). If track-day lap analysis ever matters, the
upgrade is a 10-25 Hz GNSS unit (RaceBox Mini class), not the FeatherWing.
DECIDED 2026-08-22: no GPS hardware.

## NeoPixel shift / warning light

The owner wants a 36" (91 cm) bar across the top of the dash: ~55 px of
60/m WS2812B. That is too much for the deck's USB budget (all-amber flash
at 20 % is ~450 mA before the board's own 150 mA), so the strip gets its
own supply and only data + ground come from the Feather:

- Strip data on D11 (D5/D6/D9/D10 belong to the e-ink wing, A0 is the mode
  jumper), through a 74AHCT125 level shifter (REQUIRED at this length and
  with a separate supply -- ground-offset glitches otherwise) and a 470 ohm
  resistor; 1000 uF across the strip's power input.
- Power: SWITCHED 12 V (add-a-circuit fuse tap, 3 A fuse) -> 12-to-5 V
  buck (3-5 A, e.g. Pololu D24V22F5) -> strip. Not constant battery power:
  55 powered-but-dark pixels idle at ~1 mA each, ~1 Ah/day of leech. Not
  OBD pin 16 for the same reason. All grounds (buck, strip, Feather)
  common.
- Mount in a 1 m aluminum channel with frosted diffuser on 3M VHB (dash
  tops hit 70-80 C; strip adhesive alone fails, and the diffuser controls
  night windshield reflection).
- 55 px = 1.7 ms per refresh, invisible at the 14 Hz loop.

BOM (~CAD 70-90): 1 m WS2812B 60/m IP65 black PCB (Adafruit #1461 or
BTF-Lighting), Pololu D24V22F5 buck, add-a-circuit + 3 A fuse, 74AHCT125
(#1787), 470 ohm + 1000 uF, 1 m aluminum channel + diffuser + VHB, ~2 m
3-core 22 AWG + JST-SM pairs.

Pattern note for the wide bar (tune during integration): center-out fill
for the shift bar (faster peripheral read than left-to-right), knock
flashes at the bar ends, warnings as edge zones; day/night brightness via
a RealDash command frame later.

GPS BOM (deferred -- only if the board ever needs standalone position):
Ultimate GPS FeatherWing #3133, SMA->uFL #851, active antenna #960,
FeatherWing Tripler #3417. Not purchased; deck GPS covers the plan.

Behavior (constants at the top of the file): dark at cruise; green->amber
progressive fill 4500-6200 rpm; all-red flash >= 6300. Overrides, highest
priority first: knock retard past threshold -> fast red; underboost /
overboost vs spec (-300 / +250 mbar under load) -> blue/red alternating;
coolant > 105 C or oil > 130 C -> amber pulse; stored DTC -> one steady red
end pixel. ~60 lines in gauge_main.py, rendered between TP 2.0 polls, no
rate impact.

## Firmware changes (staged in board/, deploy when reachable)

1. Tee v1 needs nothing -- the data port already carries frames+commands.
2. Shift/warning light on D11 (independent of deck work, any time).
3. Phase 5 burst assist.
4. Retire CAR MODE file logging once deck logging is proven.

## Development access

ADB over WiFi from the laptop, driven from this session. Bootstrap once:
Settings -> tap System 4x -> 3368 -> Developer Options -> USB debugging;
adb connect <deck-ip>:5555; sideload F-Droid -> Termux + Termux:API +
Termux:Boot; pkg install openssh python rclone; pip install pyserial;
start sshd; thereafter plain ssh/scp from here. Claude-on-the-deck:
rejected -- the deck runs a dumb daemon, every tool lives here.

## Phases

Status 2026-08-22: phases 3 and 4 are BENCH-PROVEN on the laptop (sim ->
tee -> session files -> R2 -> dashboard at scirocco.example.com; session
2026-08-22_1448 on the live dashboard is a simulator drive through the real
tee). The cloud side of phase 5 is deployed. Remaining before the car:
RealDash-on-Windows TCP check (the owner, 2 min), then deck recon.

1. Deck recon (deck on, laptop on same WiFi): getprop
   ro.build.version.release / ro.board.platform; su present?;
   /dev/ttyACM* present?; CIRCUITPY automounted?; phantom-killer flags;
   sleep setting; wake -> WiFi reassociation time. Full probe checklist:
   the probes in deck/recon.sh (the raw research dump is not included in this repo).
2. Root go/no-go from recon results; if go, Magisk per the XDA FYT 7870
   DUDU OS guide (boot.img patch, both slots; note the OTA-loop caveat).
   Phantom killer disabled persistently; Termux + sshd + Termux:Boot
   installed; wake lock tested.
3. Tee v1: pyserial /dev/ttyACM1 <-> TCP :35000 passthrough + raw.bin;
   RealDash switched to the WiFi/LAN connection; buttons verified over
   TCP. Accept: gauges live, Clear Codes works, raw.bin grows, survives a
   sleep/resume cycle (port reopen loop).
4. Decoder + sessions + burst segmentation + GPS merge.
5. Uploader: home-SSID watch, wake lock through upload, retry queue; R2
   bucket + token + Worker dashboard; firmware burst assist; shift light
   hardware.
6. Hardening: cold-boot autostart proof, disk caps + rotation, upload
   backoff, watchdog, then a month of logs without touching it.

## Decisions (the owner, 2026-08-22)

1. Root: recon first, then decide. Targeted research confirms the Dudu7
   does NOT ship rooted; the documented path is the XDA "FYT 7870 DUDU OS"
   community flow -- Magisk-patch the unit's own boot.img, flash to both
   boot slots via adb/fastboot. Post-root caveats: (a) DuduOS OTA updates
   fail in a loop on rooted units -- firmware updates become manual
   offline installs, which the vendor explicitly supports for rooted
   units; (b) every firmware flash replaces boot and so REMOVES root --
   after each update, re-patch the new firmware's boot.img with Magisk and
   reflash (~10 min, same fastboot flow); (c) keep every installed
   firmware package archived (R2), both for the matching boot.img and as
   the recovery path. Recon must confirm the exact model/firmware and how
   fastboot is reached before anything is flashed.
2. Cloud: R2 + custom Worker dashboard (all-Cloudflare). See Cloud section.
3. Upload trigger: home SSID only.
4. Burst thresholds: as specced.

## Waiting on the owner (as of 2026-08-22 evening)

1. Deck in the driveway + dev options enabled (Settings -> tap System 4x ->
   3368 -> USB debugging) + the deck's WiFi IP -> run tools/deck-recon.ps1.
2. Google OAuth client id + secret (console.cloud.google.com, redirect URI
   https://<your-team>.cloudflareaccess.com/cdn-cgi/access/callback) -> then
   add the google IdP via API and set the app's allowed_idps to
   [google, onetimepin] so the unrelated Microsoft button stays off this
   login page. Until then, One-Time PIN to either allowed email works.
3. Shift-light parts per the BOM.

## Risks

| Risk | Mitigation |
|---|---|
| Rooting bricks the deck | Magisk patch of the unit's own boot image; FYT family has recovery flows and community firmware; recon first, root second |
| USB permission dialog per boot (no-root path) | that is why root is recommended; fallback = UartBridge fork holding the USB grant as default handler |
| Phantom process killer kills the daemon | disable via device_config at boot (root); verify it sticks across reboot during recon |
| Deck cold-boots mid-drive | continuous flush; session recovery on restart; never buffer a drive in RAM |
| USB re-enumerates on sleep/resume | reopen loop keyed on /dev/ttyACM* appearance; sessions survive reopen |
| RealDash cycles the socket if frames pause | tee sends a status frame (0xC82) within 1 s of accept and keeps a heartbeat during board silence |
| Grafana rejects late backfill | upload promptly per drive; the R2 raw archive is the source of truth regardless |
| CIRCUITPY OTA write corrupts FAT | parked-only updates, sync and settle before ignition off; laptop fallback |
