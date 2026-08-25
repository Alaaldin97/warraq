"""Page image cleanup: deskew, denoise, contrast, border/margin crop, resize."""
from __future__ import annotations

import cv2
import numpy as np


def deskew(gray: np.ndarray, angle: float) -> np.ndarray:
    if abs(angle) < 0.12 or abs(angle) > 20:
        return gray
    h, w = gray.shape
    m = cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1.0)
    return cv2.warpAffine(gray, m, (w, h), flags=cv2.INTER_CUBIC,
                          borderMode=cv2.BORDER_REPLICATE)


def remove_scan_border(gray: np.ndarray) -> np.ndarray:
    """Remove dark scanner edges/shadow frames around the page."""
    h, w = gray.shape
    band = max(2, int(min(h, w) * 0.012))
    out = gray.copy()
    for sl in (np.s_[:band, :], np.s_[-band:, :], np.s_[:, :band], np.s_[:, -band:]):
        strip = out[sl]
        if strip.mean() < 110:            # dark frame artefact
            out[sl] = 255
    return out


def denoise(gray: np.ndarray, strength: str = "medium") -> np.ndarray:
    if strength == "none":
        return gray
    g = cv2.medianBlur(gray, 3)
    if strength == "strong":
        g = cv2.fastNlMeansDenoising(g, None, 8, 7, 21)
    # remove isolated specks in the ink layer only
    _, ink = cv2.threshold(g, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    n, lab, stats, _ = cv2.connectedComponentsWithStats(ink, connectivity=8)
    if n > 1:
        kill = np.zeros(n, dtype=bool)
        for i in range(1, n):
            a = stats[i, cv2.CC_STAT_AREA]
            bw, bh = stats[i, cv2.CC_STAT_WIDTH], stats[i, cv2.CC_STAT_HEIGHT]
            if a <= 3 and bw <= 3 and bh <= 3:
                kill[i] = True
        mask = kill[lab]
        g[mask] = 255
    return g


def enhance_contrast(gray: np.ndarray, aggressive: bool = False) -> np.ndarray:
    """Flatten scanner shading, then stretch contrast for e-ink."""
    bg = cv2.medianBlur(gray, 31)
    bg = np.where(bg < 40, 40, bg).astype(np.float32)
    flat = np.clip(gray.astype(np.float32) / bg * 235.0, 0, 255).astype(np.uint8)
    clahe = cv2.createCLAHE(clipLimit=2.6 if aggressive else 1.8, tileGridSize=(8, 8))
    out = clahe.apply(flat)
    lo, hi = np.percentile(out, (2, 98))
    if hi - lo > 20:
        out = np.clip((out.astype(np.float32) - lo) * 255.0 / (hi - lo), 0, 255).astype(np.uint8)
    return out


def crop_to_content(gray: np.ndarray, bbox_frac, pad_frac: float = 0.012) -> np.ndarray:
    """Crop away excess margins, keeping a small even padding."""
    if not bbox_frac:
        return gray
    h, w = gray.shape
    x0 = int(bbox_frac[0] * w)
    y0 = int(bbox_frac[1] * h)
    x1 = int(bbox_frac[2] * w)
    y1 = int(bbox_frac[3] * h)
    px, py = int(w * pad_frac), int(h * pad_frac)
    x0, y0 = max(0, x0 - px), max(0, y0 - py)
    x1, y1 = min(w, x1 + px), min(h, y1 + py)
    if x1 - x0 < w * 0.25 or y1 - y0 < h * 0.25:
        return gray                      # refuse suspicious crops
    return gray[y0:y1, x0:x1]


def fit_to_screen(gray: np.ndarray, target=(1264, 1680), margin: int = 26,
                  bg: int = 255) -> np.ndarray:
    """Scale to fit the device screen, centred, with uniform page dimensions."""
    tw, th = target
    aw, ah = tw - 2 * margin, th - 2 * margin
    h, w = gray.shape
    s = min(aw / w, ah / h)
    interp = cv2.INTER_AREA if s < 1 else cv2.INTER_CUBIC
    resized = cv2.resize(gray, (max(1, int(w * s)), max(1, int(h * s))), interpolation=interp)
    canvas = np.full((th, tw), bg, dtype=np.uint8)
    rh, rw = resized.shape
    y = (th - rh) // 2
    x = (tw - rw) // 2
    canvas[y:y + rh, x:x + rw] = resized
    return canvas


def quantize_gray(gray: np.ndarray, levels: int = 16) -> np.ndarray:
    """Match e-ink's 16-level greyscale so files stay small without banding."""
    step = 256 // levels
    return ((gray // step) * step + step // 2).clip(0, 255).astype(np.uint8)


def clean_page(gray: np.ndarray, page_info: dict, *, aggressive: bool = False,
               do_crop: bool = True) -> np.ndarray:
    g = remove_scan_border(gray)
    g = deskew(g, page_info.get("skew_deg", 0.0))
    g = denoise(g, "strong" if aggressive else "medium")
    g = enhance_contrast(g, aggressive)
    if do_crop:
        g = crop_to_content(g, page_info.get("content_bbox_frac"))
    return g
