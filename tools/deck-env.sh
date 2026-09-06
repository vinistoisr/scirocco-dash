# deck-env.sh -- find the head unit, and keep every tool agreeing on where it is.
#
# Sourced by deploy-board.sh and deploy-deck.sh. Not executable on its own.
#
# WHY THIS EXISTS
#   The deck's address was once hardcoded in two scripts. When its DHCP
#   lease moved, every tool broke at once,
#   mid-session, with "device offline" and no hint that the address was the
#   problem rather than the deck being asleep.
#
#   Give it a static DHCP lease, which is the real fix -- this file just
#   makes sure a future move costs seconds instead of a debugging detour.
#
# ORDER OF RESOLUTION
#   1. $DEV, if the caller set it       (explicit always wins)
#   2. .deck-ip, cached from last success
#   3. DECK_DEFAULT, the static lease
#   4. a sweep of the /24, verified by model string so we never talk to some
#      other adb device on the network
#
# The sweep uses bash's /dev/tcp rather than nmap or nc, neither of which is
# present in this Git Bash environment.

DECK_DEFAULT="${DECK_DEFAULT:-192.168.1.50:5555}"   # CHANGE ME: your deck's static lease
DECK_MODEL="${DECK_MODEL:-uis7870sc_2h10}"
DECK_SUBNET="${DECK_SUBNET:-192.168.1}"           # CHANGE ME: your LAN /24
DECK_CACHE="$REPO/.deck-ip"

_adb_is_deck() {
  # A device that answers adb is not necessarily THIS device -- a phone on
  # the same wifi with debugging on would happily accept a firmware push.
  local dev="$1" model
  "$ADB" connect "$dev" >/dev/null 2>&1
  [ "$("$ADB" -s "$dev" get-state 2>&1 | tr -d '\r')" = "device" ] || return 1
  model="$("$ADB" -s "$dev" shell getprop ro.product.model 2>/dev/null | tr -d '\r\n')"
  [ "$model" = "$DECK_MODEL" ]
}

_port_open() {
  timeout 0.4 bash -c "echo > /dev/tcp/$1/5555" >/dev/null 2>&1
}

_sweep_for_deck() {
  echo "sweeping $DECK_SUBNET.0/24 for the deck..." >&2
  local i hits=""
  # Probe all 254 in parallel; a serial sweep of adb connects takes minutes.
  for i in $(seq 1 254); do
    ( _port_open "$DECK_SUBNET.$i" && echo "$DECK_SUBNET.$i:5555" ) &
  done > "$REPO/.deck-sweep.tmp" 2>/dev/null
  wait
  hits="$(sort -u "$REPO/.deck-sweep.tmp" 2>/dev/null)"
  rm -f "$REPO/.deck-sweep.tmp"
  local cand
  for cand in $hits; do
    if _adb_is_deck "$cand"; then echo "$cand"; return 0; fi
  done
  return 1
}

resolve_dev() {
  local cand
  if [ -n "${DEV:-}" ]; then
    if _adb_is_deck "$DEV"; then echo "$DEV"; return 0; fi
    echo "ERROR: DEV=$DEV was set explicitly but is not the deck" >&2
    return 1
  fi
  for cand in "$(cat "$DECK_CACHE" 2>/dev/null)" "$DECK_DEFAULT"; do
    [ -n "$cand" ] || continue
    if _adb_is_deck "$cand"; then
      echo "$cand" > "$DECK_CACHE"
      echo "$cand"
      return 0
    fi
  done
  cand="$(_sweep_for_deck)" || {
    echo "ERROR: cannot find the deck. Is it powered and on wifi?" >&2
    echo "       Tried ${DECK_DEFAULT}, the cache, and a sweep of $DECK_SUBNET.0/24." >&2
    return 1
  }
  echo "found the deck at $cand (was expecting $DECK_DEFAULT)" >&2
  echo "$cand" > "$DECK_CACHE"
  echo "$cand"
}
