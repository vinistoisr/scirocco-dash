#!/data/data/com.termux/files/usr/bin/sh
#
# ~/.termux/boot/boot-scirocco.sh -- started by Termux:Boot on cold boot.
#
# The head unit deep-sleeps on ignition-off rather than rebooting, so this
# usually only fires after a genuine power cycle (the multi-day sleep
# timeout, a forced shutdown, or a battery disconnect). Everything here is
# therefore written to be safe when a previous copy is still alive.
#
# All this does is launch the supervisor: watchdog.sh owns tee.py, upload.py
# and nursing the bridge app, so there is exactly one thing to start here
# and exactly one thing to look for when checking whether the car is logging.

P=/data/data/com.termux/files/usr
export PREFIX=$P
export HOME=/data/data/com.termux/files/home
export PATH=$P/bin:/system/bin
export LD_LIBRARY_PATH=$P/lib
export TMPDIR=$P/tmp

# The wake lock comes first: without it Android may freeze these processes
# as soon as the screen goes off, and the whole point is to keep logging
# while the car is running.
termux-wake-lock 2>/dev/null

pgrep -f "watchdog\.sh" >/dev/null 2>&1 || \
  nohup sh "$HOME/scirocco/watchdog.sh" >/dev/null 2>&1 &

exit 0
