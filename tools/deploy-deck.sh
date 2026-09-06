#!/bin/bash
# deploy-deck.sh -- push daemon files to the head unit's Termux, SNAPSHOT FIRST.
#
# WHY THIS EXISTS
#   The deck half of the system had no deploy tool at all: files went over by
#   hand-assembled base64 one-liners. Two things went wrong that way.
#
#   1. On 2026-08-23 the repo's placeholder HOME_SSID was pushed over the
#      deck's real value. The uploader then correctly refused to upload
#      anything for an entire evening, silently, because it believed it was
#      never on home WiFi. config.py is now REFUSED unless --allow-config is
#      passed explicitly.
#   2. Nothing was ever backed up before being overwritten -- the same
#      mistake tools/deploy-board.sh was written to stop.
#
#   /data/data/com.termux is private, so adb push cannot reach it and
#   run-as cannot read /data/local/tmp either. The transport is base64 over
#   adb shell stdin, with \r stripped on the device (adb's line translation
#   would otherwise corrupt the stream) and a sha256 check afterwards.
#
# USAGE
#   tools/deploy-deck.sh deck/tee.py deck/upload.py     # push, then restart
#   tools/deploy-deck.sh --no-restart deck/canbox.py    # push only
#   tools/deploy-deck.sh --allow-config deck/config.py  # deliberate, checked
set -u
export MSYS_NO_PATHCONV=1
ADB="${ADB:-$(command -v adb || echo adb)}"
REPO="$(cd "$(dirname "$0")/.." && pwd)"
. "$REPO/tools/deck-env.sh"
STAMP="$(date +%Y%m%d-%H%M%S)"
SNAP="$REPO/backups/deck/$STAMP"
HOME_T=/data/data/com.termux/files/home
DIR="$HOME_T/scirocco"

RESTART=1
ALLOW_CONFIG=0
FILES=()
for a in "$@"; do
  case "$a" in
    --no-restart)   RESTART=0 ;;
    --allow-config) ALLOW_CONFIG=1 ;;
    -*) echo "unknown flag: $a" >&2; exit 2 ;;
    *)  FILES+=("$a") ;;
  esac
done
[ "${#FILES[@]}" -ge 1 ] || {
  echo "usage: $0 [--no-restart] [--allow-config] <file> [file...]" >&2; exit 2; }

DEV="$(resolve_dev)" || exit 1
echo "deck: $DEV"

# --- 1. validate BEFORE anything is touched on the device ------------------
for src in "${FILES[@]}"; do
  [ -f "$src" ] || { echo "ERROR: $src not found" >&2; exit 1; }
  base="$(basename "$src")"
  if [ "$base" = "config.py" ] && [ "$ALLOW_CONFIG" -eq 0 ]; then
    echo "REFUSED: config.py carries HOME_SSID, which is per-device." >&2
    echo "         Check the deck's current value first:" >&2
    echo "           adb -s $DEV shell \"run-as com.termux grep HOME_SSID $DIR/config.py\"" >&2
    echo "         then re-run with --allow-config." >&2
    exit 1
  fi
  case "$src" in
    *.py) python -c "import ast,sys;ast.parse(open(sys.argv[1],encoding='utf-8').read())" "$src" \
            || { echo "ERROR: $src is not valid Python - not deploying" >&2; exit 1; } ;;
    *.sh) sh -n "$src" || { echo "ERROR: $src is not valid sh - not deploying" >&2; exit 1; } ;;
  esac
done
echo "validated ${#FILES[@]} file(s)"

# sha256sum, not python: this runs under Git Bash, where an MSYS path like
# /c/Users/... is meaningless to Windows Python. The absolute snapshot paths
# hit exactly that and made the snapshot check fail on a file that was fine.
sha_local() {
  sha256sum < "$1" | awk '{print $1}'
}
sha_deck() {
  "$ADB" -s "$DEV" shell "run-as com.termux sha256sum $1" 2>/dev/null \
    | tr -d '\r' | awk '{print $1}'
}

