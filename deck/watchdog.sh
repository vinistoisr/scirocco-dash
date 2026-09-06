#!/data/data/com.termux/files/usr/bin/sh
#
# watchdog.sh -- keep the whole chain alive without anyone looking at it.
#
# The chain is:
#   Feather --USB--> bridge app --TCP 2323--> tee.py --TCP 35000--> RealDash
#                                              |
#                                              +--> drive-logs --> upload.py --> R2
#
# Who recovers from what, so this script only has to cover the real gaps:
#
#   RealDash      reconnects on its own; it is a TCP client that retries.
#   tee.py        already reconnects to 2323 forever, and re-accepts RealDash.
#                 It does NOT recover from its own process dying.
#   bridge app    the weak link. It holds the USB claim but its TCP server
#                 stops if the app is backgrounded or trimmed, and nothing
#                 inside Android brings it back. Observed live on 2026-08-23.
#   upload.py     --loop survives its own errors, not its own death.
#
# So: restart dead processes, and if 2323 stops listening while the board is
# still plugged in, poke the bridge app back to the foreground. Everything is
# idempotent -- running two copies of this is harmless.
#
# Deliberately NOT using `pkill python3`: Termux may be running other things,
# and this must never take out an unrelated session.

P=/data/data/com.termux/files/usr
export PREFIX=$P
export HOME=/data/data/com.termux/files/home
export PATH=$P/bin:/system/bin
export LD_LIBRARY_PATH=$P/lib
export TMPDIR=$P/tmp

APP=com.clusterrr.usbserialtelnetserver
DIR=$HOME/scirocco
LOG=$HOME/watchdog.log
PERIOD=20            # seconds between checks
BRIDGE_GRACE=3       # consecutive misses before poking the app

say() { echo "$(date '+%m-%d %H:%M:%S') $*" >> "$LOG"; }

# 2323 is bound to 127.0.0.1, which Android reports in the tcp6 table as a
# v4-mapped address, so check both tables and match the hex port (0x913).
port_up() {
  cat /proc/net/tcp /proc/net/tcp6 2>/dev/null \
    | awk '$4=="0A" {split($2,a,":"); if (a[2]=="0913") found=1} END {exit !found}'
}

board_present() {
  grep -qs "CircuitPython" /sys/bus/usb/devices/1-1.1:1.0/interface 2>/dev/null
}

alive() { pgrep -f "$1" >/dev/null 2>&1; }

# Start the bridge's SERVER, not just its window.
#
# This is the fix for "someone has to tap START before every drive", and the
# old version of this function was actively making it worse. It ran
#
#     am start -n "$APP/.MainActivity"
#
# with no action, and the app's MainActivity does, in effect:
#
#     String action = intent.getAction();
#     switch (action) { case BOOT_COMPLETED: case USB_DEVICE_ATTACHED:
#                       case "need_to_start": ... start(); }
#
# With no -a, getAction() is null. So an actionless poke either lands in
# onCreate (which never reads the intent, so the server does not start) or --
# if the app is already running -- reaches onNewIntent and throws
# NullPointerException on switch(null), KILLING the app. Both outcomes were
# in the deck's own crash log:
#
#   FATAL EXCEPTION: main ... java.lang.NullPointerException:
#     ... String.hashCode() on a null object reference
#     at com.clusterrr.usbserialtelnetserver.MainActivity.onNewIntent
#
# The app's Autostart setting is Enabled and works -- but its trigger is
# USB_DEVICE_ATTACHED, which Android does not deliver for a device that was
# already attached at boot or resume. The Feather is powered by the deck's
# own USB and never disconnects, so that event never fires.
#
# Passing the app's own internal action gives it the third case. Twice,
# because the first call has to materialise the singleTop activity (its
# onCreate ignores the intent) and only the second is delivered to
# onNewIntent, where the switch actually runs. start() is idempotent -- the
# app checks isStarted() first -- so a spurious extra poke is harmless.
#
# Verified on the deck 2026-08-24: force-stop the app, poke twice, and
# 127.0.0.1:2323 is LISTENING with no screen tap and no new crash.
poke_bridge() {
  # Subshell: Android's own tools must not inherit Termux's LD_LIBRARY_PATH.
  # It poisons app_process, which is why the old `monkey` fallback could
  # never have run ("cannot locate symbol Xzs_Construct").
  (
    unset LD_LIBRARY_PATH
    am start -n "$APP/.MainActivity" -a need_to_start >/dev/null 2>&1
    sleep 2
    am start -n "$APP/.MainActivity" -a need_to_start >/dev/null 2>&1
    sleep 2
    # Recovery steals the screen for a moment; give it back to the dashboard.
    am start -n com.napko.RealDash/com.napko.nuts.androidframe.NutsAndroidActivity \
      >/dev/null 2>&1
  )
}

start_tee() {
  say "starting tee.py"
  cd "$DIR" && nohup python3 tee.py --tcp 127.0.0.1:2323 --telnet \
       --bind 127.0.0.1:35000 --logdir "$HOME/drive-logs" \
       >> "$HOME/tee.log" 2>&1 &
}

start_canbox() {
  # canbox.py tails the head unit's CAN box log (steering angle, turn
  # signals) into the tee's inject port. Harmless when the launcher's
  # "Export CANBUS Log" toggle is off -- it just waits for the file.
  say "starting canbox.py"
  cd "$DIR" && nohup python3 canbox.py >> "$HOME/canbox.log" 2>&1 &
}

start_upload() {
  say "starting upload.py"
  cd "$DIR" && nohup python3 upload.py --loop --logdir "$HOME/drive-logs" \
       >> "$HOME/upload.log" 2>&1 &
}

# Termux's wake lock is not really about the CPU here -- it is what makes
# TermuxService run as a FOREGROUND service. Without it Termux is just a
# cached app (oom_score_adj 945) and Android's cached-app freezer suspends
# the whole uid the moment RealDash takes the screen. On 2026-08-24 that
# froze tee.py, canbox.py, upload.py AND this watchdog mid-instruction at
# 20:00:17: RealDash went blank, nothing was logged for ten minutes, and
# the drive could not close or upload until the process group was thawed
# by hand. Re-assert it every pass -- it is idempotent, and it has to
# survive Termux being restarted underneath us.
wake_lock() { termux-wake-lock 2>/dev/null; }

wake_lock
say "watchdog up (pid $$)"

misses=0
while true; do
  wake_lock
  alive "tee\.py"    || start_tee
  alive "canbox\.py" || start_canbox
  alive "upload\.py" || start_upload

  if port_up; then
    misses=0
  elif board_present; then
    misses=$((misses + 1))
    if [ "$misses" -ge "$BRIDGE_GRACE" ]; then
      say "bridge TCP down with board attached -- starting $APP"
      poke_bridge
      misses=0
    fi
  else
    # No board plugged in: ignition off / Feather unpowered. Nothing to do,
    # and poking the app here would just fight a state that is correct.
    misses=0
  fi

  sleep "$PERIOD"
done
