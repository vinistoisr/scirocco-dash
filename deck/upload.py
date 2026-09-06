#!/usr/bin/env python3
"""
upload.py -- push closed drive sessions from the deck to Cloudflare R2.

Runs on the Dudu7 head unit inside Termux (plain `python` from pkg), but
degrades cleanly to a laptop for testing: every Termux-specific command is
optional, and --force bypasses the home-SSID gate.

Why a separate process from the tee: uploads happen only in the driveway
(home WiFi), the tee runs on every drive.  Keeping them apart means an
upload hang can never stall frame logging, and the uploader can be killed
and re-run freely while a drive is in progress.

Contract with the tee (docs/PLAN-deck.md, "Session files"):

    <logdir>/<session_id>/          e.g. ~/drive-logs/2026-08-22_0731/
        raw.bin.gz      every byte both directions        (required)
        drive.csv.gz    1 Hz baseline rows                (required)
        bursts.csv.gz   full-rate burst rows              (optional --
                                                           no pulls, no file)
        meta.json       written when the session CLOSES; its presence is
                        what marks a session as ready to upload

Upload order matters: data files first, meta.json LAST.  meta.json is the
commit marker -- the dashboard treats a drive as existing only once its
meta.json is in R2, so a half-uploaded session is invisible rather than
broken.  On full success the session dir moves to <logdir>/uploaded/ so
the scan stays O(pending) instead of O(all drives ever).

R2 layout (docs/PLAN-deck.md, "Cloud"):

    <remote>:<bucket>/drives/YYYY/MM/<session_id>/<file>

with YYYY/MM taken from the session id, which the tee names
YYYY-MM-DD_HHMM (fallbacks: meta "start", then the dir mtime).  The rclone
remote is expected in the default rclone.conf as [r2] with a bucket-scoped
Object R/W token -- see deck/bootstrap.md for the template.

Failure model: any rclone failure leaves the session in place for the next
pass.  Per-session exponential backoff (state kept in meta.json under the
"upload" key, everything else in meta.json is preserved) stops one broken
session from hammering rclone every 20 s in --loop mode while still
letting fresh sessions through immediately.

Wake lock: the deck's MCU allows roughly a 20-minute full-power window
after ignition off IF someone holds a partial wake lock; termux-wake-lock
is taken for the duration of each upload pass so a big raw.bin.gz survives
the walk from the driveway to the front door.
"""

import argparse
import json
import os
import gzip
import pathlib
import re
import shutil
import subprocess
import sys
import time

# Shared constants live in deck/config.py (owned by the tee).  Fall back to
# the plan's defaults so this file also works standalone on the laptop.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    import config as _config
except ImportError:
    _config = None


def _cfg(name, default):
    return getattr(_config, name, default) if _config else default


HOME_SSID = _cfg("HOME_SSID", "")            # empty = not configured yet
if HOME_SSID.startswith("CHANGEME"):         # config.py ships a placeholder
    HOME_SSID = ""
LOG_DIR = _cfg("LOG_DIR", "~/drive-logs")
R2_REMOTE = _cfg("R2_REMOTE", "r2")          # [r2] section in rclone.conf
R2_BUCKET = _cfg("R2_BUCKET", "scirocco-drives")
POLL_S = _cfg("UPLOAD_POLL_S", 20)
BACKOFF_BASE_S = _cfg("UPLOAD_BACKOFF_BASE_S", 60)
BACKOFF_CAP_S = _cfg("UPLOAD_BACKOFF_CAP_S", 3600)
RCLONE_TIMEOUT_S = _cfg("UPLOAD_RCLONE_TIMEOUT_S", 600)

REQUIRED = ("raw.bin.gz", "drive.csv.gz")
OPTIONAL = ("bursts.csv.gz",)

_warned_no_termux_wifi = False


def log(msg):
    print(time.strftime("%Y-%m-%d %H:%M:%S"), msg, flush=True)


def iso_now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


class UploadError(Exception):
    """One session's upload failed; the pass continues with the next."""


# ---------------------------------------------------------------- gating