# --- 2. snapshot the deck's CURRENT copies ---------------------------------
#
# "The pull produced nothing" and "there is no such file" are NOT the same
# thing, and conflating them is dangerous: rollback treats a missing snapshot
# as "this file was new, delete it". A flaky adb pull would then have
# rollback DELETE the deck's working tee.py rather than restore it. So
# existence is asked for separately, and a failed pull of a file that DOES
# exist is a hard stop before anything is written.
mkdir -p "$SNAP"
NEWFILES="$SNAP/.new-files"
: > "$NEWFILES"
for src in "${FILES[@]}"; do
  base="$(basename "$src")"
  out="$SNAP/$base"
  if "$ADB" -s "$DEV" shell "run-as com.termux test -f $DIR/$base && echo yes" 2>/dev/null \
       | tr -d '\r' | grep -q yes; then
    "$ADB" -s "$DEV" exec-out "run-as com.termux cat $DIR/$base" > "$out" 2>/dev/null
    deck_sha="$(sha_deck "$DIR/$base")"
    if [ ! -s "$out" ] || [ "$(sha_local "$out")" != "$deck_sha" ]; then
      echo "ERROR: $base exists on the deck but could not be snapshotted intact." >&2
      echo "       Refusing to deploy - a deploy without a good backup is how" >&2
      echo "       the only working copy gets lost. Retry, or check the link." >&2
      exit 1
    fi
    echo "  snapshot $base ($(wc -c < "$out") bytes)"
  else
    rm -f "$out"
    echo "$base" >> "$NEWFILES"
    echo "  snapshot $base: not present on the deck (new file)"
  fi
done
echo "snapshot -> backups/deck/$STAMP"

# --- health gate and rollback ----------------------------------------------
#
# "The process is alive" is not health. On 2026-08-24 this script reported a
# clean deploy while the tee was throwing AttributeError on every frame: a
# new tee.py had gone over without the config.py it depended on, the data
# thread died, and RealDash sat blank at 0.0 Hz. Alive, and useless.
#
# So the gate is the number the whole system exists to produce -- the frame
# rate -- measured strictly AFTER the restart. Fail it and the snapshot
# taken in step 2 goes straight back.
log_lines() {
  "$ADB" -s "$DEV" shell "run-as com.termux sh -c 'wc -l < $1 2>/dev/null || echo 0'" 2>/dev/null \
    | tr -d '\r' | tr -dc '0-9'
}
log_since() {
  # $1 = path, $2 = line count at the mark
  "$ADB" -s "$DEV" exec-out "run-as com.termux sh -c 'tail -n +$(( ${2:-0} + 1 )) $1'" 2>/dev/null | tr -d '\r'
}

health_check() {
  local tee_mark="${1:-0}" up_mark="${2:-0}" out hz
  out="$(log_since "$HOME_T/tee.log" "$tee_mark")"
  if [ -z "$out" ]; then
    echo "  UNHEALTHY: tee.log has produced nothing since the restart" >&2
    return 1
  fi
  if echo "$out" | grep -q "Traceback"; then
    echo "  UNHEALTHY: traceback in tee.log SINCE the restart" >&2
    echo "$out" | grep -A6 "Traceback" | tail -14 >&2
    return 1
  fi
  # upload.py got no health check at all until a review pointed it out, and
  # this script has already shipped changes to it.
  if log_since "$HOME_T/upload.log" "$up_mark" | grep -q "Traceback"; then
    echo "  UNHEALTHY: traceback in upload.log SINCE the restart" >&2
    log_since "$HOME_T/upload.log" "$up_mark" | grep -A6 "Traceback" | tail -14 >&2
    return 1
  fi
  hz="$(echo "$out" | grep -o '^\[tee\] [0-9.]* Hz' | tail -1 | awk '{print $2}')"
  if [ -z "$hz" ]; then
    echo "  UNHEALTHY: no status line since the restart" >&2; return 1
  fi
  # Board unplugged / ignition off is a legitimate 0.0 Hz. Decide from the
  # tee's own view (it says "source:" and then reconnects) rather than one
  # hardcoded sysfs path, which only describes one USB port.
  if [ "${hz%%.*}" -eq 0 ] 2>/dev/null; then
    if "$ADB" -s "$DEV" shell "grep -qsr CircuitPython /sys/bus/usb/devices/*/interface" 2>/dev/null; then
      echo "  UNHEALTHY: a CircuitPython board is attached but the tee reads $hz Hz" >&2
      return 1
    fi
    echo "  0.0 Hz and no board attached - cannot verify (ignition off?)"
    echo "  RE-RUN THIS DEPLOY WITH THE CAR ON before trusting it." >&2
    return 0
  fi
  echo "  healthy: $hz Hz"
  return 0
}

