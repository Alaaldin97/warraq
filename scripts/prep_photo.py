"""Clean up the Kindle photograph for use as a social image.

The raw phone photo is tilted, unevenly lit, and framed against a desk. The
device itself is the subject, so this finds the screen, straightens it, lifts
the e-ink contrast, and drops the result on a neutral ground.

    python scripts/prep_photo.py <input.jpg> <output.png>
"""
from __future__ import annotations

import pathlib
import sys

import cv2
import numpy as np


def find_screen(img: np.ndarray) -> np.ndarray | None:
    """Locate the bright rectangular page area.

    The e-ink page is the largest bright quadrilateral in the frame, which is
    a much more reliable target than the dark bezel against a dark desk.
    """
    g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    g = cv2.GaussianBlur(g, (7, 7), 0)
    _, th = cv2.threshold(g, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    th = cv2.morphologyEx(th, cv2.MORPH_CLOSE,
                          cv2.getStructuringElement(cv2.MORPH_RECT, (25, 25)))
    cnts, _ = cv2.findContours(th, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not cnts:
        return None
    c = max(cnts, key=cv2.contourArea)
    if cv2.contourArea(c) < 0.15 * img.shape[0] * img.shape[1]:
        return None
    peri = cv2.arcLength(c, True)
    for eps in (0.02, 0.03, 0.04, 0.05):
        approx = cv2.approxPolyDP(c, eps * peri, True)
        if len(approx) == 4:
            return approx.reshape(4, 2).astype(np.float32)
    box = cv2.boxPoints(cv2.minAreaRect(c))
    return box.astype(np.float32)


def order_corners(pts: np.ndarray) -> np.ndarray:
    s = pts.sum(axis=1)
    d = np.diff(pts, axis=1).ravel()
    return np.array([pts[np.argmin(s)], pts[np.argmin(d)],
                     pts[np.argmax(s)], pts[np.argmax(d)]], dtype=np.float32)


def deskew_to_page(img: np.ndarray, quad: np.ndarray, pad: float = 0.06):
    """Warp the page flat, keeping a margin so some bezel stays visible."""
    tl, tr, br, bl = order_corners(quad)
    w = int(max(np.linalg.norm(tr - tl), np.linalg.norm(br - bl)))
    h = int(max(np.linalg.norm(bl - tl), np.linalg.norm(br - tr)))
    mx, my = int(w * pad), int(h * pad)
    dst = np.array([[mx, my], [w + mx, my], [w + mx, h + my], [mx, h + my]],
                   dtype=np.float32)
    m = cv2.getPerspectiveTransform(np.array([tl, tr, br, bl], np.float32), dst)
    return cv2.warpPerspective(img, m, (w + 2 * mx, h + 2 * my),
                               flags=cv2.INTER_CUBIC,
                               borderMode=cv2.BORDER_REPLICATE)


def lift_eink(img: np.ndarray) -> np.ndarray:
    """E-ink under room light is grey, flat and colour-cast. Flatten the
    illumination, neutralise the cast, then stretch the range so the page
    reads as paper and the text as ink.

    The panel is greyscale in reality, so any colour here is the room's
    lighting rather than the device, and dropping it is truer to what the
    screen actually looks like.
    """
    g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY).astype(np.float32)

    # Divide out the lighting gradient. A wide kernel keeps letterforms while
    # removing the soft shadow across the page.
    bg = cv2.GaussianBlur(g, (0, 0), sigmaX=max(g.shape) * 0.10)
    bg = np.maximum(bg, 1.0)
    flat = np.clip(g / bg * 200.0, 0, 255)

    lo, hi = np.percentile(flat, 1), np.percentile(flat, 99.5)
    if hi > lo:
        flat = np.clip((flat - lo) * (255.0 / (hi - lo)), 0, 255)
    out = flat.astype(np.uint8)

    out = cv2.createCLAHE(clipLimit=1.4, tileGridSize=(8, 8)).apply(out)
    out = cv2.addWeighted(out, 1.30, cv2.GaussianBlur(out, (0, 0), 2.5), -0.30, 0)

    # Warm the paper very slightly so it reads as a page, not a fax.
    rgb = cv2.cvtColor(out, cv2.COLOR_GRAY2BGR).astype(np.float32)
    rgb[:, :, 0] *= 0.985          # B
    rgb[:, :, 2] *= 1.008          # R
    return np.clip(rgb, 0, 255).astype(np.uint8)


def main() -> int:
    if len(sys.argv) < 3:
        print(__doc__, file=sys.stderr)
        return 2
    src, dst = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])
    img = cv2.imread(str(src))
    if img is None:
        print(f"cannot read {src}", file=sys.stderr)
        return 1

    quad = find_screen(img)
    if quad is None:
        print("could not locate the page - falling back to the whole frame",
              file=sys.stderr)
        out = img
    else:
        out = deskew_to_page(img, quad)
    out = lift_eink(out)

    dst.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(dst), out, [cv2.IMWRITE_PNG_COMPRESSION, 6])
    print(f"wrote {dst}  {out.shape[1]}x{out.shape[0]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