def current_ssid():
    """The connected SSID, or None when it cannot be determined.

    termux-wifi-connectioninfo needs the Location permission granted to
    the Termux:API app AND the system location toggle on; otherwise
    Android censors the SSID to "<unknown ssid>" (SSIDs count as location
    data since Android 8.1).  Some builds also wrap the SSID in literal
    double quotes -- stripped here.
    """
    try:
        r = subprocess.run(["termux-wifi-connectioninfo"],
                           capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.TimeoutExpired) as e:
        log("termux-wifi-connectioninfo failed: %r" % (e,))
        return None
    if r.returncode != 0:
        log("termux-wifi-connectioninfo exit %d" % r.returncode)
        return None
    try:
        info = json.loads(r.stdout)
    except ValueError:
        log("termux-wifi-connectioninfo returned non-JSON output")
        return None
    ssid = (info.get("ssid") or "").strip().strip('"')
    if not ssid or "unknown ssid" in ssid.lower():
        log("SSID reads as unknown -- grant Location to Termux:API and "
            "turn system location ON (see deck/bootstrap.md step 8)")
        return None
    return ssid


def home_gate(args):
    """True when an upload pass may run right now.

    Order of the escapes matters: --force is for the laptop and for
    emergencies; a missing termux command means we are not on the deck at
    all, so gating on SSID would be meaningless -- warn once and let the
    pass run (rclone still has to succeed for anything to actually move).
    """
    global _warned_no_termux_wifi
    if args.force:
        return True
    if shutil.which("termux-wifi-connectioninfo") is None:
        if not _warned_no_termux_wifi:
            log("termux-wifi-connectioninfo not found; skipping the "
                "home-SSID check (fine on the laptop; on the deck install "
                "the Termux:API app and `pkg install termux-api`)")
            _warned_no_termux_wifi = True
        return True
    if not HOME_SSID:
        log("HOME_SSID is not set (deck/config.py); refusing to upload "
            "without --force")
        return False
    ssid = current_ssid()
    if ssid is None:
        return False
    if ssid != HOME_SSID:
        log("on '%s', not home ('%s'); holding uploads" % (ssid, HOME_SSID))
        return False
    return True


def wake_lock(on):
    """Best-effort termux-wake-lock/-unlock; silently a no-op elsewhere."""
    cmd = "termux-wake-lock" if on else "termux-wake-unlock"
    if shutil.which(cmd) is None:
        return
    try:
        subprocess.run([cmd], timeout=15)
    except (OSError, subprocess.TimeoutExpired) as e:
        log("%s failed: %r" % (cmd, e))


# ----------------------------------------------------------- meta.json

def read_meta(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError) as e:
        log("%s: unreadable meta.json (%r)" % (path.parent.name, e))
        return None


def write_meta(path, meta):
    """Atomic-enough rewrite: the tee's crash-safety rules apply here too
    (a cold boot mid-write must never leave a truncated meta.json, because
    its presence is what marks the session uploadable)."""
    tmp = path.parent / (path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2)
        f.write("\n")
    os.replace(tmp, path)


MISFILE_TOLERANCE_S = 2 * 86400.0


def _newest_mtime(sdir):
    try:
        return max((f.stat().st_mtime for f in sdir.iterdir() if f.is_file()),
                   default=sdir.stat().st_mtime)
    except OSError:
        return None


def year_month(sid, meta, sdir):
    """Where in the bucket this drive belongs: drives/YYYY/MM/<sid>/.

    The session id is normally authoritative, but it is derived from the
    wall clock at the moment the session STARTED -- and the deck's RTC is
    routinely wrong until NTP lands.  tee.py's clock-jump fix repairs that
    only for a session that is still OPEN when the correction arrives; a
    drive that started and finished before NTP caught up keeps its bad name,
    and two real drives went to drives/2026/01/ exactly that way.

    File mtimes are the cross-check.  They are written continuously, so the
    newest one reflects the clock at the END of the session -- by which time
    the correction has usually landed.  When the two disagree by more than
    MISFILE_TOLERANCE_S, the id is the suspect one.

    Honest limitation: if the clock was wrong for the WHOLE session and only
    corrected after it closed, every timestamp the session owns is wrong
    together and nothing here can recover the truth.  That case is logged.
    """
    mt = _newest_mtime(sdir)
    m = re.match(r"^(\d{4})-(\d{2})-\d{2}_\d{4}", sid)
    if m and mt is not None:
        try:
            claimed = time.mktime(time.strptime(sid[:15], "%Y-%m-%d_%H%M"))
        except ValueError:
            claimed = None
        if claimed is not None and abs(mt - claimed) > MISFILE_TOLERANCE_S:
            t = time.localtime(mt)
            log("%s: id claims %s but its files were written %s -- filing by "
                "the files (RTC was not synced when the session started)"
                % (sdir.name, time.strftime("%Y-%m", time.localtime(claimed)),
                   time.strftime("%Y-%m-%d", t)))
            return "%04d" % t.tm_year, "%02d" % t.tm_mon
    if (meta or {}).get("adopted") and mt is not None:
        t = time.localtime(mt)
        return "%04d" % t.tm_year, "%02d" % t.tm_mon
    if m:
        return m.group(1), m.group(2)
    m = re.match(r"^(\d{4})-(\d{2})", str((meta or {}).get("start", "")))
    if m:
        return m.group(1), m.group(2)
    t = time.localtime(sdir.stat().st_mtime)
    return "%04d" % t.tm_year, "%02d" % t.tm_mon


