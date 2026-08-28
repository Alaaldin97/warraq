"""Output generation: reflowable AZW3, fixed-layout AZW3, Kindle-sized PDF."""
from __future__ import annotations

import html
import os
import shutil
import sys
import zipfile

import cv2
import fitz
import numpy as np

from . import device as dev
from . import proc

EBOOK_CONVERT = os.environ.get(
    "KBO_EBOOK_CONVERT", r"C:\Program Files\Calibre2\ebook-convert.exe")
EBOOK_META = os.environ.get(
    "KBO_EBOOK_META", r"C:\Program Files\Calibre2\ebook-meta.exe")
def _asset_root() -> str:
    """Assets directory, working both from source and from a frozen bundle."""
    env = os.environ.get("KBO_ASSETS")
    if env and os.path.isdir(env):
        return env
    base = getattr(sys, "_MEIPASS", None)
    if base:
        p = os.path.join(base, "assets")
        if os.path.isdir(p):
            return p
    return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "assets")


ASSETS = _asset_root()


def calibre_available() -> bool:
    return os.path.exists(EBOOK_CONVERT)


# ------------------------------------------------------------------ CSS
ARABIC_FONTS = {
    # name: (regular, bold, human description, line-height)
    "amiri": ("Amiri-Regular.ttf", "Amiri-Bold.ttf",
              "Amiri - classical Naskh, traditional book typography", 1.7),
    "notonaskh": ("NotoNaskhArabic-Regular.ttf", "NotoNaskhArabic-Bold.ttf",
                  "Noto Naskh - clean and highly legible", 1.6),
    "scheherazade": ("ScheherazadeNew-Regular.ttf", "ScheherazadeNew-Bold.ttf",
                     "Scheherazade New - SIL, designed for long-form reading",
                     1.75),
    "lateef": ("Lateef-Regular.ttf", None,
               "Lateef - compact Naskh, more words per page", 1.65),
    "markazi": ("Markazi-Regular.ttf", None,
                "Markazi Text - contemporary, low contrast", 1.6),
}


def resolve_font(name: str, cache_dir: str) -> dict | None:
    """Locate a font, flattening it for pre-shaped rendering if needed."""
    from . import fontfix
    spec = ARABIC_FONTS.get(name.lower())
    if not spec:
        return None
    reg, bold, desc, lh = spec
    out = {"name": name, "description": desc, "line_height": lh, "files": []}
    for f in (reg, bold):
        if not f:
            out["files"].append(None)
            continue
        src = None
        for d in (os.path.join(ASSETS, "fonts"), ASSETS):
            p = os.path.join(d, f)
            if os.path.exists(p):
                src = p
                break
        if not src:
            out["files"].append(None)
            continue
        flat, info = fontfix.ensure_preshape_font(src, cache_dir)
        out["files"].append(flat)
        out.setdefault("flatten", []).append(info)
    if not out["files"] or out["files"][0] is None:
        return None
    return out


