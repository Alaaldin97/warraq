"""Document model extraction: text layer or OCR -> ordered semantic blocks.

Handles column reconstruction, RTL ordering, running head/footer removal,
heading detection, footnote separation and paragraph reassembly.
"""
from __future__ import annotations

import re
import statistics

import fitz

from . import analyze as _an
from . import arabic


class Block:
    __slots__ = ("kind", "text", "page", "level", "meta")

    def __init__(self, kind, text, page, level=0, meta=None):
        self.kind = kind          # heading | para | footnote | image | pagebreak
        self.text = text
        self.page = page
        self.level = level
        self.meta = meta or {}

    def __repr__(self):
        return f"<{self.kind} p{self.page} {self.text[:40]!r}>"


# ------------------------------------------------------------ line building
def _lines_from_page(page: fitz.Page) -> list[dict]:
    d = page.get_text("dict")
    lines = []
    for b in d.get("blocks", []):
        if b.get("type") != 0:
            continue
        for ln in b.get("lines", []):
            spans = ln.get("spans", [])
            txt = "".join(s.get("text", "") for s in spans)
            if not txt.strip():
                continue
            sizes = [s.get("size", 0) for s in spans if s.get("text", "").strip()]
            flags = [s.get("flags", 0) for s in spans]
            fonts = [s.get("font", "") for s in spans]
            lines.append({
                "text": txt,
                "bbox": list(ln["bbox"]),
                "size": round(statistics.median(sizes), 2) if sizes else 0,
                "bold": any(f & 2 ** 4 for f in flags) or
                        any("bold" in f.lower() for f in fonts),
                "fonts": fonts,
            })
    return lines


def _ocr_lines_to_common(ocr_lines: list[dict], scale: float) -> list[dict]:
    return [{"text": l["text"],
             "bbox": [c / scale for c in l["bbox"]],
             "size": l["size"] / scale,
             "bold": False,
             "conf": l["conf"],
             "fonts": []} for l in ocr_lines]


# ------------------------------------------------------------ ordering
def order_lines(lines: list[dict], columns: int, rtl: bool,
                page_width: float) -> list[dict]:
    """Sort lines into true reading order, reconstructing columns."""
    if not lines:
        return []
    if columns <= 1:
        return sorted(lines, key=lambda l: (round(l["bbox"][1], 1), l["bbox"][0]))

    centers = sorted((l["bbox"][0] + l["bbox"][2]) / 2 for l in lines)
    # split at the widest gaps between line centres -> column boundaries
    gaps = sorted(((centers[i + 1] - centers[i], i) for i in range(len(centers) - 1)),
                  reverse=True)[:columns - 1]
    cuts = sorted(centers[i] + g / 2 for g, i in gaps)

    def col_of(l):
        c = (l["bbox"][0] + l["bbox"][2]) / 2
        idx = sum(1 for cut in cuts if c > cut)
        return (columns - 1 - idx) if rtl else idx

    buckets: dict[int, list] = {}
    for l in lines:
        buckets.setdefault(col_of(l), []).append(l)
    out = []
    for c in sorted(buckets):
        out.extend(sorted(buckets[c], key=lambda l: (round(l["bbox"][1], 1),
                                                     l["bbox"][0])))
    return out


# ------------------------------------------------------------ classification
_NUM_ONLY = re.compile(r"^[\s\d\u0660-\u0669ivxlcIVXLC\.\-\u2013\u2014\|]+$")


def _is_running(text: str, repeated: dict) -> bool:
    key = _an._repeat_key(text)
    return bool(key) and key in repeated


def _looks_like_page_number(text: str) -> bool:
    return bool(text.strip()) and bool(_NUM_ONLY.match(text.strip())) and len(text.strip()) <= 12


_CHAPTER_RE = re.compile(
    r"^\s*(chapter|part|section|book|appendix|prologue|epilogue|introduction|"
    r"preface|conclusion|\u0627\u0644\u0641\u0635\u0644|\u0627\u0644\u0628\u0627\u0628|"
    r"\u0645\u0642\u062f\u0645\u0629|\u062e\u0627\u062a\u0645\u0629|\u0627\u0644\u0645\u0642\u062f\u0645\u0629|"
    r"\u062a\u0645\u0647\u064a\u062f|\u0627\u0644\u0642\u0633\u0645)\b",
    re.IGNORECASE)


