"""
bodyscan.py -- find out what modules this car actually has, and where they
keep their body-electronics state.

Turn signals, headlights, door contacts, fuel level and selected gear are
NOT in the engine ECU. On PQ35 they live in other modules reachable through
the same OBD connector over TP 2.0.

WHY THIS DOES NOT TRUST THE LABEL FILES
    The Ross-Tech label files give an address -> module table, and on this
    car it is WRONG. Address 0x09, which should be Central Electrics,
    answered with ident "1K0909144E ... EPS_ZFLS Kl. 185" -- that part
    number is the electric power steering controller. So nothing is
    assumed: every address is asked what it is, and the map is built from
    the answers.

TWO PHASES, in one run:
    Phase 1  connect to every VAG address and read its ident string. Fast
             (a second or two each) and it produces the real address map.
    Phase 2  block-scan only the addresses that answered, so no time is
             spent sweeping 40 blocks on modules that are not fitted.

Set MODE to the string "watch" for the follow-up pass: it re-reads a short
list of blocks a few times a second and prints ONLY what changed, with
bit-level detail. Flip the left indicator and the changed bit IS the left
indicator. That is how the mapping actually gets nailed down.

*** READ ONLY. *** The only KWP services used are 0x21
(readDataByLocalIdentifier) and 0x1A (identification). Nothing here can
write, code, adapt or actuate; services 0x2C/0x2E/0x30/0x31/0x3B and
security access are never sent. That matters more than usual on body
modules, where a stray actuator command moves real locks and windows.

PRACTICALITIES
  * Needs the ignition ON. Engine running is better -- a long scan on
    ignition-only drains the battery, and some modules drop the channel
    when they see low voltage.
  * Channels are closed cleanly after each module (A8), so nothing is left
    holding a gateway slot.
"""

import time
import board
import digitalio
import displayio
import terminalio
from adafruit_display_text import label

import uds
import tp20
import usb_cdc

try:
    from fourwire import FourWire
except ImportError:
    FourWire = displayio.FourWire

# ----------------------------------------------------------------- output
# boot.py disables the USB console so RealDash gets the data port at IF0,
# which means the built-in print() writes to a port nobody can open. A scan
# whose results are invisible is useless, so print is redirected down
# usb_cdc.data -- the stream the head unit's bridge app already reads.
# write_timeout matters: an unread CDC buffer otherwise blocks write()
# forever and the scan hangs part-way through a module.
_out = usb_cdc.data
if _out is not None:
    try:
        _out.write_timeout = 0.5
    except Exception:
        pass

_builtin_print = print


def print(*args, **kw):
    line = " ".join(str(a) for a in args) + "\r\n"
    if _out is None:
        _builtin_print(line, end="")
        return
    try:
        _out.write(line.encode("utf-8"))
    except Exception:
        pass


# ----------------------------------------------------------------- config

MODE = "watch"           # "scan" | "sweep" | "watch"

# "scan" uses the curated VAG list. That list is how the ride-height module
# got missed on the first pass: it has no 0x34 (Level Control) and no 0x13,
# among others. "sweep" walks the WHOLE 7-bit address space and asks each
# one for its ident, which is the only way to be sure what this car has --
# especially since the addresses that did answer disagreed with the label
# files (0x09 identified itself as the power steering controller).
SWEEP_FIRST = 0x00
SWEEP_LAST = 0x7F

# Steering column (J527 -- steering angle) and ABS/ESP (yaw, lateral g).
# 0x03 answered a channel but returned NOTHING for blocks 1..40, so this
# run goes all the way to 255: plenty of VAG modules keep their
# interesting groups well above 40.
SCAN_ADDRESSES = (0x2A, 0x03)

BLOCK_FIRST = 1
BLOCK_LAST = 255
READ_TIMEOUT = 0.35