def _css(rtl: bool, embed_font: bool = True, family: str = "KBOArabic",
         files: tuple = ("Amiri-Regular.ttf", "Amiri-Bold.ttf"),
         line_height: float = 1.5) -> str:
    fonts = ""
    if rtl and embed_font:
        reg = files[0]
        bold = files[1] if len(files) > 1 and files[1] else files[0]
        fonts = f"""
@font-face {{ font-family: "{family}"; font-weight: normal; font-style: normal;
             src: url("fonts/{reg}"); }}
@font-face {{ font-family: "{family}"; font-weight: bold; font-style: normal;
             src: url("fonts/{bold}"); }}
"""
    if rtl:
        body_font = f'"{family}", serif' if embed_font else "serif"
    else:
        body_font = "serif"
    align = "justify"
    return f"""{fonts}
body {{
  font-family: {body_font};
  direction: {"rtl" if rtl else "ltr"};
  text-align: {align};
  line-height: {line_height};
  margin: 0.35em 0.5em;
  widows: 2; orphans: 2;
  {"" if rtl else "hyphens: auto; -webkit-hyphens: auto;"}
}}
h1, h2, h3 {{
  font-family: {body_font};
  text-align: {"right" if rtl else "left"};
  line-height: 1.3;
  page-break-after: avoid;
  margin: 1.1em 0 0.55em 0;
}}
h1 {{ font-size: 1.5em; page-break-before: always; }}
h2 {{ font-size: 1.22em; }}
p  {{ margin: 0 0 0.32em 0; text-indent: 1.15em; }}
p.first, h1 + p, h2 + p {{ text-indent: 0; }}
p.fn {{ font-size: 0.82em; text-indent: 0; margin: 0.25em 0;
        line-height: 1.35; }}
hr.fnsep {{ width: 35%; margin: 0.7em 0 0.4em 0; border: 0;
            border-top: 1px solid #888; }}
div.img {{ text-align: center; margin: 0.6em 0; page-break-inside: avoid; }}
div.img img {{ max-width: 100%; max-height: 88%; }}
blockquote {{ margin: 0.5em 1.2em; font-size: 0.95em; }}
"""


# ------------------------------------------------------------------ HTML
def _preshape(text: str) -> str:
    """Convert logical Arabic to explicit presentation forms.

    Used only as a fallback for readers whose engine cannot perform contextual
    shaping. The glyphs are then drawn as-is, so joining cannot break. Costs
    plain-Arabic searchability, so it is never the default.
    """
    try:
        import arabic_reshaper
    except ImportError:
        return text
    if not any("\u0600" <= c <= "\u06FF" for c in text):
        return text
    try:
        return arabic_reshaper.reshape(text)
    except Exception:
        return text


def write_html_book(blocks, meta: dict, out_dir: str, rtl: bool,
                    image_dir: str | None = None,
                    font_mode: str = "native",
                    arabic_font: str | None = None) -> str:
    """font_mode: native | embed | preshape

    native   - real Unicode text, shaped by the device's own Arabic font
    preshape - letters pre-joined; required for a CUSTOM embedded Arabic font,
               because the Kindle Oasis does not shape embedded fonts
    embed    - embed a font and rely on device shaping (broken on the Oasis)
    """
    from . import fontfix
    embed_font = font_mode in ("embed", "preshape")
    shape = font_mode == "preshape"
    os.makedirs(out_dir, exist_ok=True)

    font_files = ("Amiri-Regular.ttf", "Amiri-Bold.ttf")
    line_height = 1.5
    font_paths: list = []
    if rtl and embed_font:
        cache = os.path.join(out_dir, "_fontcache")
        spec = resolve_font(arabic_font or "amiri", cache)
        if spec:
            font_paths = spec["files"]
            line_height = spec["line_height"]
        fdir = os.path.join(out_dir, "fonts")
        os.makedirs(fdir, exist_ok=True)
        names = []
        for p in font_paths:
            if p and os.path.exists(p):
                shutil.copy2(p, os.path.join(fdir, os.path.basename(p)))
                names.append(os.path.basename(p))
            else:
                names.append(names[0] if names else None)
        if names and names[0]:
            font_files = tuple(names[:2]) if len(names) > 1 else (names[0], names[0])

    if image_dir and os.path.isdir(image_dir):
        dst = os.path.join(out_dir, "images")
        os.makedirs(dst, exist_ok=True)
        for f in os.listdir(image_dir):
            shutil.copy2(os.path.join(image_dir, f), os.path.join(dst, f))

    with open(os.path.join(out_dir, "style.css"), "w", encoding="utf-8") as f:
        f.write(_css(rtl, embed_font, "KBOArabic", font_files, line_height))

    primary_font = font_paths[0] if font_paths else None

    def T(s: str) -> str:
        if shape:
            s = _preshape(s)
            if primary_font:
                s, _ = fontfix.adapt_text_to_font(s, primary_font)
        return html.escape(s)

    lang = "ar" if rtl else "en"
    parts = [
        "<?xml version='1.0' encoding='utf-8'?>",
        '<!DOCTYPE html>',
        f'<html xmlns="http://www.w3.org/1999/xhtml" lang="{lang}" '
        f'xml:lang="{lang}" dir="{"rtl" if rtl else "ltr"}">',
        "<head>",
        '<meta http-equiv="Content-Type" content="text/html; charset=utf-8"/>',
        f"<title>{html.escape(meta.get('title', 'Book'))}</title>",
        '<link rel="stylesheet" type="text/css" href="style.css"/>',
        "</head>",
        f'<body dir="{"rtl" if rtl else "ltr"}">',
    ]
    if not any(b.kind == "heading" for b in blocks):
        parts.append(f"<h1>{T(meta.get('title', 'Book'))}</h1>")

    prev_kind = None
    for b in blocks:
        if b.kind == "heading":
            tag = "h1" if b.level <= 1 else "h2"
            parts.append(f"<{tag}>{T(b.text)}</{tag}>")
        elif b.kind == "para":
            cls = ' class="first"' if prev_kind in (None, "heading", "image") else ""
            parts.append(f"<p{cls}>{T(b.text)}</p>")
        elif b.kind == "footnote":
            if prev_kind != "footnote":
                parts.append('<hr class="fnsep"/>')
            parts.append(f'<p class="fn">{T(b.text)}</p>')
        elif b.kind == "image":
            src = "images/" + b.meta["file"]
            parts.append(f'<div class="img"><img src="{src}" alt="figure"/></div>')
        prev_kind = b.kind
    parts.append("</body></html>")
    path = os.path.join(out_dir, "index.html")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(parts))
    return path


