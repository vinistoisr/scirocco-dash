"""
boot.py -- USB layout and filesystem mode switch.

--- usb serial: ONE cdc port, on purpose ---

CircuitPython builds its USB descriptors in a fixed order and hands out
interface numbers as a running counter, console first:

    console enabled:  IF0/IF1 = "CircuitPython CDC"   (the REPL)
                      IF2/IF3 = "CircuitPython CDC2"  (usb_cdc.data)
    console disabled: IF0/IF1 = "CircuitPython CDC2"  (usb_cdc.data)

That ordering broke the car. RealDash on Android claims *port index 0* and
offers no way to choose another, so with both ports enabled it attached to
the REPL and sat there reading Python text while every gauge frame went out
the port it was not listening to. Confirmed on the head unit from sysfs:

    1-1.1:1.0  class=02  driver=usbfs    CircuitPython CDC control   <- RealDash
    1-1.1:1.2  class=02  driver=cdc_acm  CircuitPython CDC2 control  <- our frames

So the console is OFF by default and the data port becomes IF0. RealDash then
lands on the real stream, and its command frames (Clear Codes, Reset Peak)
reach usb_cdc.data instead of being typed at a REPL.

What that costs: no USB REPL and no serial tracebacks. In this car that is no
loss -- the head unit is not rooted, so /dev/ttyACM* is root-only and nothing
there could read the console anyway. Recovery does not depend on it either:
the deck mounts CIRCUITPY read-write, so tools/deploy-board.ps1 can always
push a fix over WiFi and auto-reload picks it up.

    JUMPER A1 -> GND at power-up to get the console back.

Use that on the bench when you want the REPL. It is read once, here, so it
only takes effect on a hard reset: writing files triggers a soft reload,
which does not re-run boot.py.

--- filesystem ---

CircuitPython lets either the USB host write to CIRCUITPY or the running
program, never both, because two writers on one FAT volume corrupts it.

    No jumper  ->  DEV MODE. Host can drag-and-drop code; the deck can deploy
                   firmware over WiFi. The board cannot write its own CSV.

    A0 to GND  ->  CAR MODE. The board writes boost_log.csv; the drive is
                   read-only to the host, so remote deploys stop working.

CAR MODE is effectively retired: logging happens on the head unit now. The
board's 2 MB flash holds only minutes of full-rate CSV, and locking out
remote deploys to get it is a bad trade. The interlock stays because
tools/deploy-board.ps1 checks for it and refuses to write.
"""

import board
import digitalio
import storage
import usb_cdc

FS_PIN = board.A0        # jumper to GND -> CAR MODE (board writes the CSV)
CONSOLE_PIN = board.A1   # jumper to GND -> bring the USB REPL back


def _jumpered(pin):
    """True when pin is tied to GND. Left floating it reads high."""
    try:
        sel = digitalio.DigitalInOut(pin)
        sel.switch_to_input(pull=digitalio.Pull.UP)
        low = not sel.value
        sel.deinit()          # release it; code.py may want the pin
        return low
    except Exception:
        return False


want_console = _jumpered(CONSOLE_PIN)
usb_cdc.enable(console=want_console, data=True)

if _jumpered(FS_PIN):
    # readonly=False means "not read-only to the board"
    storage.remount("/", readonly=False)
    print("boot: CAR MODE -- board can write, host is read-only")
else:
    print("boot: DEV MODE -- host can write, board cannot")

print("boot: usb console=%s, data=True (RealDash needs data on IF0)"
      % want_console)