# watch mode: (address, block) pairs to sample continuously. Fill these in
# from the scan results -- watching everything is too slow to see a toggle.
# Module 02 is a DSG (ident "02E300051R ... LGSG DSG AG6"). Block 003 field 1
# came back as formula 17 = two ASCII chars, reading " P" with the car in
# Park -- so the selected gear is a literal LETTER, not a number. These are
# the blocks that should move as the lever does.
# DCC / adaptive chassis, address 0x0C. Block 009 is three level sensors
# (~2.5 V at mid-travel) plus one input at the 5 V rail; block 011 is the
# four damper channels, identical while the car is at rest. Keep this list
# SHORT -- each block costs a round trip, and a bounce has to be caught
# while the corner is still down.
# Steering column J527 at 0x2A. All 7 blocks it answers, because with the
# wheels straight the angle field reads zero and is indistinguishable from
# padding -- only turning the wheel reveals which field moves.
# The steering column module 0x2A does NOT carry steering angle -- proven by
# a lock-to-lock sweep that left all 7 of its blocks static. The electric
# power steering at 0x09 is the better candidate: EPS cannot function
# without knowing steering angle and torque, and its blocks are full of
# signed-looking f81/f93 values that mean nothing standing still.
WATCH = (
    (0x09, 5), (0x09, 7), (0x09, 11), (0x09, 1), (0x09, 9), (0x09, 3),
)
WATCH_PERIOD = 0.05
HEARTBEAT_S = 5.0        # silence must not look the same as working


# ----------------------------------------------------------------- display

def make_display():
    for pin in (board.D5, board.D6):
        try:
            digitalio.DigitalInOut(pin).switch_to_output(True)
        except Exception:
            pass
    try:
        displayio.release_displays()
        bus = FourWire(board.SPI(), command=board.D10, chip_select=board.D9,
                       reset=None, baudrate=1000000)
        time.sleep(1)
        import adafruit_ssd1680
        return adafruit_ssd1680.SSD1680(
            bus, width=250, height=122, rotation=270, busy_pin=None,
            highlight_color=0xFF0000, colstart=8, seconds_per_frame=1)
    except Exception:
        return None


class Screen:
    def __init__(self):
        self.d = make_display()
        self.last = None
        if self.d is None:
            return
        w, h = self.d.width, self.d.height
        self.g = displayio.Group()
        bg = displayio.Bitmap(w, h, 1)
        pal = displayio.Palette(1)
        pal[0] = 0xFFFFFF
        self.g.append(displayio.TileGrid(bg, pixel_shader=pal))
        self.title = label.Label(terminalio.FONT, text="", color=0xFF0000,
                                 scale=2, x=4, y=14)
        self.g.append(self.title)
        self.lines = []
        y = 36
        while y < h - 6 and len(self.lines) < 6:
            lb = label.Label(terminalio.FONT, text="", color=0x000000, x=4, y=y)
            self.g.append(lb)
            self.lines.append(lb)
            y += 13
        try:
            self.d.root_group = self.g
        except AttributeError:
            self.d.show(self.g)

    def show(self, title, *rows):
        if self.d is None or (title, rows) == self.last:
            return
        self.title.text = title[:20]
        for i, lb in enumerate(self.lines):
            lb.text = rows[i][:40] if i < len(rows) else ""
        try:
            if self.d.time_to_refresh > 0:
                return          # never block the scan on the e-ink
            self.d.refresh()
            self.last = (title, rows)
        except Exception:
            pass


# ----------------------------------------------------------------- helpers

def hexs(b):
    return "".join("%02X" % x for x in b)


def bits(a, b):
    """Formula 16 fields are two bytes of flags. Print them as bits, MSB
    first, because that is how a human compares two samples by eye."""
    return "{:08b} {:08b}".format(a, b)


def describe(data):
    """Decode a block into readable fields, keeping bitfields as bit strings
    since those are what door/light states arrive in."""
    out = []
    for i in range(0, len(data) - 2, 3):
        fid, a, b = data[i], data[i + 1], data[i + 2]
        if fid == 16:
            out.append("f16[%s]" % bits(a, b))
        else:
            out.append(tp20._field(fid, a, b))
    return " | ".join(out)