# ------------------------------------------------------------------ AZW3
def _meta_args(meta: dict, lang: str) -> list[str]:
    args = []
    if meta.get("title"):
        args += ["--title", meta["title"]]
    if meta.get("author"):
        args += ["--authors", meta["author"]]
    args += ["--language", lang]
    if meta.get("cover") and os.path.exists(meta["cover"]):
        args += ["--cover", meta["cover"]]
    if meta.get("publisher"):
        args += ["--publisher", meta["publisher"]]
    return args


def html_to_azw3(html_path: str, out_azw3: str, meta: dict, rtl: bool,
                 font_mode: str = "embed") -> dict:
    lang = "ar" if rtl else "en"
    cmd = [EBOOK_CONVERT, html_path, out_azw3,
           "--output-profile", dev.get()["calibre_profile"],
           "--input-encoding", "utf-8",
           "--change-justification", "original" if rtl else "justify",
           "--chapter", "//h:h1",
           "--level1-toc", "//h:h1", "--level2-toc", "//h:h2",
           "--page-breaks-before", "//h:h1",
           "--disable-font-rescaling"]
    if rtl and font_mode in ("embed", "preshape"):
        # NEVER subset an Arabic font: calibre's subsetter does not follow the
        # GSUB closure for contextual forms, so initial/medial/final glyphs go
        # missing and letters render disconnected on the device.
        cmd += ["--embed-all-fonts"]
    cmd += _meta_args(meta, lang)
    r = proc.run(cmd, capture_output=True, text=True, encoding="utf-8",
                       errors="replace")
    return {"ok": r.returncode == 0 and os.path.exists(out_azw3),
            "cmd": " ".join(cmd), "stdout": r.stdout[-2500:], "stderr": r.stderr[-1500:]}


