# Body electronics: where each signal lives

Sourced from the real Ross-Tech label files (plain-text .lbl), not guessed.
Found in two GitHub mirrors of a full VCDS Labels folder; the address-09
measuring-block section is byte-identical across 3C0-937-049-23-H.lbl,
3C0-937-049-30-H.lbl and the 1K0-937-049-21-B.lbl "Basis" file, so the
numbering is stable across the whole 1K/3C central-electronics family.
Label sources: github.com/dspl1236/KWPBridge (labels/modules/) and
github.com/Pabloar7882/audi-diag (#Labels/).

This car's 09 is 3C8 937 049 E, which VCDS labels with 3C0-937-049-30-H.lbl.

## Address 09 Central Electrics (J519) -- lighting and contacts

| Block | Field | Signal | RealDash targetId |
|---|---|---|---|
| 001 | 1-4 | Parking lights LF / RF / LR / RR, % | 155 |
| 002 | 1-2 | Low beam L / R, % | 156 |
| 002 | 3-4 | Front fog L / R, % | - |
| 003 | 1-2 | **Turn signal front L / R, %** | **160 / 161** |
| 003 | 3-4 | Turn signal rear L / R, % | - |
| 004 | 1-2 | Side repeaters L / R, % | - |
| 004 | 3 | **High beam (single combined channel), %** | **157** |
| 005 | 1-3 | Brake lights L / R / centre, % | - |
| 005 | 4 | License plate light, % | - |
| 006 | 1 | Instrument illumination (term 58d), % | - |
| 006 | 2-3 | Reverse lights L / R, % | 159 |
| 006 | 4 | Rear fog left, % | 158 |
| 008 | 1-2 | Light sensor brightness 0-7, rain 0-7 | - |
| 010 | 1 | Horn operated | 165 |
| 011 | 2 | Hazard button LED, % | - |
| 013 | 1 | Terminal 30 voltage | - |
| 015 | 1 | **Terminal 15 = ignition on** | **167** |
| 015 | 2 | Terminal 50 = cranking | - |
| 016 | 3 | Front wiper active | 181 |
| 017 | 1-3 | Light switch off / parking / low beam | - |
| 018 | 3 | Brake light switch F | - |
| 018 | 4 | Reverse switch F4 (cleanest reverse source) | 159 |
| 019 | 1 | **Hazard switch operated** | **166** |
| 019 | 2 | **Engine hood open** | - |
| 021 | 1-2 | Door latch rotary catch LF / RF | 162 |
| 022 | 1 | **Tailgate main catch** | **163** |
| 023 | 1 | Rotary light switch position (numeric) | - |
| 024 | 1 | Steering column stalk state via LIN (packed) | - |

## Address 17 Instruments (J285)

| Block | Field | Signal | targetId |
|---|---|---|---|
| 001 | 1-3 | Vehicle speed, engine speed, oil pressure switch | - |
| 002 | 1 | Odometer, total km | 78 |
| 002 | 2 | **Fuel level in LITRES** (convert to % for 170) | 170 |
| 002 | 3 | Fuel sender resistance, ohms | - |
| 002 | 4 | Ambient temperature | 173 |
| 003 | 1 | Coolant temperature | - |

Address 15 Airbag carries the seat-belt buckle switches (targetId 168).
Doors also appear at 42/52 (door modules) but 09/021 is simpler.

## Things this rules OUT

* **Parking brake is not exposed as a measuring value anywhere on this car.**
  The cluster only has a selective output test (T1047) for the K7 lamp, and
  the MK60EC1 ABS has no handbrake block. RealDash targetId 164 has no honest
  source here.
* **Gear/shifter has no source if the car is a manual.** The reference
  auto-scan (a 2009 Scirocco 2.0 TSI) has no address 02 at all, i.e. no
  transmission control module. Confirm on THIS car with a module sweep before
  promising RealDash 139/140/141/200 anything. If it is a manual, the honest
  options are RealDash's own calculated gear (id 25, from speed/rpm/ratio,
  which needs the real gear ratios set in vehicle settings) or nothing.

## Implementation notes

* **Turn signals BLINK.** Sampling a blinking lamp at a low rate aliases: the
  indicator will look random. Latch in firmware -- hold "on" for ~700 ms after
  any non-zero sample -- so RealDash shows a steady arrow while the stalk is
  held, which is what a stock dashboard expects.
* Lamp outputs are PERCENT (bulb PWM / current monitoring), not booleans.
  Threshold them (>5 %) into the 0/1 RealDash wants for a lamp input.
* A low-beam bulb fault will make 002/2 read 0 with the lights on; the
  reference car had exactly that DTC. Prefer the SWITCH blocks (017/018) when
  what you want is "did the driver ask for it" rather than "is the bulb lit".
* These are a SECOND TP 2.0 channel alongside the engine one. Body state
  changes slowly (except indicators), so it does not need the engine channel's
  rate -- but two concurrent channels need care, see the firmware notes.