# ------------------------------------------------------------ transfer

try:
    import r2put
except ImportError:          # laptop-only checkouts / partial deploys
    r2put = None


def _key_from_remote(remote):
    """rclone remotes look like `r2:scirocco-drives/drives/...`; the S3 path
    needs just the part after the bucket."""
    _, _, path = remote.partition(":")
    bucket, _, key = path.partition("/")
    return bucket, key


def s3_copyto(local, remote, args):
    """Upload with the stdlib SigV4 client instead of rclone.

    Preferred on the head unit: no 50 MB dependency to install over a link
    that struggles with 5 MB, and one less thing that can be missing at 6am
    in a driveway. Falls back to rclone when credentials are absent.
    """
    _, key = _key_from_remote(remote)
    if args.dry_run:
        log("[dry-run] s3 put %s -> %s" % (local, key))
        return True
    ctype = ("application/gzip" if str(local).endswith(".gz")
             else "application/json" if str(local).endswith(".json")
             else None)
    try:
        return r2put.put(str(local), key, timeout=RCLONE_TIMEOUT_S,
                         content_type=ctype)
    except Exception as e:
        log("s3 upload failed on %s: %s" % (os.path.basename(str(local)), e))
        return False


def copyto(local, remote, args):
    """Dispatch to whichever backend is actually usable here."""
    if getattr(args, "rclone", False):
        return rclone_copyto(local, remote, args)
    if r2put is not None and r2put.available():
        return s3_copyto(local, remote, args)
    return rclone_copyto(local, remote, args)


def rclone_copyto(local, remote, args):
    """One file up, via `rclone copyto` so the remote name is exact
    (`copy` would treat the destination as a directory).  rclone verifies
    size+checksum itself and is idempotent, which is what makes retrying
    a half-done session safe."""
    cmd = ["rclone", "copyto", str(local), remote]
    if args.dry_run:
        log("[dry-run] " + " ".join(cmd))
        return True
    try:
        r = subprocess.run(cmd, capture_output=True, text=True,
                           timeout=RCLONE_TIMEOUT_S)
    except FileNotFoundError:
        log("rclone not found on PATH (`pkg install rclone` on the deck)")
        return False
    except subprocess.TimeoutExpired:
        log("rclone timed out after %ds on %s" % (RCLONE_TIMEOUT_S, local))
        return False
    if r.returncode != 0:
        stderr = (r.stderr or "").strip()
        tail = stderr.splitlines()[-1] if stderr else "no stderr"
        log("rclone exit %d on %s: %s" % (r.returncode, local.name, tail))
        return False
    return True