# ------------------------------------------------------------ fixed layout
def write_fixed_epub(page_pngs: list[str], out_epub: str, meta: dict,
                     rtl: bool, chapters: list[tuple[str, int]] | None = None,
                     screen=(1264, 1680)) -> str:
    """Pre-paginated EPUB, one device-sized image per page, with a real TOC."""
    w, h = screen
    lang = "ar" if rtl else "en"
    uid = "kbo-" + os.path.basename(out_epub).replace(".", "-")
    tmp = out_epub + ".build"
    if os.path.exists(tmp):
        shutil.rmtree(tmp)
    oebps = os.path.join(tmp, "OEBPS")
    os.makedirs(os.path.join(oebps, "images"))
    os.makedirs(os.path.join(tmp, "META-INF"))

    names = []
    for i, p in enumerate(page_pngs, 1):
        n = f"p{i:05d}.png"
        shutil.copy2(p, os.path.join(oebps, "images", n))
        names.append(n)

    for i, n in enumerate(names, 1):
        with open(os.path.join(oebps, f"page{i:05d}.xhtml"), "w",
                  encoding="utf-8") as f:
            f.write(
                "<?xml version='1.0' encoding='utf-8'?>\n"
                '<!DOCTYPE html>\n<html xmlns="http://www.w3.org/1999/xhtml" '
                f'lang="{lang}" xml:lang="{lang}">\n<head>\n'
                f"<title>Page {i}</title>\n"
                f'<meta name="viewport" content="width={w}, height={h}"/>\n'
                "<style>html,body{margin:0;padding:0;height:100%;}"
                "img{width:100%;height:100%;display:block;}</style>\n"
                "</head>\n<body>\n"
                f'<div><img src="images/{n}" alt="page {i}"/></div>\n'
                "</body>\n</html>\n")

    manifest, spine = [], []
    for i, n in enumerate(names, 1):
        manifest.append(f'<item id="pg{i}" href="page{i:05d}.xhtml" '
                        f'media-type="application/xhtml+xml" properties="svg"/>')
        manifest.append(f'<item id="im{i}" href="images/{n}" media-type="image/png"/>')
        spine.append(f'<itemref idref="pg{i}"/>')
    cover_item = ""
    if names:
        cover_item = '<meta name="cover" content="im1"/>'

    toc_rows = chapters or [(f"Page {i}", i) for i in range(1, len(names) + 1, 25)]
    nav_items = "\n".join(
        f'<li><a href="page{p:05d}.xhtml">{html.escape(t)}</a></li>'
        for t, p in toc_rows if 1 <= p <= len(names))

    with open(os.path.join(oebps, "nav.xhtml"), "w", encoding="utf-8") as f:
        f.write('<?xml version="1.0" encoding="utf-8"?>\n<!DOCTYPE html>\n'
                '<html xmlns="http://www.w3.org/1999/xhtml" '
                'xmlns:epub="http://www.idpf.org/2007/ops"><head>'
                "<title>Contents</title></head><body>"
                '<nav epub:type="toc" id="toc"><h1>Contents</h1><ol>'
                f"{nav_items}</ol></nav></body></html>")

    with open(os.path.join(oebps, "content.opf"), "w", encoding="utf-8") as f:
        f.write(
            '<?xml version="1.0" encoding="utf-8"?>\n'
            '<package xmlns="http://www.idpf.org/2007/opf" version="3.0" '
            'unique-identifier="bookid" prefix="rendition: '
            'http://www.idpf.org/vocab/rendition/#">\n'
            '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">\n'
            f'<dc:identifier id="bookid">{html.escape(uid)}</dc:identifier>\n'
            f"<dc:title>{html.escape(meta.get('title', 'Book'))}</dc:title>\n"
            f"<dc:creator>{html.escape(meta.get('author', 'Unknown'))}</dc:creator>\n"
            f"<dc:language>{lang}</dc:language>\n"
            '<meta property="rendition:layout">pre-paginated</meta>\n'
            '<meta property="rendition:orientation">auto</meta>\n'
            '<meta property="rendition:spread">none</meta>\n'
            f"{cover_item}\n</metadata>\n<manifest>\n"
            '<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" '
            'properties="nav"/>\n' + "\n".join(manifest) +
            f'\n</manifest>\n<spine page-progression-direction='
            f'"{"rtl" if rtl else "ltr"}">\n' + "\n".join(spine) +
            "\n</spine>\n</package>\n")

    with open(os.path.join(tmp, "META-INF", "container.xml"), "w",
              encoding="utf-8") as f:
        f.write('<?xml version="1.0"?>\n<container version="1.0" '
                'xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
                '<rootfiles><rootfile full-path="OEBPS/content.opf" '
                'media-type="application/oebps-package+xml"/></rootfiles></container>')

    with zipfile.ZipFile(out_epub, "w") as z:
        z.writestr("mimetype", "application/epub+zip", zipfile.ZIP_STORED)
        for root, _, files in os.walk(tmp):
            for fn in files:
                full = os.path.join(root, fn)
                rel = os.path.relpath(full, tmp).replace("\\", "/")
                z.write(full, rel, zipfile.ZIP_DEFLATED)
    shutil.rmtree(tmp, ignore_errors=True)
    return out_epub