unstage_all() {
  for f in "${FILES[@]}"; do
    "$ADB" -s "$DEV" shell "run-as com.termux rm -f $DIR/$(basename "$f").new" >/dev/null 2>&1
  done
}

# Ctrl-C between "kill the daemons" and "verify they came back" used to exit
# silently, leaving a deck whose logger was down and whose state nobody had
# checked. It cannot safely auto-roll-back (a second interrupt mid-restore is
# worse than either end state), so it says exactly what is true and where the
# good copies are.
on_signal() {
  trap - INT TERM
  echo "" >&2
  echo "INTERRUPTED. The daemons were killed and may not be back yet." >&2
  echo "  The watchdog restarts them within ~20 s. Check:" >&2
  echo "    $ADB -s $DEV shell \"run-as com.termux tail -3 $HOME_T/tee.log\"" >&2
  echo "  Pre-deploy copies for a manual rollback: backups/deck/$STAMP" >&2
  unstage_all
  exit 130
}

rollback() {
  echo "ROLLING BACK to backups/deck/$STAMP" >&2
  local failed=""
  for src in "${FILES[@]}"; do
    base="$(basename "$src")"
    if grep -qxF "$base" "$NEWFILES" 2>/dev/null; then
      # Only a file step 2 PROVED absent is safe to delete. A missing
      # snapshot is never treated as "it was new" -- step 2 refuses to
      # proceed in that case precisely so this branch stays trustworthy.
      echo "  $base was new; removing" >&2
      "$ADB" -s "$DEV" shell "run-as com.termux rm -f $DIR/$base" >/dev/null 2>&1
      continue
    fi
    if [ ! -f "$SNAP/$base" ]; then
      echo "  CANNOT RESTORE $base: no snapshot and not marked new" >&2
      failed="$failed $base"
      continue
    fi
    base64 -w0 "$SNAP/$base" > "$SNAP/.rb.b64"
    "$ADB" -s "$DEV" shell "run-as com.termux sh -c 'tr -d \"\\r\" | base64 -d > $DIR/$base'" \
        < "$SNAP/.rb.b64" >/dev/null 2>&1
    if [ "$(sha_deck "$DIR/$base")" = "$(sha_local "$SNAP/$base")" ]; then
      echo "  restored $base (verified)" >&2
    else
      echo "  RESTORE FAILED for $base - deck copy does not match the snapshot" >&2
      failed="$failed $base"
    fi
  done
  rm -f "$SNAP/.rb.b64"
  "$ADB" -s "$DEV" shell "run-as com.termux sh -c 'pkill -f \"[t]ee.py\"; pkill -f \"[u]pload.py\"; pkill -f \"[c]anbox.py\"'" >/dev/null 2>&1
  if [ -n "$failed" ]; then
    echo "ROLLBACK INCOMPLETE - these are NOT restored:$failed" >&2
    echo "  Good copies are in backups/deck/$STAMP - push them by hand." >&2
    return 1
  fi
  echo "ROLLED BACK. The deck is running the code it had before this deploy." >&2
}

