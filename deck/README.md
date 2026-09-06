# deck/ -- the head-unit daemon

The tee daemon owns the Feather's data serial port, serves the frames to
RealDash over TCP, and logs every drive into per-session files. Architecture
and all decisions: `docs/PLAN-deck.md`.

    frame_schema.py   decoder generated from board/scirocco_realdash.xml
    tee.py            the daemon: serial <-> TCP tee + logger + burst logic
    sim_feather.py    laptop stand-in for the board (bench testing)
    config.py         every tunable in one place
    (uploader)        separate task, not here yet

Stdlib only; pyserial is needed only for `--serial` mode on the deck.

## Bench test on Windows (no car, no board)

Two terminals in the repo root:

    python deck\sim_feather.py --fast
    python deck\tee.py --sim --logdir %USERPROFILE%\drive-logs

The sim plays a looping drive profile (60 s idle, 12 s pull to 6500 rpm /
+1.1 bar with a knock event on cylinder 2, 20 s cruise) using the values
captured live on the car as the idle baseline, encoded by the board's own
`realdash.py` so the bytes are identical to the real feed. `--fast`
compresses the profile 10x (full loop in 9.2 s) without changing the 14 Hz
frame rate, so burst triggers and the 3 s release happen in seconds.

Expected within ~15 s of starting both: the tee prints `session ... started`
and `burst segment 1 triggered`, and a status line every 10 s. Note the
cruise phase sits exactly on the burst OFF threshold (rpm < 2500 is never
sustained at a 2500 rpm cruise), so each pull's segment deliberately runs
through the cruise and ends ~3 s into the next idle.

Quick sanity checks:

    python deck\frame_schema.py        # schema self-test vs the captured frame

Stop the sim and the tee closes the session ~30 s later (`--idle-end 8`
shortens that for bench runs). Buttons: bind a RealDash button to a 0xC90
value; the tee prints the forwarded command and the sim prints it decoded
(`Reset Peak` actually resets the sim's peak, visible on a dashboard).

## Pointing RealDash at the tee

Same XML as before (`board/scirocco_realdash.xml`); only the connection type
changes, serial to network. RealDash is always the TCP client.

On the deck (or RealDash for Windows against a bench tee):

    Garage -> Connections -> RealDash CAN -> WiFi/LAN
    host 127.0.0.1, port 35000, select scirocco_realdash.xml

Use the laptop's LAN IP instead of 127.0.0.1 if RealDash runs on a different
machine than the tee (then start the tee with `--bind 0.0.0.0:35000`).
"Send config frames" can stay enabled -- the tee tolerates and discards the
0x67 config frames RealDash sends on connect. If no data flows for a second
(board asleep, port reopening), the tee sends a synthetic 0xC82 status frame
every second so RealDash never times out and reconnect-cycles.

## Running on the deck (Termux, root)

    python tee.py --serial /dev/ttyACM1

ttyACM1 is the data CDC port (ttyACM0 is the console). The port is opened in
a retry loop with backoff, so deck sleep/resume and USB re-enumeration just
cause a reopen, never a crash; sessions survive reopens. GPS rows appear
automatically when `termux-location` exists and Termux:API has the Location
permission; anywhere else the GPS columns are simply blank.

## What a session looks like

One directory per drive under `~/drive-logs` (or `--logdir`), started on the
first decoded frame, closed after 30 s of serial silence:

    ~/drive-logs/2026-08-22_0731/
      raw.bin        every byte, both directions. Record: struct '<BHQ'
                     (dir 0=board->app 1=app->board, payload_len, unix_ms)
                     followed by the payload chunk. fsync'd every second.
      drive.csv.gz   1 Hz baseline rows: t_unix, all 39 decoded channels
                     (stable order from the XML), lat,lon,alt,gps_speed
      bursts.csv.gz  full-rate (~14 Hz) rows, same columns plus a trailing
                     segment id; each segment includes a 5 s pre-roll.
                     Trigger ON: rpm>=3000 or load>=80 or boost>=+0.30 bar.
                     OFF: all of rpm<2500, load<60, boost<0.15 for 3 s.
      meta.json      start/end, row/segment/reconnect counters, last DTC
                     words, channel list, upload state. Written LAST -- its
                     presence is the uploader's signal that the directory
                     is complete.

The CSVs are gzipped and the plain files removed when the session closes; a
directory containing a bare `drive.csv` (no meta.json) is a session that was
cut down mid-write by a cold boot -- raw.bin holds everything up to the last
second and the CSVs can be regenerated from it (recovery tooling is phase 6).
