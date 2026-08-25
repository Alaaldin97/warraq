"""Conversion report generation."""
from __future__ import annotations

import os
import textwrap


def _line(k, v):
    return f"  {k:<26}: {v}"


def write_report(r: dict, path: str) -> str:
    a = r["analysis"]
    meta = r.get("meta", {})
    o = r["outputs"]
    L = []
    L.append("=" * 72)
    L.append("KINDLE OASIS BOOK OPTIMIZER - CONVERSION REPORT")
    L.append("Target device: Kindle Oasis 9th Generation (2017), 7in, 300 ppi, 1264x1680")
    L.append("=" * 72)

    L.append("\n1. SOURCE")
    L.append(_line("File", os.path.basename(r["input"])))
    L.append(_line("Pages", a["page_count"]))
    L.append(_line("Title", meta.get("title", "-")))
    L.append(_line("Author", meta.get("author", "-")))
    L.append(_line("Source TOC entries", a["toc_entries"]))

    L.append("\n2. DETECTED LANGUAGE AND DOCUMENT TYPE")
    langmap = {"ar": "Arabic", "en": "English", "bilingual": "Bilingual (Arabic + English)"}
    L.append(_line("Language", langmap.get(a.get("language"), "undetermined")))
    L.append(_line("Arabic / Latin ratio",
                   f"{a['arabic_ratio']:.2f} / {a['latin_ratio']:.2f}"))
    L.append(_line("Reading direction", "right-to-left" if r.get("rtl") else "left-to-right"))
    dt = {"text": "text-based (native text layer)",
          "scanned": "image-based / scanned",
          "text_over_scan": "scanned pages with a pre-existing OCR text layer",
          "mixed": "mixed (some pages scanned)"}
    L.append(_line("Document type", dt.get(a["doc_type"], a["doc_type"])))
    L.append(_line("Text pages", f"{a['text_page_ratio']:.0%}"))
    L.append(_line("Columns",
                   f"dominant {a['dominant_columns']}  distribution {a['column_histogram']}"))
    L.append(_line("Presentation-form text",
                   "yes - normalized to base Arabic letters" if a["presentation_forms"] else "no"))

    L.append("\n3. PROCESSING METHOD")
    routes = {"reflow": "Reflowable text from the native PDF text layer",
              "ocr": "OCR -> reflowable text",
              "fixed": "Fixed layout - cleaned page images preserved"}
    L.append(_line("Route", routes.get(r["route"], r["route"])))
    L.append(_line("Reason", r["route_reason"]))

    L.append("\n4. OCR STATUS")
    oc = r.get("ocr", {})
    if oc.get("used"):
        eng = {"azure": "Azure AI Document Intelligence (prebuilt-read)",
               "tesseract": "Tesseract 5"}.get(oc.get("engine", "tesseract"),
                                               oc.get("engine"))
        L.append(_line("Engine", f"{eng} [{oc['lang']}]"))
        L.append(_line("Pages OCR'd", oc["pages_ocred"]))
        L.append(_line("Mean confidence", f"{oc['mean_conf']} ({oc['verdict']})"))
        L.append(_line("Low-confidence pages", len(oc.get("low_conf_pages", []))))
        cmp_ = r.get("engine_comparison")
        if cmp_:
            L.append(_line("Engine comparison",
                           f"azure {cmp_['azure']['score']} vs "
                           f"tesseract {cmp_['tesseract']['score']} "
                           f"(higher is better)"))
    else:
        L.append(_line("OCR", "not required - native text layer used"))

    L.append("\n5. CLEANUP AND FORMATTING ACTIONS")
    st = r.get("extract_stats", {})
    acts = []
    if a["skew_max"] > 0.15:
        acts.append(f"Deskew applied (median {a['skew_median']}deg, max {a['skew_max']}deg)")
    if a["blank_pages"]:
        acts.append(f"Blank pages skipped: {len(a['blank_pages'])} "
                    f"({a['blank_pages'][:12]}{'...' if len(a['blank_pages']) > 12 else ''})")
    if r.get("dropped_duplicates"):
        acts.append(f"Duplicate pages dropped: {len(r['dropped_duplicates'])} "
                    f"{r['dropped_duplicates'][:12]}")
    if a["rotated_pages"]:
        acts.append(f"Rotation normalized on {len(a['rotated_pages'])} page(s)")
    if a["noisy_pages"]:
        acts.append(f"Denoise + contrast/shading correction on {len(a['noisy_pages'])} noisy page(s)")
    if r["route"] in ("ocr", "fixed"):
        acts.append("Automatic margin detection and cropping to content")
        acts.append("Uniform page size 1264x1680 px, 16-level greyscale for e-ink")
    if a["dominant_columns"] > 1:
        acts.append(f"Column detection and reading-order reconstruction "
                    f"({a['dominant_columns']} columns)")
    if st.get("running_heads_removed"):
        acts.append(f"Repeated headers/footers removed: {st['running_heads_removed']} line(s)")
    if st.get("page_numbers_removed"):
        acts.append(f"Standalone page numbers removed: {st['page_numbers_removed']}")
    if st.get("presentation_form_lines"):
        acts.append(f"Arabic presentation forms normalized on {st['presentation_form_lines']} line(s)")
    if st.get("arabic_reversed_lines"):
        acts.append(f"Visual-order Arabic restored to logical order on "
                    f"{st['arabic_reversed_lines']} line(s)")
    if st.get("headings"):
        acts.append(f"Headings detected: {st['headings']} -> chapter navigation + TOC")
    if st.get("toc_headings"):
        acts.append(f"Source bookmarks used to rebuild chapter navigation: "
                    f"{st['toc_headings']} entry/entries")
    if st.get("footnotes"):
        acts.append(f"Footnotes separated and kept: {st['footnotes']}")
    if st.get("images"):
        acts.append(f"Figures preserved inline: {st['images']}")
    if st.get("pages_kept_as_image"):
        acts.append(f"Low-confidence pages preserved as original page images "
                    f"instead of unreliable text: {st['pages_kept_as_image']}")
    if a["low_dpi_pages"]:
        acts.append(f"Low-resolution source images upscaled/sharpened on "
                    f"{len(a['low_dpi_pages'])} page(s)")
    if not acts:
        acts.append("No cleanup required - source was already clean")
    for x in acts:
        L.append("  - " + x)

    L.append("\n6. OUTPUT FORMATS GENERATED")
    for k in ("azw3", "azw3_searchable", "azw3_fixed", "kfx", "pdf", "report"):
        if o.get(k):
            size = os.path.getsize(o[k]) / 1024 / 1024 if os.path.exists(o[k]) else 0
            label = {"azw3_fixed": "AZW3 (fixed layout)",
                     "azw3_searchable": "AZW3 (searchable)"}.get(k, k.upper())
            L.append(_line(label, f"{os.path.basename(o[k])}  ({size:.1f} MB)"))
    if r.get("arabic_font"):
        L.append(_line("Arabic typeface",
                       f"{r['arabic_font']} (embedded, pre-shaped so the "
                       f"Kindle renders it correctly)"))
    kf = r.get("kfx", {})
    if not o.get("kfx"):
        L.append("  KFX                       : NOT GENERATED")
        L.append(textwrap.fill(kf.get("reason", ""), 70,
                               initial_indent="    ", subsequent_indent="    "))
        L.append(textwrap.fill(kf.get("note", ""), 70,
                               initial_indent="    ", subsequent_indent="    "))

    L.append("\n7. QUALITY ASSURANCE")
    L.append(_line("Overall QA verdict", r.get("qa_gate", "n/a")))
    for kind, q in r.get("qa", {}).items():
        if kind == "report":
            continue
        L.append(f"  [{kind.upper()}]")
        fc = q.get("format_check", {})
        L.append(_line("  opens correctly", q.get("opens")))
        L.append(_line("  format verified",
                       f"{fc.get('ok')} ({fc.get('signature', fc.get('reason', ''))})"))
        m = q.get("metadata", {})
        if m:
            L.append(_line("  title in file", m.get("title", "-")))
            L.append(_line("  author in file", m.get("author(s)", m.get("author", "-"))))
            if m.get("languages"):
                L.append(_line("  language in file", m["languages"]))
        cov = q.get("coverage")
        if cov and cov.get("token_ratio") is not None:
            L.append(_line("  content vs source",
                           f"tokens {cov['token_ratio']:.0%}, vocabulary recall "
                           f"{cov['vocab_recall']:.0%}"))
        if q.get("pages"):
            L.append(_line("  pages in output", q["pages"]))
        ar = q.get("arabic")
        if ar:
            L.append(_line("  Arabic validation",
                           "PASS" if ar["ok"] else "ISSUES: " + ", ".join(ar["issues"])))
            L.append(_line("  Arabic words checked", ar["words"]))
        fo = q.get("font")
        if fo and fo.get("checked"):
            L.append(_line("  Arabic font embedding", fo.get("note", "")))
        for i, s in enumerate(q.get("samples", [])):
            tag = ["start", "middle", "end"][i] if i < 3 else str(i)
            snippet = " ".join(s.split())[:150]
            L.append(f"    sample ({tag}): {snippet}")

    L.append("\n8. PAGES REQUIRING MANUAL REVIEW")
    mr = r.get("manual_review_pages", [])
    kept = r.get("extract_stats", {}).get("pages_kept_as_image", 0)
    if mr:
        L.append(f"  {len(mr)} page(s) with low OCR confidence: "
                 f"{mr[:40]}{'...' if len(mr) > 40 else ''}")
        if kept:
            L.append(f"  {kept} of them were embedded as the original page image "
                     f"instead of text, so nothing is corrupted - they simply "
                     f"cannot be resized or searched.")
        else:
            L.append("  Please spot-check these pages in the output.")
    else:
        L.append("  None flagged.")

    L.append("\n9. KNOWN LIMITATIONS AND COMPROMISES")
    lim = list(r.get("warnings", []))
    if r["route"] == "fixed":
        lim.append("Fixed layout: font size cannot be changed on the device; pages are "
                   "pre-rendered to 1264x1680 to exactly fill the Oasis screen.")
        lim.append("Text is not selectable in the AZW3; the companion PDF carries an "
                   "invisible OCR text layer for search where OCR succeeded.")
    if r["route"] in ("reflow", "ocr"):
        lim.append("Reflowable output: original page numbering is not preserved "
                   "(standard for reflowable e-books).")
    if oc.get("used") and oc.get("verdict") != "good":
        lim.append(f"OCR confidence was {oc['verdict']}; check flagged pages.")
    if a["page_size_variants"] and len(a["page_size_variants"]) > 1:
        lim.append(f"Source had {len(a['page_size_variants'])} different page sizes; "
                   f"all output pages were normalized to one size.")
    if not lim:
        lim.append("None.")
    for x in lim:
        L.append(textwrap.fill("- " + x, 72, subsequent_indent="  "))

    L.append("\n10. RECOMMENDED FILE FOR YOUR KINDLE OASIS")
    if o.get("kfx"):
        rec = ("Use the .kfx file (Enhanced Typesetting). "
               "The .azw3 is an equally valid fallback.")
    elif r.get("qa_gate") == "FAIL" and o.get("azw3_fixed"):
        rec = (f"Use {os.path.basename(o['azw3_fixed'])} (page-exact). The reflowable "
               f"AZW3 did not pass the content check for this source, so treat it as "
               f"a convenience copy only.")
    elif o.get("azw3_searchable"):
        rec = (f"Use {os.path.basename(o['azw3'])} - Arabic set in "
               f"{r.get('arabic_font', 'Amiri')}. Its text is pre-shaped, so "
               f"Kindle search and dictionary lookup do not work on it; side-load "
               f"{os.path.basename(o['azw3_searchable'])} as well if you want "
               f"search. "
               + (f"{os.path.basename(o['azw3_fixed'])} is the page-exact backup."
                  if o.get("azw3_fixed") else ""))
    elif o.get("azw3_fixed"):
        rec = (f"Start with {os.path.basename(o['azw3'])} (reflowable OCR text - you "
               f"can change the font size). If any Arabic page looks wrong, switch to "
               f"{os.path.basename(o['azw3_fixed'])}, which shows the original page "
               f"images exactly as printed.")
    elif r["route"] == "fixed":
        rec = (f"Use {os.path.basename(o.get('azw3', '-'))} - it is page-perfect and "
               f"fills the screen. If you prefer pinch-zoom, side-load "
               f"{os.path.basename(o.get('pdf', '-'))} instead.")
    else:
        rec = (f"Use {os.path.basename(o.get('azw3', '-'))} - reflowable, so you can "
               f"change font size, and it has working chapter navigation.")
    L.append("  " + rec)
    L.append("\n  Transfer by USB to the 'documents' folder, or e-mail to your "
             "Send-to-Kindle address.")
    L.append("\n" + "=" * 72)
    L.append(f"Processing time: {r.get('elapsed_sec', '?')} s")

    text = "\n".join(L)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return text
