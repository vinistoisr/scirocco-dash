# deploy-board.ps1 -- RETIRED 2026-08-24. Use tools/deploy-board.sh instead.
#
# This script was a trap, not a tool. Two faults, either of which loses work:
#
#   1. It pushed board/code.py as the firmware. That file was a STALE copy of
#      gauge_main_safe.py (md5 67e2bc32) while the board has been running
#      gauge_main.py (2c3755cf) since 2026-08-23. Running this would have
#      silently DOWNGRADED the board -- losing the command-drain fixes, the
#      deadline-scheduled block rotation, and the debounced boost warning --
#      and reported success. board/code.py has now been deleted so no tool can
#      make that mistake again; gauge_main.py is the only firmware source.
#
#   2. It looked for the board at /storage/usbdisk. The real mount is
#      /storage/USB1, so it could not have worked anyway.
#
# It also had none of the safety deploy-board.sh was written for: no snapshot
# to backups/board/<stamp>/ before writing, no per-type validation, no refusal
# to overwrite the on-board backup in the same run.
#
# The one genuinely good idea here was stubbing code.py first so libraries
# could land without the old program running against them. If that turns out
# to matter, port it INTO deploy-board.sh rather than reviving a second,
# divergent deploy path -- two tools disagreeing about which file is the
# firmware is exactly how the board gets quietly downgraded.

Write-Error @"
tools/deploy-board.ps1 is retired -- it would push a stale build.

Use:
    bash tools/deploy-board.sh board/gauge_main.py board/realdash.py

See the comments at the top of this file, and docs/RELIABILITY.md.
"@
exit 1
