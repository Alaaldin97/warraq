"""Tesseract OCR wrapper with per-word confidence and searchable-PDF output."""
from __future__ import annotations

import csv
import io
import os
from . import proc
import tempfile

import cv2
import numpy as np

TESSERACT = os.environ.get(
    "KBO_TESSERACT", r"C:\Program Files\Tesseract-OCR\tesseract.exe")
TESSDATA = os.environ.get(
    "KBO_TESSDATA", os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "tools", "tessdata"))

LANG_MAP = {"en": "eng", "ar": "ara", "bilingual": "ara+eng", "ar+en": "ara+eng"}


def available() -> bool:
    return os.path.exists(TESSERACT)


def languages() -> list[str]:
    out = proc.run([TESSERACT, "--list-langs"], capture_output=True,
                         text=True, env=_env())
    return [l.strip() for l in out.stdout.splitlines()[1:] if l.strip()]


def _env() -> dict:
    e = dict(os.environ)
    e["TESSDATA_PREFIX"] = TESSDATA
    return e


def _write_tmp(img: np.ndarray) -> str:
    fd, path = tempfile.mkstemp(suffix=".png")
    os.close(fd)
    cv2.imwrite(path, img)
    return path


def ocr_page(img: np.ndarray, lang: str = "eng", psm: int = 1,
             extra: list[str] | None = None) -> dict:
    """OCR one page image. Returns lines with text, bbox and confidence."""
    path = _write_tmp(img)
    try:
        cmd = [TESSERACT, path, "stdout", "-l", lang, "--psm", str(psm),
               "-c", "preserve_interword_spaces=1", "tsv"]
        if extra:
            cmd[6:6] = extra
        r = proc.run(cmd, capture_output=True, text=True, encoding="utf-8",
                           env=_env())
    finally:
        os.unlink(path)
    if r.returncode != 0:
        return {"lines": [], "mean_conf": 0.0, "words": 0, "error": r.stderr[-400:]}

    rows = list(csv.DictReader(io.StringIO(r.stdout), delimiter="\t",
                               quoting=csv.QUOTE_NONE))
    lines: dict[tuple, dict] = {}
    confs = []
    for row in rows:
        try:
            conf = float(row.get("conf", -1))
        except (TypeError, ValueError):
            continue
        text = (row.get("text") or "").strip()
        if conf < 0 or not text:
            continue
        key = (int(row["block_num"]), int(row["par_num"]), int(row["line_num"]))
        L, T = int(row["left"]), int(row["top"])
        W, H = int(row["width"]), int(row["height"])
        d = lines.setdefault(key, {"words": [], "confs": [],
                                   "bbox": [L, T, L + W, T + H],
                                   "block": int(row["block_num"]),
                                   "par": int(row["par_num"])})
        d["words"].append(text)
        d["confs"].append(conf)
        b = d["bbox"]
        d["bbox"] = [min(b[0], L), min(b[1], T), max(b[2], L + W), max(b[3], T + H)]
        confs.append(conf)

    out = []
    for key in sorted(lines):
        d = lines[key]
        out.append({
            "text": " ".join(d["words"]),
            "bbox": d["bbox"],
            "conf": round(sum(d["confs"]) / len(d["confs"]), 1),
            "block": d["block"],
            "par": d["par"],
            "size": d["bbox"][3] - d["bbox"][1],
        })
    return {
        "lines": out,
        "words": len(confs),
        "mean_conf": round(sum(confs) / len(confs), 1) if confs else 0.0,
        "low_conf_words": sum(1 for c in confs if c < 60),
    }


def ocr_to_pdf_page(img: np.ndarray, lang: str, psm: int = 1) -> bytes | None:
    """Produce a single-page searchable PDF (image + invisible text layer)."""
    src = _write_tmp(img)
    base = src[:-4] + "_ocr"
    try:
        r = proc.run([TESSERACT, src, base, "-l", lang, "--psm", str(psm),
                            "pdf"], capture_output=True, text=True, env=_env())
        if r.returncode != 0 or not os.path.exists(base + ".pdf"):
            return None
        with open(base + ".pdf", "rb") as f:
            return f.read()
    finally:
        for p in (src, base + ".pdf"):
            if os.path.exists(p):
                os.unlink(p)


def confidence_verdict(mean_conf: float, lang: str) -> str:
    """Arabic OCR needs a stricter bar than Latin before we trust the text."""
    threshold = 78.0 if lang.startswith("ara") else 70.0
    if mean_conf >= threshold:
        return "good"
    if mean_conf >= threshold - 12:
        return "marginal"
    return "poor"
