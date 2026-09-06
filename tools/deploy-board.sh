#!/bin/bash
# deploy-board.sh -- push firmware to the Feather, SNAPSHOT FIRST.
#
# WHY THIS EXISTS
#   On 2026-08-23 a deploy pushed a new gauge_main.py to BOTH code.py and
#   code_gauge_backup.py in one step. The new build broke the board, the tee
#   went to 0.0 Hz, and the only good copy on the device had just been
#   overwritten by the thing it was supposed to protect. It had to be
#   reconstructed by hand-reverting edits.
#
#   Two rules came out of that, and this script enforces both:
#     1. Every deploy first copies the board's CURRENT files to a timestamped
#        directory on this laptop. The laptop has a disk; use it.
#     2. The on-board backup is NEVER written in the same run as code.py.
#        A backup you update at the same moment as the original is not a
#        backup.
#
# USAGE
#   tools/deploy-board.sh board/gauge_main.py            # -> code.py
#   tools/deploy-board.sh board/auxmods.py               # -> auxmods.py
#   tools/deploy-board.sh --promote-backup               # bless the RUNNING
#                                                        # code.py as the
#                                                        # on-board fallback
set -u
export MSYS_NO_PATHCONV=1
ADB="${ADB:-$(command -v adb || echo adb)}"
REPO="$(cd "$(dirname "$0")/.." && pwd)"
. "$REPO/tools/deck-env.sh"
STAMP="$(date +%Y%m%d-%H%M%S)"
SNAP="$REPO/backups/board/$STAMP"

board_mount() {
  "$ADB" -s "$DEV" shell 'for p in /storage/USB1 /storage/USB2 /storage/USB3; do
      if grep -qs CircuitPython $p/boot_out.txt 2>/dev/null; then echo $p; break; fi
    done' 2>/dev/null | tr -d '\r\n '
}

DEV="$(resolve_dev)" || exit 1
echo "deck: $DEV"
BOARD="$(board_mount)"
[ -n "$BOARD" ] || { echo "ERROR: no CircuitPython volume found" >&2; exit 1; }
echo "board: $BOARD"

# --- 1. snapshot, ALWAYS, before touching anything -----------------------
#
# The sweep is deliberately indiscriminate -- it takes every .py on the drive
# so nothing is lost -- but "everything on the drive" included secrets.py, the
# stock Adafruit WiFi template left behind by an AirLift demo that was never
# part of this project. Nothing here ever imported it, yet 46 snapshots copied
# a real WiFi password onto the laptop, and 34 of those reached git history.
# Capture everything EXCEPT credentials, and say so out loud when skipping.
skip_file() {
  case "$1" in
    secrets.py|*secret*|*.env) return 0 ;;
    *) return 1 ;;
  esac
}

mkdir -p "$SNAP"
for f in $("$ADB" -s "$DEV" shell "ls $BOARD/*.py $BOARD/*.xml 2>/dev/null" 2>/dev/null | tr -d '\r'); do
  base="$(basename "$f")"
  if skip_file "$base"; then
    echo "  SKIPPED $base -- looks like credentials, not snapshotting it"
    continue
  fi
  "$ADB" -s "$DEV" pull "$f" "$(cygpath -w "$SNAP/$base" 2>/dev/null || echo "$SNAP/$base")" >/dev/null 2>&1 \
    && echo "  snapshot $base"
done
n=$(ls -1 "$SNAP" 2>/dev/null | wc -l)
if [ "$n" -eq 0 ]; then
  echo "ERROR: snapshot captured 0 files - refusing to deploy blind" >&2
  rmdir "$SNAP" 2>/dev/null; exit 1
fi
echo "snapshot: $n files -> backups/board/$STAMP"

# --- 2. promote-backup is its OWN operation, never bundled ---------------
if [ "${1:-}" = "--promote-backup" ]; then
  "$ADB" -s "$DEV" shell "cp $BOARD/code.py $BOARD/code_gauge_backup.py && sync" >/dev/null 2>&1
  echo "promoted the RUNNING code.py to code_gauge_backup.py"
  exit 0
fi

[ $# -ge 1 ] || { echo "usage: $0 <file> [file...] | --promote-backup" >&2; exit 2; }

# --- 3. deploy -----------------------------------------------------------
for src in "$@"; do
  [ -f "$src" ] || { echo "ERROR: $src not found" >&2; exit 1; }
  # validate by type: shipping a file its consumer cannot parse is exactly
  # the failure this script exists to prevent
  case "$src" in
    *.py)  check="import ast,sys;ast.parse(open(sys.argv[1],encoding='utf-8').read())" ;;
    *.xml) check="import xml.etree.ElementTree as E,sys;E.parse(sys.argv[1])" ;;
    *)     check="" ;;
  esac
  # cygpath, for the same reason the push below needs it: this is Windows
  # Python behind an MSYS shell, and it cannot open a /c/... path. Without
  # it, deploying by ABSOLUTE path always failed validation ("No such file
  # or directory") while the identical relative path worked -- so the guard
  # fired on good files and the caller looked like it had shipped a syntax
  # error. Caught 2026-08-24 by tools/enginescan.sh, which passes $REPO/...
  if [ -n "$check" ] && ! python -c "$check" "$(cygpath -w "$src" 2>/dev/null || echo "$src")"; then
    echo "ERROR: $src failed validation - not deploying" >&2
    exit 1
  fi
  base="$(basename "$src")"
  [ "$base" = "gauge_main.py" ] && base="code.py"
  if [ "$base" = "code_gauge_backup.py" ]; then
    echo "REFUSED: write the backup with --promote-backup, not as a file push" >&2
    exit 1
  fi
  "$ADB" -s "$DEV" push "$(cygpath -w "$src" 2>/dev/null || echo "$src")" "$BOARD/$base" 2>&1 | tail -1
done
"$ADB" -s "$DEV" shell sync >/dev/null 2>&1
echo "deployed. Verify the tee recovers before promoting this build:"
echo "  tools/deploy-board.sh --promote-backup"
