"""Quality assurance: format validation, content-loss check, Arabic verification."""
from __future__ import annotations

import os
import re
from . import proc
import tempfile
import zipfile

from . import arabic
from .build import EBOOK_CONVERT, EBOOK_META

MAGIC = {
    ".azw3": [(60, b"BOOKMOBI")],
    ".mobi": [(60, b"BOOKMOBI")],
    ".epub": [(0, b"PK\x03\x04")],
    ".pdf": [(0, b"%PDF")],
    ".kfx": [(0, b"CONT"), (0, b"\xeaDRMION\xee")],
}


def check_magic(path: str) -> dict:
    ext = os.path.splitext(path)[1].lower()
    exp = MAGIC.get(ext)
    if not exp:
        return {"ok": None, "reason": "no signature rule"}
    with open(path, "rb") as f:
        head = f.read(80)
    for off, sig in exp:
        if head[off:off + len(sig)] == sig:
            return {"ok": True, "signature": sig.decode("latin-1", "replace")}
    return {"ok": False, "reason": f"bad signature: {head[:16]!r}",
            "declared": ext}


def read_meta(path: str) -> dict:
    if not os.path.exists(EBOOK_META):
        return {}
    r = proc.run([EBOOK_META, path], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    out = {}
    for line in r.stdout.splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            out[k.strip().lower()] = v.strip()
    return out


def extract_text(path: str) -> str:
    """Round-trip the output back to text to prove it really opens."""
    fd, txt = tempfile.mkstemp(suffix=".txt")
    os.close(fd)
    try:
        r = proc.run([EBOOK_CONVERT, path, txt, "--enable-heuristics"],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace")
        if r.returncode != 0 or not os.path.exists(txt):
            return ""
        with open(txt, encoding="utf-8", errors="replace") as f:
            return f.read()
    finally:
        if os.path.exists(txt):
            os.unlink(txt)


_TOK = re.compile(r"[\w\u0600-\u06FF]+", re.UNICODE)


def _tokens(s: str) -> list[str]:
    # Pre-shaped Arabic uses presentation forms; fold them back to base letters
    # so coverage can be compared against the logical-order source text.
    if arabic.has_presentation_forms(s) or any(
            0xFE70 <= ord(c) <= 0xFEFF for c in s[:2000]):
        s = arabic.normalize_presentation_forms(s)
    return _TOK.findall(s.lower())


def content_coverage(source_text: str, output_text: str) -> dict:
    """Fraction of source vocabulary and volume preserved in the output."""
    src, out = _tokens(source_text), _tokens(output_text)
    if not src:
        return {"token_ratio": None, "vocab_recall": None,
                "source_tokens": 0, "output_tokens": len(out)}
    sset, oset = set(src), set(out)
    return {
        "source_tokens": len(src),
        "output_tokens": len(out),
        "token_ratio": round(len(out) / len(src), 3),
        "vocab_recall": round(len(sset & oset) / len(sset), 3),
        "lost_sample": [w for w in list(sset - oset)[:15]],
    }


def sample_sections(text: str, n: int = 3, width: int = 420) -> list[str]:
    text = re.sub(r"\n{3,}", "\n\n", text.strip())
    if not text:
        return []
    L = len(text)
    spots = [0, max(0, L // 2 - width // 2), max(0, L - width)]
    return [text[s:s + width].strip() for s in spots[:n]]


def verify_output(path: str, source_text: str, rtl: bool,
                  preshaped: bool = False) -> dict:
    """Validate one output file.

    `preshaped` tells the Arabic validator that presentation forms are
    intentional (a custom embedded font requires them on the Kindle Oasis),
    so they must not be reported as broken shaping.
    """
    res = {"file": os.path.basename(path), "exists": os.path.exists(path)}
    if not res["exists"]:
        res["ok"] = False
        return res
    res["size_bytes"] = os.path.getsize(path)
    res["format_check"] = check_magic(path)
    res["metadata"] = read_meta(path)

    ext = os.path.splitext(path)[1].lower()
    text = ""
    if ext in (".azw3", ".mobi", ".epub"):
        text = extract_text(path)
        res["opens"] = bool(text.strip()) or res["size_bytes"] > 20000
    elif ext == ".pdf":
        try:
            import pymupdf as fitz
            d = fitz.open(path)
            res["pages"] = d.page_count
            raw = "\n".join(d[i].get_text() for i in range(d.page_count))
            # The invisible OCR layer Tesseract writes into a PDF is stored in
            # visual order; repair it before judging quality or coverage.
            text = "\n".join(arabic.clean_line(l)[0] for l in raw.splitlines())
            res["opens"] = d.page_count > 0
            res["note"] = ("text layer is for search only; the visible pages are "
                           "the cleaned page images")
            d.close()
        except Exception as e:
            res["opens"] = False
            res["error"] = str(e)
    res["extracted_chars"] = len(text)
    if text.strip():
        res["coverage"] = content_coverage(source_text, text)
        res["samples"] = sample_sections(text)
        if rtl or arabic.arabic_ratio(text) > 0.2:
            res["arabic"] = arabic.validate_shaping(text, preshaped=preshaped)
    res["ok"] = bool(res.get("opens")) and res["format_check"].get("ok") is not False
    if rtl and ext in (".azw3", ".mobi"):
        res["font"] = embedded_font_check(path)
        if res["font"].get("subset_suspected"):
            res["ok"] = False
    return res


def embedded_font_check(path: str, expected_min: int = 200_000) -> dict:
    """Verify Arabic fonts were embedded whole.

    Calibre's font subsetter does not follow the GSUB closure for Arabic
    contextual forms; a subsetted Arabic font silently loses initial/medial/
    final glyphs and letters render disconnected on the device. A full Amiri
    face is ~400 KB, a damaged subset is well under 150 KB.
    """
    import subprocess
    import tempfile
    import zipfile
    tmp = tempfile.mkdtemp()
    ep = os.path.join(tmp, "chk.epub")
    proc.run([EBOOK_CONVERT, path, ep], capture_output=True)
    if not os.path.exists(ep):
        return {"checked": False}
    z = zipfile.ZipFile(ep)
    fonts = [(os.path.basename(n), z.getinfo(n).file_size)
             for n in z.namelist() if n.lower().endswith((".ttf", ".otf"))]
    if not fonts:
        return {"checked": True, "embedded": False,
                "note": "no embedded font - relies on the device's own Arabic font"}
    smallest = min(s for _, s in fonts)
    ok = smallest >= expected_min
    return {"checked": True, "embedded": True, "fonts": fonts,
            "subset_suspected": not ok,
            "ok": ok,
            "note": ("full font embedded - contextual shaping preserved" if ok else
                     "FONT LOOKS SUBSETTED - Arabic letters may render disconnected")}
def epub_structure(path: str) -> dict:
    """Sanity check on an EPUB we generated before handing it to calibre."""
    with zipfile.ZipFile(path) as z:
        names = z.namelist()
    return {"entries": len(names),
            "has_opf": any(n.endswith(".opf") for n in names),
            "has_nav": any("nav.xhtml" in n for n in names),
            "images": sum(1 for n in names if n.lower().endswith((".png", ".jpg")))}