# canio permits ONE all-matches listener at a time, so every Channel built
# here SHARES one. Building a Listener per Channel is what killed the first
# run: "Already have all-matches listener", raised on the second module.
SHARED = {"listener": None}


def shared_listener(can):
    if SHARED["listener"] is None:
        SHARED["listener"] = can.listen(timeout=0.02)
    return SHARED["listener"]


def drop(ch):
    """Release a module's channel, leaving the shared listener alone."""
    if ch is None:
        return
    try:
        ch.disconnect()
    except Exception:
        pass


def open_channel(can, addr, tries=2, attempts=4):
    """Open a channel to one module, or None.

    `attempts` matters more than it looks. Channel.connect() defaults to
    FOUR handshake attempts, because a channel left half-open by a previous
    run has to be torn down and retried -- essential for the modules we
    actually talk to. But when PROBING, most addresses are simply empty, and
    paying 0.2 s + 4 x 0.6 s twice over for each one turned a 128-address
    sweep into ~20 minutes. A module that is present answers on the first
    attempt, so probing passes attempts=1 and the sweep drops to ~2 minutes.
    The block-scan phase re-opens with the full retry budget.
    """
    for _ in range(tries):
        ch = tp20.Channel(can, dest=addr, listener=shared_listener(can))
        try:
            ch.connect(timeout=0.6, tries=1, attempts=attempts)
            return ch
        except tp20.TP20Error:
            drop(ch)
    return None


# ----------------------------------------------------------------- modes

def identify(can, screen):
    """Phase 1: ask every address what it is. Returns [(addr, ident)]."""
    print(">>>BODYSCAN-BEGIN<<<")
    print("=" * 64)
    print("PHASE 1 -- who is actually on this bus")
    print("=" * 64)
    found = []
    for addr in SCAN_ADDRESSES:
        guess = tp20.ADDRESS_NAMES.get(addr, "?")
        screen.show("IDENT %02X" % addr, guess[:34])
        ch = open_channel(can, addr, tries=1, attempts=1)
        if ch is None:
            print("  %02X  --  no channel" % addr)
            continue
        ident = None
        try:
            ident = tp20.ecu_ident(ch)
        except tp20.TP20Error:
            pass
        print("  %02X  ANSWERED  guess=%-22s ident=%s"
              % (addr, guess, ident or "(no ident)"))
        found.append((addr, ident))
        drop(ch)
        time.sleep(0.2)
    print("")
    print("  %d module(s) answered" % len(found))
    return found


def sweep(can, screen):
    """Ident every address in the 7-bit space, then block-scan the hits.

    Slower than scan() but it assumes nothing. An address that does not
    exist costs one failed channel setup (~1.2 s), so the whole space is a
    few minutes.
    """
    global SCAN_ADDRESSES
    SCAN_ADDRESSES = tuple(range(SWEEP_FIRST, SWEEP_LAST + 1))
    scan(can, screen)


def scan(can, screen):
    found = identify(can, screen)
    print("")
    print("=" * 64)
    print("PHASE 2 -- measuring blocks, read only (KWP 0x21)")
    print("=" * 64)
    summary = []
    for addr, ident in found:
        screen.show("SCAN %02X" % addr, (ident or "?")[:34], "blocks...")
        ch = open_channel(can, addr)
        if ch is None:
            print("")
            print("-- %02X went away between phases" % addr)
            continue
        print("")
        print("-" * 64)
        print("MODULE %02X   ident: %s" % (addr, ident or "?"))
        print("-" * 64)
        hits = 0
        for n in range(BLOCK_FIRST, BLOCK_LAST + 1):
            if not ch.connected:
                try:
                    ch.connect(timeout=0.6, tries=1)
                except tp20.TP20Error:
                    break
            try:
                d = tp20.read_block(ch, n, timeout=READ_TIMEOUT)
            except tp20.TP20Error:
                continue
            if not d:
                continue
            hits += 1
            print("  %02X/%03d %s  %s" % (addr, n, hexs(d), describe(d)))
            if hits % 8 == 0:
                screen.show("SCAN %02X" % addr, (ident or "?")[:34],
                            "block %03d" % n, "%d found" % hits)
        print("  -> %d blocks answered on %02X" % (hits, addr))
        summary.append((addr, ident, hits))
        drop(ch)
        time.sleep(0.3)

    print("")
    print("=" * 64)
    print("SUMMARY -- the real address map for this car")
    for addr, ident, hits in summary:
        print("  %02X  %-34s %3d blocks" % (addr, (ident or "?")[:34], hits))
    print("=" * 64)
    print(">>>BODYSCAN-END<<<")
    screen.show("SCAN DONE", "%d modules" % len(summary),
                "see serial log", "set MODE=watch next")


