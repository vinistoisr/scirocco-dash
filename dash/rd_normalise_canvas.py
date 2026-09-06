#!/usr/bin/env python3
"""Restore a RealDash save's design resolution to the v2 editor canvas.

RealDash writes the *window client area* as the dashboard's design resolution
every time it saves, so a file edited in a 1920x1080 window comes back
declaring 1904x1039 and every normalised rectangle re-fitted to that aspect.
That is fatal for the pipeline: ``rd_config_v2`` refuses anything that is not
1920x1080, by design.

This is only safe because of what runs next.  ``rd_manifest --apply-v2-layout``
rewrites all of the rectangles from ``v2_manifest``, so the re-fitted geometry
in the saved file is discarded wholesale a moment later; the only thing worth
keeping from a RealDash save is the *structure* it added.  Restoring the header
is therefore a header edit, not a geometry conversion, and it must always be
followed by the layout stage.

Use it when the editor has been used to add or remove controls and the result
needs to become the pipeline's new snapshot:

    python dash/rd_normalise_canvas.py <saved.rd> <snapshot.rd>
"""

from __future__ import annotations

import argparse
import struct
import tempfile
from pathlib import Path

CANVAS_OFFSET = 0x34            # two int32: width, height
ASPECT_OFFSET = 0x3C            # float32: width / height
import sys as _sys
_sys.path.insert(0, str(Path(__file__).resolve().parent))
from layout_v2 import EDITOR_CANVAS as TARGET


def normalise(source: Path, output: Path) -> str:
    data = bytearray(source.read_bytes())
    width, height = struct.unpack_from("<ii", data, CANVAS_OFFSET)
    aspect = struct.unpack_from("<f", data, ASPECT_OFFSET)[0]
    if not (320 <= width <= 8192 and 240 <= height <= 8192):
        raise ValueError("%s: header does not look like a canvas (%dx%d)"
                         % (source, width, height))

    struct.pack_into("<ii", data, CANVAS_OFFSET, *TARGET)
    struct.pack_into("<f", data, ASPECT_OFFSET, TARGET[0] / TARGET[1])

    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=output.parent, delete=False,
                                     prefix=output.name + ".",
                                     suffix=".tmp") as temp:
        temp.write(data)
        temp_path = Path(temp.name)
    temp_path.replace(output)

    final_w, final_h = struct.unpack_from("<ii", output.read_bytes(),
                                          CANVAS_OFFSET)
    if (final_w, final_h) != TARGET:
        raise AssertionError("canvas verification failed: %dx%d"
                             % (final_w, final_h))
    return ("%dx%d (aspect %.6f) -> %dx%d; run --apply-v2-layout next"
            % (width, height, aspect, *TARGET))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    print(normalise(args.source, args.output))


if __name__ == "__main__":
    main()
