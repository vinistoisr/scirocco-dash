"""
config.py -- every tunable for the deck daemon in one place.

Plain module constants, imported by tee.py and sim_feather.py. Command-line
flags override the ones that vary per machine (device paths, ports, log
directory); the behavioral constants live only here so a threshold change is
one edit, not a hunt through the daemon.
"""

# --- TCP server for RealDash -------------------------------------------------
# RealDash is ALWAYS the TCP client (Garage -> Connections -> RealDash CAN ->
# WiFi/LAN -> this host:port). Loopback, because RealDash and the tee run on
# the same head unit; Android permits loopback between Termux and a normal
# app. Bind 0.0.0.0 (via --bind) only for bench tests from another machine.
REALDASH_BIND_HOST = "127.0.0.1"
REALDASH_BIND_PORT = 35000

# RealDash cycles the connection if nothing arrives for a couple of seconds,
# so the tee sends a synthetic 0xC82 status frame immediately on accept and
# whenever nothing has gone to the client for this long (board asleep,
# ignition off, serial reopening).
HEARTBEAT_PERIOD_S = 1.0

# A stalled client must not stall the serial path: sends time out after this
# and the client is dropped (it will reconnect; it always does).
CLIENT_SEND_TIMEOUT_S = 1.0

# --- board serial ------------------------------------------------------------
# The DATA CDC port (boot.py exposes console on ttyACM0, data on ttyACM1).
# On Windows bench setups this is a COMx name instead.
SERIAL_DEVICE = "/dev/ttyACM1"
SERIAL_BAUD = 115200            # RealDash serial convention; USB CDC ignores it
# Reopen backoff when the port vanishes (deck sleep/resume re-enumerates USB).
SERIAL_BACKOFF_MIN_S = 0.5
SERIAL_BACKOFF_MAX_S = 5.0

# --- simulator (laptop bench mode) -------------------------------------------
# Local frame injection (canbox.py -> tee): deck-side readers push RealDash
# '44' frames here; the tee logs them and forwards them to RealDash, but
# never sends them to the board. 35001 is taken by SIM_PORT.
INJECT_HOST = "127.0.0.1"
INJECT_PORT = 35002

SIM_HOST = "127.0.0.1"
SIM_PORT = 35001
SIM_RATE_HZ = 14.0              # the board's real acquisition rate

# --- logging -----------------------------------------------------------------
# Session directories land here, one per drive: <LOG_DIR>/2026-08-22_0731/.
# expanduser() makes "~" work on both Windows and Termux.
LOG_DIR = "~/drive-logs"

# A session starts on the first decoded frame and ends after this much serial
# silence (ignition off; the board dies with it). Short enough that the
# uploader gets the files while the deck is still awake in the driveway.
SESSION_IDLE_END_S = 30.0

# A session is one ignition cycle, and it ends this long after the ENGINE
# stops -- see Tee._engine_running() for what counts as stopped.
#
# Short deliberately. The deck keeps full power for only ~30 s after the key
# comes out, so anything longer means parking at home, walking away, and
# having the session still open when the deck dies: it is then left for the
# uploader to adopt on the next boot, which skips the clock-jump repair and
# files the drive under a January name. 20 s closes and uploads inside the
# shutdown window instead. It cannot split a drive at a red light -- the
# engine is still turning there -- and 20 uninterrupted seconds of rpm 0
# while the ECU is still answering does not happen mid-drive.
SESSION_ENGINE_OFF_S = 20.0

# Files are flushed (raw.bin also fsync'd -- it is the replay source of
# truth) at least this often, so a cold boot mid-drive loses at most ~1 s.
FLUSH_INTERVAL_S = 1.0

# --- burst logging (spec CONFIRMED 2026-08-22, PLAN-deck.md) ------------------
BASELINE_PERIOD_S = 1.0         # drive.csv: one full row per second, always
RING_SECONDS = 5.0              # pre-roll of full-rate rows kept while idle

# Trigger ON when ANY of these is met; OFF when ALL are below the OFF
# thresholds continuously for TRIGGER_OFF_SUSTAIN_S. Hysteresis plus the
# sustain window keeps a lift-and-squeeze mid-pull inside one segment.
TRIGGER_ON_RPM = 3000.0
TRIGGER_ON_LOAD = 80.0          # % -- the WOT proxy (pedal is snapshot-only)
# ...but ONLY above this rpm. This ECU reports engine_load = 100% at idle
# and with the engine off, so a bare load clause fires forever: on the
# 2026-08-23 drive, 2252 of 2687 idle rows triggered bursts and 30% of all
# burst rows were recorded at idle while only 12% were genuinely hot.
TRIGGER_LOAD_MIN_RPM = 2000.0
TRIGGER_ON_BOOST_BAR = 0.30
TRIGGER_OFF_RPM = 2500.0
TRIGGER_OFF_LOAD = 60.0
TRIGGER_OFF_BOOST_BAR = 0.15
TRIGGER_OFF_SUSTAIN_S = 3.0

# Channel names (as derived from scirocco_realdash.xml by frame_schema.py)
# that feed the trigger. rpm and engine_load come from 0xC80, boost (already
# in gauge bar, negative under vacuum) from 0xC81.
CHANNEL_RPM = "rpm"
CHANNEL_LOAD = "engine_load"
CHANNEL_BOOST_BAR = "boost"

# --- GPS ---------------------------------------------------------------------
# termux-location fixes merged into log rows. Auto-disabled when the binary
# is absent (Windows bench). Requires the Location permission on Termux:API
# and system location ON (see PLAN-deck.md GPS section).
GPS_POLL_S = 2.0
GPS_TIMEOUT_S = 15.0            # subprocess kill; a GPS hang must never block
GPS_MAX_AGE_S = 10.0            # older fixes log as blank, not as stale lies

# --- status ------------------------------------------------------------------
STATUS_PERIOD_S = 10.0          # one status line to stdout

# --- uploader (owned by the uploader task; placed here so every tunable
# lives in one file) -----------------------------------------------------------
HOME_SSID = "CHANGEME"              # uploads run only on this network.
                                    # Set your home WiFi SSID in the DECK's
                                    # copy of this file. upload.py treats any
                                    # value starting with CHANGEME as unset
                                    # and refuses to upload unforced; and
                                    # deploy-deck.sh refuses to push this
                                    # file without --allow-config, because
                                    # overwriting the deck's value silently
                                    # disables uploads.
