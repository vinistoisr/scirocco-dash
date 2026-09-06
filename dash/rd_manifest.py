#!/usr/bin/env python3
"""Inspect and safely patch the small, verified parts of a RealDash .rd file.

RealDash's dashboard format is proprietary.  This tool deliberately avoids
pretending to parse the whole format.  It only reports fields whose storage has
been verified against the Windows editor:

* canvas width/height/aspect in the dashboard header;
* UTF-16 gauge names;
* normalised L/T/R/B rectangles near each gauge name.

It never guesses bindings, ranges, colours, page ownership, or gauge types from
unverified offsets.  Use the editor for those fields.
"""

from __future__ import annotations

import argparse
import json
import re
import struct
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path


GAUGE_NAME_RE = re.compile(
    r"^(?:(?:Text|Bar|Image|Needle|Button) Gauge \d+|Clear Codes \(hold 2 s\))$"
)
UTF16_ASCII_RE = re.compile(rb"(?:[\x20-\x7e]\x00){4,}")


@dataclass(frozen=True)
class Gauge:
    name: str
    name_offset: int
    asset_name: str | None
    rect_offset: int | None
    rect_normalised: tuple[float, float, float, float] | None
    rect_pixels: tuple[int, int, int, int] | None


def _valid_rect(values: tuple[float, float, float, float]) -> bool:
    left, top, right, bottom = values
    return (
        all(-1e-6 <= value <= 1.000001 for value in values)
        and right > left
        and bottom > top
        and right - left >= 1 / 1920
        and bottom - top >= 1 / 1200
    )


def _rect_at(data: bytes, offset: int) -> tuple[float, float, float, float] | None:
    if offset < 0 or offset + 16 > len(data):
        return None
    values = struct.unpack_from("<4f", data, offset)
    return values if _valid_rect(values) else None


def _direct_rect_at(data: bytes, offset: int) -> tuple[float, float, float, float] | None:
    """Read a verified direct rectangle, including a newly added off-canvas gauge."""
    if offset < 0 or offset + 16 > len(data):
        return None
    values = struct.unpack_from("<4f", data, offset)
    left, top, right, bottom = values
    if (all(-1.0 <= value <= 2.5 for value in values) and
            right > left and bottom > top and
            right - left >= 1 / 1920 and bottom - top >= 1 / 1200):
        return values
    return None


def _find_rect(data: bytes, name: str, name_end: int) -> tuple[int, tuple[float, float, float, float]] | None:
    # Every v2-added needle carries an inline image name before its rect;
    # the original seven do not.  Needle Gauge 13 (injector pulse width, added
    # 2026-08-29) was missing here, so it took the no-inline-asset fast path
    # and matched the (0, 0, L, T) decoy eight bytes early -- a decoy that is
    # only harmless when the real left and top are both zero.
    inline_needles = {"Needle Gauge %d" % number for number in range(8, 14)}
    has_inline_asset = (name.startswith("Image Gauge") or
                        name in inline_needles or
                        name == "Clear Codes (hold 2 s)")
    # Verified fast path for text, bar and needle gauges.
    if not has_inline_asset:
        direct = name_end + 16
        rect = _direct_rect_at(data, direct)
        if rect is not None:
            return direct, rect

    # Image gauges carry an image-name string before the rectangle.  The
    # record has eight bytes, a u32 UTF-16 character count, the filename,
    # four bytes, then L/T/R/B.  Unlike most binary formats these floats are
    # not guaranteed to be four-byte aligned.
    if has_inline_asset and name_end + 12 <= len(data):
        filename_chars = struct.unpack_from("<I", data, name_end + 8)[0]
        if 0 < filename_chars < 256:
            image_rect = name_end + 16 + filename_chars * 2
            rect = _direct_rect_at(data, image_rect)
            if rect is not None:
                return image_rect, rect

    # Image/needle records carry an image-name string before their rect.  Scan
    # aligned float quads in the small verified neighbourhood and choose the
    # first proper on-canvas rectangle.  The common decoy (0,0,L,T) fails the
    # right>left/bottom>top test when L/T are both zero.
    scan_end = min(len(data) - 16, name_end + 480)
    for offset in range(name_end, scan_end + 1):
        rect = _rect_at(data, offset)
        if rect is not None:
            return offset, rect
    return None


