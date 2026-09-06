# Scirocco CAN Gauge - Feather M4 CAN

**You are looking at a live microcontroller, not a normal folder.** This drive is an
Adafruit Feather M4 CAN Express running CircuitPython 10.2.1, mounted over USB. Saving
any file here makes the board reboot and re-run `code.py` within about a second. There
is no build step and no deploy step. Editing is deploying.

The drive letter varies by machine. It is often `D:` on Windows; check for
`boot_out.txt` at the root to identify it elsewhere.

## What this device is for

Reading live boost out of a **2009 VW Scirocco 2.0 TSI**, JDM import, and streaming it
to the car's Dudu7 Android head unit as a gauge.

| | |
|---|---|
| Engine | CAWB, EA888 Gen 1, 200 PS |
| ECU | Bosch MED17.5, part `06J906026AR` |
| VIN | `WVWZZZ13Z9V0xxxxx` |
| Tune | Stratified flash tune |
| Bus | 500 kbit/s, 11-bit IDs |
| Protocol | **KWP2000 over VW TP 2.0.** Not UDS. |
| Engine channel | dest `0x01`, tester tx `0x740`, rx `0x300` |
| Status | Working, ~17.6 Hz. Needs confirming under load on a drive. |

## The two facts the whole project rests on

**1. Boost is not broadcast on any CAN bus in this car.** No module needs to know charge
pressure, so nothing publishes it. The OBD port's CAN is a dedicated diagnostic bus
behind the gateway and is silent until addressed. Everything must be polled. If an idea
starts with "let's just listen to the bus", that is why it will not work.

**2. This ECU does not speak UDS.** The OBD port serves generic EOBD on `0x7E0`/`0x7E8`
and nothing more. Service `0x22` and service `0x10` both go unanswered. Manufacturer
data comes only from KWP2000 carried over TP 2.0, and service `0x21` block N is exactly
the VCDS measuring block N.

## Where the data comes from

Service `0x21` block N is exactly VCDS measuring block N. Block 115 is read every cycle
so boost stays fast; the rest rotate one per four cycles, which is ample for
temperatures and voltage and keeps boost at ~16 Hz.

| Block | Fields used | Confidence |
|---|---|---|
| **115** | rpm, engine load %, boost specified, boost actual | Ross-Tech documented |
| **118** | rpm, intake air temp, N75 duty, boost actual | Ross-Tech documented |
| **004** | rpm, **battery volts**, **coolant**, IAT, -, -, **oil temp**, - | first four documented; oil inferred |
| **003** | rpm, **MAF g/s**, **throttle %** | value-checked at idle |
| **005** | rpm, load, **road speed km/h** | formula 7 is km/h |
| **032** | **fuel trim additive %**, **fuel trim multiplicative %** | documented lambda group |
| **007** | ..., **ambient temp** at field 6 | inferred |
| **002** | rpm, load, **injection time ms**, MAF | value-checked at idle |
| **011** | ..., **charge air temp** at field 5 | inferred |
| **075** | ..., **odometer** at field 8, in 10 km counts | verified against the dash |
| **020** | **knock retard, cylinders 1-4** | documented group; scale unverified |
| **078** | barometric pressure at field 6 | value-checked |

**Odometer**: block 075 field 8 read 18588 when the dash showed 185885 km, so it
counts in 10 km steps and the last digits are simply below its resolution.

**Knock retard**: Ross-Tech documents group 020 as the knock sensor group and all four
fields read exactly 0 at idle, which is what no-knock looks like. The zero is
trustworthy; **the degrees-per-count scale is not verified**, because nothing knocks at
idle. Check it against a VCDS or tuner log under load before relying on the number.

## EOBD and TP 2.0 cannot run at the same time

Measured, not assumed. Every generic mode 01 request on `0x7E0` makes this ECU tear the
TP 2.0 channel down with an `A8`. Interleaving them dropped the gauge from 16 Hz to
**0.7 Hz**, left block reads taking 300 ms instead of 50 ms, and forced a reconnect
after every other PID, in a perfect alternating pattern.

So the EOBD PIDs are read **once at startup, before the channel opens**
(`boost.eobd_snapshot`), and never polled live. That covers fuel rail pressure,
commanded lambda, catalyst temp, O2 voltage, pedal position, run time and distance
since codes were cleared. They stay fixed for the session; anything needed live has to
be found in a measuring block instead.

`eobdmix.py` reproduces the measurement if this is ever doubted.

Pressures are **absolute mbar**; gauge boost is `(actual - baro) / 1000` bar. At idle
actual is ~1000 mbar against ~1005 baro, so the gauge reads about zero. The ~350 mbar
readings elsewhere are post-throttle manifold pressure, a different thing.

**Inferred channels are honest guesses, not documented facts.** Oil temp reads ~82 C
with coolant at 96 C at warm idle, which is what oil does, and the same value appears in
blocks 003 and 008. To confirm it, watch a **cold start**: coolant climbs fast, oil lags
well behind. If they track each other exactly, it is not oil.

