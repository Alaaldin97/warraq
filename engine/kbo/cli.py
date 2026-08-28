"""Kindle Oasis Book Optimizer - pipeline orchestrator.

Usage:
  python -m kbo.cli <input.pdf> --out <dir> [--max-pages N] [--force-route R]
Routes: auto | reflow | ocr-reflow | fixed
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import json
import os
import re
import shutil
import sys
import time

import cv2
import pymupdf as fitz
import numpy as np

from . import analyze as anz
from . import arabic, azure_ocr, build, clean, device, extract, kfx, ocr, qa, score
from .report import write_report

RENDER_DPI = 300


def log(msg: str):
    # Arabic book titles and filenames flow through here. On Windows the console
    # defaults to a legacy code page that cannot represent them, which turned a
    # progress message into a crash. Reconfigure once, and degrade to replacement
    # characters rather than failing if that is not possible.
    try:
        print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)
    except UnicodeEncodeError:
        enc = getattr(sys.stdout, "encoding", None) or "utf-8"
        safe = msg.encode(enc, errors="replace").decode(enc, errors="replace")
        print(f"[{time.strftime('%H:%M:%S')}] {safe}", flush=True)


def _use_utf8_streams() -> None:
    """Make the standard streams carry Arabic text safely."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def safe_name(s: str) -> str:
    s = re.sub(r"[^\w\u0600-\u06FF \-]+", "", s).strip()
    s = re.sub(r"\s+", "_", s)
    return (s or "Book")[:60]


# ------------------------------------------------------------------ routing
def decide_route(a: dict) -> tuple[str, str]:
    dt, lang = a["doc_type"], a.get("language")
    if dt == "text":
        if a["dominant_columns"] > 2:
            return "fixed", "dense multi-column layout preserves better fixed"
        return "reflow", f"native text layer on {a['text_page_ratio']:.0%} of pages"
    if dt == "text_over_scan":
        return "ocr", ("pages are scans carrying a pre-existing OCR text layer; "
                       "re-running OCR on cleaned images gives better accuracy "
                       "and correct reading order")
    if dt == "scanned":
        return "ocr", "no usable text layer - OCR required"
    return "ocr", f"mixed document ({a['text_page_ratio']:.0%} text pages) - OCR fills gaps"


# ------------------------------------------------------------------ helpers
def render_clean_pages(pdf: str, a: dict, work: str, aggressive: bool,
                       dpi: int = RENDER_DPI, workers: int = 4) -> list[dict]:
    """Render, clean and device-fit every non-blank page."""
    d = device.get()
    os.makedirs(work, exist_ok=True)
    skip = set(a["blank_pages"])
    # keep the first occurrence of duplicated pages, drop later repeats
    seen_sig, drop_dupe = set(), set()
    for p in a["pages"]:
        if p["blank"]:
            continue
        if p["signature"] in seen_sig:
            drop_dupe.add(p["number"])
        seen_sig.add(p["signature"])
    todo = [p for p in a["pages"] if p["number"] not in skip
            and p["number"] not in drop_dupe]

    def one(p):
        doc = fitz.open(pdf)
        page = doc[p["number"] - 1]
        gray = anz.render_gray(page, dpi)
        doc.close()
        g = clean.clean_page(gray, p, aggressive=aggressive)
        g = clean.fit_to_screen(g, d["screen_px"], d["reflow_margin_px"])
        g = clean.quantize_gray(g, d["greyscale_levels"])
        path = os.path.join(work, f"pg{p['number']:05d}.png")
        build.save_png(g, path)
        return {"page": p["number"], "png": path, "info": p}

    out = []
    with cf.ThreadPoolExecutor(max_workers=workers) as ex:
        for r in ex.map(one, todo):
            out.append(r)
    out.sort(key=lambda r: r["page"])
    return out, sorted(drop_dupe)


def ocr_pages(pages: list[dict], lang_code: str, workers: int = 4,
              make_pdf_layer: bool = True, text_only: bool = False,
              layer_only: bool = False) -> list[dict]:
    """OCR the rendered pages with Tesseract.

    `layer_only` keeps any existing recognition result (e.g. from Azure) and
    only generates the invisible PDF text layer.
    """
    def one(rec):
        img = cv2.imread(rec["png"], cv2.IMREAD_GRAYSCALE)
        psm = 1 if rec["info"]["columns"] > 1 else 3
        rec = dict(rec)
        if not layer_only:
            rec["ocr"] = ocr.ocr_page(img, lang_code, psm=psm)
        if make_pdf_layer:
            rec["ocr_pdf"] = ocr.ocr_to_pdf_page(img, lang_code, psm)
        return rec

    out = []
    with cf.ThreadPoolExecutor(max_workers=workers) as ex:
        for r in ex.map(one, pages):
            out.append(r)
    out.sort(key=lambda r: r["page"])
    return out


