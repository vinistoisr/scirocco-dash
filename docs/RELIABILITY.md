# Reliability: why drives get lost, and the plan

## The finding

Nine failures so far. **None of them were CAN, decoding, or data problems.**
Every one was an Android app-lifecycle or permission problem on the head
unit. The engine-data path has never lost a drive; the four-app relay it
runs through (bridge app, Termux, RealDash, Android power management) has
lost nine.

So reliability work belongs on *removing links from the relay*, not on
hardening the data path.

## VERIFIED on the car, 2026-08-24

All three ways this system loses its logger, each tested by actually doing
it and then touching nothing:

| Scenario | Result |
|---|---|
| **Cold reboot** | watchdog up 22:20:12, daemons started, bridge started by the watchdog 22:20:54, RealDash connected, 15.8 Hz, reconnects 0 |
| **Bridge killed mid-session** (x2) | port 2323 down at t+3 s, LISTENING again by t+60 s, tee back to 15.8 Hz, no new crash |
| **Sleep -> resume** (5 min asleep) | Termux SURVIVED (watchdog pid 5068 and tee pid 5112 show unbroken elapsed time); the bridge went down and the watchdog restarted it at 22:25:04; RealDash reconnected untouched |

The sleep result is the important one: it proves the wake-lock fix and the
poke fix are both load-bearing, and it means **sleep is now preferable to
power-off** -- it recovers automatically and skips the slow boot.

## Fixed 2026-08-24 (commit 0ce26f0, deployed and verified)

* Android 13's cached-app freezer suspending the whole `com.termux` uid.
  `termux-wake-lock` is what makes Termux a FOREGROUND service
  (`oom_score_adj` 945 -> 50); the watchdog re-asserts it every pass.
* GPS `termux-api` helper leak -- process-group kill on timeout, plus a
  Termux:API restart after 5 silent rounds.
* Sessions orphaned on SIGTERM -- clean unwind, plus `upload.py` adopting
  already-orphaned sessions (guarded by mtime AND `/proc` fds). Recovered
  all 11 stranded drives from 08-23.
* Unsynced RTC naming drives in January -- detected via monotonic drift at
  close and renamed.
* GPS health in the tee status line.
* `tools/deploy-deck.sh`: snapshot, validate, refuse `config.py`, checksum,
  then verify the tee is actually producing frames and roll back if not.

## Still open

| # | Item | Why it matters |
|---|---|---|
| 2 | Set the deck to **off**, not **sleep** | Sleep does not re-enumerate USB, so no attach event fires |
| 3 | `health.json` + dashboard tile + a "seconds since last frame" gauge | Every failure so far was found hours late |
| 1a | Screenshot the bridge app's settings for an autostart option | Never inspected; if the checkbox exists the START tap is over |
| 1b | `android-tools` in Termux, adb to `127.0.0.1:5555` | Injecting input needs a privilege Termux lacks; this grants it |
| 4 | Suppress the 0xC90 flood, rotate `tee.log`, fold in `canbox.py` | 6,420 empty frames/drive; 3.2 MB of log noise |
| 1c | Drop the bridge app via `termux-usb` | Removes an entire app from the chain; needs a spike |
| 5 | SD logging on the board | REVISED DOWN -- see below. Not now. |

Recommended order: **2 now**, then **3**, then **1a**; **1b/4** after that.

## Tier 5 (SD on the board) was revised DOWN on 2026-08-24

The first draft made this the headline fix. Two objections defeated it:

* **It costs acquisition rate.** CircuitPython has no threads, so an SD write
  blocks the one loop that matters. The budget at 17 Hz is ~58 ms; consumer
  SD cards stall for tens to hundreds of ms during wear-levelling. This ECU
  already tears down the TP 2.0 channel when the board is slow -- that is how
  the T3 = 1 ms experiment failed.
* **The gain is smaller than claimed.** The board is powered by the DECK'S
  USB. If the deck loses power the board dies with it, so SD protects against
  no power-loss case at all. It covers Android SOFTWARE failure only -- which
  is what was fixed on 2026-08-24.

What remains: if the bridge app's START is never pressed, the board streams
into a void and the drive is lost. Real, but one failure mode against a
permanent latency risk.