def classify_line(line: dict, body_size: float, page_h: float,
                  mode: str = "text") -> str:
    """Classify a line.

    mode:
      text         - native text layer, font size is reliable
      ocr          - OCR text; line height is a noisy size proxy, so be strict
      conservative - only explicit chapter wording counts (used when the source
                     already provides bookmarks)
    """
    t = line["text"].strip()
    size = line["size"]
    if not t:
        return "skip"
    ratio = size / body_size if body_size else 1.0
    words = t.split()
    is_footnote = ratio <= 0.82 and line["bbox"][3] > page_h * 0.72
    if _CHAPTER_RE.match(t) and len(t) <= 80 and len(words) <= 12:
        return "heading"
    if mode == "conservative":
        return "footnote" if is_footnote else "para"
    if mode == "ocr":
        if (ratio >= 1.5 and len(words) <= 8 and len(t) <= 50
                and not t.endswith((".", ",", ";", ":", "\u060C", "\u061B"))):
            return "heading"
        return "footnote" if is_footnote else "para"
    short = len(t) <= 80 and len(words) <= 12
    if ratio >= 1.45 and short and not t.endswith(","):
        return "heading"
    if ratio >= 1.18 and short and (line["bold"] or t.isupper()):
        return "heading"
    return "footnote" if is_footnote else "para"


def _norm(s: str) -> str:
    return re.sub(r"[^\w\u0600-\u06FF]+", " ", s).strip().lower()


def dedupe_headings(blocks: list[Block]) -> int:
    """Drop adjacent duplicate/nested headings so the TOC stays clean."""
    out, removed = [], 0
    for b in blocks:
        if (b.kind == "heading" and out and out[-1].kind == "heading"):
            a, c = _norm(out[-1].text), _norm(b.text)
            if a and c and (a in c or c in a):
                if len(c) > len(a):        # keep the more complete title
                    out[-1] = b
                removed += 1
                continue
        out.append(b)
    blocks[:] = out
    return removed


# ------------------------------------------------------------ paragraph join
_HYPHEN_END = re.compile(r"([A-Za-z\u00C0-\u024F]{2,})[-\u2010\u2011]$")


def join_lines(lines: list[str], rtl: bool) -> str:
    """Merge wrapped lines into a paragraph without breaking words."""
    out = ""
    for raw in lines:
        s = raw.strip()
        if not s:
            continue
        if not out:
            out = s
            continue
        m = _HYPHEN_END.search(out)
        if m and not rtl:
            out = out[: m.start(1)] + m.group(1) + s      # repair split word
        else:
            out = out + " " + s
    return re.sub(r"\s{2,}", " ", out).strip()


_SENT_END = re.compile(r"[.!?:;\u061F\u06D4\u060C\u061B\"'\u2019\u201D\u00BB)\]]\s*$")


def _para_breaks(lines: list[dict], rtl: bool) -> list[list[dict]]:
    """Group consecutive lines into paragraphs using indent / gap / punctuation."""
    if not lines:
        return []
    heights = [l["bbox"][3] - l["bbox"][1] for l in lines]
    med_h = statistics.median(heights) if heights else 12
    lefts = [l["bbox"][0] for l in lines]
    rights = [l["bbox"][2] for l in lines]
    body_left = statistics.median(lefts)
    body_right = statistics.median(rights)
    groups, cur = [], [lines[0]]
    for prev, cur_line in zip(lines, lines[1:]):
        gap = cur_line["bbox"][1] - prev["bbox"][3]
        big_gap = gap > med_h * 0.85
        new_para = big_gap
        if rtl:
            if cur_line["bbox"][2] < body_right - med_h * 0.9:
                new_para = True
            if prev["bbox"][0] > body_left + med_h * 1.6:   # short last line
                new_para = True
        else:
            if cur_line["bbox"][0] > body_left + med_h * 0.9:
                new_para = True
            if prev["bbox"][2] < body_right - med_h * 1.6:
                new_para = True
        # Veto layout-only breaks that would cut a sentence in half.
        if new_para and not big_gap:
            prev_open = not _SENT_END.search(prev["text"].strip())
            nxt = cur_line["text"].strip()
            continues = prev_open and bool(nxt) and (
                nxt[0].islower() if not rtl else not nxt[0].isupper())
            if continues:
                new_para = False
        if new_para:
            groups.append(cur)
            cur = [cur_line]
        else:
            cur.append(cur_line)
    groups.append(cur)
    return groups