def _find_asset_name(data: bytes, name_end: int) -> str | None:
    for match in UTF16_ASCII_RE.finditer(data, name_end, min(len(data), name_end + 500)):
        candidate = match.group().decode("utf-16le")
        if ".png" in candidate.lower():
            return candidate
    return None


def inspect(path: Path) -> dict:
    data = path.read_bytes()
    width, height = struct.unpack_from("<ii", data, 0x34)
    aspect = struct.unpack_from("<f", data, 0x3C)[0]
    gauges: list[Gauge] = []
    seen_special_names: set[str] = set()
    for match in UTF16_ASCII_RE.finditer(data):
        name = match.group().decode("utf-16le")
        if not GAUGE_NAME_RE.fullmatch(name):
            continue
        # Native Button records repeat their name in the serialized action
        # state after the page-owned visual record.  The editor exposes one
        # control, so count only its first occurrence.
        if name == "Clear Codes (hold 2 s)":
            if name in seen_special_names:
                continue
            seen_special_names.add(name)
        name_end = match.end()
        asset_name = (_find_asset_name(data, name_end)
                      if name.startswith(("Image", "Needle")) or
                      name == "Clear Codes (hold 2 s)" else None)
        found = _find_rect(data, name, name_end)
        if found is None:
            gauges.append(Gauge(name, match.start(), asset_name, None, None, None))
            continue
        rect_offset, rect = found
        left, top, right, bottom = rect
        pixels = (
            round(left * width),
            round(top * height),
            round((right - left) * width),
            round((bottom - top) * height),
        )
        gauges.append(Gauge(name, match.start(), asset_name, rect_offset, rect, pixels))

    return {
        "file": str(path),
        "size": len(data),
        "canvas": {"width": width, "height": height, "aspect": aspect},
        "gauge_count": len(gauges),
        "gauges": [asdict(gauge) for gauge in gauges],
    }


def apply_v2_layout(source: Path, output: Path) -> dict:
    """Patch only verified gauge rectangles from ``v2_manifest.py``.

    Bindings, pages, ranges, colours and assets stay editor-owned.  Refuse
    the write unless the finished editor file has the exact expected gauge
    inventory and every record has a verified rectangle offset.
    """
    from v2_manifest import EXPECTED_GAUGE_COUNT, GAUGES

    before = inspect(source)
    canvas = before["canvas"]
    width, height = canvas["width"], canvas["height"]
    from layout_v2 import EDITOR_CANVAS
    if (width, height) != tuple(EDITOR_CANVAS):
        raise ValueError("refusing non-v2 canvas %dx%d, expected %dx%d"
                         % (width, height, *EDITOR_CANVAS))
    if before["gauge_count"] != EXPECTED_GAUGE_COUNT:
        raise ValueError(
            "refusing %d-gauge file; v2 layout requires %d"
            % (before["gauge_count"], EXPECTED_GAUGE_COUNT)
        )

    records = {gauge["name"]: gauge for gauge in before["gauges"]}
    missing = sorted(set(GAUGES) - set(records))
    extra = sorted(set(records) - set(GAUGES))
    if missing or extra:
        raise ValueError("gauge-name mismatch; missing=%r extra=%r" % (missing, extra))
    no_rect = sorted(name for name, gauge in records.items()
                     if gauge["rect_offset"] is None)
    if no_rect:
        raise ValueError("unverified rectangles for %r" % no_rect)

    data = bytearray(source.read_bytes())
    for name, spec in GAUGES.items():
        x, y, gauge_width, gauge_height = spec["rect_editor"]
        rect = (x / width, y / height,
                (x + gauge_width) / width, (y + gauge_height) / height)
        struct.pack_into("<4f", data, records[name]["rect_offset"], *rect)

    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=output.parent, delete=False,
                                     prefix=output.name + ".", suffix=".tmp") as temp:
        temp.write(data)
        temp_path = Path(temp.name)
    temp_path.replace(output)

    after = inspect(output)
    actual = {gauge["name"]: tuple(gauge["rect_pixels"])
              for gauge in after["gauges"]}
    wrong = {name: {"expected": tuple(spec["rect_editor"]), "actual": actual[name]}
             for name, spec in GAUGES.items()
             if actual[name] != tuple(spec["rect_editor"])}
    if wrong:
        raise AssertionError("rectangle verification failed: %r" % wrong)
    return after