Three formula IDs were identified from this car's own data rather than a table, by
checking the result against what the sensor must read at warm idle:

| Formula | Meaning | Check at idle |
|---|---|---|
| 20 | `a*(b-128)*0.01` % | block 032 is the documented fuel-trim group, gives +2.1 / +2.5 % |
| 22 | `a*b*0.001` ms | 1.53 ms injection time |
| 25 | `b*1.421 + a/182` g/s | 3.2 g/s airflow, right for a 2.0 at idle |

## Files

| File | Role |
|---|---|
| `code.py` | Runs on boot. The live gauge: TP 2.0 -> boost -> RealDash frames. |
| `tp20.py` | VW TP 2.0 transport + KWP2000 client. The core of the project. |
| `boost.py` | Channel decode, gauge maths, peak hold, CSV logging. |
| `realdash.py` | RealDash CAN frame encoder. |
| `scirocco_realdash.xml` | **Copy this to the head unit.** Tells RealDash how to read the frames. |
| `uds.py` | ISO-TP + UDS client. Only used now for the EOBD probe and CAN bring-up. |
| `boot.py` | Filesystem mode switch and USB serial layout. |
| `tpdiag.py` | Promiscuous TP 2.0 diagnostic. Reach for this when the bus goes quiet. |
| `boostfind.py` | Ranks pressure fields by swing. Used to identify channels. |
| `ratetest.py` | Times the read loop against the serial writes. |
| `hunt.py` | Sweeps alternative UDS addressing. Kept; it proved there is none. |
| `HANDOFF.md` | Decisions and dead ends. **Read before changing direction.** |
| `tools/readserial.ps1` | Restart the board and capture serial output. |
| `tools/readframes.ps1` | Capture raw bytes and decode RealDash frames. |
| `code_ORIGINAL_airlift_can.py` | The AirLift demo that shipped on the board. Do not delete. |
| `lib/` | Only 3 libraries, all required. |

## Running it

```powershell
powershell -ExecutionPolicy Bypass -File tools\readserial.ps1 -Seconds 40
```

Auto-detects the COM port, restarts `code.py`, prints everything. To check the binary
frames instead:

```powershell
powershell -ExecutionPolicy Bypass -File tools\readframes.ps1 -Port COM5 -Seconds 30 -Reload
```

Read the **tail** of that capture, not the head: the first second includes channel setup
and a reconnect, which understates the sample rate.

## Setting up RealDash on the head unit

> **The custom dashboard lives in `dash/`.** Two pages, ~80 gauges, covering
> every channel the board sends. Before doing ANY work on it, read
> **`dash/EDITOR-NOTES.md`** — the `.rd` format is not scriptable, so the
> dashboard is built by driving the RealDash editor with synthetic input, and
> that file records the traps (canvas clicks do not select gauges; artwork
> renders tinted until blend colours are set to white; gauge rects can be
> patched directly in the `.rd` but input bindings cannot). `dash/README.md`
> has the layout and bindings; `dash/HANDOFF-deck-install.md` is the
> at-the-car install procedure. The automation harness is `dash/tools_ui.ps1`.

1. Install **RealDash** from the Play Store on the Dudu7.
2. Copy `scirocco_realdash.xml` onto the head unit (USB stick or cloud, anywhere in
   internal storage).
3. Connect the Feather to the head unit's USB port with a USB-C cable. It powers from
   that port, so it sleeps with the ignition.
4. In RealDash: **Garage -> Connections -> Add new connection**.
5. Pick **RealDash CAN**. This is the important step - it is *not* one of the named ECU
   types in that list. Those are for specific aftermarket ECUs; ours is the generic
   custom-frame protocol.
6. Choose **Serial / USB** as the transport, **115200** baud.
7. When asked for the CAN description XML, select `scirocco_realdash.xml`.
8. Android will ask for permission to access the USB device. Accept, and tick "use by
   default" so it does not ask on every ignition cycle.

RealDash for **Windows** works the same way and is the quicker way to iterate on gauge
layouts: same USB cable, same RealDash CAN / serial connection, same XML.

These bind to built-in RealDash targets, so stock dashboards pick them up with no
configuration: engine speed, manifold pressure, intake air temp, coolant temp, oil
temp, battery voltage, vehicle speed, throttle position, injector pulse width, both
fuel trims, and boost target.

These are custom inputs and appear under their own names when editing a gauge:
`Boost`, `Boost Peak`, `Engine Load`, `N75 Duty`, `MAF`, `Ambient Temp`,
`Charge Air Temp`, `Baro`, `Fault Codes`, `Reconnects`, `Sample Rate`.

If RealDash sees the port but no data, the likely cause is that it opened the wrong CDC
channel; set `REALDASH_ON_CONSOLE = True` in `code.py`.

## Filesystem modes (`boot.py`)

CircuitPython lets either the USB host write to the drive or the running program write
to it, never both.

