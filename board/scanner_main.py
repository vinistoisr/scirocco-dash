"""
code.py -- VAG UDS DID discovery scanner with e-ink status.

Hardware:
    Adafruit Feather M4 CAN Express
    Adafruit 2.13" tri-color eInk FeatherWing, 250x122, SSD1680

Car:
    2009 VW Scirocco 2.0 TSI (CAWB), Bosch MED17.5

What it does:
    1. Brings up the CAN transceiver and the e-ink panel.
    2. Probes the bus to work out whether the ECU speaks generic OBD-II
       and whether it speaks UDS (service 0x22).
    3. If UDS: walks the whole DID space with service 0x22 and records
       every identifier that answers, streaming results to the serial
       console and (if the filesystem is writable) to a CSV.
    4. If KWP2000/TP2.0 instead (this car): opens a TP 2.0 channel via
       tp20.py, scans VCDS measuring blocks 001-255, flags any block with
       a pressure-formula field, then live-polls those so a throttle blip
       identifies the real boost signal.

READ ONLY. Nothing here writes to the ECU.

Run it parked with the engine idling. Results stream as they are found, so
stopping partway still leaves you with usable data.

--- e-ink refresh note ---
Tri-color panels are slow: a full refresh takes roughly 15 seconds and they
do not support partial refresh. The panel is only updated on a real state
change, and refreshes are rate limited by SECONDS_PER_FRAME. Detailed
progress goes to the serial console, which is instant.
"""

import time
import board
import digitalio
import displayio
import terminalio
from adafruit_display_text import label

import uds
import tp20
import hunt

# CircuitPython 9 moved FourWire out of displayio and replaced
# display.show() with the root_group property. Support both so this runs
# on the old 7.1 firmware and on current builds without edits.
try:
    from fourwire import FourWire
except ImportError:
    FourWire = displayio.FourWire


def set_root_group(display, group):
    try:
        display.root_group = group
    except AttributeError:
        display.show(group)

# ------------------------------------------------------------------ config

DID_START = 0x0000
DID_END = 0xFFFF

# Per-request timeout. Unsupported DIDs normally get a fast negative
# response, so this only bites on identifiers the ECU ignores entirely.
# Lower = faster scan, but too low starts missing slow replies.
SCAN_TIMEOUT = 0.08

# Some DIDs only exist inside an extended diagnostic session (0x10 0x03).
# Leaving this off keeps the ECU in its normal default session, which is
# the conservative choice. Turn it on for a second pass if the first pass
# does not find the boost channels.
USE_EXTENDED_SESSION = False

PROBE_ONLY = False          # True = report protocol and stop, no scan
PROGRESS_PCT = 10           # e-ink progress update interval
LOGFILE = "/did_scan.csv"

# If the text comes out sideways or upside down, try 0, 90, 180, 270.
ROTATION = 270
SECONDS_PER_FRAME = 25      # minimum seconds between e-ink refreshes

# Panels tried in order. adafruit_ssd1680 here is a backport of the current
# upstream driver onto CircuitPython 7's displayio; the stock Dec 2021 .mpy
# predates this FeatherWing and never sends set_current_column /
# set_current_row, so its RAM pointer is never rewound and each refresh
# lands further across the panel than the last.
PANELS = (
    # module name,        class,     width, height, colstart
    ("adafruit_ssd1680", "SSD1680",   250,   122,     8),
    ("adafruit_il0373",  "IL0373",    212,   104,     0),
)

# ------------------------------------------------------------------ display