# ------------------------------------------------------------ main entry
def build_blocks(page_lines: list[tuple[int, list[dict], dict]],
                 analysis: dict, rtl: bool, from_ocr: bool = False,
                 heading_mode: str = "text") -> tuple[list[Block], dict]:
    """page_lines: [(page_number, lines, page_info)] -> ordered blocks."""
    stats = {"headings": 0, "paragraphs": 0, "footnotes": 0,
             "running_heads_removed": 0, "page_numbers_removed": 0,
             "arabic_reversed_lines": 0, "presentation_form_lines": 0,
             "hyphen_joins": 0, "pages_kept_as_image": 0}
    rh = analysis["running_heads"]
    repeated = set(rh["top_line"]) | set(rh["bottom_line"])

    sizes = [l["size"] for _, lines, _ in page_lines for l in lines if l["size"]]
    body_size = statistics.median(sizes) if sizes else 12.0

    blocks: list[Block] = []
    pending: list[dict] = []
    pending_page = 0

    def flush():
        nonlocal pending, pending_page
        if not pending:
            return
        for grp in _para_breaks(pending, rtl):
            text = join_lines([g["text"] for g in grp], rtl)
            if text:
                blocks.append(Block("para", text, pending_page))
                stats["paragraphs"] += 1
        pending = []

    for pno, lines, pinfo in page_lines:
        page_h = pinfo.get("size_pt", [612, 792])[1]
        for line in lines:
            if line.get("image"):
                flush()
                blocks.append(Block("image", "", pno, meta=line["image"]))
                if line["image"].get("whole_page"):
                    stats["pages_kept_as_image"] += 1
                continue
            raw = line["text"]
            if rtl or arabic.arabic_ratio(raw) > 0.1:
                cleaned, flags = arabic.clean_line(raw, allow_reversal=not from_ocr)
                if flags["reversed"]:
                    stats["arabic_reversed_lines"] += 1
                if flags["presentation_forms"]:
                    stats["presentation_form_lines"] += 1
                line = dict(line, text=cleaned)
            t = line["text"].strip()
            if not t:
                continue
            if _is_running(t, repeated):
                stats["running_heads_removed"] += 1
                continue
            if _looks_like_page_number(t):
                stats["page_numbers_removed"] += 1
                continue
            kind = classify_line(line, body_size, page_h, heading_mode)
            if kind == "heading":
                flush()
                lvl = 1 if line["size"] >= body_size * 1.6 else 2
                blocks.append(Block("heading", t, pno, lvl))
                stats["headings"] += 1
            elif kind == "footnote":
                flush()
                blocks.append(Block("footnote", t, pno))
                stats["footnotes"] += 1
            else:
                pending.append(line)
                pending_page = pno
        # keep paragraphs flowing across page boundaries unless page ended cleanly
        if pending and re.search(r"[.!?\u061F\u06D4:\u060C]\s*$", pending[-1]["text"]):
            flush()
    flush()
    stats["cross_page_merges"] = merge_cross_page_paragraphs(blocks)
    clean_heading_text(blocks)
    return blocks, stats


def _image_pseudo_lines(doc, page, out_dir, page_no, min_frac=0.03) -> list[dict]:
    """Extract embedded figures as pseudo-lines so they keep their place in flow."""
    import os
    items = []
    page_area = max(page.rect.width * page.rect.height, 1)
    seen = set()
    for info in page.get_image_info(xrefs=True):
        xref = info.get("xref", 0)
        bbox = info.get("bbox")
        if not xref or not bbox:
            continue
        w = bbox[2] - bbox[0]
        h = bbox[3] - bbox[1]
        if (w * h) / page_area < min_frac or w < 40 or h < 40:
            continue
        if xref in seen:
            continue
        seen.add(xref)
        try:
            img = doc.extract_image(xref)
        except Exception:
            continue
        name = f"img_p{page_no}_{xref}.{img['ext']}"
        path = os.path.join(out_dir, name)
        with open(path, "wb") as f:
            f.write(img["image"])
        items.append({"text": "", "bbox": list(bbox), "size": 0, "bold": False,
                      "fonts": [],
                      "image": {"file": name, "w": img["width"], "h": img["height"],
                                "page": page_no}})
    return items