def watch(can, screen):
    """Sample a few blocks continuously and print only what changed.

    This is the half that actually identifies signals: the owner toggles one
    thing at a time and the diff names the block, field and bit.
    """
    print(">>>BODYSCAN-BEGIN<<<")
    print("=" * 64)
    print("WATCH MODE -- toggle ONE thing at a time; changes print below")
    print("=" * 64)
    chans = {}
    last = {}
    for addr, blk in WATCH:
        if addr not in chans:
            chans[addr] = open_channel(can, addr)
            print("  %02X %s" % (addr, "open" if chans[addr] else "NO CHANNEL"))
    screen.show("WATCH", "toggle one thing", "at a time", "watch serial")

    beat = time.monotonic()
    cycles = 0
    while True:
        cycles += 1
        if time.monotonic() - beat >= HEARTBEAT_S:
            beat = time.monotonic()
            # print the CURRENT value of everything, not just changes: a
            # capture that shows nothing must be distinguishable from a
            # channel that died, which is exactly what bit us before.
            parts = []
            for a2, b2 in WATCH:
                v = last.get((a2, b2))
                parts.append("%02X/%03d=%s" % (a2, b2, hexs(v) if v else "--"))
            print("  [alive] %d cycles | %s" % (cycles, "  ".join(parts)))
        for addr, blk in WATCH:
            ch = chans.get(addr)
            if ch is None:
                continue
            if not ch.connected:
                try:
                    ch.connect(timeout=0.6, tries=1)
                except tp20.TP20Error:
                    continue
            try:
                d = tp20.read_block(ch, blk, timeout=READ_TIMEOUT)
            except tp20.TP20Error:
                continue
            if not d:
                continue
            key = (addr, blk)
            prev = last.get(key)
            if prev is None:
                print("  first %02X/%03d %s  %s"
                      % (addr, blk, hexs(d), describe(d)))
            if prev is not None and bytes(prev) != bytes(d):
                print("CHANGE %02X/%03d" % (addr, blk))
                print("   was %s  %s" % (hexs(prev), describe(prev)))
                print("   now %s  %s" % (hexs(d), describe(d)))
                # name the exact bytes that moved, which is what identifies
                # a single lamp or contact
                for i in range(min(len(prev), len(d))):
                    if prev[i] != d[i]:
                        print("       byte %d: %02X -> %02X  (%s -> %s)"
                              % (i, prev[i], d[i],
                                 "{:08b}".format(prev[i]),
                                 "{:08b}".format(d[i])))
            last[key] = bytes(d)
        time.sleep(WATCH_PERIOD)


# ----------------------------------------------------------------- main

screen = Screen()
screen.show("BODY SCAN", "mode: %s" % MODE, "read only")

bus = uds.Bus()
bus.listener.deinit()

try:
    if MODE == "watch":
        watch(bus.can, screen)
    elif MODE == "sweep":
        sweep(bus.can, screen)
    else:
        scan(bus.can, screen)
except Exception as e:
    print("bodyscan failed: %s: %s" % (type(e).__name__, e))
    print(">>>BODYSCAN-END<<<")
    screen.show("SCAN ERROR", str(e)[:34])

while True:
    time.sleep(1)
