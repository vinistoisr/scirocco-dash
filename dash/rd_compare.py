#!/usr/bin/env python3
"""Compare one RealDash gauge record across two saved dashboards.

This is a read-only reverse-engineering aid.  It finds records through the
same verified manifest logic as the production patchers, then reports compact
runs of changed bytes relative to each record rather than comparing unstable
absolute file offsets (the embedded PNG table can change size).
"""

from __future__ import annotations

import argparse
import struct
from pathlib import Path

import rd_manifest


def record(path: Path, name: str) -> bytes:
    data = path.read_bytes()
    gauges = sorted(rd_manifest.inspect(path)["gauges"],
                    key=lambda item: item["name_offset"])
    matches = [index for index, gauge in enumerate(gauges)
               if gauge["name"] == name]
    if len(matches) != 1:
        raise ValueError(f"{path}: expected one {name!r}, found {len(matches)}")
    index = matches[0]
    start = gauges[index]["name_offset"] - 4
    end = (gauges[index + 1]["name_offset"] - 4
           if index + 1 < len(gauges) else len(data))
    return data[start:end]


def changed_runs(before: bytes, after: bytes) -> list[tuple[int, int]]:
    if len(before) != len(after):
        raise ValueError(f"record sizes differ: {len(before)} != {len(after)}")
    positions = [index for index, pair in enumerate(zip(before, after))
                 if pair[0] != pair[1]]
    if not positions:
        return []
    runs: list[tuple[int, int]] = []
    start = previous = positions[0]
    for position in positions[1:]:
        if position != previous + 1:
            runs.append((start, previous + 1))
            start = position
        previous = position
    runs.append((start, previous + 1))
    return runs


def describe(chunk: bytes) -> str:
    parts = [chunk.hex(" ").upper()]
    if len(chunk) == 4:
        parts.append(f"u32={struct.unpack('<I', chunk)[0]}")
        parts.append(f"f32={struct.unpack('<f', chunk)[0]:.9g}")
    elif len(chunk) == 8:
        parts.append("2xf32=" + repr(struct.unpack("<2f", chunk)))
    elif len(chunk) % 4 == 0 and len(chunk) <= 32:
        parts.append("f32=" + repr(struct.unpack("<%df" % (len(chunk) // 4), chunk)))
    return " | ".join(parts)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("before", type=Path)
    parser.add_argument("after", type=Path)
    parser.add_argument("gauge")
    parser.add_argument("--context", type=int, default=4)
    args = parser.parse_args()

    before = record(args.before, args.gauge)
    after = record(args.after, args.gauge)
    print(f"{args.gauge}: {len(before)} bytes")
    runs = changed_runs(before, after)
    print(f"changed runs: {len(runs)}")
    for start, end in runs:
        left = max(0, start - args.context)
        right = min(len(before), end + args.context)
        print(f"  +0x{start:04X}..+0x{end:04X}")
        print(f"    before[{left:#x}:{right:#x}] {describe(before[left:right])}")
        print(f"    after [{left:#x}:{right:#x}] {describe(after[left:right])}")


if __name__ == "__main__":
    main()
