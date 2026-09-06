# Deck bootstrap checklist

> **SUPERSEDED IN PART -- read this box first (2026-08-23).**
> Recon and the owner's decisions have overtaken several steps below:
>
> * **The deck is NOT rooted and will NOT be rooted.** Every "run it under
>   `su`" instruction below is void. `/dev/ttyACM1` is `crw------- root
>   root`, so no unprivileged process can open it: an Android **bridge app**
>   owns the USB device and re-exposes it as a local TCP socket, and
>   `deck/tee.py --tcp HOST:PORT` consumes that instead of a serial device.
> * **The developer-options password is NOT 3368** on DuduOS 3.7 (3368,
>   8888, 123456 and 7890+hour all fail). You do not need it --
>   `development_settings_enabled` is already 1, so open the menu sideways:
>   `adb shell am start -a android.settings.APPLICATION_DEVELOPMENT_SETTINGS`
> * **Network ADB is already on**, port 5555, no PIN, no pairing. Deck was
>   <deck-ip> on SSID `<home-ssid>`.
> * The phantom-process-killer fix does **not** need root: the plain
>   `adb shell settings put global ...` form works (verified reversibly).
> * Firmware deploys work over WiFi today: `tools\deploy-board.ps1`.
>
> Everything else (Termux install, wake locks, autostart, rclone) still
> applies. Fix the steps as you go rather than trusting them.


Turning the Dudu7 head unit into the tee/logging/upload host, per
docs/PLAN-deck.md. Work through this top to bottom; each step ends with a
verification. Steps 1-2 are phase 1 (recon); everything from step 4 on
assumes the root go/no-go decision has been taken (plan phase 2 -- rooting
itself follows the XDA "FYT 7870 DUDU OS" Magisk flow and is deliberately
NOT scripted here; see Decisions #1 in the plan for the OTA-loop and
re-root-after-flash caveats).

Conventions used throughout:

- `<deck-ip>` -- the head unit's WiFi address (Settings -> WiFi, or your
  router's client list).
- Laptop commands are PowerShell from the repo root
  (`<repo>\`).
- Deck code lives at `~/scirocco/` inside Termux; drive logs at
  `~/drive-logs/`.

---

## 0. Before touching anything

- [ ] Car parked, ignition on (deck powered), laptop on the same WiFi.
- [ ] Open Factory Settings (step 1 password) and PHOTOGRAPH every page
      before changing anything -- it holds CANBUS type, amp, camera and
      key mappings with no export function.

## 1. Developer options + network ADB

- [ ] **Already done on this unit.** The DuduOS password gate is NOT
      3368 and did not yield to the usual list. It is also unnecessary:
      `adb shell am start -a android.settings.APPLICATION_DEVELOPMENT_SETTINGS`
      opens Developer Options directly, and ADB on 5555 is already
      listening with no PIN.
- [ ] Developer Options -> enable **USB debugging**. On FYT firmware this
      enables ADB **over the network only** -- the unit's USB ports are
      host-only, so there is no cable route and none is needed.
- [ ] Laptop: `adb connect <deck-ip>:5555` -- expect `connected to ...`.
      An authorization prompt may appear on the deck screen; accept and
      tick "always allow".

Verify: `adb devices` lists `<deck-ip>:5555   device` (not `offline` /
`unauthorized`).

## 2. Recon (phase 1 -- do this before installing anything)

- [ ] `.\tools\deck-recon.ps1 -DeckIp <deck-ip>`
- [ ] Read the verdict summary; the full dump lands in
      `docs\recon-<date>.txt`. This answers: su present? ttyACM nodes?
      CIRCUITPY automounted? phantom-killer flags? Android/platform?
- [ ] Root go/no-go decision per docs/PLAN-deck.md phase 2. If rooting:
      do it now (XDA FYT 7870 DUDU OS guide, Magisk-patch the unit's own
      boot.img, both slots), then re-run the recon and confirm
      `su=yes`.

## 3. F-Droid

Termux MUST come from F-Droid, not the Play Store (the Play build is
deprecated and its plugins are signature-incompatible).

- [ ] On the deck browser: download https://f-droid.org F-Droid.apk.
- [ ] Android will ask to allow "install unknown apps" for the browser /
      file manager -- allow it.
- [ ] Install F-Droid, open it once, let it refresh its index.

## 4. Termux + Termux:API + Termux:Boot (order matters)

All three from F-Droid, same signing key:

- [ ] Install **Termux**, open it once (bootstraps the environment).
- [ ] Install **Termux:API** (the app -- the `pkg` half comes in step 5).
- [ ] Install **Termux:Boot**, then OPEN IT ONCE -- until it has been
      launched a single time, Android never delivers BOOT_COMPLETED to it
      and nothing in `~/.termux/boot/` runs.

## 5. Packages inside Termux

```sh
pkg update
pkg install openssh python rclone termux-api
pip install pyserial
```

Verify: `python --version`, `rclone version`, `sshd --help` all answer,
and `termux-toast hello` pops a toast (proves the API app link works).

## 6. sshd + key login from the laptop

On the deck (Termux):

```sh
passwd            # set a throwaway password, used once below
sshd              # listens on port 8022
```

On the laptop (generate a key first with `ssh-keygen` if you have none):

```powershell
type $env:USERPROFILE\.ssh\id_ed25519.pub |
  ssh -p 8022 <deck-ip> "mkdir -p ~/.ssh && cat >> ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys"