# --- 3. push, staged then committed ----------------------------------------
#
# Two phases so a checksum failure on the LAST file cannot leave the earlier
# ones already live: everything is written to <name>.new and verified first,
# and only then are they moved into place. The mv is verified too - an
# unchecked mv silently turns a failed deploy into "pushed ... verified".
for src in "${FILES[@]}"; do
  base="$(basename "$src")"
  want="$(sha_local "$src")"
  base64 -w0 "$src" > "$SNAP/.push.b64"
  # tr -d '\r' undoes adb's stdin line translation; without it base64 -d
  # fails or silently produces a corrupt file.
  "$ADB" -s "$DEV" shell "run-as com.termux sh -c 'tr -d \"\\r\" | base64 -d > $DIR/$base.new'" \
      < "$SNAP/.push.b64" >/dev/null 2>&1
  got="$(sha_deck "$DIR/$base.new")"
  if [ -z "$got" ] || [ "$got" != "$want" ]; then
    echo "ERROR: $base checksum mismatch after transfer (want $want, got ${got:-EMPTY})" >&2
    unstage_all
    echo "       Nothing was committed; the deck is untouched." >&2
    exit 1
  fi
  # Shell scripts get parsed by the shell that will actually RUN them.
  # `sh -n` here is Git Bash in posix mode, which is not Termux's sh -- it
  # accepts and rejects different things, so a local pass proves little.
  case "$src" in
    *.sh)
      if ! "$ADB" -s "$DEV" shell "run-as com.termux sh -n $DIR/$base.new && echo SHOK" 2>&1 \
           | tr -d '\r' | grep -q SHOK; then
        echo "ERROR: $base does not parse under the DECK's shell" >&2
        "$ADB" -s "$DEV" shell "run-as com.termux sh -n $DIR/$base.new" 2>&1 | tr -d '\r' | head -5 >&2
        unstage_all
        exit 1
      fi
      echo "  staged $base ($want, parses on the deck)"
      ;;
    *) echo "  staged $base ($want)" ;;
  esac
done
for src in "${FILES[@]}"; do
  base="$(basename "$src")"
  want="$(sha_local "$src")"
  "$ADB" -s "$DEV" shell "run-as com.termux sh -c 'mv $DIR/$base.new $DIR/$base && chmod 700 $DIR/$base'" >/dev/null 2>&1
  if [ "$(sha_deck "$DIR/$base")" != "$want" ]; then
    echo "ERROR: $base did not commit (mv failed); deck state is now MIXED" >&2
    rm -f "$SNAP/.push.b64"
    rollback; exit 1
  fi
  echo "  pushed $base"
done
rm -f "$SNAP/.push.b64"

# --- 4. restart, then PROVE it still works ---------------------------------
if [ "$RESTART" -eq 1 ]; then
  # Mark where each log currently ENDS. Everything the health gate looks at
  # must come after these marks, or it judges the new process on the old
  # process's output -- in both directions: an Hz line from the daemon we
  # just killed reads as success, and a traceback from before the deploy
  # rolls back the very fix that cured it.
  TEE_MARK="$(log_lines "$HOME_T/tee.log")"
  UP_MARK="$(log_lines "$HOME_T/upload.log")"
  trap on_signal INT TERM
  echo "restarting daemons (the watchdog brings them straight back)..."
  # The [x] bracket trick stops pkill matching its own command line, which
  # otherwise fails with "Operation not permitted".
  "$ADB" -s "$DEV" shell "run-as com.termux sh -c 'pkill -f \"[t]ee.py\"; pkill -f \"[u]pload.py\"; pkill -f \"[c]anbox.py\"'" >/dev/null 2>&1
  echo "waiting for the watchdog to restart them and for a status line..."
  sleep 35
  missing=""
  for proc in tee.py upload.py canbox.py; do
    "$ADB" -s "$DEV" shell "ps -A -o ARGS" 2>/dev/null | tr -d '\r' | grep -q "$proc" \
      || missing="$missing $proc"
  done
  if [ -n "$missing" ]; then
    echo "  UNHEALTHY: not running:$missing" >&2
    rollback; exit 1
  fi
  if ! health_check "$TEE_MARK" "$UP_MARK"; then
    rollback; exit 1
  fi
  trap - INT TERM
  echo "deploy verified."
else
  echo "not restarting; the deck keeps running the OLD code until you do."
fi
