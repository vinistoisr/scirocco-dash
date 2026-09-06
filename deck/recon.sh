#!/bin/sh
# recon.sh -- Dudu7 head unit recon, phase 1 of docs/PLAN-deck.md.
#
# Run ON the deck, either way:
#   adb shell sh /data/local/tmp/scirocco-recon.sh   (pushed by tools/deck-recon.ps1)
#   sh recon.sh                                      (inside Termux, later)
#
# Collects the unknowns worth probing on a new head unit:
# platform identity, root/su, SELinux, cdc_acm ttys, CIRCUITPY automount,
# phantom-process-killer flags, adb-over-tcp props, WiFi/SSID visibility,
# and the sleep/power model.  Pure POSIX sh (mksh/toybox on Android); every
# probe is individually guarded so one missing command or permission denial
# never kills the run.  Expect different failures from the adb-shell vs
# Termux contexts (dumpsys works for shell, termux-* only in Termux) --
# both runs are informative, adb shell first.
#
# Output: labeled sections to stdout, then machine-parseable VERDICT lines
# at the end (tools/deck-recon.ps1 greps those for its summary).

sec() {
    echo ""
    echo "===================================================================="
    echo "== $1"
    echo "===================================================================="
}

p() {
    # p <label> <command string> -- one guarded probe; failure is data too.
    echo ""
    echo "--- $1"
    sh -c "$2" 2>&1
    if [ $? -ne 0 ]; then
        echo "    (probe failed or unavailable)"
    fi
}

echo "scirocco deck recon -- $(date) -- uid $(id -u 2>/dev/null) ($(id -un 2>/dev/null))"

# -------------------------------------------------------------------------
sec "identity (confirm UIS7862 vs UIS7870, Android 12 vs 13, real not faked)"
p "date / uptime"           'date; uptime'
p "model"                   'getprop ro.product.model'
p "device"                  'getprop ro.product.device'
p "manufacturer / brand"    'getprop ro.product.manufacturer; getprop ro.product.brand'
p "android release / sdk"   'echo "release=$(getprop ro.build.version.release) sdk=$(getprop ro.build.version.sdk)"'
p "platform"                'getprop ro.board.platform'
p "build id / fingerprint"  'getprop ro.build.display.id; getprop ro.build.fingerprint'
p "fyt / unisoc / dudu props" 'getprop | grep -i -e fyt -e sprd -e unisoc -e dudu -e mekede | head -n 40'
p "kernel"                  'cat /proc/version'

# -------------------------------------------------------------------------
sec "root / su / SELinux (decides the whole USB-ownership design)"
p "which su" 'command -v su || echo "su: not on PATH"'
if command -v su >/dev/null 2>&1; then
    if command -v timeout >/dev/null 2>&1; then
        p "su -c id (5s timeout -- WATCH THE SCREEN for a Magisk prompt)" \
          'timeout 5 su -c id </dev/null'
    else
        echo ""
        echo "--- su -c id"
        echo "    (skipped: no timeout binary; a Magisk consent prompt would hang"
        echo "     this script -- run \"su -c id\" manually with the screen in view)"
    fi
fi
p "getenforce (permissive would moot the SELinux question)" 'getenforce'
p "magisk / kernelsu packages" \
  'pm list packages 2>/dev/null | grep -i -e magisk -e kernelsu || echo "none visible (or pm unavailable here)"'

# -------------------------------------------------------------------------
sec "USB serial -- Feather M4 CAN (needs the board plugged into the deck)"
p "/dev/ttyACM*" 'ls -l /dev/ttyACM* 2>/dev/null || echo "no /dev/ttyACM nodes"'
p "/dev/ttyUSB*" 'ls -l /dev/ttyUSB* 2>/dev/null || echo "no /dev/ttyUSB nodes"'
p "kernel acm driver registered (/proc/tty/drivers)" \
  'grep -i acm /proc/tty/drivers || echo "no acm line (driver missing, or file unreadable)"'
p "usb device tree" 'ls /sys/bus/usb/devices 2>/dev/null'
p "adafruit vid 239a in sysfs" \
  'grep -l -i 239a /sys/bus/usb/devices/*/idVendor 2>/dev/null || echo "no Adafruit VID visible"'
p "dmesg cdc/acm lines (usually root-only)" \
  'dmesg 2>/dev/null | grep -i -e cdc -e ttyACM | tail -n 20 || echo "nothing (dmesg likely needs root)"'

# -------------------------------------------------------------------------
sec "storage -- CIRCUITPY automount (OTA firmware path)"
p "/storage"      'ls -l /storage 2>/dev/null'
p "/mnt/media_rw" 'ls -l /mnt/media_rw 2>/dev/null || echo "empty or unreadable (unreadable from Termux is normal; retry via adb shell)"'
p "vfat / media_rw mounts" \
  'mount 2>/dev/null | grep -i -e vfat -e media_rw || echo "no vfat/media_rw mounts visible"'