class StatusScreen:
    """Status panel for the tri-color eInk FeatherWing.

    set() records what should be shown and prints it to serial immediately.
    service() pushes it to the panel when the panel is ready, and returns
    straight away when it is not, so the scan never blocks on the display.
    """

    def __init__(self):
        self.display = None
        self.driver = None
        self.colour = False
        self._pending = None
        self._shown = None

        # The wing also carries an SD slot (CS = D5) and an SRAM chip
        # (CS = D6) on the same SPI bus. displayio knows about neither, and
        # a chip select left floating low will fight for the bus. Park both.
        for pin in (board.D5, board.D6):
            try:
                cs = digitalio.DigitalInOut(pin)
                cs.switch_to_output(True)
            except Exception:
                pass

        try:
            displayio.release_displays()
            spi = board.SPI()
            # FeatherWing: ECS = D9, D/C = D10. RST and BUSY are not
            # connected unless you have bridged the solder pads.
            bus = FourWire(
                spi,
                command=board.D10,
                chip_select=board.D9,
                reset=None,
                baudrate=1000000,
            )
            time.sleep(1)
            self.display = self._try_panels(bus)
        except Exception as e:
            print("e-ink: bus setup failed (%s)" % e)

        if self.display is None:
            print("e-ink: unavailable -- serial output only")
            return

        self._build()
        print(
            "e-ink: %s ready, logical %dx%d"
            % (self.driver, self.display.width, self.display.height)
        )

    def _try_panels(self, bus):
        for modname, clsname, w, h, colstart in PANELS:
            try:
                mod = __import__(modname)
                cls = getattr(mod, clsname)
            except Exception as e:
                print("e-ink: %s not available (%s)" % (modname, e))
                continue

            base = {
                "width": w,
                "height": h,
                "rotation": ROTATION,
                "busy_pin": None,
            }
            # Older driver builds accept fewer keywords than current ones.
            # Walk from richest to plainest and report exactly what is
            # rejected, so a stale library shows up as a message not a
            # silent fallback to the wrong panel.
            ladder = (
                ("full", {"highlight_color": 0xFF0000, "colstart": colstart,
                          "seconds_per_frame": SECONDS_PER_FRAME}),
                ("no seconds_per_frame", {"highlight_color": 0xFF0000,
                                          "colstart": colstart}),
                ("no colstart", {"highlight_color": 0xFF0000,
                                 "seconds_per_frame": SECONDS_PER_FRAME}),
                ("colour only", {"highlight_color": 0xFF0000}),
                ("mono + colstart", {"colstart": colstart}),
                ("bare", {}),
            )
            for tag, extra in ladder:
                if not colstart and "colstart" in extra:
                    continue
                # note: CircuitPython has no multiple ** unpacking, merge first
                kwargs = dict(base)
                kwargs.update(extra)
                try:
                    d = cls(bus, **kwargs)
                    # This driver build accepts `rotation` and then ignores
                    # it, leaving the frame in the panel's native portrait.
                    # Force the property and report whether it took.
                    try:
                        print("e-ink: rotation before=%s size=%dx%d"
                              % (d.rotation, d.width, d.height))
                        d.rotation = ROTATION
                        print("e-ink: rotation after =%s size=%dx%d"
                              % (d.rotation, d.width, d.height))
                    except Exception as re:
                        print("e-ink: rotation property failed (%s)" % re)
                    print("e-ink: %s accepted [%s], native %dx%d -> logical %dx%d"
                          % (clsname, tag, w, h, d.width, d.height))
                    self.driver = "%s %dx%d" % (clsname, d.width, d.height)
                    self.colour = "highlight_color" in extra
                    return d
                except TypeError as e:
                    print("e-ink: %s rejected [%s] -> %s" % (clsname, tag, e))
                except Exception as e:
                    print("e-ink: %s init error [%s] -> %s: %s"
                          % (clsname, tag, type(e).__name__, e))
                    break
        return None

    def _build(self):
        w, h = self.display.width, self.display.height
        self.group = displayio.Group()

        bg = displayio.Bitmap(w, h, 1)
        pal = displayio.Palette(1)
        pal[0] = 0xFFFFFF
        self.group.append(displayio.TileGrid(bg, pixel_shader=pal))

        # headline in the panel's red ink
        self.phase_lbl = label.Label(
            terminalio.FONT, text="", color=0xFF0000, scale=2, x=4, y=14
        )
        self.group.append(self.phase_lbl)

        self.detail_lbls = []
        y = 36
        while y < h - 6 and len(self.detail_lbls) < 6:
            lbl = label.Label(terminalio.FONT, text="", color=0x000000, x=4, y=y)
            self.group.append(lbl)
            self.detail_lbls.append(lbl)
            y += 13

        # terminalio.FONT is 6px wide, so this is how much actually fits
        self.cols = max(8, (w - 8) // 6)
        set_root_group(self.display, self.group)

    def set(self, phase, *details):
        self._pending = (phase, tuple(details))
        print("[%s] %s" % (phase, "  |  ".join(details)))

    def service(self, force=False):
        if self.display is None:
            return False
        if self._pending is None or self._pending == self._shown:
            return False
        if self.display.time_to_refresh > 0:
            if not force:
                return False
            while self.display.time_to_refresh > 0:
                time.sleep(0.2)

        phase, details = self._pending
        self.phase_lbl.text = phase[: self.cols // 2]
        for i, lbl in enumerate(self.detail_lbls):
            lbl.text = details[i][: self.cols] if i < len(details) else ""
        try:
            self.display.refresh()
        except RuntimeError:
            return False
        self._shown = self._pending
        return True


# ------------------------------------------------------------------ helpers

try:
    import neopixel

    _px = neopixel.NeoPixel(board.NEOPIXEL, 1, brightness=0.2, auto_write=True)
except Exception:
    _px = None

BLUE = (0, 0, 255)
AMBER = (255, 140, 0)
GREEN = (0, 255, 0)
RED = (255, 0, 0)
PURPLE = (160, 0, 255)


def pixel(color):
    if _px is not None:
        _px.fill(color)


def hexstr(b):
    return "".join("%02X" % x for x in b)


def open_log(path):
    """CIRCUITPY is read-only to the board unless boot.py remounts it.
    Returns a file handle, or None if we can only use serial."""
    try:
        f = open(path, "a")
        f.write("# --- scan start ---\n")
        f.write("did,status,detail\n")
        f.flush()
        return f
    except OSError:
        return None


def fmt_secs(s):
    s = int(s)
    return "%d:%02d" % (s // 60, s % 60)


def park(screen, colour):
    """Show the final state and stop."""
    screen.service(force=True)
    pixel(colour)
    while True:
        time.sleep(1)


# ------------------------------------------------------------------ kwp/tp2.0


def open_mb_log(path):
    try:
        f = open(path, "a")
        f.write("# --- mb scan start ---\n")
        f.write("block,raw,decoded\n")
        f.flush()
        return f
    except OSError:
        return None


def run_kwp(screen, bus):
    """TP 2.0 / KWP2000 path: scan VCDS measuring blocks for boost.

    If TP 2.0 turns out to be absent, hunts for UDS on alternative CAN
    addressing instead and RETURNS a new uds.Bus for it; the caller then
    runs the normal DID scan on that. Otherwise never returns: ends parked
    on an error screen, or in a live loop streaming pressure blocks.
    """
    screen.set("KWP TP2.0", "obd-ii ok, uds no", "opening channel")
    screen.service()

    # tp20 manages its own listeners on 0x201/0x300; the 0x7E8 one is done.
    bus.listener.deinit()

    ids = tp20.sniff(bus.can, 1.0)
    if ids:
        print("sniff: bus traffic:",
              ", ".join("0x%03X x%d" % (i, c) for i, c in sorted(ids.items())))
    else:
        print("sniff: bus silent (diag CAN behind gateway, as expected)")

    ch = tp20.Channel(bus.can)
    try:
        tx, rx = ch.connect()
    except tp20.TP20Error as e:
        print("tp20: engine setup failed (%s), sweeping all addresses" % e)
        hits = tp20.probe_dests(bus.can)
        print("tp20: sweep result:",
              ", ".join("%02X->0x%03X" % h for h in hits) if hits else "nothing speaks tp2.0 here")
        if not hits:
            # No TP2.0 anywhere: transition-era car. Manufacturer UDS is
            # probably on 29-bit or non-7E0 11-bit IDs. Go find it.
            screen.set("UDS HUNT", "no tp2.0 here", "sweeping alt ids", "watch serial")
            screen.service()
            uhits = hunt.sweep(bus.can)
            if not uhits:
                screen.set(
                    "DEAD END",
                    "port speaks eobd only",
                    "no uds, no tp2.0",
                    "see serial log",
                )
                park(screen, RED)
            # prefer the VAG scheme's engine address 01, else first responder
            pick = None
            for h in uhits:
                if h[2] and h[0] == 0x17FC0001:
                    pick = h
                    break
            if pick is None:
                pick = uhits[0]
            req_id, resp_id, ext, first = pick
            print("hunt: using req 0x%X resp 0x%X extended=%s (first reply %s)"
                  % (req_id, resp_id, ext, hexstr(first)))
            screen.set(
                "ALT UDS",
                "req 0x%X" % req_id,
                "resp 0x%X" % resp_id,
                "starting did scan",
            )
            return uds.Bus(req_id=req_id, resp_id=resp_id, can=bus.can, extended=ext)
        screen.set(
            "TP20 FAIL",
            str(e)[:34],
            "sweep: " + ",".join("%02X" % d for d, _ in hits),
            "see serial log",
        )
        park(screen, RED)
    print("tp20: channel open, tester tx 0x%03X rx 0x%03X" % (tx, rx))

    ident = None
    try:
        ident = tp20.ecu_ident(ch)
    except tp20.TP20Error as e:
        print("tp20: ident failed (%s)" % e)
    print("tp20: ecu ident:", ident)

    try:
        if not ch.connected:
            ch.connect()
        print("tp20: session 89 ->", tp20.start_session(ch))
    except tp20.TP20Error as e:
        # not fatal, measuring blocks usually work in the default session
        print("tp20: session failed (%s)" % e)
        try:
            ch.connect()
        except tp20.TP20Error as e2:
            screen.set("TP20 LOST", str(e2)[:34], "see serial log")
            park(screen, RED)

    screen.set("MB SCAN", (ident or "no ident")[:34], "blocks 001-255", "watch serial")
    screen.service()

    log = open_mb_log("/mb_scan.csv")
    print("mb scan: logging to %s" % ("/mb_scan.csv" if log else "serial only"))

    found = []
    boost = []
    t0 = time.monotonic()
    for n in range(1, 256):
        if not ch.connected:
            try:
                ch.connect()
            except tp20.TP20Error as e:
                print("mb scan: reconnect failed at block %03d (%s)" % (n, e))
                break
        try:
            data = tp20.read_block(ch, n, timeout=0.4)
        except tp20.TP20Error as e:
            print("mb %03d: transport error (%s)" % (n, e))
            continue
        if data is None:
            continue
        fields = tp20.decode_fields(data)
        line = "%03d,%s,%s" % (n, hexstr(data), " | ".join(fields))
        print("MB " + line)
        if log:
            log.write(line + "\n")
            log.flush()
        found.append(n)
        if tp20.has_pressure(data):
            boost.append(n)

    elapsed = time.monotonic() - t0
    print("=" * 46)
    print("mb scan: %d blocks answered, %d with pressure fields, %s"
          % (len(found), len(boost), fmt_secs(elapsed)))
    print("blocks:", " ".join("%03d" % n for n in found))
    print("pressure blocks:", " ".join("%03d" % n for n in boost))
    print("=" * 46)
    if log:
        log.write("# done,%d found,pressure %s\n"
                  % (len(found), " ".join(str(n) for n in boost)))
        log.flush()
        log.close()

    poll = boost[:3]
    if not poll:
        screen.set(
            "MB DONE",
            "%d blocks, no mbar" % len(found),
            "see mb_scan.csv" if log else "see serial log",
            "need manual diff",
        )
        park(screen, PURPLE)

    screen.set(
        "MB LIVE",
        "polling " + " ".join("%03d" % n for n in poll),
        "rev the engine,",
        "the value that moves",
        "with throttle = boost",
    )
    screen.service()
    pixel(GREEN)

    # Idle vs a throttle blip makes the real boost signal obvious in the
    # stream. Reconnects if the ECU drops the channel.
    while True:
        vals = []
        for n in poll:
            try:
                if not ch.connected:
                    ch.connect()
                data = tp20.read_block(ch, n, timeout=0.4)
            except tp20.TP20Error:
                vals.append("%03d:tp20err" % n)
                time.sleep(0.5)
                continue
            if data is None:
                vals.append("%03d:gone" % n)
                continue
            p0 = tp20.pressure_value(data, 0)
            p1 = tp20.pressure_value(data, 1)
            s = "%03d:%s" % (n, "%.0f" % p0 if p0 is not None else "?")
            if p1 is not None:
                s += "/%.0f" % p1
            vals.append(s + " mbar")
        print("live  " + "  ".join(vals))
        time.sleep(0.15)


# ------------------------------------------------------------------ main

pixel(BLUE)
screen = StatusScreen()
screen.set("BOOT", "feather m4 can", "uds did scanner", screen.driver or "no panel")
screen.service(force=True)

screen.set("CAN INIT", "500 kbit/s", "req 7E0 / resp 7E8")
pixel(AMBER)
try:
    bus = uds.Bus()
except Exception as e:
    screen.set("CAN FAIL", str(e)[:34])
    park(screen, RED)

screen.set("PROBING", "checking obd-ii", "checking uds 22F190")
screen.service()

info = bus.probe()
print("probe:", info)

if not info["obd2"] and not info["uds"]:
    # Nothing answered at all. Almost always wiring, termination, or the
    # transceiver enable pins rather than a protocol problem.
    screen.set(
        "NO BUS",
        "no reply from ecu",
        "check CANH pin6",
        "check CANL pin14",
        "check GND pin4",
        "ign on? term off?",
    )
    park(screen, RED)

if not info["uds"]:
    # F190 silence alone does not prove the ECU is not UDS -- some ECUs
    # simply do not expose the VIN DID. Poke the physical ID with a mode 01
    # request and UDS session control before switching transports.
    r = bus.request(bytes([0x01, 0x0C]), timeout=0.5)
    print("recheck: mode01 rpm on 7E0 ->", hexstr(r) if r else "no reply")
    r = bus.request(bytes([0x10, 0x01]), timeout=0.5)
    print("recheck: uds 10 01 on 7E0 ->", hexstr(r) if r else "no reply")
    if r is not None and len(r) >= 1 and r[0] == 0x50:
        print("recheck: session control works -- ECU is UDS, VIN DID absent")
        info["uds"] = True
        info["vin"] = None

if not info["uds"]:
    # Bus is alive but neither service 0x22 nor 0x10 answers on 0x7E0.
    # Try KWP2000/TP2.0; failing that, run_kwp hunts for UDS on alternative
    # addressing and returns a Bus for it so the DID scan below still runs.
    bus = run_kwp(screen, bus)
    st, val = bus.read_did(0xF190, timeout=0.6)
    if st == "ok":
        info["vin"] = "".join(chr(b) for b in val if 32 <= b < 127)
    print("alt uds: F190 ->", st,
          hexstr(val) if st == "ok" else val)
    info["uds"] = True

vin = info["vin"] or "?"
screen.set("UDS OK", "vin " + vin[-11:], "service 22 works")
screen.service(force=True)
pixel(GREEN)
print("VIN:", vin)

if PROBE_ONLY:
    screen.set("PROBE DONE", "vin " + vin[-11:], "PROBE_ONLY=False", "to run full scan")
    park(screen, GREEN)

if USE_EXTENDED_SESSION:
    print("extended session:", bus.start_extended_session())

log = open_log(LOGFILE)
if log is None:
    print("filesystem read-only -- serial output only (see boot.py)")
else:
    print("logging to", LOGFILE)
    log.write("# vin,%s\n" % vin)

total = DID_END - DID_START + 1
step = max(1, total // max(1, (100 // PROGRESS_PCT)))
found = 0
locked = 0
t0 = time.monotonic()
last_tp = t0

screen.set("SCANNING", "0x%04X-0x%04X" % (DID_START, DID_END), "starting...")
screen.service()

try:
    for i, did in enumerate(range(DID_START, DID_END + 1)):
        status, val = bus.read_did(did, timeout=SCAN_TIMEOUT)

        if status == "ok":
            found += 1
            line = "%04X,ok,%s" % (did, hexstr(val))
            print("FOUND " + line)
            if log:
                log.write(line + "\n")
                log.flush()
        elif status == "nrc" and val in (0x33, 0x22):
            # exists but gated: worth knowing about
            locked += 1
            line = "%04X,nrc%02X,%s" % (did, val, uds.NRC.get(val, "?"))
            print("LOCK  " + line)
            if log:
                log.write(line + "\n")
                log.flush()

        if USE_EXTENDED_SESSION and time.monotonic() - last_tp > 2.0:
            bus.tester_present()
            last_tp = time.monotonic()

        if i % step == 0 and i > 0:
            elapsed = time.monotonic() - t0
            eta = elapsed / i * (total - i)
            screen.set(
                "SCANNING",
                "at 0x%04X  %d%%" % (did, 100 * i // total),
                "found %d  locked %d" % (found, locked),
                "eta %s" % fmt_secs(eta),
            )

        screen.service()

except KeyboardInterrupt:
    print("\ninterrupted by user")

elapsed = time.monotonic() - t0
if log:
    log.write("# done,found %d,locked %d,%ds\n" % (found, locked, int(elapsed)))
    log.flush()
    log.close()

print("=" * 46)
print("scan complete: %d found, %d locked, %s elapsed" % (found, locked, fmt_secs(elapsed)))
print("=" * 46)

screen.set(
    "SCAN DONE",
    "found %d dids" % found,
    "locked %d" % locked,
    "took %s" % fmt_secs(elapsed),
    "see did_scan.csv" if log else "serial log only",
)
park(screen, PURPLE)
