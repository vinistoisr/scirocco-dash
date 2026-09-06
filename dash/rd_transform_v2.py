#!/usr/bin/env python3
"""Apply verified Scirocco v2 asset and record transformations to a .rd file.

The RealDash asset table and its UTF-16 strings are length-prefixed.  This
tool rebuilds only those self-describing structures.  It does not guess at
bindings, ranges, actions, colours, or other undocumented gauge fields.
"""

from __future__ import annotations

import argparse
import hashlib
import struct
import tempfile
from dataclasses import dataclass
from pathlib import Path


# Verified against the 2.4.1 Windows editor header.  These fields are not
# naturally aligned: two bytes of header state precede the u32 image count.
ASSET_COUNT_OFFSET = 0x72
ASSET_TABLE_OFFSET = 0x76
PAGE_MARKER = b"\x53\xc3\x00\x00"
BUTTON_OLD_NAME = "Button 1Button Gauge 1"
BUTTON_NAME = "Clear Codes (hold 2 s)"


@dataclass(frozen=True)
class Asset:
    name: str
    data: bytes
    flags: int = 0


def read_lp_utf16(data: bytes, offset: int) -> tuple[str, int]:
    chars = struct.unpack_from("<I", data, offset)[0]
    start = offset + 4
    end = start + chars * 2
    if end > len(data):
        raise ValueError("UTF-16 string exceeds file at 0x%X" % offset)
    return data[start:end].decode("utf-16le"), end


def write_lp_utf16(value: str) -> bytes:
    encoded = value.encode("utf-16le")
    return struct.pack("<I", len(value)) + encoded


def parse_assets(data: bytes) -> tuple[list[Asset], int]:
    count = struct.unpack_from("<I", data, ASSET_COUNT_OFFSET)[0]
    offset = ASSET_TABLE_OFFSET
    assets: list[Asset] = []
    for index in range(count):
        if offset + 4 > len(data):
            raise ValueError("asset %d flags exceed file" % index)
        flags = struct.unpack_from("<I", data, offset)[0]
        offset += 4
        name, offset = read_lp_utf16(data, offset)
        if offset + 8 > len(data):
            raise ValueError("asset %d length exceeds file" % index)
        size = struct.unpack_from("<Q", data, offset)[0]
        offset += 8
        end = offset + size
        if end > len(data):
            raise ValueError("asset %r exceeds file" % name)
        blob = data[offset:end]
        if not blob.startswith(b"\x89PNG\r\n\x1a\n") or b"IEND" not in blob[-32:]:
            raise ValueError("asset %r is not a complete PNG" % name)
        assets.append(Asset(name, blob, flags))
        offset = end
    return assets, offset


def build_assets(assets: list[Asset]) -> bytes:
    output = bytearray()
    for asset in assets:
        output += struct.pack("<I", asset.flags)
        output += write_lp_utf16(asset.name)
        output += struct.pack("<Q", len(asset.data))
        output += asset.data
    return bytes(output)


def replace_lp_exact(data: bytes, old: str, new: str, *, limit: int | None = None) -> tuple[bytes, int]:
    """Replace length-prefixed UTF-16 strings whose value exactly matches."""
    needle = write_lp_utf16(old)
    replacement = write_lp_utf16(new)
    count = data.count(needle) if limit is None else min(data.count(needle), limit)
    if count:
        data = data.replace(needle, replacement, count)
    return data, count


def gauge_offsets(data: bytes) -> list[tuple[int, str]]:
    prefixes = ("Image Gauge ", "Needle Gauge ", "Text Gauge ", "Bar Gauge ")
    found: list[tuple[int, str]] = []
    for prefix in prefixes:
        number = 1
        while True:
            value = "%s%d" % (prefix, number)
            offset = data.find(write_lp_utf16(value))
            if offset < 0:
                break
            found.append((offset, value))
            number += 1
    special = data.find(write_lp_utf16(BUTTON_NAME))
    if special >= 0:
        found.append((special, BUTTON_NAME))
    return sorted(found)


def replace_in_gauge(data: bytes, gauge_name: str, old: str, new: str) -> tuple[bytes, int]:
    records = gauge_offsets(data)
    matches = [item for item in records if item[1] == gauge_name]
    if len(matches) != 1:
        raise ValueError("expected one page-owned %r record, found %d" % (gauge_name, len(matches)))
    start = matches[0][0]
    later = [offset for offset, _ in records if offset > start]
    end = min(later) if later else len(data)
    record = data[start:end]
    record, count = replace_lp_exact(record, old, new)
    if not count:
        raise ValueError("%r does not reference %r" % (gauge_name, old))
    return data[:start] + record + data[end:], count


def assign_page_background(data: bytes, first_gauge: str, asset_name: str) -> bytes:
    gauge = data.find(write_lp_utf16(first_gauge))
    if gauge < 0:
        raise ValueError("page first gauge %r not found" % first_gauge)
    marker = data.rfind(PAGE_MARKER, max(0, gauge - 240), gauge)
    if marker < 0:
        raise ValueError("page marker before %r not found" % first_gauge)
    cursor = marker + len(PAGE_MARKER)
    first, first_end = read_lp_utf16(data, cursor)
    second, second_end = read_lp_utf16(data, first_end)
    if first or second:
        expected = (asset_name, asset_name)
        if (first, second) == expected:
            return data
        raise ValueError("page before %r already has unexpected background %r/%r" %
                         (first_gauge, first, second))
    refs = write_lp_utf16(asset_name) + write_lp_utf16(asset_name)
    return data[:cursor] + refs + data[second_end:]