- **No jumper: DEV MODE.** You can edit files. The gauge cannot write `boost_log.csv`.
- **A0 jumpered to GND at power-up: CAR MODE.** The gauge writes `boost_log.csv` and the
  drive is read-only from the PC. Use this for a drive you want logged.

If your edits to this drive are silently failing, A0 is grounded. Pull the jumper and
reset.

`boot.py` also enables a second USB serial channel so binary frames stay clear of
console text. That needs a **physical replug** to take effect; Windows caches USB
descriptors and even `microcontroller.reset()` will not do it. Until then the code
automatically puts frames on the console port and goes silent, which works fine.

## Buttons from RealDash

RealDash can send values back to the board on frame `0xC90`. Bind a button to set a
value to 1; the board acts on the **0 to 1 edge** and ignores it while it stays at 1, so
set it back to 0 to arm it again.

| Button | Effect |
|---|---|
| `Cmd Clear Codes` | Clears stored fault codes. **Writes to the ECU.** Refused above 0 km/h. |
| `Cmd Reset Peak` | Zeroes the boost peak hold |
| `Cmd Refresh Codes` | Re-reads stored fault codes |

**Commands need the second USB serial channel and are disabled without it.** A command
payload can contain the byte `0x03`, which on the console channel is Ctrl-C and would
kill the running program mid-drive. `boot.py` creates the data channel, but that needs a
**physical replug** to take effect. Until then the board prints
`commands: DISABLED` and only streams outward.

## Hard rules

- **Read-only diagnostics, with one deliberate exception.** Only KWP2000 `0x1A`
  (identification), `0x18` (read fault codes), `0x21` (measuring blocks) and a session
  request are sent during normal operation. Never code, adapt, or flash. The car is
  tuned.
- **The exception is `tp20.clear_dtcs()`, KWP service `0x14`**, reachable only from the
  RealDash button. It is a standard diagnostic operation and does not touch the tune,
  but clearing also discards freeze-frame data and **resets the emissions readiness
  monitors**, which then need a full drive cycle to re-run. Read the codes first; never
  clear speculatively.
- **Never connect OBD pin 16 (+12 V) to the Feather.** Power it from USB.
- The transceiver needs `CAN_STANDBY` low **and** `BOOST_ENABLE` high. Miss either and
  the bus is silent with no error at all. `uds.Bus` handles this; do not remove it.
- Check the board's `Trm` jumper against the car before assuming termination is right.
  Details in `HANDOFF.md`.
- Do not delete `lib/`. Those three libraries are the minimum set and are not
  recoverable without a download.

## TP 2.0 rules that already cost hours

- The channel setup request must carry a **real ID in both** ID fields
  (`01 C0 00 03 00 03 01`). The widely-published form with `00 10` in bytes 2-3 gets no
  reply at all on this car, which looks exactly like a dead bus.
- Use **one promiscuous listener** and filter in software. Rebuilding a `canio.Listener`
  per reconnect exhausts the SAME51's filter slots (`Filters too complex`), and a filter
  scoped to `0x200-0x2FF` hides the params reply that arrives on `0x300`.
- **Drain the RX FIFO** when a channel starts. A leftover `A8` from a dead channel reads
  as a healthy channel hanging up.
- A refusal (`D7`) or a mid-handshake `A8` means a stale channel is still open. Send
  `A8` to the advertised tx ID and retry; `connect()` does this already.

## CircuitPython gotchas that already cost hours

- No multiple `**` unpacking in a call. Merge dicts first.
- No `bytes.fromhex`. No `str.isalnum`.
- **CPython's `py_compile` will not catch any of the above.** Deploy and read the serial
  output to verify.
- `displayio.FourWire` on CP7 became `fourwire.FourWire` on CP9+, and `display.show()`
  became `display.root_group`. The shims in `code.py` support both; leave them alone.
- A tri-color e-ink refresh blocks ~15 s, longer than the TP 2.0 channel timeout, so the
  ECU hangs up and the next sample reconnects. Expected, not a fault.

## Windows gotchas

- `Set-Content -Encoding utf8` in Windows PowerShell 5.1 writes a **BOM** and
  CircuitPython rejects it with `SyntaxError` on line 1. Use
  `[System.IO.File]::WriteAllText($path, $text, (New-Object System.Text.UTF8Encoding($false)))`.
- Write/Edit tooling cannot create files at the root of this FAT volume
  (`EPERM: mkdir 'D:\'`). Stage elsewhere and `Copy-Item` across.

## Status and next action

Working on the car. VIN, ECU ident, fault codes and live boost all read correctly, and
frames are verified on the wire.

**Next step: drive it.** At idle boost sits at about 0.00 bar, which is correct but
proves nothing about the scaling. A drive should show it climbing toward +1 bar under
load, with the peak hold and `boost_log.csv` agreeing.

Note the car has two stored boost-related faults, **P0234 overboost** and **P2261
diverter valve mechanical**. Both predate this project and P2261 in particular will
affect real boost behaviour.