```

(Termux sshd is single-user; whatever username you pass is accepted.)

Verify: `ssh -p 8022 <deck-ip> uname -a` logs in WITHOUT a password.
From here on, ssh/scp replaces the deck's on-screen keyboard entirely.

## 7. Storage permission

- [ ] In Termux: `termux-setup-storage` -> grant the dialog.

Verify: `ls ~/storage/shared` shows the deck's internal storage.

## 8. Location permission for Termux:API (the SSID gate depends on it)

Android treats SSIDs as location data: without this, the uploader's
home-WiFi check reads `<unknown ssid>` and never fires.

- [ ] Android Settings -> Apps -> **Termux:API** -> Permissions ->
      Location -> **Allow all the time**.
- [ ] System Location toggle **ON** (quick settings or Settings ->
      Location). termux-location also feeds GPS rows into the drive log,
      so this stays on permanently.

Verify: `termux-wifi-connectioninfo` prints the real `"ssid"` of the
home network, not `<unknown ssid>`.

## 9. Phantom process killer (Android 12/13 kills Termux children)

One-time, from the laptop:

```powershell
adb -s <deck-ip>:5555 shell settings put global settings_enable_monitor_phantom_procs false
adb -s <deck-ip>:5555 shell device_config set_sync_disabled_for_tests persistent
adb -s <deck-ip>:5555 shell device_config put activity_manager max_phantom_processes 2147483647
```

The `settings put` survives reboot by itself; the `device_config` flag can
be reverted by Google's flag sync, which is why `set_sync_disabled_for_tests
persistent` comes first AND the boot script (step 12) re-applies all three
under su on every boot -- the root-persistent variant.

Verify (now and again after a reboot):

```powershell
adb -s <deck-ip>:5555 shell settings get global settings_enable_monitor_phantom_procs   # false
adb -s <deck-ip>:5555 shell device_config get activity_manager max_phantom_processes    # 2147483647
```

## 10. rclone remote for R2

One bucket, one bucket-scoped token, never-expiring, per the plan's Cloud
section.

On the Cloudflare dashboard (laptop):

- [ ] R2 -> Create bucket -> `scirocco-drives` (default region).
- [ ] R2 -> Manage R2 API Tokens -> Create token: **Object Read & Write**,
      scoped to ONLY that bucket, TTL forever. Note the Access Key ID,
      Secret Access Key, and the account id from the endpoint URL.

On the deck, write `~/.config/rclone/rclone.conf` (template -- replace the
three placeholders; NEVER commit the filled-in file anywhere):

```ini
[r2]
type = s3
provider = Cloudflare
access_key_id = <R2_ACCESS_KEY_ID>
secret_access_key = <R2_SECRET_ACCESS_KEY>
endpoint = https://<CLOUDFLARE_ACCOUNT_ID>.r2.cloudflarestorage.com
acl = private
no_check_bucket = true
```

```sh
chmod 600 ~/.config/rclone/rclone.conf
```

The token can only touch this one bucket and is revocable in one click if
the deck is ever stolen -- that is the whole blast-radius model, so do not
be tempted to reuse an account-wide credential here.

Verify:

```sh
rclone lsd r2:                                        # lists scirocco-drives
echo hello > ~/r2test.txt
rclone copyto ~/r2test.txt r2:scirocco-drives/test/r2test.txt
rclone ls r2:scirocco-drives/test/
rclone deletefile r2:scirocco-drives/test/r2test.txt
rm ~/r2test.txt
```

## 11. Deploy the deck code + config

From the laptop:

```powershell
ssh -p 8022 <deck-ip> "mkdir -p ~/scirocco"
scp -P 8022 deck\*.py <deck-ip>:scirocco/
```

- [ ] Set the real values in `~/scirocco/config.py` on the deck (at
      minimum `HOME_SSID`; the SSID string is a secret-ish local detail
      that stays out of the repo). If config.py does not exist yet,
      upload.py falls back to built-in defaults -- but HOME_SSID has no
      sane default, so the uploader will refuse to run unforced until it
      is set.

Verify: `ssh -p 8022 <deck-ip> "cd scirocco && python upload.py --dry-run --force"`
prints a clean "nothing to upload" pass.

## 12. Termux:Boot script

Create `~/.termux/boot/00-scirocco.sh` on the deck:

```sh
#!/data/data/com.termux/files/usr/bin/sh
# Boot everything the car needs; runs at BOOT_COMPLETED via Termux:Boot.