def _sync_dir(path):
    """fsync a directory so a rename inside it survives power loss."""
    try:
        fd = os.open(str(path), os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def gzip_in_place(sdir, base):
    """<base> -> <base>.gz, atomically, leaving the original in place until
    the compressed copy is complete so an interrupted pass cannot destroy the
    only copy of a drive."""
    plain, gz = sdir / base, sdir / (base + ".gz")
    if gz.exists() or not plain.exists():
        return
    tmp = sdir / (base + ".gz.part")
    try:
        # fsync before the rename, and again on the directory after it. The
        # deck's power is cut by the ignition: without this, a shutdown
        # between the rename and the unlink can leave the compressed copy
        # unwritten and the original already gone -- losing the only copy of
        # a drive, which is the one outcome this whole file exists to avoid.
        with open(plain, "rb") as fin, gzip.open(tmp, "wb") as fout:
            shutil.copyfileobj(fin, fout, 1024 * 256)
            fout.flush()
            os.fsync(fout.fileno())
        tmp.replace(gz)
        _sync_dir(sdir)
        plain.unlink()
        log("%s: compressed %s -> %s.gz" % (sdir.name, base, base))
    except Exception as e:
        log("%s: could not compress %s: %s" % (sdir.name, base, e))
        try:
            tmp.unlink()
        except OSError:
            pass


def compress_raw(sdir):
    gzip_in_place(sdir, "raw.bin")


# ------------------------------------------------------------- session

def upload_session(sdir, meta, args):
    """Upload one closed session; raises UploadError on any failure.

    Handles the crash-shaped edge too: a session whose meta.json already
    says "uploaded" (we died between the meta upload and the local move)
    gets its meta.json re-pushed -- idempotent and cheap -- and is then
    moved without re-uploading the data files.
    """
    sid = sdir.name
    yy, mm = year_month(sid, meta, sdir)
    prefix = "%s:%s/drives/%s/%s/%s" % (args.remote, args.bucket, yy, mm, sid)
    up = meta.get("upload") or {}

    if up.get("state") != "uploaded":
        # tee.py gzips the CSVs on session close but leaves raw.bin plain --
        # it is the biggest file and compressing it there would stall the
        # acquisition loop. Do it here instead, where a slow second costs
        # nothing, and only once: a resumed upload finds the .gz already made.
        compress_raw(sdir)
        missing = [n for n in REQUIRED if not (sdir / n).exists()]
        if missing:
            raise UploadError("missing %s (session not closed cleanly?)"
                              % ", ".join(missing))
        names = list(REQUIRED) + [n for n in OPTIONAL if (sdir / n).exists()]
        for n in names:
            if not copyto(sdir / n, "%s/%s" % (prefix, n), args):
                raise UploadError("rclone failed on %s" % n)
        meta["upload"] = {
            "state": "uploaded",
            "uploaded_at": iso_now(),
            "attempts": int(up.get("attempts", 0)) + 1,
            "dest": prefix,
        }
        if not args.dry_run:
            write_meta(sdir / "meta.json", meta)
        # The commit marker, strictly last.
        if not copyto(sdir / "meta.json", prefix + "/meta.json", args):
            raise UploadError("rclone failed on meta.json (commit marker)")
    else:
        log("%s: already uploaded, re-pushing meta.json and moving" % sid)
        if not copyto(sdir / "meta.json", prefix + "/meta.json", args):
            raise UploadError("rclone failed on meta.json (commit marker)")

    if args.dry_run:
        log("[dry-run] would move %s -> %s" % (sdir, args.logdir / "uploaded"))
        return
    dest_root = args.logdir / "uploaded"
    dest_root.mkdir(exist_ok=True)
    dest = dest_root / sid
    n = 1
    while dest.exists():
        dest = dest_root / ("%s-dup%d" % (sid, n))
        n += 1
    shutil.move(str(sdir), str(dest))
    log("%s: uploaded to %s, moved to %s" % (sid, prefix, dest))


def record_failure(sdir, meta, err, args):
    """Exponential backoff, per session, persisted in meta.json so it
    survives an uploader restart and even a deck cold boot."""
    log("%s: FAILED: %s" % (sdir.name, err))
    if args.dry_run or not isinstance(meta, dict):
        return
    up = meta.get("upload") or {}
    attempts = int(up.get("attempts", 0)) + 1
    delay = min(BACKOFF_BASE_S * (2 ** (attempts - 1)), BACKOFF_CAP_S)
    meta["upload"] = {
        "state": "pending",
        "attempts": attempts,
        "last_error": str(err)[:300],
        "last_attempt": iso_now(),
        "next_after": int(time.time() + delay),
    }
    try:
        write_meta(sdir / "meta.json", meta)
    except OSError as e:
        log("%s: could not record failure state: %r" % (sdir.name, e))
    log("%s: attempt %d, next try in %ds" % (sdir.name, attempts, delay))


# -------------------------------------------------------------- orphans

# meta.json is written only by Session.close().  A tee that is killed --
# Termux swiped away, deck rebooted, process OOM-killed -- leaves a full
# directory of real data with no commit marker, and the uploader skips it
# FOREVER.  On 2026-08-24 eleven drives from 2026-08-23 were sitting on the
# deck in exactly that state, none of them ever going anywhere.
#
# Adopting them is safe as long as we never adopt the session the tee is
# writing RIGHT NOW.  THREE independent guards, because getting this wrong
# means stealing a live drive out from under the tee:
#   1. the tee's .active marker names a pid that is still a live tee, OR
#   2. a live process holds a file open under the directory (/proc -- the
#      tee runs as the same uid, so its fds are readable), OR
#   3. something in the directory was touched within ORPHAN_IDLE_S.
#
# Guard 1 exists because guards 2 and 3 both fail during Session.close():
# it closes every file descriptor BEFORE gzipping and writing meta.json, so
# mid-close a live session has no open fds -- and after the 2026-08-24
# freeze its files were already 600 s stale, sailing past guard 3 as well.
#
# ORPHAN_IDLE_S is deliberately longer than that observed 600 s freeze. A
# frozen tee is not a dead tee: it resumed and closed its session correctly
# once thawed, and adopting it would have destroyed a real drive.
ORPHAN_IDLE_S = 900.0
ACTIVE_MARKER = ".active"


def _tee_is_alive(pid):
    """True when this pid is a running tee.py.

    A pid alone is not enough -- pids are reused, and a recycled pid that
    happened to match would block adoption forever.  Checking the cmdline
    costs one read and removes the ambiguity.
    """
    try:
        with open("/proc/%d/cmdline" % pid, "rb") as f:
            return b"tee.py" in f.read()
    except (OSError, ValueError):
        return False                    # no such process, or not ours


def _active_pid(sdir):
    """The pid in a session's .active marker, or None."""
    try:
        with open(sdir / ACTIVE_MARKER, "r") as f:
            return int(f.read().strip())
    except (OSError, ValueError):
        return None


def _dirs_in_use(logdir):
    """Session directory names currently held open by any of our processes."""
    busy = set()
    root = str(logdir.resolve())
    try:
        pids = [p for p in os.listdir("/proc") if p.isdigit()]
    except OSError:
        # Failing open here used to be silent, which meant the strongest
        # guard could vanish without anyone knowing. Say so.
        log("WARNING: /proc unreadable; adoption is relying on .active and "
            "file age alone")
        return busy
    for pid in pids:
        fddir = "/proc/%s/fd" % pid
        try:
            names = os.listdir(fddir)
        except OSError:
            continue                    # not ours, or exited mid-scan
        for fd in names:
            try:
                target = os.readlink(os.path.join(fddir, fd))
            except OSError:
                continue
            if target.startswith(root + os.sep):
                rel = target[len(root) + 1:].split(os.sep)
                if rel:
                    busy.add(rel[0])
    return busy


def adopt_orphans(logdir):
    """Give abandoned-but-complete session dirs the meta.json they never got."""
    busy = _dirs_in_use(logdir)
    now = time.time()
    adopted = 0
    for sdir in sorted(logdir.iterdir()):
        if (not sdir.is_dir() or sdir.name == "uploaded"
                or (sdir / "meta.json").exists()):
            continue
        files = [f for f in sdir.iterdir() if f.is_file()]
        if not any(f.name.startswith("drive.csv") for f in files):
            continue                    # not a session dir at all
        pid = _active_pid(sdir)
        if pid is not None and _tee_is_alive(pid):
            continue                    # a LIVE tee owns this session
        if sdir.name in busy:
            continue                    # something still holds a file open
        mtimes = [f.stat().st_mtime for f in files]
        newest = max(mtimes)
        if now - newest < ORPHAN_IDLE_S:
            continue                    # too fresh to be sure it is dead
        # Session.close() is also what gzips the CSVs, so an orphan still has
        # them plain and would fail the REQUIRED check. Do that work here,
        # before the meta is written -- after which the directory is
        # indistinguishable from a cleanly closed one.
        start = _first_row_time(sdir)   # read BEFORE gzipping
        for base in ("drive.csv", "bursts.csv"):
            gzip_in_place(sdir, base)
        # Only claim the session once it really is complete. Writing
        # meta.json after a failed gzip marks it uploadable when it is not,
        # and every later pass then fails on the same missing file.
        if not (sdir / "drive.csv.gz").exists():
            log("%s: cannot adopt yet - drive.csv did not compress"
                % sdir.name)
            continue
        meta = {
            "session": sdir.name,
            # mtimes track when a file was last WRITTEN, so every one of them
            # is near the session END -- using the oldest as the start made
            # a 50-minute drive look like it lasted seconds. The first row's
            # own timestamp is the real answer.
            "t_start_unix": round(start if start is not None else newest, 3),
            "t_end_unix": round(newest, 3),
            "channels": [],
            "files": {"drive": "drive.csv.gz", "bursts": "bursts.csv.gz",
                      "raw": "raw.bin"},
            "adopted": True,            # the tee never closed this one
            "adopted_at": iso_now(),
            "upload": {"state": "pending"},
        }
        if start is not None:
            meta["start"] = time.strftime("%Y-%m-%dT%H:%M:%S",
                                          time.localtime(start))
        write_meta(sdir / "meta.json", meta)
        adopted += 1
        log("%s: adopted an unclosed session (tee died before close)"
            % sdir.name)
    return adopted


def _first_row_time(sdir):
    """t_unix of the first data row of drive.csv, or None.

    An adopted session has no meta.json to say when it began, and file
    mtimes only say when it ended.  The CSV knows.
    """
    for name, opener in (("drive.csv", open),
                         ("drive.csv.gz", lambda p: gzip.open(p, "rt"))):
        p = sdir / name
        if not p.exists():
            continue
        try:
            with opener(p) as f:
                f.readline()                    # header
                first = f.readline().split(",")[0]
            return float(first)
        except (OSError, ValueError, IndexError):
            return None
    return None


# ----------------------------------------------------------------- pass

def run_pass(args):
    """One scan of the log dir.  Returns (uploaded, failed) counts."""
    logdir = args.logdir
    if not logdir.is_dir():
        log("log dir %s does not exist; nothing to do" % logdir)
        return 0, 0
    # --dry-run must be exactly that. adopt_orphans gzips CSVs, unlinks the
    # originals and writes meta.json -- all real, irreversible changes to the
    # log directory that a dry run has no business making.
    if not args.dry_run:
        try:
            adopt_orphans(logdir)
        except OSError as e:
            log("orphan scan failed: %r" % (e,))
    sessions = sorted(
        d for d in logdir.iterdir()
        if d.is_dir() and d.name != "uploaded" and (d / "meta.json").exists()
    )
    if not sessions:
        return 0, 0

    log("%d closed session(s) pending" % len(sessions))
    hold = not args.dry_run
    if hold:
        wake_lock(True)
    ok = fail = 0
    try:
        now = time.time()
        for sdir in sessions:
            meta = read_meta(sdir / "meta.json")
            if meta is None:
                fail += 1               # stays in place; a human must look
                continue
            next_after = (meta.get("upload") or {}).get("next_after", 0)
            if now < next_after:
                log("%s: backing off for another %ds"
                    % (sdir.name, int(next_after - now)))
                continue
            try:
                upload_session(sdir, meta, args)
                ok += 1
            except UploadError as e:
                record_failure(sdir, meta, e, args)
                fail += 1
            except Exception as e:      # one bad session must not end the pass
                record_failure(sdir, meta, repr(e), args)
                fail += 1
    finally:
        if hold:
            wake_lock(False)
    return ok, fail


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Upload closed drive sessions to Cloudflare R2 "
                    "(home WiFi only unless --force).")
    ap.add_argument("--logdir", default=LOG_DIR,
                    help="session directory (default %s)" % LOG_DIR)
    ap.add_argument("--remote", default=R2_REMOTE,
                    help="rclone remote name (default %s)" % R2_REMOTE)
    ap.add_argument("--bucket", default=R2_BUCKET,
                    help="R2 bucket (default %s)" % R2_BUCKET)
    ap.add_argument("--force", action="store_true",
                    help="skip the home-SSID gate (laptop testing)")
    ap.add_argument("--loop", action="store_true",
                    help="poll forever instead of one pass")
    ap.add_argument("--poll", type=int, default=POLL_S,
                    help="loop poll interval in seconds (default %d)" % POLL_S)
    ap.add_argument("--rclone", action="store_true",
                    help="force the rclone backend; default is the stdlib "
                         "S3 client when R2 credentials are present")
    ap.add_argument("--dry-run", action="store_true",
                    help="print rclone commands; write and move nothing")
    args = ap.parse_args(argv)
    args.logdir = pathlib.Path(os.path.expanduser(args.logdir))

    if not args.loop:
        if not home_gate(args):
            return 0                    # not home is not an error
        ok, fail = run_pass(args)
        log("pass done: %d uploaded, %d failed" % (ok, fail))
        return 1 if fail else 0

    log("loop mode: polling every %ds (logdir %s)" % (args.poll, args.logdir))
    try:
        while True:
            try:
                if home_gate(args):
                    run_pass(args)
            except Exception as e:
                # A wedged uploader that stops looping uploads nothing
                # forever; log and keep going.
                log("pass crashed: %r" % (e,))
            time.sleep(args.poll)
    except KeyboardInterrupt:
        log("interrupted; exiting")
        return 0


if __name__ == "__main__":
    sys.exit(main())