**If it is ever built, build it as a FALLBACK, not a second log.** The tee
sends a heartbeat down the channel that already exists; the board writes to
SD only when no heartbeat has arrived for ~10 s. Healthy drives do zero SD
I/O. A broken deck still gets a complete recording.

## The START tap: FIXED 2026-08-24

**Two lines in watchdog.sh. No new app, no root, no board change.**

Autostart exists, is set to Enabled, and works. Its trigger is
USB_DEVICE_ATTACHED, which Android does not deliver for a device already
attached at boot or resume -- and the Feather is powered by the deck's own
USB, so it never disconnects. That much was right.

What was NOT right: the watchdog's recovery poke was making it worse.

    am start -n "$APP/.MainActivity"      # no -a

MainActivity effectively does `switch (intent.getAction())` over
BOOT_COMPLETED, USB_DEVICE_ATTACHED and its own internal action
`need_to_start`. With no `-a`, getAction() is null, so the poke either
landed in onCreate (which never reads the intent, so the server did not
start) or -- if the app was already running -- reached onNewIntent and threw
`NullPointerException` on `switch(null)`, **killing the app**. The deck's own
crash log had it twice:

    FATAL EXCEPTION: main
    java.lang.NullPointerException: ... String.hashCode() on a null object
      at com.clusterrr.usbserialtelnetserver.MainActivity.onNewIntent

The fix is to pass the app's own action, twice -- the first call
materialises the singleTop activity (its onCreate ignores the intent), the
second is delivered to onNewIntent where the switch actually runs. `start()`
is idempotent (the app checks `isStarted()`), so an extra poke is harmless:

    am start -n "$APP/.MainActivity" -a need_to_start
    sleep 2
    am start -n "$APP/.MainActivity" -a need_to_start

Also dropped the `|| monkey` fallback: it could never execute, because the
watchdog exports Termux's LD_LIBRARY_PATH and that poisons `app_process`
("cannot locate symbol Xzs_Construct"). `am` survives only because on
Android 13 it is `cmd activity`. The poke now runs in a subshell with
LD_LIBRARY_PATH unset, and hands the screen back to RealDash afterwards.

**Verified end to end, unattended:**

    force-stop the bridge app, then touch nothing
    t+3s    port 2323: down
    t+60s   port 2323: LISTENING     <- watchdog recovered it alone
    t+110s  port 2323: LISTENING
    tee back to 15.8 Hz, RealDash in front, no new NPE

This also covers sleep/resume, which failed for the same reason, so the deck
can stay on **sleep** rather than full power-off.

### Rejected on the way here

* **A different app** (UsbTerminal etc.) -- unnecessary; also UsbTerminal is
  a terminal UI with no way to hand the port to another process.
* **termux-usb / writing our own app** -- real options, ~½ day to 2 days,
  but they solve a problem that turned out to be two lines. Keep as the
  fallback if the app is ever updated and the internal action string changes
  (pin the APK).
* **`microcontroller.reset()` on the board** -- the earlier plan. It was
  built, deployed in detection-only mode, and REVERTED. Its detector never
  fired: with the bridge app force-stopped for 150 s, the board's writes
  still drained completely, so "short write means nobody is reading" is
  false on this hardware. Staging it behind a flag is what kept that from
  being a live reset loop. Even with a working detector it would have been
  the wrong fix -- an attach that lands in onCreate is ignored.

## On rooting

Not yet, and probably never needed. Root would buy input injection, a
direct USB-permission write, a device-wide freezer disable, and the tee as
a system service -- but every one of those is reachable without root via
1b, which is reversible and cannot brick anything.

The decisive argument: **root does not remove Cause A.** A rooted head unit
is still a head unit that must be awake and healthy for a drive to be
recorded -- and it is a UIS7870 ROM in the dash of a daily driver, where a
bad boot image means the radio is a brick with no obvious recovery.

## Adversarial review of the 2026-08-24 fixes

A 38-agent review of the same evening's commits raised **30 findings**; 23
survived an adversarial pass whose verifiers were told to default to
"refuted". All 23 are fixed. The ones that mattered:

**Critical**

* `adopt_orphans` could adopt a session while `Session.close()` was
  finalising it. `close()` releases every file descriptor BEFORE it gzips
  and writes meta.json, so mid-close a live session has no open fds -- and
  after the 600 s freeze its files were already stale enough to clear the
  age guard too. Both guards failed on exactly the sequence that happened.
  Fixed with a `.active` marker holding the tee's pid: a LIVE tee blocks
  adoption, a dead pid does not. Verified on the car.