def epub_to_azw3(epub: str, out_azw3: str, meta: dict, rtl: bool,
                 fixed: bool = True) -> dict:
    cmd = [EBOOK_CONVERT, epub, out_azw3,
           "--output-profile", dev.get()["calibre_profile"]]
    if fixed:
        cmd += ["--no-inline-toc"]
    cmd += _meta_args(meta, "ar" if rtl else "en")
    r = proc.run(cmd, capture_output=True, text=True, encoding="utf-8",
                       errors="replace")
    return {"ok": r.returncode == 0 and os.path.exists(out_azw3),
            "stdout": r.stdout[-2500:], "stderr": r.stderr[-1500:]}


def cbz_from_pngs(page_pngs: list[str], out_cbz: str) -> str:
    with zipfile.ZipFile(out_cbz, "w", zipfile.ZIP_DEFLATED) as z:
        for i, p in enumerate(page_pngs, 1):
            z.write(p, f"{i:05d}.png")
    return out_cbz


def cbz_to_azw3(cbz: str, out_azw3: str, meta: dict, rtl: bool) -> dict:
    cmd = [EBOOK_CONVERT, cbz, out_azw3,
           "--output-profile", dev.get()["calibre_profile"],
           "--dont-normalize", "--dont-sharpen", "--keep-aspect-ratio",
           "--disable-trim", "--no-process"]
    cmd += _meta_args(meta, "ar" if rtl else "en")
    r = proc.run(cmd, capture_output=True, text=True, encoding="utf-8",
                       errors="replace")
    return {"ok": r.returncode == 0 and os.path.exists(out_azw3),
            "stdout": r.stdout[-2000:], "stderr": r.stderr[-1200:]}


# ------------------------------------------------------------------ PDF
def build_optimized_pdf(page_pngs: list[str], out_pdf: str, meta: dict,
                        ocr_pdf_pages: list[bytes] | None = None,
                        toc: list | None = None) -> str:
    """Device-sized PDF; uses OCR'd searchable pages when supplied."""
    d = dev.get()
    pw, ph = d["page_pt"]
    doc = fitz.open()
    for i, png in enumerate(page_pngs):
        if ocr_pdf_pages and i < len(ocr_pdf_pages) and ocr_pdf_pages[i]:
            src = fitz.open("pdf", ocr_pdf_pages[i])
            page = doc.new_page(width=pw, height=ph)
            page.show_pdf_page(fitz.Rect(0, 0, pw, ph), src, 0)
            src.close()
        else:
            page = doc.new_page(width=pw, height=ph)
            page.insert_image(fitz.Rect(0, 0, pw, ph), filename=png)
    doc.set_metadata({"title": meta.get("title", ""),
                      "author": meta.get("author", ""),
                      "producer": "Kindle Oasis Book Optimizer"})
    if toc:
        try:
            doc.set_toc([[1, t, p] for t, p in toc])
        except Exception:
            pass
    doc.save(out_pdf, garbage=4, deflate=True)
    doc.close()
    return out_pdf


def save_png(img: np.ndarray, path: str) -> str:
    cv2.imwrite(path, img, [cv2.IMWRITE_PNG_COMPRESSION, 6])
    return path