def apply_source_toc(blocks: list[Block], analysis: dict) -> int:
    """Promote lines to headings using the PDF's own bookmarks.

    Keeps the author's chapter structure even when visual heading detection
    misses it (common with scanned or unusually styled books).
    """
    toc = analysis.get("toc") or []
    if not toc:
        return 0
    wanted = {}
    for entry in toc:
        lvl, title, page = entry[0], entry[1], entry[2]
        if lvl <= 2 and page and str(title).strip():
            wanted.setdefault(page, (str(title).strip(), min(lvl, 2)))
    promoted = 0
    matched_pages = set()
    existing = {}
    for b in blocks:
        if b.kind == "heading":
            existing.setdefault(b.page, []).append(_norm(b.text))
    for b in blocks:
        if b.page in wanted and b.page not in matched_pages and b.kind == "para":
            title, lvl = wanted[b.page]
            norm = re.sub(r"\s+", " ", b.text).strip().lower()
            tnorm = re.sub(r"\s+", " ", title).lower()
            if norm.startswith(tnorm[:20]) and len(b.text) <= 140:
                b.kind = "heading"
                b.level = lvl
                promoted += 1
                matched_pages.add(b.page)
    # bookmarks that matched no text block still become navigation points
    for page in sorted(set(wanted) - matched_pages):
        title, lvl = wanted[page]
        tn = _norm(title)
        # skip if a detected heading on/near that page already says the same thing
        near = [h for p in (page - 1, page, page + 1) for h in existing.get(p, [])]
        if any(tn and (tn in h or h in tn) for h in near):
            continue
        for i, b in enumerate(blocks):
            if b.page >= page:
                blocks.insert(i, Block("heading", title, page, lvl))
                promoted += 1
                break
    dedupe_headings(blocks)
    clean_heading_text(blocks)
    return promoted


_LEAD_NUM = re.compile(r"^[\s>\u203A\d\u0660-\u0669.,\-\u2013\u2014)\]]{1,8}\s+")
_TRAIL_NUM = re.compile(r"\s+[\s>\u203A\d\u0660-\u0669.,\-\u2013\u2014(\[]{1,8}$")


def merge_cross_page_paragraphs(blocks: list[Block]) -> int:
    """Rejoin paragraphs that a page break cut in half.

    A paragraph that ends without terminal punctuation and is followed by a
    paragraph starting mid-sentence on the next page is one paragraph.
    """
    out, merged = [], 0
    for b in blocks:
        if (out and b.kind == "para" and out[-1].kind == "para"
                and b.page - out[-1].page in (0, 1)):
            prev = out[-1]
            prev_open = not _SENT_END.search(prev.text.strip())
            nxt = b.text.strip()
            starts_mid = bool(nxt) and (nxt[0].islower() if nxt[0].isascii()
                                        else not nxt[0].isupper())
            if prev_open and starts_mid and len(prev.text) > 15:
                prev.text = join_lines([prev.text, b.text],
                                       arabic.is_rtl_text(prev.text))
                merged += 1
                continue
        out.append(b)
    blocks[:] = out
    return merged


def clean_heading_text(blocks: list[Block]) -> int:
    """Strip stray page numbers / bullets that OCR glued onto a heading."""
    n = 0
    for b in blocks:
        if b.kind == "heading":
            t = _LEAD_NUM.sub("", b.text)
            t = _TRAIL_NUM.sub("", t)
            t = t.strip(" .,-\u2013\u2014>\u203A\u060C")
            if t and t != b.text:
                b.text = t
                n += 1
    return n


def extract_from_textlayer(pdf_path: str, analysis: dict,
                           image_dir: str | None = None) -> tuple[list[Block], dict]:
    doc = fitz.open(pdf_path)
    rtl = (analysis.get("language") in ("ar", "bilingual")
           and analysis.get("arabic_ratio", 0) >= 0.35)
    page_lines = []
    n_images = 0
    for p in analysis["pages"]:
        if p["blank"]:
            continue
        page = doc[p["number"] - 1]
        lines = _lines_from_page(page)
        if image_dir:
            imgs = _image_pseudo_lines(doc, page, image_dir, p["number"])
            n_images += len(imgs)
            lines.extend(imgs)
        lines = order_lines(lines, p["columns"], rtl, page.rect.width)
        page_lines.append((p["number"], lines, p))
    blocks, stats = build_blocks(page_lines, analysis, rtl)
    doc.close()
    stats["rtl"] = rtl
    stats["images"] = n_images
    return blocks, stats
