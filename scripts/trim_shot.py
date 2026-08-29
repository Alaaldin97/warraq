"""Trim the empty middle of an app screenshot.

The library view is mostly whitespace below the drop zone, which wastes half
a portrait card. This keeps the top content and the status bar and removes
the blank band between them, with a hairline to mark the join.
"""
from __future__ import annotations

import pathlib
import sys

import cv2
import numpy as np


def main() -> int:
    src, dst = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])
    keep_top = int(sys.argv[3]) if len(sys.argv) > 3 else 585
    status_h = int(sys.argv[4]) if len(sys.argv) > 4 else 34

    img = cv2.imread(str(src))
    if img is None:
        print(f"cannot read {src}", file=sys.stderr)
        return 1

    h = img.shape[0]
    top = img[:keep_top]
    status = img[h - status_h:]
    gap = np.full((1, img.shape[1], 3), 214, np.uint8)

    out = np.vstack([top, gap, status])
    cv2.imwrite(str(dst), out)
    print(f"wrote {dst}  {out.shape[1]}x{out.shape[0]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