# Hold the CPU: the deck's MCU grants ~20 min of full power after
# ignition-off only while a wake lock is held (the driveway upload window).
termux-wake-lock

# Phantom-process-killer disable, root-persistent variant (step 9).
su -c 'device_config set_sync_disabled_for_tests persistent'
su -c 'device_config put activity_manager max_phantom_processes 2147483647'
su -c 'settings put global settings_enable_monitor_phantom_procs false'

# Always reachable from the laptop when home.
sshd

# The tee daemon owns /dev/ttyACM1 (root: Android will not hand a tty to
# an untrusted app, and the Magisk su domain is SELinux-exempt). Full
# paths: root's PATH has no Termux bin dir.
su -c "/data/data/com.termux/files/usr/bin/python \
       /data/data/com.termux/files/home/scirocco/tee.py" \
    >> ~/tee.log 2>&1 &

# The uploader loop; gates itself on HOME_SSID, so it can just always run.
python ~/scirocco/upload.py --loop >> ~/upload.log 2>&1 &
```

```sh
chmod +x ~/.termux/boot/00-scirocco.sh
```

> **File-ownership caution (tee <-> uploader integration).** The tee runs
> as root, the uploader as the Termux user. If root-owned session files
> land in `~/drive-logs/`, the uploader cannot rewrite meta.json or move
> the session dir. The tee must chown each session dir to the Termux
> uid/gid when it closes the session (or equivalently umask/chmod them
> group-writable). Do NOT "fix" this by running upload.py under su: the
> termux-api commands (wake lock, SSID) talk to the Termux:API app and
> misbehave as root.

Verify: reboot the deck (long-press power or ignition cycle past deep
sleep), then from the laptop:

```powershell
ssh -p 8022 <deck-ip> "pgrep -f tee.py; pgrep -f upload.py; termux-wake-lock --help > /dev/null && echo api-ok"
```

Both PIDs print; `logcat` (adb) shows no phantom kills.

## 13. End-to-end verification

- [ ] RealDash: Garage -> Connections -> delete the old USB serial
      connection -> add Adapters (CAN/LIN) -> RealDash CAN -> WiFi/LAN ->
      `127.0.0.1` port `35000`, select `scirocco_realdash.xml`. Gauges
      live within ~2 s.
- [ ] Buttons (Clear Codes) still work -- commands travel TCP -> tee ->
      board.
- [ ] Take a short drive: a new `~/drive-logs/<session>/` appears and
      grows; on returning to the driveway, files show up under
      `r2:scirocco-drives/drives/YYYY/MM/<session>/` (meta.json last) and
      the session dir moves to `~/drive-logs/uploaded/`.
- [ ] `adb shell settings get global settings_enable_monitor_phantom_procs`
      still `false` after a full cold boot.
- [ ] Leave it alone for a week; sessions keep appearing in R2 without
      anyone touching the deck.
