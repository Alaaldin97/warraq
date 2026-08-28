"""Per-page and document-level PDF analysis.

Every page is inspected for: text layer, language, columns, skew, margins,
noise, blankness, duplication, rotation, image resolution and repeated
headers/footers. Produces a JSON-serialisable analysis dict that drives routing.
"""
from __future__ import annotations

import collections
import hashlib
import math
import re

import cv2
import pymupdf as fitz
import numpy as np

from . import arabic

ANALYSIS_DPI = 110


# ---------------------------------------------------------------- rendering
def render_gray(page: fitz.Page, dpi: int = ANALYSIS_DPI) -> np.ndarray:
    zoom = dpi / 72.0
    pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), colorspace=fitz.csGRAY,
                          alpha=False)
    return np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width)


def _binarize(gray: np.ndarray) -> np.ndarray:
    """Return ink mask (255 = ink)."""
    blur = cv2.GaussianBlur(gray, (3, 3), 0)
    _, th = cv2.threshold(blur, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    return th


# ---------------------------------------------------------------- metrics
def estimate_skew(ink: np.ndarray) -> float:
    """Skew angle in degrees (positive = counter-clockwise correction needed)."""
    h, w = ink.shape
    small = cv2.resize(ink, (min(w, 1000), int(h * min(w, 1000) / w)))
    # connect characters into text lines so their orientation dominates
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (25, 3))
    closed = cv2.morphologyEx(small, cv2.MORPH_CLOSE, kernel)
    coords = cv2.findNonZero(closed)
    if coords is None or len(coords) < 100:
        return 0.0
    angles = []
    contours, _ = cv2.findContours(closed, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    for c in contours:
        if cv2.contourArea(c) < 200:
            continue
        (_, _), (bw, bh), ang = cv2.minAreaRect(c)
        if max(bw, bh) < 40:
            continue
        if bw < bh:
            ang += 90
        if -20 < ang < 20:
            angles.append(ang)
    if not angles:
        return 0.0
    return float(np.median(angles))


def estimate_noise(gray: np.ndarray, ink: np.ndarray) -> dict:
    """Speckle / shadow / background-defect estimate."""
    n, labels, stats, _ = cv2.connectedComponentsWithStats(ink, connectivity=8)
    if n <= 1:
        return {"speckle_ratio": 0.0, "bg_uniformity": 1.0, "components": 0}
    areas = stats[1:, cv2.CC_STAT_AREA]
    speck = int((areas <= 4).sum())
    # background brightness spread -> scanner shadow / gray cast
    bg = gray[ink == 0]
    uniformity = 1.0 - (float(bg.std()) / 128.0) if bg.size else 1.0
    return {"speckle_ratio": round(speck / max(len(areas), 1), 3),
            "bg_uniformity": round(max(0.0, min(1.0, uniformity)), 3),
            "bg_mean": round(float(bg.mean()), 1) if bg.size else 255.0,
            "components": int(n - 1)}


def content_bbox(ink: np.ndarray, ignore_border_frac: float = 0.01) -> tuple | None:
    """Tight bounding box of real content, ignoring scan border artefacts."""
    h, w = ink.shape
    m = int(min(h, w) * ignore_border_frac)
    core = ink[m:h - m, m:w - m] if m else ink
    # drop specks before measuring
    core = cv2.morphologyEx(core, cv2.MORPH_OPEN,
                            cv2.getStructuringElement(cv2.MORPH_RECT, (2, 2)))
    cols = core.sum(axis=0)
    rows = core.sum(axis=1)
    cthr = max(cols.max() * 0.004, 255 * 2)
    rthr = max(rows.max() * 0.004, 255 * 2)
    xs = np.where(cols > cthr)[0]
    ys = np.where(rows > rthr)[0]
    if xs.size == 0 or ys.size == 0:
        return None
    return (int(xs[0] + m), int(ys[0] + m), int(xs[-1] + m + 1), int(ys[-1] + m + 1))


def detect_columns(ink: np.ndarray, bbox) -> int:
    """Count text columns via vertical whitespace gutters inside the content box."""
    if bbox is None:
        return 1
    x0, y0, x1, y1 = bbox
    region = ink[y0:y1, x0:x1]
    if region.size == 0:
        return 1
    h, w = region.shape
    if w < 100:
        return 1
    profile = (region > 0).sum(axis=0).astype(float)
    profile = cv2.GaussianBlur(profile.reshape(1, -1), (1, 9), 0).ravel()
    empty = profile < (h * 0.012)
    # find internal gutters wide enough to separate columns
    gutters, run = [], 0
    min_gut = max(int(w * 0.035), 10)
    for i, e in enumerate(empty):
        if e:
            run += 1
        else:
            if run >= min_gut:
                gutters.append((i - run, i))
            run = 0
    edge = int(w * 0.10)
    inner = [g for g in gutters if g[0] > edge and g[1] < w - edge]
    # a column split must be near-central-ish and text must exist on both sides
    valid = 0
    for a, b in inner:
        left = profile[:a].sum()
        right = profile[b:].sum()
        if left > h * 5 and right > h * 5:
            valid += 1
    return min(valid + 1, 4)


def page_signature(gray: np.ndarray) -> str:
    small = cv2.resize(gray, (32, 32), interpolation=cv2.INTER_AREA)
    return hashlib.md5((small > small.mean()).tobytes()).hexdigest()


def image_dpi(page: fitz.Page) -> float | None:
    """Effective DPI of the dominant raster image on the page."""
    best = None
    pw = page.rect.width / 72.0
    for img in page.get_images(full=True):
        try:
            w, h = img[2], img[3]
        except Exception:
            continue
        if pw > 0:
            d = w / pw
            if best is None or d > best:
                best = d
    return round(best, 1) if best else None


# ---------------------------------------------------------------- page pass
def analyze_page(page: fitz.Page) -> dict:
    text = page.get_text("text") or ""
    raw = page.get_text("dict")
    blocks = [b for b in raw.get("blocks", []) if b.get("type") == 0]
    img_blocks = [b for b in raw.get("blocks", []) if b.get("type") == 1]
    gray = render_gray(page)
    ink = _binarize(gray)
    h, w = ink.shape
    ink_frac = float((ink > 0).sum()) / (h * w)
    bbox = content_bbox(ink)
    noise = estimate_noise(gray, ink)

    margins = None
    if bbox:
        margins = {"left": round(bbox[0] / w, 4), "top": round(bbox[1] / h, 4),
                   "right": round((w - bbox[2]) / w, 4),
                   "bottom": round((h - bbox[3]) / h, 4)}

    chars = len(text.strip())
    is_blank = chars < 3 and ink_frac < 0.002
    lang = arabic.classify_language(text) if chars > 30 else None

    # a page is 'scanned' when it is essentially one big image with no/scarce text
    img_area = sum((b["bbox"][2] - b["bbox"][0]) * (b["bbox"][3] - b["bbox"][1])
                   for b in img_blocks)
    page_area = max(page.rect.width * page.rect.height, 1)
    img_cover = img_area / page_area
    scanned = img_cover > 0.55 and chars < 60
    image_backed = img_cover > 0.55

    return {
        "number": page.number + 1,
        "rotation": page.rotation,
        "size_pt": [round(page.rect.width, 1), round(page.rect.height, 1)],
        "chars": chars,
        "text_blocks": len(blocks),
        "image_blocks": len(img_blocks),
        "image_coverage": round(img_cover, 3),
        "image_dpi": image_dpi(page),
        "ink_fraction": round(ink_frac, 4),
        "blank": bool(is_blank),
        "scanned": bool(scanned),
        "image_backed": bool(image_backed),
        "language": lang,
        "arabic_ratio": round(arabic.arabic_ratio(text), 3) if chars else 0.0,
        "presentation_forms": arabic.has_presentation_forms(text),
        "columns": detect_columns(ink, bbox) if not is_blank else 1,
        "skew_deg": round(estimate_skew(ink), 2) if not is_blank else 0.0,
        "margins": margins,
        "content_bbox_frac": ([round(bbox[0] / w, 4), round(bbox[1] / h, 4),
                               round(bbox[2] / w, 4), round(bbox[3] / h, 4)]
                              if bbox else None),
        "noise": noise,
        "signature": page_signature(gray),
        "top_line": _edge_line(page, "top"),
        "bottom_line": _edge_line(page, "bottom"),
    }


def _edge_line(page: fitz.Page, which: str) -> str:
    """First/last text line, used for repeated header/footer detection."""
    d = page.get_text("dict")
    lines = []
    for b in d.get("blocks", []):
        if b.get("type") != 0:
            continue
        for ln in b.get("lines", []):
            txt = "".join(s.get("text", "") for s in ln.get("spans", [])).strip()
            if txt:
                lines.append((ln["bbox"][1], txt))
    if not lines:
        return ""
    lines.sort()
    band = page.rect.height * 0.09
    if which == "top":
        y, t = lines[0]
        return t if y <= page.rect.y0 + band else ""
    y, t = lines[-1]
    return t if y >= page.rect.y1 - band else ""


# ---------------------------------------------------------------- doc pass
_NUM_RE = re.compile(r"\d+|[\u0660-\u0669]+")


def _repeat_key(s: str) -> str:
    return _NUM_RE.sub("#", s).strip().lower()


def detect_running_heads(pages: list[dict]) -> dict:
    """Identify header/footer strings that repeat across the book."""
    out = {}
    for slot in ("top_line", "bottom_line"):
        counter = collections.Counter(_repeat_key(p[slot]) for p in pages if p[slot])
        n = max(len(pages), 1)
        repeated = {k: c for k, c in counter.items()
                    if k and c >= max(3, n * 0.25) and len(k) <= 90}
        out[slot] = repeated
    return out


def analyze(pdf_path: str, max_pages: int | None = None,
            sample_pages: int | None = None) -> dict:
    """Full or sampled analysis.

    `sample_pages` analyses a spread of N pages instead of every page. Used by
    the interactive inspect pass, where a 225-page full scan costs ~53 s and
    would stall the UI. Sampled results carry `sampled: True` and must not be
    used to drive the conversion itself.
    """
    doc = fitz.open(pdf_path)
    total = doc.page_count
    n = total if max_pages is None else min(total, max_pages)

    if sample_pages and sample_pages < n:
        # Always include the first pages (cover/title carry metadata) plus an
        # even spread through the body.
        head = list(range(min(3, n)))
        step = max(1, (n - 1) / max(sample_pages - len(head), 1))
        body = sorted({int(head[-1] + 1 + i * step) for i in range(sample_pages)})
        idxs = sorted({i for i in head + body if 0 <= i < n})
    else:
        idxs = list(range(n))

    pages = [analyze_page(doc[i]) for i in idxs]
    sampled = len(idxs) < n

    all_text = "\n".join((doc[i].get_text("text") or "") for i in idxs[:40])
    text_pages = [p for p in pages if p["chars"] >= 60]
    scanned_pages = [p for p in pages if p["scanned"]]
    blanks = [p["number"] for p in pages if p["blank"]]

    sigs = collections.Counter(p["signature"] for p in pages if not p["blank"])
    dupes = [s for s, c in sigs.items() if c > 1]
    dupe_pages = [p["number"] for p in pages if p["signature"] in dupes]

    langs = collections.Counter(p["language"] for p in text_pages if p["language"])
    if langs:
        doc_lang = arabic.classify_language(all_text)
    else:
        doc_lang = None   # decided after OCR

    text_ratio = len(text_pages) / max(len(pages), 1)
    image_backed_ratio = (sum(1 for p in pages if p["image_backed"])
                          / max(len(pages), 1))
    if text_ratio > 0.85 and image_backed_ratio >= 0.6:
        # Scanned pages that already carry an OCR text layer (common with
        # archive.org / library scans). That layer is often low quality and in
        # the wrong reading order, so it must not be trusted blindly.
        doc_type = "text_over_scan"
    elif text_ratio > 0.85:
        doc_type = "text"
    elif text_ratio < 0.15:
        doc_type = "scanned"
    else:
        doc_type = "mixed"

    col_counter = collections.Counter(p["columns"] for p in pages if not p["blank"])
    dominant_cols = col_counter.most_common(1)[0][0] if col_counter else 1

    skews = [abs(p["skew_deg"]) for p in pages if not p["blank"]]
    noisy = [p["number"] for p in pages
             if p["noise"]["speckle_ratio"] > 0.35 or p["noise"]["bg_uniformity"] < 0.72]

    md = doc.metadata or {}
    toc = doc.get_toc(simple=True) or []
    fonts = set()
    for i in range(min(n, 25)):
        for f in doc.get_page_fonts(i):
            fonts.add(f[3])

    result = {
        "file": pdf_path,
        "page_count": doc.page_count,
        "analyzed_pages": len(pages),
        "sampled": sampled,
        "metadata": {k: (v or "") for k, v in md.items()},
        "toc_entries": len(toc),
        "toc": toc[:400],
        "fonts": sorted(fonts)[:40],
        "doc_type": doc_type,
        "language": doc_lang,
        "arabic_ratio": round(arabic.arabic_ratio(all_text), 3),
        "latin_ratio": round(arabic.latin_ratio(all_text), 3),
        "presentation_forms": arabic.has_presentation_forms(all_text),
        "dominant_columns": dominant_cols,
        "column_histogram": dict(col_counter),
        "text_page_ratio": round(text_ratio, 3),
        "image_backed_ratio": round(image_backed_ratio, 3),
        "scanned_page_count": len(scanned_pages),
        "blank_pages": blanks,
        "duplicate_pages": dupe_pages,
        "rotated_pages": [p["number"] for p in pages if p["rotation"] % 360],
        "skew_median": round(float(np.median(skews)), 2) if skews else 0.0,
        "skew_max": round(max(skews), 2) if skews else 0.0,
        "noisy_pages": noisy,
        "low_dpi_pages": [p["number"] for p in pages
                          if p["image_dpi"] and p["image_dpi"] < 200],
        "running_heads": detect_running_heads(pages),
        "page_size_variants": sorted({tuple(p["size_pt"]) for p in pages}),
        "pages": pages,
    }
    doc.close()
    return result


def summarize(a: dict) -> str:
    rh = a["running_heads"]
    lines = [
        f"File           : {a['file']}",
        f"Pages          : {a['page_count']} (analyzed {a['analyzed_pages']})",
        f"Document type  : {a['doc_type']}",
        f"Language       : {a['language']} (arabic {a['arabic_ratio']}, latin {a['latin_ratio']})",
        f"Presentation   : {'yes - needs normalization' if a['presentation_forms'] else 'no'}",
        f"Columns        : dominant {a['dominant_columns']}  {a['column_histogram']}",
        f"TOC entries    : {a['toc_entries']}",
        f"Skew           : median {a['skew_median']}deg  max {a['skew_max']}deg",
        f"Blank pages    : {len(a['blank_pages'])}",
        f"Duplicate pages: {len(a['duplicate_pages'])}",
        f"Rotated pages  : {len(a['rotated_pages'])}",
        f"Noisy pages    : {len(a['noisy_pages'])}",
        f"Low-DPI pages  : {len(a['low_dpi_pages'])}",
        f"Running heads  : top={len(rh['top_line'])} bottom={len(rh['bottom_line'])}",
        f"Page sizes     : {a['page_size_variants'][:4]}",
    ]
    return "\n".join(lines)