def probe_language(pages: list[dict], samples: int = 5) -> tuple[str, dict]:
    """Detect language by OCR-ing several pages spread through the book.

    Sampling only page 1 is unreliable - covers are often decorative or
    English-titled even in Arabic books.
    """
    if not pages:
        return "en", {}
    n = len(pages)
    start = min(3, max(0, n - 1))          # skip cover / title pages
    idxs = sorted({start + int((n - 1 - start) * i / max(samples - 1, 1))
                   for i in range(samples)})
    texts, per_page = [], {}

    def one(i):
        img = cv2.imread(pages[i]["png"], cv2.IMREAD_GRAYSCALE)
        r = ocr.ocr_page(img, "ara+eng", psm=1)
        return i, " ".join(l["text"] for l in r["lines"])

    with cf.ThreadPoolExecutor(max_workers=min(len(idxs), 5)) as ex:
        for i, t in ex.map(one, idxs):
            if len(t.strip()) > 40:
                texts.append(t)
                per_page[pages[i]["page"]] = round(arabic.arabic_ratio(t), 2)
    joined = "\n".join(texts)
    lang = arabic.classify_language(joined) if joined.strip() else "en"
    return lang, {"sampled_pages": list(per_page), "arabic_ratio_by_page": per_page,
                  "chars_sampled": len(joined)}


def azure_ocr_pages(pdf: str, a: dict, pages_rendered: list[dict],
                    work: str, chunk: int = 15) -> dict:
    """Run Azure Document Intelligence over the cleaned pages.

    The cleaned/deskewed images are re-assembled into small PDFs and sent in
    chunks: this keeps each upload modest, survives flaky links, and lets a
    single failed chunk fall back without losing the whole book.
    """
    import pymupdf as fitz
    results: dict[int, list[dict]] = {}
    confs = []
    total = (len(pages_rendered) + chunk - 1) // chunk
    for idx, i in enumerate(range(0, len(pages_rendered), chunk), 1):
        part = pages_rendered[i:i + chunk]
        doc = fitz.open()
        for rec in part:
            img = fitz.open(rec["png"])
            rect = img[0].rect
            pdfbytes = img.convert_to_pdf()
            img.close()
            src = fitz.open("pdf", pdfbytes)
            page = doc.new_page(width=rect.width, height=rect.height)
            page.show_pdf_page(page.rect, src, 0)
            src.close()
        tmp = os.path.join(work, f"_az_{i:05d}.pdf")
        doc.save(tmp, deflate=True)
        doc.close()
        size_mb = os.path.getsize(tmp) / 1024 / 1024
        log(f"  Azure DI chunk {idx}/{total}: pages "
            f"{part[0]['page']}-{part[-1]['page']} ({size_mb:.1f} MB)")
        r = azure_ocr.ocr_pdf_file(tmp, timeout=1200)
        os.unlink(tmp)
        if not r["ok"]:
            return {"ok": False, "reason": r.get("reason", "unknown"),
                    "completed_pages": len(results)}
        confs.append(r["mean_conf"])
        for j, pg in enumerate(r["pages"]):
            if j < len(part):
                results[part[j]["page"]] = pg["lines"]
    return {"ok": True, "lines_by_page": results,
            "mean_conf": round(sum(confs) / len(confs), 1) if confs else 0.0,
            "pages": len(results)}


def source_text(pdf: str, a: dict, limit: int = 1500) -> str:
    """Reference text from the source, used for the content-loss check.

    Must cover exactly the pages that were converted - no more. When a page
    range is requested, comparing the output against the *whole* book would
    report a false content loss.
    """
    doc = fitz.open(pdf)
    n = min(doc.page_count, limit)
    if a.get("analyzed_pages") and not a.get("sampled"):
        n = min(n, a["analyzed_pages"])
    txt = []
    for i in range(n):
        t = doc[i].get_text("text") or ""
        if arabic.has_presentation_forms(t):
            t = arabic.normalize_presentation_forms(t)
        txt.append(t)
    doc.close()
    return "\n".join(txt)


