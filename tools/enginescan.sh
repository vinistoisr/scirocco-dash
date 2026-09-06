#!/bin/bash
# enginescan.sh -- run the engine measuring-block probe and capture it.
#
# WHY A SCRIPT
#   This swaps the car's gauge firmware for a probe. Every step of that is
#   reversible, but only if it is done in the right order, and the order is
#   not obvious:
#
#     * The probe REPLACES code.py, so `sendcmd reload` has to be answered
#       by the probe itself (it is -- see pump() in enginescan.py). Deploy
#       a probe without that and the only way back is rebooting the deck
#       that powers the board.
#     * Nothing is killed. The bridge app fans a second telnet client out
#       the full stream -- measured 2026-08-24: a second reader saw the
#       same 15.6 Hz the tee did, and the tee never dropped below 15.2 Hz.
#       So the capture attaches ALONGSIDE tee.py, and the watchdog, the
#       tee and the uploader all keep running untouched. This is the part
#       that used to require stopping the whole chain.
#     * Probe output still must not go THROUGH the tee: the tee files bytes
#       into the current drive session and closes that session when the
#       gauge frames stop, which is exactly what loses probe output (a
#       shifter sweep died this way on 2026-08-23). A second client bypasses
#       the tee entirely.
#
# USAGE
#   tools/enginescan.sh scan [seconds] [probe.py]   deploy and capture
#   tools/enginescan.sh capture [secs]   capture only (probe already running)
#   tools/enginescan.sh restore          put gauge_main.py back and reload
set -u
export MSYS_NO_PATHCONV=1
ADB="${ADB:-$(command -v adb || echo adb)}"
REPO="$(cd "$(dirname "$0")/.." && pwd)"
. "$REPO/tools/deck-env.sh"

CAP_PORT=12323          # local -> deck 2323 (the bridge itself)
CMD_PORT=13500          # local -> deck 35000 (the tee's RealDash port)
OUTDIR="$REPO/captures"

# Windows Python behind an MSYS shell cannot open a /c/... path, so every
# script path handed to it goes through cygpath. Same trap that broke
# sha_local() in deploy-deck.sh and the validation step in deploy-board.sh.
pyrun() {
  local f="$1"; shift
  python "$(cygpath -w "$f" 2>/dev/null || echo "$f")" "$@"
}

board_mount() {
  "$ADB" -s "$DEV" shell 'for p in /storage/USB1 /storage/USB2 /storage/USB3; do
      if grep -qs CircuitPython $p/boot_out.txt 2>/dev/null; then echo $p; break; fi
    done' 2>/dev/null | tr -d '\r\n '
}

reload_board() {
  "$ADB" -s "$DEV" forward "tcp:$CMD_PORT" tcp:35000 >/dev/null || return 1
  pyrun "$REPO/deck/sendcmd.py" reload --port "$CMD_PORT"
}

DEV="$(resolve_dev)" || exit 1
echo "deck: $DEV"
MODE="${1:-scan}"
SECS="${2:-420}"
PROBE="${3:-board/probes/enginescan.py}"
PROBE_BASE="$(basename "$PROBE")"

case "$MODE" in
  scan)
    BOARD="$(board_mount)"
    [ -n "$BOARD" ] || { echo "ERROR: no CircuitPython volume found" >&2; exit 1; }
    # deploy-board.sh owns the snapshot: it copies every .py/.xml off the
    # board before writing anything, and refuses to deploy on an empty
    # snapshot. Reuse it rather than reimplementing that guard here.
    bash "$REPO/tools/deploy-board.sh" "$REPO/$PROBE" || exit 1
    echo "installing $PROBE_BASE as code.py"
    "$ADB" -s "$DEV" shell "cp $BOARD/$PROBE_BASE $BOARD/code.py && sync" || exit 1
    reload_board || { echo "ERROR: reload not sent - board still on the gauge" >&2; exit 1; }
    echo "board reloading into the probe; it waits 40 s before phase 1"
    ;;
  capture) ;;
  restore)
    bash "$REPO/tools/deploy-board.sh" "$REPO/board/gauge_main.py" || exit 1
    reload_board || exit 1
    echo "gauge restored. Confirm it is really back:"
    echo "  adb -s $DEV shell 'run-as com.termux sh -c \"grep -o \\\"[0-9.]* Hz\\\" ~/tee.log | tail -3\"'"
    exit 0
    ;;
  *) echo "usage: $0 {scan|capture|restore} [seconds]" >&2; exit 2 ;;
esac

mkdir -p "$OUTDIR"
OUT="$OUTDIR/${PROBE_BASE%.py}-$(date +%Y%m%d-%H%M%S).log"
"$ADB" -s "$DEV" forward "tcp:$CAP_PORT" tcp:2323 >/dev/null || exit 1
echo "capturing ${SECS}s -> $OUT"
pyrun "$REPO/deck/probecap.py" --port "$CAP_PORT" --seconds "$SECS"       --out "$(cygpath -w "$OUT" 2>/dev/null || echo "$OUT")" || exit 1
echo ""
grep -c '^  BLK ' "$OUT" 2>/dev/null | sed 's/^/blocks found: /'
echo "restore the gauge with:  tools/enginescan.sh restore"