p "vold volumes" 'sm list-volumes all 2>/dev/null || echo "sm unavailable here"'
echo ""
echo "--- CIRCUITPY hunt (boot_out.txt is CircuitPython's marker file)"
FOUND_CIRCUITPY=""
for d in /mnt/media_rw/*/ /storage/*/; do
    [ -f "${d}boot_out.txt" ] || continue
    FOUND_CIRCUITPY="$d"
    echo "    CIRCUITPY mounted at $d"
    sed 's/^/    /' "${d}boot_out.txt" 2>/dev/null
done
[ -n "$FOUND_CIRCUITPY" ] || echo "    no boot_out.txt under /mnt/media_rw or /storage"

# -------------------------------------------------------------------------
sec "phantom process killer (Android 12+; must end up disabled)"
p "settings_enable_monitor_phantom_procs (want: false)" \
  'settings get global settings_enable_monitor_phantom_procs'
p "max_phantom_processes (want: 2147483647)" \
  'device_config get activity_manager max_phantom_processes'
p "device_config sync mode (want: persistent, else flag sync reverts it)" \
  'device_config get_sync_disabled_for_tests 2>/dev/null || echo "subcommand unavailable on this build"'
p "all activity_manager flags mentioning phantom" \
  'device_config list activity_manager 2>/dev/null | grep -i phantom || echo "none listed"'

# -------------------------------------------------------------------------
sec "adb over tcp (network-only on FYT units)"
p "service.adb.tcp.port" 'getprop service.adb.tcp.port | grep . || echo "(empty)"'
p "persist.adb.tcp.port" 'getprop persist.adb.tcp.port | grep . || echo "(empty)"'
p "adbd state"           'getprop init.svc.adbd'
p "all adb-ish props"    'getprop | grep -i adb'

# -------------------------------------------------------------------------
sec "wifi / SSID visibility (upload gate depends on this)"
p "wlan0 address" 'ip addr show wlan0 2>/dev/null || ifconfig wlan0 2>/dev/null'
p "wifi state (dumpsys, adb-shell only)" \
  'dumpsys wifi 2>/dev/null | grep -i -m 8 -e "mWifiInfo" -e "^Wi-Fi is" -e "SSID:" || echo "dumpsys denied (normal inside Termux)"'
p "termux-wifi-connectioninfo (Termux only; SSID needs Location granted + location ON)" \
  'termux-wifi-connectioninfo'
p "location mode (0 = off -> SSID reads <unknown ssid>)" \
  'settings get secure location_mode'

# -------------------------------------------------------------------------
sec "sleep / power model (deep sleep vs power-off, driveway window)"
p "stay_on_while_plugged_in" 'settings get global stay_on_while_plugged_in'
p "screen_off_timeout"     'settings get system screen_off_timeout'
p "wakefulness"            'dumpsys power 2>/dev/null | grep -i -e mWakefulness -e "Display Power" | head -n 10 || echo "dumpsys denied"'
p "wake locks held"        'dumpsys power 2>/dev/null | grep -i -A 5 "Wake Locks:" | head -n 15 || echo "dumpsys denied"'
p "battery service"        'dumpsys battery 2>/dev/null | head -n 15 || echo "dumpsys denied"'
p "deviceidle / doze"      'dumpsys deviceidle 2>/dev/null | head -n 12 || echo "dumpsys denied"'

# -------------------------------------------------------------------------
sec "installed packages of interest"
p "termux / realdash / fdroid / magisk" \
  'pm list packages 2>/dev/null | grep -i -e termux -e realdash -e fdroid -e magisk || echo "pm unavailable or none found"'

# -------------------------------------------------------------------------
sec "VERDICTS (machine-parseable; tools/deck-recon.ps1 reads these)"
if command -v su >/dev/null 2>&1; then
    echo "VERDICT su=yes"
else
    echo "VERDICT su=no"
fi
if ls /dev/ttyACM* >/dev/null 2>&1; then
    echo "VERDICT ttyacm=yes ($(ls /dev/ttyACM* 2>/dev/null | tr '\n' ' '))"
else
    echo "VERDICT ttyacm=no"
fi
CV="no"
for d in /mnt/media_rw/*/ /storage/*/; do
    [ -f "${d}boot_out.txt" ] && CV="yes ($d)"
done
echo "VERDICT circuitpy=$CV"
PM=$(settings get global settings_enable_monitor_phantom_procs 2>/dev/null)
[ -n "$PM" ] || PM="unavailable"
echo "VERDICT phantom_monitor=$PM"
MP=$(device_config get activity_manager max_phantom_processes 2>/dev/null)
[ -n "$MP" ] || MP="unavailable"
echo "VERDICT max_phantom_processes=$MP"
SE=$(getenforce 2>/dev/null)
[ -n "$SE" ] || SE="unavailable"
echo "VERDICT selinux=$SE"
REL=$(getprop ro.build.version.release 2>/dev/null)
echo "VERDICT android_release=${REL:-unavailable}"
PLAT=$(getprop ro.board.platform 2>/dev/null)
echo "VERDICT platform=${PLAT:-unavailable}"

echo ""
echo "recon done."