def make_cover(pdf: str, out_png: str) -> str | None:
    try:
        doc = fitz.open(pdf)
        g = anz.render_gray(doc[0], 200)
        doc.close()
        g = clean.enhance_contrast(g)
        g = clean.fit_to_screen(g, (1264, 1680), 0)
        build.save_png(g, out_png)
        return out_png
    except Exception:
        return None


def guess_meta(pdf: str, a: dict, blocks=None) -> dict:
    md = a["metadata"]
    title = (md.get("title") or "").strip()
    if not title or len(title) < 3 or title.lower().endswith(".pdf"):
        title = ""
        if blocks:
            for b in blocks[:6]:
                if b.kind == "heading" and len(b.text) > 3:
                    title = b.text.strip()
                    break
    if not title:
        title = os.path.splitext(os.path.basename(pdf))[0].replace("_", " ")
    author = (md.get("author") or "").strip() or "Unknown"
    return {"title": title[:120], "author": author[:80],
            "publisher": (md.get("producer") or "").strip()[:60]}


def chapters_from_blocks(blocks) -> list[tuple[str, int]]:
    out = []
    for b in blocks:
        if b.kind == "heading" and b.level <= 1:
            out.append((b.text[:70], b.page))
    return out


def chapters_from_ocr(pages: list[dict], rtl: bool) -> list[tuple[str, int]]:
    """Find headings on OCR'd pages: short, large lines near the top."""
    heights = [l["size"] for p in pages for l in p["ocr"]["lines"] if l["text"].strip()]
    if not heights:
        return []
    body = float(np.median(heights))
    out = []
    for p in pages:
        for l in p["ocr"]["lines"][:4]:
            t = l["text"].strip()
            if not t or len(t) > 70 or l["conf"] < 65:
                continue
            if l["size"] >= body * 1.45 or extract._CHAPTER_RE.match(t):
                out.append((t, p["page"]))
                break
    # dedupe consecutive repeats
    dedup = []
    for t, pg in out:
        if not dedup or dedup[-1][0] != t:
            dedup.append((t, pg))
    return dedup