* `deploy-deck.sh` rollback treated a missing snapshot as "this file was
  new" and DELETED it. A flaky pull would have had rollback remove the
  deck's working `tee.py` instead of restoring it. Absence is now proven
  separately, and restores are checksum-verified.

**Major**

* The GPS provider list was walked from the top every round, so a hanging
  `gps` provider re-paid the full 15 s timeout every time -- with
  GPS_MAX_AGE_S at 10 s, rows logged blank while `network` answered fine.
  Order is adaptive now: winner promoted, timeout demoted.
* The clock-jump fix renamed the directory but left every row stamped from
  the bogus clock -- a drive filed under August whose rows claimed January.
  Rows are corrected during compression, and only those written BEFORE the
  resync: NTP lands mid-session, so a uniform offset would move the error
  onto the good half. Tested in both directions.
* The rename ignored `uploaded/`, so a corrected name could collide with an
  already-uploaded session id and overwrite it in R2.
* The deploy health gate had no since-the-restart boundary: it could roll
  back on a traceback predating the deploy, or pass on the status line of
  the process it had just killed. Both logs are marked before the restart
  now, and upload.py is checked rather than merely being alive.
* `upload.py --dry-run` really gzipped, unlinked and wrote meta.json.
* `gzip_in_place` unlinked the original without fsync, on a deck whose
  power is cut by the ignition.
* A checksum failure on the last file left earlier files already live. The
  push is staged then committed, and the `mv` is verified.
* Adopted sessions took `t_start` from mtimes, which all track the session
  END, so a 50-minute drive looked seconds long. Read from the first row.

**Two claims did NOT survive, both reasoned from repo files not hardware:**

* "The board runs `gauge_main_safe.py`, so command-drain fixes are not on
  the car." FALSE -- the live snapshot's `code.py` is byte-identical to
  `board/gauge_main.py` (md5 `2c3755cf...`). The repo's `board/code.py` is a
  STALE leftover that caused the confusion; delete it.
* "The e-ink refresh blocks the loop ~15 s every 60 s." FALSE in the running
  build -- `Screen.show()` returns early unless `force` is set, and the
  periodic call site does not force. Measured across the whole 54-minute
  drive `2026-08-24_1906`: median row spacing 1.04 s, max 1.56 s, zero gaps
  over 3 s.

**Trap for next time:** the workflow's own `confirmed` filter returned an
empty list while 23 verdicts in its journal said `real: true`. Read
`journal.jsonl` before believing a clean bill of health.

## Established by review, and useful later

* The tee forwards ANY 17-byte tag+checksum-valid frame to the board
  verbatim -- there is no frame-id filter. New downstream frame IDs need no
  tee change, only a board-side dispatch branch beside `realdash.FRAME_CMD`.
* Inbound payload is exactly 8 bytes (four uint16). GPS as two int32 scaled
  1e-7 deg fits one frame at ~11 mm; a uint32 Unix epoch fits in half.
* `deck/sendcmd.py` already proves the deck can originate its own frames.
* The deck's injected CAN-box frames (steering, turn signals) NEVER reach
  the board today.
* Heading/bearing is never captured at all: the GPS poller reads only
  latitude, longitude, altitude, speed and accuracy.
* `realdash.CommandReader.poll` consumes 17 bytes BEFORE validating the
  checksum, where the tee's parser advances one byte and counts a rejection.
  Masked today because inbound traffic is a single mostly-zero frame id;
  fix it before adding any second inbound frame type.

## Constraints checked, not assumed

* The e-ink FeatherWing already has a microSD slot, CS on `D5`, same SPI bus;
  `board/scanner_main.py` deasserts D5 (SD) and D6 (SRAM) at startup.
* The wing brings no RTC, so the board cannot name a file by wall-clock time
  alone -- the deck would have to send time down.
* Board internal flash is NOT an option: 1.6 MB free, one drive's `raw.bin`
  was 3.9 MB, and it is mounted host-writable which makes it read-only to
  the board's own code.
* Deck is Android 13 (SDK 33), unrooted, static DHCP lease.
