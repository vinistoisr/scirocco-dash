"""
frame_schema.py -- logger-side frame decoder built from scirocco_realdash.xml.

PLAN-deck.md: "Decoder is generated from scirocco_realdash.xml -- one schema
for board, RealDash and logger." The XML is the single source of truth for
frame ids, byte offsets, signedness and scaling, so this module derives the
tee's decoder from it at startup instead of keeping a second hand-maintained
table that would silently drift from what the board sends and RealDash shows.

What the XML gives us and how it is interpreted here:

  * Frames are 8-byte payloads of (usually four) little-endian u16 words.
    The XML only ever declares endianness="little"; the loader asserts that
    rather than half-supporting big-endian nobody uses.
  * signed= is a per-frame default with a per-value override (0xC81 is a
    signed frame but its N75 Duty word carries signed="false").
  * conversion= is always of the form "V*<float>" when present; absent means
    the raw word IS the value. Anything else is a schema error and load()
    raises, because a silently unscaled channel poisons every log.
  * enum= values (fault codes, command buttons) decode to the raw number.
    The enum text is RealDash display sugar; the log wants the number.
  * Values that carry only a RealDash targetId get stable names from
    _TARGETID_NAMES below; values with name= are snake_cased. Both must be
    stable forever -- they become CSV column headers, and the cloud
    dashboard will key on them across years of drives.
  * Write-direction frames (writeInterval= present, e.g. 0xC90 buttons) are
    skipped: they describe RealDash -> board traffic, which the tee forwards
    verbatim and does not decode into log rows.

Stdlib only (xml.etree), CPython 3.10+, runs on Windows and Termux.
"""

import re
import struct
import xml.etree.ElementTree as ET
from collections import namedtuple
from pathlib import Path

# The XML file this schema mirrors. deck/ and board/ are siblings in the repo.
# In the repo the XML lives in board/; when this is deployed to the head unit
# everything is copied flat into one directory, so accept a sibling copy too.
# Order matters: the repo path wins on the laptop, the sibling wins on the deck.
_HERE = Path(__file__).resolve().parent
_XML_CANDIDATES = (
    _HERE.parent / "board" / "scirocco_realdash.xml",
    _HERE / "scirocco_realdash.xml",
)
DEFAULT_XML = next((p for p in _XML_CANDIDATES if p.exists()), _XML_CANDIDATES[0])

# RealDash built-in targetIds -> stable channel names. These ids are fixed by
# RealDash itself (see the comment block at the top of the XML), so the map
# can be baked in here. If a new targetId ever appears in the XML, load()
# fails loudly rather than inventing a name that would churn the CSV header.
# RealDash built-in input ids -> our CSV column names.
#
# These names are a PERMANENT contract: they are the CSV headers, and the
# cloud dashboard keys on them across years of drives. When a channel is
# re-bound from a name= to a targetId (so stock dashboards pick it up without
# hand-wiring), its entry here MUST reproduce the old snake_cased name, or
# every historical log silently stops lining up with new ones.
#   e.g. name="Engine Load" -> engine_load, so 100 must map to engine_load.
_TARGETID_NAMES = {
    11: "baro",
    12: "battery_v",
    14: "coolant_c",
    17: "trim_short",
    18: "trim_long",          # bank 2 short term; kept for older XMLs
    27: "iat",
    30: "maf",
    31: "map_kpa",
    33: "run_time",
    35: "inj_ms",
    37: "rpm",
    38: "timing_deg",
    42: "throttle",
    65: "check_engine",
    160: "turn_left",         # body lamp: left indicator (from the CAN box)
    161: "turn_right",        # body lamp: right indicator
    78: "odometer",
    81: "speed_kmh",
    83: "boost",
    100: "engine_load",
    102: "trim_long",         # bank 1 LONG term -- the right partner for 17
    119: "n75_duty",          # RealDash "Duty Cycle 1"
    152: "oil_c",
    173: "ambient_temp",
    202: "fuel_rail_pressure",
    270: "boost_target",
}

# Display-only frames: RealDash-local scratch values (0xC92 ghost-needle
# visibility toggles, flipped by gauge tap actions). Nothing ever transmits
# them, so they are excluded here to keep them out of the CSV header
# contract.
_DISPLAY_ONLY = frozenset({0xC92})

# (channel_name, byte_offset, length, signed, conversion_fn)
Value = namedtuple("Value", "name offset length signed convert")

_CONV_RE = re.compile(r"^\s*V\s*\*\s*([-+]?[0-9]*\.?[0-9]+(?:[eE][-+]?[0-9]+)?)\s*$")


def _snake(name):
    return re.sub(r"[^0-9A-Za-z]+", "_", name).strip("_").lower()


def _make_convert(expr, where):
    if expr is None:
        return float
    m = _CONV_RE.match(expr)
    if not m:
        raise ValueError("unsupported conversion %r at %s (only V*<float> is used)"
                         % (expr, where))
    k = float(m.group(1))
    return lambda raw, _k=k: raw * _k


