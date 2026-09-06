# The head unit's CAN box (comfort bus) -- decoded

The DUDU7 has a separate CAN decoder box behind the radio, wired to the
car's COMFORT bus. That is the bus the OBD gateway refuses to route, so the
box sees signals the M4 cannot reach at any price.

Enable **Launcher settings -> CANBUS Beta / Info Log -> Export CANBUS Log to
.txt File** (NOT just "Use CANBUS Beta"). It writes
`/sdcard/canBusALog/log_YYYY-MM-DD_N.txt`, readable over adb with no root.
Turn it off when finished -- the app warns about storage, and it grew 35 KB
-> 343 KB in ten minutes.

## Header identifies the car correctly

```
CanBox{protocolId=1048627, name='...VW 08-10 Scirocco', companyId=3, carTypeId=452}
protocol box version: VW-RZ-08-0041.212.04-HSE
MCU version:          LSDD_53_L7870_N32P48F64_3G_E60_V:1.0
```

## Frame format

`2e <id> <len?> <payload...> <checksum>`

The LAST byte is a checksum, not data. For a given frame id,
`value + checksum` is constant -- e.g. on `2e 14` it is always 0xEA, and on
the 17-byte `2e 41` it is 0x120. Decoding a byte as part of a 16-bit value
when it is actually the checksum produces a plausible-looking but wrong
number, so identify the checksum column first.

## What was observed (2026-08-23, 10 minutes, engine mostly off)

| Frame | Len | n | Meaning |
|---|---|---|---|
| `2e 14` | 5 | 526 | unidentified (NOT steering -- see correction) |
| `2e 41` | 17 | 622 | status; bytes 8-9 = battery mV (1245 = 12.45 V) |
| `2e 7d` | 7 | 229 | **multiplexed: turn signals + steering angle** |
| `2e 20` | 6 | 8 | **steering wheel buttons** -- press then `00 00` release |
| `2e 24` | 6 | 9 | button-like, second group |
| `2e 22/23` | 8 | 40 | four booleans; `23` flipped 0->1 once |
| `2e 25/26` | 6 | 2/3 | rare |

Decoded value lines also appear in plain text, e.g. odometer
`dis=185885.0 -> 185885 km` (matches the car exactly) and outside temp
`lastOutTemp=19.0`.

## CORRECTION: `2e 14` is NOT steering angle

An earlier pass called `2e 14` "steering angle, unmistakable" on the
strength of a smooth monotonic sweep. That was wrong. A deliberate,
narrated test (indicator left 10 s, right 10 s, then lock-to-lock) produced
**zero** `2e 14` frames while the wheel was turned. Counting frames per
minute settles it: `2e 14` appears only 20:25-20:31 and never again,
including throughout the steering test at 20:36-20:37.

A smooth sweep is not proof of identity. The lesson is that a signal must be
confirmed against a KNOWN, NARRATED action -- "I turned the wheel at this
moment" -- not against activity that merely happens to look physical.
`2e 14` remains unidentified.

## `2e 7d` is a multiplexed container -- this is the useful frame

    2e 7d <sublen> <subid> <payload...> <cksum>       value + cksum = 0x7F

### Turn signals -- `2e 7d 02 01 <flags>`

| flags | meaning |
|---|---|
| `0x10` | LEFT indicator |
| `0x08` | RIGHT indicator |
| `0x00` | off |

Blinks at ~2.5 Hz. Confirmed against a narrated test: 11 left blinks
20:36:11.2-20:36:19.7, then 11 right blinks 20:36:24.5-20:36:33.0, matching
"left for ten seconds, off, right for ten seconds" exactly.

Hazard is presumably `0x18` (both bits) but was NOT tested.

### Steering angle -- `2e 7d 03 08 <signed 16-bit LITTLE-endian>`

Confirmed against the same narrated test:

```
20:36:34.5      0     centre
20:36:40.8   -517     full left, held to 20:36:44.7
20:36:47.6    -16     passing centre
20:36:54.9   +525     full right, held to 20:36:56.5
20:37:00.1     +7     back to centre
```

Range -519..+528, span 1047 counts. A Scirocco is about 2.75 turns
lock-to-lock (~990 deg), so **one count is approximately one degree** and
negative is LEFT. No unwrapping needed -- unlike `2e 14`, this does not wrap.

Rate is roughly 4-5 Hz in the log. Compare the OBD route: 470 ms per visit
to EPS `0x09`, which also drops the engine stream to 4% of baseline while a
second channel is open. This is strictly better and costs the M4 nothing.

### `2e 7d 03 0b ...` -- unidentified, periodic

## NOT found in this capture

* **Yaw rate / lateral g.** Consistent with the launcher's key namespace,
  which has no yaw or lateral-acceleration key across ~11k keys covering all
  supported protocols.
* **RPM / speed.** `2e 41` bytes 4-5 stayed 0 with the engine off.

## Implemented (2026-08-23 evening): canbox.py -> tee -> everywhere

`deck/canbox.py` tails the launcher's log and injects frame `0xC8E`
(turn_left, turn_right, steering_deg, canbox_age) into the tee's local-only
side door at `cfg.INJECT_PORT` (35002; 35001 was taken by SIM_PORT).
Injected frames are decoded into the CSV row and forwarded to RealDash --
turn signals bind to targetIds 160/161 so stock dashboards blink on their
own -- but they never go to the board and never start a session.

Verified end to end with the car asleep: a fake RealDash client on 35000
received 0xC8E at the 1 Hz heartbeat, and canbox_age read 0 while the box's
climate chatter flowed. The parser was unit-tested against the real
narrated capture: steering min/max reproduced byte-exact (-519/+528).

THE SELINUX TRAP: processes started through `run-as` inherit the
`runas_app` SELinux context, which cannot read /sdcard even with every
storage permission granted -- reads fail with EACCES while the very same
uid's app-context processes succeed. The fix is `~/.bashrc` starting the
watchdog from a real Termux session (Termux:Boot does the same at boot),
plus uid-level NO_ISOLATED_STORAGE / READ_EXTERNAL_STORAGE for Termux.