# ------------------------------------------------------------------ main
def run(pdf: str, outdir: str, max_pages: int | None = None,
        force_route: str | None = None, aggressive: bool = False,
        make_pdf: str = "auto", workers: int = 4, keep_work: bool = False,
        font_mode: str = "auto", ocr_engine: str = "auto",
        arabic_font: str = "amiri") -> dict:
    t0 = time.time()
    os.makedirs(outdir, exist_ok=True)
    work = os.path.join(outdir, "_work")
    os.makedirs(work, exist_ok=True)
    imgdir = os.path.join(work, "figs")
    os.makedirs(imgdir, exist_ok=True)

    log(f"Analyzing {os.path.basename(pdf)} ...")
    a = anz.analyze(pdf, max_pages=max_pages)
    print(anz.summarize(a))
    with open(os.path.join(work, "analysis.json"), "w", encoding="utf-8") as f:
        json.dump({k: v for k, v in a.items() if k != "pages"} |
                  {"pages": a["pages"]}, f, ensure_ascii=False, indent=1)

    route, why = decide_route(a)
    if force_route and force_route != "auto":
        route, why = force_route, "forced by operator"
    log(f"Route: {route} ({why})")

    result = {"input": pdf, "analysis": a, "route": route, "route_reason": why,
              "outputs": {}, "qa": {}, "warnings": [], "manual_review_pages": []}

    cover = make_cover(pdf, os.path.join(work, "cover.png"))
    src_text = source_text(pdf, a)
    rtl = False
    blocks, stats = [], {}
    ocr_info = {"used": False}
    pages_rendered, dupes = [], []

    # ---------------------------------------------------------- text route
    if route == "reflow":
        blocks, stats = extract.extract_from_textlayer(pdf, a, imgdir)
        rtl = stats["rtl"]
        stats["toc_headings"] = extract.apply_source_toc(blocks, a)
        meta = guess_meta(pdf, a, blocks)
        meta["cover"] = cover
        log(f"Extracted {len(blocks)} blocks "
            f"(headings {stats['headings']}, paras {stats['paragraphs']}, "
            f"figures {stats.get('images', 0)}, rtl={rtl}, "
            f"bookmark-headings {stats['toc_headings']})")

    # ---------------------------------------------------------- OCR route
    if route in ("ocr", "fixed"):
        log("Rendering + cleaning pages at 300 dpi ...")
        pages_rendered, dupes = render_clean_pages(pdf, a, work, aggressive,
                                                   workers=workers)
        log(f"{len(pages_rendered)} pages prepared "
            f"({len(a['blank_pages'])} blank, {len(dupes)} duplicate dropped)")
        lang_guess = a.get("language")
        if not lang_guess:
            lang_guess, probe_info = probe_language(pages_rendered)
            a["language"] = lang_guess
            log(f"OCR language probe -> {lang_guess} {probe_info['arabic_ratio_by_page']}")
        lang_code = ocr.LANG_MAP.get(lang_guess, "eng")
        rtl = lang_guess in ("ar", "bilingual")
        want_pdf_layer = make_pdf != "never"
        engine = "tesseract"
        azure_res = None
        if ocr_engine in ("azure", "auto") and azure_ocr.available():
            log(f"Azure Document Intelligence on {len(pages_rendered)} pages ...")
            azure_res = azure_ocr_pages(pdf, a, pages_rendered, work)
            if azure_res["ok"]:
                engine = "azure"
                log(f"Azure DI mean confidence {azure_res['mean_conf']}")
            else:
                log(f"Azure DI failed ({azure_res['reason'][:120]}) - "
                    f"falling back to Tesseract")
                azure_res = None
        elif ocr_engine == "azure":
            log("Azure requested but not configured - using Tesseract")

        # When Azure is confident there is nothing to gain from a second
        # recognition pass, and it costs ~20% of total runtime. Only run the
        # comparison when the result is in doubt.
        AZURE_TRUST = 90.0
        skip_tesseract = (azure_res is not None
                          and azure_res["mean_conf"] >= AZURE_TRUST)

        if skip_tesseract:
            log(f"Skipping Tesseract - Azure confidence "
                f"{azure_res['mean_conf']} is above the trust threshold")
            for p in pages_rendered:
                lines = azure_res["lines_by_page"].get(p["page"], [])
                p["ocr"] = {"lines": lines,
                            "mean_conf": azure_res["mean_conf"],
                            "words": sum(len(l["text"].split()) for l in lines),
                            "low_conf_words": 0}
            mean_conf = azure_res["mean_conf"]
            if want_pdf_layer:
                pages_rendered = ocr_pages(pages_rendered, lang_code, workers,
                                           make_pdf_layer=True,
                                           text_only=False, layer_only=True)
        else:
            log(f"Tesseract ({lang_code}) on {len(pages_rendered)} pages ...")
            pages_rendered = ocr_pages(pages_rendered, lang_code, workers,
                                       make_pdf_layer=want_pdf_layer)
            confs = [p["ocr"]["mean_conf"] for p in pages_rendered
                     if p["ocr"]["words"] > 5]
            mean_conf = float(np.mean(confs)) if confs else 0.0

        # When both engines ran, keep whichever text scores better.
        if azure_res and not skip_tesseract:
            az_text = "\n".join(l["text"] for lines in
                                azure_res["lines_by_page"].values() for l in lines)
            ts_text = "\n".join(l["text"] for p in pages_rendered
                                for l in p["ocr"]["lines"])
            lang_key = "ar" if rtl else "en"
            winner, scores = score.pick_best(
                {"azure": az_text, "tesseract": ts_text}, lang_key)
            result["engine_comparison"] = scores
            log(f"engine scores: azure={scores['azure']['score']} "
                f"tesseract={scores['tesseract']['score']} -> {winner}")
            engine = winner
            if winner == "azure":
                # graft Azure's lines onto the page records
                for p in pages_rendered:
                    lines = azure_res["lines_by_page"].get(p["page"])
                    if lines is not None:
                        p["ocr"] = {"lines": lines,
                                    "mean_conf": azure_res["mean_conf"],
                                    "words": sum(len(l["text"].split())
                                                 for l in lines),
                                    "low_conf_words": 0}
                mean_conf = azure_res["mean_conf"]

        verdict = ocr.confidence_verdict(mean_conf, lang_code)
        thresh = 78 if lang_code.startswith("ara") else 70
        if engine == "azure":
            thresh = 0          # service confidence is already validated
        low_pages = [p["page"] for p in pages_rendered
                     if p["ocr"]["words"] > 5 and p["ocr"]["mean_conf"] < thresh]
        ocr_info = {"used": True, "engine": engine, "lang": lang_code,
                    "mean_conf": round(mean_conf, 1),
                    "verdict": verdict, "low_conf_pages": low_pages,
                    "pages_ocred": len(pages_rendered)}
        log(f"OCR mean confidence {mean_conf:.1f} -> {verdict}")
        result["manual_review_pages"] = low_pages
        ocr_text = "\n".join(l["text"] for p in pages_rendered
                             for l in p["ocr"]["lines"])

        # If the source already carries a text layer (e.g. from Adobe Acrobat's
        # Scan & OCR), score it against our fresh Tesseract pass and keep the
        # better one. Restricted to single-column books: the scorer measures
        # word quality, not reading order, and a pre-existing layer on a
        # multi-column page is exactly where column interleaving hides.
        use_layer = False
        if (a["doc_type"] == "text_over_scan" and len(src_text.strip()) > 500
                and a["dominant_columns"] == 1):
            lang_key = "ar" if rtl else "en"
            winner, scores = score.pick_best(
                {"existing_layer": src_text, "tesseract": ocr_text}, lang_key)
            result["ocr_comparison"] = scores
            log(f"text-layer quality: existing={scores['existing_layer']['score']} "
                f"tesseract={scores['tesseract']['score']} -> {winner}")
            if (winner == "existing_layer" and
                    scores["existing_layer"]["score"] >=
                    scores["tesseract"]["score"] + 8):
                use_layer = True
        if use_layer:
            log("Using the PDF's existing text layer (higher quality than "
                "re-OCR).")
            blocks, stats = extract.extract_from_textlayer(pdf, a, imgdir)
            rtl = stats["rtl"] or rtl
            stats["toc_headings"] = extract.apply_source_toc(blocks, a)
            stats["text_source"] = "existing_layer"
            ocr_info["used_existing_layer"] = True
            route = "reflow"
            result["route"] = "reflow"
            result["route_reason"] = (
                "the PDF's own text layer scored higher than a fresh OCR pass, "
                "so it was used directly")
        else:
            if len(src_text.strip()) >= 200:
                result["source_layer_comparison"] = qa.content_coverage(
                    src_text, ocr_text)
            src_text = ocr_text
            a["arabic_ratio"] = round(arabic.arabic_ratio(src_text), 3)
            a["latin_ratio"] = round(arabic.latin_ratio(src_text), 3)

        if route == "ocr" and verdict == "poor":
            route = "fixed"
            result["route"] = "fixed"
            result["route_reason"] = (f"OCR confidence {mean_conf:.1f} too low for "
                                      f"reliable text - page images preserved instead")
            log("Falling back to fixed layout: OCR confidence too low")
        elif route == "ocr" and verdict == "marginal" and lang_code.startswith("ara"):
            route = "fixed"
            result["route"] = "fixed"
            result["route_reason"] = (f"Arabic OCR only marginal ({mean_conf:.1f}) - "
                                      f"original page images preserved to avoid "
                                      f"corrupted Arabic text")
            log("Falling back to fixed layout: marginal Arabic OCR")

        if route == "ocr":
            heading_mode = "conservative" if len(a.get("toc") or []) >= 5 else "ocr"
            low_set = set(low_pages)
            page_lines = []
            kept_images = 0
            for p in pages_rendered:
                # Low-confidence page: keep the original page image instead of
                # emitting text we cannot trust (critical for Arabic).
                if p["page"] in low_set:
                    name = f"page_{p['page']:05d}.png"
                    shutil.copy2(p["png"], os.path.join(imgdir, name))
                    page_lines.append((p["page"], [{"text": "", "bbox": [0, 0, 0, 0],
                                                    "size": 0, "bold": False,
                                                    "fonts": [],
                                                    "image": {"file": name,
                                                              "w": 1264, "h": 1680,
                                                              "page": p["page"],
                                                              "whole_page": True}}],
                                       p["info"]))
                    kept_images += 1
                    continue
                scale = RENDER_DPI / 72.0
                lines = extract._ocr_lines_to_common(p["ocr"]["lines"], scale)
                # Tesseract's page-layout analysis (psm 1) already returns lines
                # in true reading order, including for multi-column pages.
                # Re-sorting them by Y would interleave the columns.
                page_lines.append((p["page"], lines, p["info"]))
            blocks, stats = extract.build_blocks(page_lines, a, rtl, from_ocr=True,
                                                 heading_mode=heading_mode)
            stats["rtl"] = rtl
            stats["toc_headings"] = extract.apply_source_toc(blocks, a)
            log(f"OCR text -> {len(blocks)} blocks "
                f"(+{stats['toc_headings']} from source bookmarks, "
                f"{kept_images} low-confidence pages kept as images, "
                f"heading_mode={heading_mode})")

    meta = guess_meta(pdf, a, blocks)
    meta["cover"] = cover
    base = safe_name(meta["title"])
    result["meta"] = {k: v for k, v in meta.items() if k != "cover"}
    result["ocr"] = ocr_info
    result["extract_stats"] = stats
    result["dropped_duplicates"] = dupes

    azw3 = os.path.join(outdir, f"{base}_KindleOasis_AZW3.azw3")

    def build_fixed_azw3(target_path: str) -> bool:
        nonlocal pages_rendered, dupes
        if not pages_rendered:
            pages_rendered, dupes = render_clean_pages(pdf, a, work, aggressive,
                                                       workers=workers)
            result["dropped_duplicates"] = dupes
        pngs = [p["png"] for p in pages_rendered]
        chaps = (chapters_from_ocr(pages_rendered, rtl)
                 if pages_rendered and "ocr" in pages_rendered[0] else [])
        if a["toc"]:
            chaps = [(t, p) for lvl, t, p in a["toc"] if lvl <= 2] or chaps
        idx = {p["page"]: i + 1 for i, p in enumerate(pages_rendered)}
        chaps = [(t, idx.get(p, 1)) for t, p in chaps][:300]
        epub = os.path.join(work, os.path.basename(target_path) + ".epub")
        build.write_fixed_epub(pngs, epub, meta, rtl, chaps)
        log(f"Fixed-layout EPUB: {qa.epub_structure(epub)}  chapters={len(chaps)}")
        rr = build.epub_to_azw3(epub, target_path, meta, rtl)
        if not rr["ok"]:
            log("EPUB->AZW3 failed, trying CBZ route ...")
            cbz = build.cbz_from_pngs(pngs, os.path.join(work, f"{base}.cbz"))
            rr = build.cbz_to_azw3(cbz, target_path, meta, rtl)
        if not rr["ok"]:
            result["warnings"].append("Fixed AZW3 failed: " + rr.get("stderr", "")[-300:])
        return rr["ok"]

    # ---------------------------------------------------------- build
    if route in ("reflow", "ocr"):
        # Arabic: embed Amiri and pre-shape the letters. The Kindle Oasis does
        # not apply contextual shaping to embedded fonts, so pre-shaping is what
        # makes a custom Arabic font render correctly.
        effective_font_mode = font_mode
        if font_mode == "auto":
            effective_font_mode = "preshape" if rtl else "native"
        result["font_mode"] = effective_font_mode
        result["arabic_font"] = arabic_font if rtl else None
        htmldir = os.path.join(work, "html")
        html_path = build.write_html_book(blocks, meta, htmldir, rtl, imgdir,
                                          font_mode=effective_font_mode,
                                          arabic_font=arabic_font)
        log(f"Converting to AZW3 (font_mode={effective_font_mode}"
            + (f", font={arabic_font}" if rtl else "") + ") ...")
        r = build.html_to_azw3(html_path, azw3, meta, rtl,
                               font_mode=effective_font_mode)
        if not r["ok"]:
            result["warnings"].append("AZW3 conversion failed: " + r["stderr"][-300:])
            log("AZW3 FAILED\n" + r["stderr"][-800:])
        else:
            result["outputs"]["azw3"] = azw3
        # Pre-shaped Arabic renders beautifully but cannot be searched by the
        # Kindle. Ship a searchable companion using the device font so both
        # reading comfort and search are available.
        if r["ok"] and rtl and effective_font_mode == "preshape":
            searchable = os.path.join(
                outdir, f"{base}_KindleOasis_Searchable.azw3")
            sdir = os.path.join(work, "html_searchable")
            smeta = dict(meta)
            smeta["title"] = meta["title"] + " (بحث)"
            sp = build.write_html_book(blocks, smeta, sdir, rtl, imgdir,
                                       font_mode="native")
            sr = build.html_to_azw3(sp, searchable, smeta, rtl,
                                    font_mode="native")
            if sr["ok"]:
                result["outputs"]["azw3_searchable"] = searchable
                log("Also built a searchable companion (device font).")

        # OCR'd text can carry recognition errors: for Arabic and for scanned
        # sources also ship a page-exact fixed-layout AZW3 so nothing is lost.
        if route == "ocr" and (rtl or a["doc_type"] in ("scanned", "text_over_scan")):
            alt = os.path.join(outdir, f"{base}_KindleOasis_FixedLayout.azw3")
            log("Also building a page-exact fixed-layout AZW3 ...")
            if build_fixed_azw3(alt):
                result["outputs"]["azw3_fixed"] = alt
    else:  # fixed
        if build_fixed_azw3(azw3):
            result["outputs"]["azw3"] = azw3

    # ---------------------------------------------------------- PDF fallback
    want_pdf = (make_pdf == "always" or
                (make_pdf == "auto" and (route == "fixed" or a["doc_type"] != "text")))
    if want_pdf:
        if not pages_rendered:
            log("Rendering pages for the optimized PDF ...")
            pages_rendered, dupes = render_clean_pages(pdf, a, work, aggressive,
                                                       workers=workers)
        pdf_out = os.path.join(outdir, f"{base}_KindleOasis_Optimized.pdf")
        ocr_layers = [p.get("ocr_pdf") for p in pages_rendered]
        toc = []
        if a["toc"]:
            idx = {p["page"]: i + 1 for i, p in enumerate(pages_rendered)}
            toc = [(t, idx.get(p, 1)) for lvl, t, p in a["toc"] if lvl <= 2][:300]
        build.build_optimized_pdf([p["png"] for p in pages_rendered], pdf_out, meta,
                                  ocr_layers if any(ocr_layers) else None, toc)
        result["outputs"]["pdf"] = pdf_out
        log(f"Optimized PDF: {os.path.basename(pdf_out)}")

    # ---------------------------------------------------------- KFX
    kfx_status = kfx.status()
    result["kfx"] = kfx_status
    if kfx_status["can_generate_kfx"] and result["outputs"].get("azw3"):
        kfx_out = os.path.join(outdir, f"{base}_KindleOasis_KFX.kfx")
        kr = kfx.convert(result["outputs"]["azw3"], kfx_out)
        result["kfx"].update(kr)
        if kr.get("ok"):
            result["outputs"]["kfx"] = kfx_out
    else:
        log("KFX skipped: " + kfx_status["reason"])

    # ---------------------------------------------------------- QA
    log("Running QA ...")
    preshaped_out = result.get("font_mode") == "preshape"
    for kind, path in result["outputs"].items():
        # only the primary AZW3 carries pre-shaped text
        ps = preshaped_out and kind == "azw3"
        result["qa"][kind] = qa.verify_output(path, src_text, rtl, preshaped=ps)

    # QA gate: if the primary output lost meaningful content, say so loudly and
    # fall back to a page-exact alternative.
    prim = result["qa"].get("azw3", {})
    cov = prim.get("coverage") or {}
    gate = "PASS"
    if cov.get("vocab_recall") is not None:
        if cov["vocab_recall"] < 0.70 or (cov.get("token_ratio") or 1) < 0.60:
            gate = "FAIL"
            result["warnings"].append(
                f"Content check: only {cov['vocab_recall']:.0%} of the source "
                f"vocabulary survived into the AZW3 - the reflowed text is not "
                f"trustworthy for this source.")
        elif cov["vocab_recall"] < 0.85:
            gate = "WARN"
    if prim.get("arabic") and not prim["arabic"]["ok"]:
        gate = "FAIL"
        result["warnings"].append(
            "Arabic validation failed on the AZW3: " + ", ".join(prim["arabic"]["issues"]))
    if prim.get("font", {}).get("subset_suspected"):
        gate = "FAIL"
        result["warnings"].append(
            "Embedded Arabic font appears subsetted - letters would render "
            "disconnected on the device.")
    result["qa_gate"] = gate
    log(f"QA gate: {gate}")
    if gate == "FAIL" and "azw3_fixed" not in result["outputs"]:
        alt = os.path.join(outdir, f"{base}_KindleOasis_FixedLayout.azw3")
        log("QA gate failed - building a page-exact fixed-layout AZW3 instead ...")
        if build_fixed_azw3(alt):
            result["outputs"]["azw3_fixed"] = alt
            result["qa"]["azw3_fixed"] = qa.verify_output(alt, src_text, rtl)
    result["rtl"] = rtl

    # preview renders for human inspection
    prev_dir = os.path.join(outdir, "preview")
    os.makedirs(prev_dir, exist_ok=True)
    result["previews"] = make_previews(result, prev_dir, base)

    result["elapsed_sec"] = round(time.time() - t0, 1)
    report_path = os.path.join(outdir, f"{base}_ConversionReport.txt")
    write_report(result, report_path)
    result["outputs"]["report"] = report_path
    with open(os.path.join(work, "result.json"), "w", encoding="utf-8") as f:
        json.dump({k: v for k, v in result.items() if k != "analysis"}, f,
                  ensure_ascii=False, indent=1, default=str)
    if not keep_work:
        for p in (imgdir,):
            pass
    log(f"Done in {result['elapsed_sec']}s")
    return result