def dump_record(path: Path, gauge_name: str) -> None:
    """Print a read-only annotated hex dump for one serialized gauge record.

    This is an analysis aid for decoding additional fields.  It deliberately
    performs no writes and makes no claims about values that have not yet been
    confirmed against controlled editor changes.
    """
    data = path.read_bytes()
    result = inspect(path)
    gauges = sorted(result["gauges"], key=lambda item: item["name_offset"])
    matches = [index for index, gauge in enumerate(gauges)
               if gauge["name"] == gauge_name]
    if len(matches) != 1:
        raise ValueError("expected one gauge named %r, found %d"
                         % (gauge_name, len(matches)))
    index = matches[0]
    start = gauges[index]["name_offset"] - 4
    end = (gauges[index + 1]["name_offset"] - 4
           if index + 1 < len(gauges) else len(data))
    record = data[start:end]
    print("%s: %s record @0x%X..0x%X (%d bytes)"
          % (path, gauge_name, start, end, len(record)))
    for match in UTF16_ASCII_RE.finditer(record):
        print("  string +0x%04X: %r"
              % (match.start(), match.group().decode("utf-16le")))
    for row in range(0, len(record), 16):
        chunk = record[row:row + 16]
        hex_text = " ".join("%02X" % value for value in chunk)
        ascii_text = "".join(chr(value) if 32 <= value < 127 else "."
                             for value in chunk)
        print("  +%04X  %-47s  %s" % (row, hex_text, ascii_text))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("dashboard", type=Path)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--apply-v2-layout", metavar="OUTPUT", type=Path,
                        help="write a copy with every v2 rectangle verified")
    parser.add_argument("--dump-record", metavar="GAUGE_NAME",
                        help="print a read-only annotated hex dump for one gauge")
    args = parser.parse_args()
    if args.dump_record:
        dump_record(args.dashboard, args.dump_record)
        return
    if args.apply_v2_layout:
        result = apply_v2_layout(args.dashboard, args.apply_v2_layout)
        print("patched %d verified rectangles -> %s"
              % (result["gauge_count"], args.apply_v2_layout))
        return
    result = inspect(args.dashboard)
    if args.json:
        print(json.dumps(result, indent=2))
        return

    canvas = result["canvas"]
    print(
        f"{result['file']}: {result['size']} bytes, "
        f"{canvas['width']}x{canvas['height']} ({canvas['aspect']:.6f}), "
        f"{result['gauge_count']} named gauges"
    )
    for gauge in result["gauges"]:
        pixels = gauge["rect_pixels"]
        rect_offset = gauge["rect_offset"]
        if pixels is None:
            print(f"  {gauge['name_offset']:08X}  {gauge['name']:<18} rect NOT FOUND")
        else:
            x, y, width, height = pixels
            print(
                f"  {gauge['name_offset']:08X}  {gauge['name']:<18} "
                f"rect@{rect_offset:08X}  {x:4},{y:4} {width:4}x{height:4}"
                + (f"  {gauge['asset_name']}" if gauge['asset_name'] else "")
            )


if __name__ == "__main__":
    main()