def transform(source: Path, output: Path, assets_dir: Path) -> dict:
    raw = source.read_bytes()
    before_assets, suffix_offset = parse_assets(raw)

    requested = {path.name: path.read_bytes() for path in assets_dir.glob("*.png")}
    required = {
        "blank.png", "bg_page1.png", "bg_page2.png", "bg_page3.png", "bg_page4.png",
        "btn_clear_codes.png", "face_boost.png", "face_charge.png", "face_coolant.png",
        "face_oil.png", "face_rpm.png", "face_volt.png", "ind_boostdev.png",
        "ind_knock.png", "ind_mil.png", "ind_overtemp.png", "ind_turn_left.png",
        "ind_turn_right.png", "needle_main.png", "needle_main_ghost.png",
        "needle_small.png", "needle_spark.png", "needle_target.png",
        "wheel_front_left.png", "wheel_front_right.png",
    }
    missing = sorted(required - requested.keys())
    if missing:
        raise ValueError("missing generated assets: %r" % missing)

    replaced_names: set[str] = set()
    assets: list[Asset] = []
    for asset in before_assets:
        if asset.name in requested:
            assets.append(Asset(asset.name, requested[asset.name], asset.flags))
            replaced_names.add(asset.name)
        else:
            assets.append(asset)
    existing_names = {asset.name for asset in assets}
    for name in sorted(required - existing_names):
        assets.append(Asset(name, requested[name], 0))

    header = bytearray(raw[:ASSET_TABLE_OFFSET])
    struct.pack_into("<I", header, ASSET_COUNT_OFFSET, len(assets))
    data = bytes(header) + build_assets(assets) + raw[suffix_offset:]

    data, renamed = replace_lp_exact(data, BUTTON_OLD_NAME, BUTTON_NAME)
    if renamed != 2:
        raise ValueError("expected two serialized button names, replaced %d" % renamed)

    data = assign_page_background(data, "Text Gauge 22", "bg_page3.png")
    data = assign_page_background(data, "Text Gauge 21", "bg_page4.png")

    indicator_assets = {
        "Image Gauge 7": "ind_turn_left.png",
        "Image Gauge 8": "ind_turn_right.png",
        "Image Gauge 9": "ind_mil.png",
        "Image Gauge 10": "ind_overtemp.png",
        "Image Gauge 11": "ind_knock.png",
        "Image Gauge 12": "ind_boostdev.png",
    }
    for gauge_name, asset_name in indicator_assets.items():
        data, _ = replace_in_gauge(data, gauge_name, "_indicators.png_", asset_name)

    needle_assets = {
        "Needle Gauge 8": "needle_main_ghost.png",
        "Needle Gauge 9": "needle_main_ghost.png",
        "Needle Gauge 10": "needle_spark.png",
        "Needle Gauge 11": "wheel_front_left.png",
        "Needle Gauge 12": "wheel_front_right.png",
        "Needle Gauge 13": "needle_spark.png",
    }
    for gauge_name, needle_asset in needle_assets.items():
        data, _ = replace_in_gauge(data, gauge_name, "roundface.png", "blank.png")
        data, _ = replace_in_gauge(data, gauge_name, "needle_main.png", needle_asset)

    data, button_asset_refs = replace_in_gauge(
        data, BUTTON_NAME, "button.png", "btn_clear_codes.png")
    # Native button labels are serialized separately from its asset refs.
    button_start = data.find(write_lp_utf16(BUTTON_NAME))
    prefix, tail = data[:button_start], data[button_start:]
    # Keep the native label even though btn_clear_codes.png carries its own
    # caption. Blanking it on 2026-08-29 made the button ILLEGIBLE: RealDash
    # renders the button artwork heavily dimmed, so the native white label
    # was the only readable part. Restore the artwork's brightness first,
    # then revisit whether the label is redundant.
    tail, button_labels = replace_lp_exact(tail, "Button", "CLEAR CODES")
    data = prefix + tail

    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=output.parent, delete=False,
                                     prefix=output.name + ".", suffix=".tmp") as temp:
        temp.write(data)
        temp_path = Path(temp.name)
    temp_path.replace(output)

    final_assets, _ = parse_assets(output.read_bytes())
    final_names = {asset.name for asset in final_assets}
    if not required <= final_names:
        raise AssertionError("asset-table verification failed")
    return {
        "source_sha256": hashlib.sha256(raw).hexdigest(),
        "output_sha256": hashlib.sha256(data).hexdigest(),
        "assets_before": len(before_assets),
        "assets_after": len(final_assets),
        "library_names_replaced": sorted(replaced_names),
        "button_asset_refs": button_asset_refs,
        "button_labels": button_labels,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--assets", type=Path,
                        default=Path(__file__).with_name("assets") / "png")
    args = parser.parse_args()
    print(transform(args.source, args.output, args.assets))


if __name__ == "__main__":
    main()