def make_previews(result: dict, prev_dir: str, base: str) -> list[str]:
    """Render beginning/middle/end of the AZW3 (via EPUB) and the PDF for review."""
    out = []
    pdf = result["outputs"].get("pdf")
    azw3 = result["outputs"].get("azw3")
    src = None
    if azw3:
        tmp_epub = os.path.join(prev_dir, "_preview.epub")
        import subprocess
        from . import proc
        r = proc.run([build.EBOOK_CONVERT, azw3, tmp_epub],
                           capture_output=True, text=True, errors="replace")
        if r.returncode == 0 and os.path.exists(tmp_epub):
            tmp_pdf = os.path.join(prev_dir, "_preview.pdf")
            r2 = proc.run(
                [build.EBOOK_CONVERT, tmp_epub, tmp_pdf,
                 "--custom-size", "4.21x5.6", "--unit", "inch",
                 "--pdf-page-margin-top", "8", "--pdf-page-margin-bottom", "8",
                 "--pdf-page-margin-left", "8", "--pdf-page-margin-right", "8"],
                capture_output=True, text=True, errors="replace")
            if r2.returncode == 0 and os.path.exists(tmp_pdf):
                src = tmp_pdf
    if src is None:
        src = pdf
    if not src or not os.path.exists(src):
        return out
    try:
        d = fitz.open(src)
        n = d.page_count
        picks = sorted({0, max(0, n // 2), max(0, n - 1)})
        for i, pno in enumerate(picks):
            pix = d[pno].get_pixmap(matrix=fitz.Matrix(1.6, 1.6))
            p = os.path.join(prev_dir, f"{base}_preview_{['start','middle','end'][i]}_p{pno+1}.png")
            pix.save(p)
            out.append(p)
        d.close()
    except Exception:
        pass
    return out


def main(argv=None):
    # RPC mode is the interface the desktop shell uses. Checked before argparse
    # so the shell never has to construct a full CLI invocation.
    argv_list = list(sys.argv[1:] if argv is None else argv)
    if argv_list and argv_list[0] == "--rpc":
        from .rpc import serve
        return serve()

    # serve() handles its own stream setup; the CLI needs it here.
    _use_utf8_streams()

    ap = argparse.ArgumentParser(description="Kindle Oasis Book Optimizer")
    ap.add_argument("pdf")
    ap.add_argument("--out", required=True)
    ap.add_argument("--max-pages", type=int, default=None)
    ap.add_argument("--force-route", default="auto",
                    choices=["auto", "reflow", "ocr", "fixed"])
    ap.add_argument("--aggressive-clean", action="store_true")
    ap.add_argument("--make-pdf", dest="make_pdf", default="auto",
                    choices=["auto", "always", "never"],
                    help="also produce the Kindle-sized optimized PDF")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--font-mode", dest="font_mode", default="auto",
                    choices=["auto", "embed", "native", "preshape"],
                    help="Arabic font strategy. 'auto' (default) pre-shapes "
                         "Arabic and embeds the chosen font - verified correct "
                         "on the Oasis 9 - and leaves English alone. 'native' "
                         "keeps searchable Unicode using the device font. "
                         "'embed' is NOT shaped correctly by the Oasis.")
    ap.add_argument("--arabic-font", dest="arabic_font", default="amiri",
                    choices=sorted(build.ARABIC_FONTS),
                    help="Embedded Arabic typeface (default: amiri)")
    ap.add_argument("--ocr-engine", dest="ocr_engine", default="auto",
                    choices=["auto", "azure", "tesseract"],
                    help="OCR engine. 'auto' uses Azure Document Intelligence "
                         "when configured (far better on Arabic) and falls back "
                         "to Tesseract otherwise.")
    args = ap.parse_args(argv)
    res = run(args.pdf, args.out, args.max_pages, args.force_route,
              args.aggressive_clean, args.make_pdf, args.workers,
              font_mode=args.font_mode, ocr_engine=args.ocr_engine,
              arabic_font=args.arabic_font)
    print("\n=== OUTPUTS ===")
    for k, v in res["outputs"].items():
        print(f"{k:8}: {v}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