class Schema:
    """frames: {frame_id: (Value, ...)}. channels: every channel name in a
    stable order -- frames sorted numerically by id, values by byte offset --
    independent of XML element order, because this list IS the CSV header."""

    def __init__(self, frames):
        self.frames = frames
        self.channels = [v.name
                         for fid in sorted(frames)
                         for v in frames[fid]]

    def decode(self, frame_id, payload):
        """Decode one 8-byte payload -> {channel: float}, or None for a
        frame id the schema does not know (future firmware, text frames)."""
        defs = self.frames.get(frame_id)
        if defs is None:
            return None
        out = {}
        for v in defs:
            if v.offset + v.length > len(payload):
                continue        # short/corrupt payload: keep what fits
            raw = int.from_bytes(payload[v.offset:v.offset + v.length],
                                 "little", signed=v.signed)
            out[v.name] = v.convert(raw)
        return out


def load(xml_path=DEFAULT_XML):
    """Parse the RealDash XML into a Schema. Raises on anything ambiguous:
    a decoder that guesses is worse than one that refuses to start.

    The XML's comments contain "--", which RealDash accepts but strict XML
    forbids, so comments are stripped before parsing. The alternative --
    rewording the canonical XML to appease expat -- would couple the board
    file's prose to this parser."""
    with open(xml_path, "r", encoding="utf-8") as f:
        text = re.sub(r"<!--.*?-->", "", f.read(), flags=re.S)
    root = ET.fromstring(text)
    frames = {}
    seen_names = set()
    for fr in root.iter("frame"):
        fid = int(fr.get("id"), 0)
        if fr.get("writeInterval") is not None:
            continue            # RealDash -> board (0xC90): not decoded
        if fid in _DISPLAY_ONLY:
            continue            # tee -> RealDash display sugar: not logged
        endian = fr.get("endianness", "little")
        if endian != "little":
            raise ValueError("frame 0x%X declares endianness=%r; the board "
                             "only ever sends little-endian" % (fid, endian))
        frame_signed = fr.get("signed", "false") == "true"
        vals = []
        for el in fr.iter("value"):
            # A value may deliberately re-read bytes another value covers, so
            # a gauge can bind to a transformed copy of a channel.  That is
            # display sugar like the 0xC92 frame and must not become a CSV
            # column: it would duplicate a column already logged.
            if el.get("displayOnly") == "true":
                continue
            offset = int(el.get("offset"))
            length = int(el.get("length", "2"))
            signed = frame_signed
            if el.get("signed") is not None:        # per-value override
                signed = el.get("signed") == "true"
            name = el.get("name")
            if name is not None:
                name = _snake(name)
            else:
                tid = int(el.get("targetId"))
                name = _TARGETID_NAMES.get(tid)
                if name is None:
                    raise ValueError("frame 0x%X: unknown targetId %d -- add "
                                     "it to _TARGETID_NAMES" % (fid, tid))
            if name in seen_names:
                raise ValueError("duplicate channel name %r (frame 0x%X)"
                                 % (name, fid))
            seen_names.add(name)
            where = "frame 0x%X offset %d (%s)" % (fid, offset, name)
            vals.append(Value(name, offset, length, signed,
                              _make_convert(el.get("conversion"), where)))
        vals.sort(key=lambda v: v.offset)
        frames[fid] = tuple(vals)
    return Schema(frames)


if __name__ == "__main__":
    # Self-check against the frame captured live on the car:
    #   44 33 22 11 80 0C 00 00 48 03 E8 03 C3 00 DB 0C
    # id 0xC80, words 840 / 1000 / 195 / 3291 = rpm, MAP mbar, load x10,
    # IAT in 0.1 K. Also a signed check with boost -5 mbar on 0xC81.
    import sys
    schema = load(sys.argv[1] if len(sys.argv) > 1 else DEFAULT_XML)

    for fid in sorted(schema.frames):
        print("0x%X:" % fid)
        for v in schema.frames[fid]:
            print("    %-22s off %d len %d %s" %
                  (v.name, v.offset, v.length, "signed" if v.signed else "unsigned"))
    print("%d channels: %s" % (len(schema.channels), ",".join(schema.channels)))

    captured = bytes.fromhex("4433221" "1800C00004803E803C300DB0C")
    assert captured[:4] == b"\x44\x33\x22\x11"
    fid = struct.unpack_from("<I", captured, 4)[0]
    d = schema.decode(fid, captured[8:16])
    print("captured 0xC80 ->", d)
    assert d["rpm"] == 840.0, d
    assert abs(d["map_kpa"] - 100.0) < 1e-9, d
    assert abs(d["engine_load"] - 19.5) < 1e-9, d
    assert abs(d["iat"] - 329.1) < 1e-9, d

    d = schema.decode(0xC81, struct.pack("<hhhH", -5, 120, 1100, 750))
    print("signed 0xC81   ->", d)
    assert abs(d["boost"] - -0.005) < 1e-9, d
    assert abs(d["n75_duty"] - 75.0) < 1e-9, d
    assert schema.decode(0xC90, b"\x00" * 8) is None      # write frame skipped
    assert schema.decode(0xCFF, b"\x00" * 8) is None      # unknown id
    print("frame_schema self-check OK")
