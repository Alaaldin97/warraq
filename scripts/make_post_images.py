"""Build the announcement image set.

Four cards at 1080x1350 (4:5), which is the tallest ratio LinkedIn shows
without cropping and therefore the most feed space per post. Arabic is drawn
from Amiri outlines through the same HarfBuzz pipeline as the rest of the
artwork, so the shaping and the shadda are correct rather than left to
whatever font the viewer has.

    python scripts/make_post_images.py

Writes docs/assets/post/.
"""
from __future__ import annotations

import base64
import pathlib
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from make_artwork import (  # noqa: E402
    AMIRI, AMIRI_BOLD, CLAY, GOLD, GREEN, INK, PARCHMENT, PARCHMENT_DIM,
    PARCHMENT_WARM, Face, centred, fit, safe_mark_drop, width,
)

ROOT = pathlib.Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "assets" / "post"
W, H = 1080, 1350

CHROME = [r"C:\Program Files\Google\Chrome\Application\chrome.exe",
          r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"]


def embed(path: pathlib.Path) -> str:
    return ("data:image/png;base64,"
            + base64.b64encode(path.read_bytes()).decode("ascii"))


def rtl(ch: str) -> bool:
    return "\u0590" <= ch <= "\u08FF" or "\uFB1D" <= ch <= "\uFDFF" \
        or "\uFE70" <= ch <= "\uFEFF"


def ltr(ch: str) -> bool:
    return ch.isalpha() and ch.isascii()


def check_single_direction(text: str) -> str:
    """Refuse mixed-direction strings.

    draw() lays glyphs out with a single advancing pen using HarfBuzz's
    direction for the whole run. That is correct for text that is entirely
    Arabic or entirely Latin, but mixed text needs the Unicode bidirectional
    algorithm to split and reorder runs. Without it the Latin comes out
    reversed - "Azure" as "eruzA" - which is precisely the class of bug this
    project exists to fix, so it fails loudly rather than shipping garbage.
    """
    if any(rtl(c) for c in text) and any(ltr(c) for c in text):
        raise ValueError(
            f"mixed-direction text is not supported by this layout: {text!r}. "
            "Use one script per line.")
    return text


def T(g: Face, text: str, size: float, cx: float, y: float, fill: str,
      tracking: float = 0.0, mark_drop: float = 0.0) -> str:
    """centred() with the direction guard applied."""
    return centred(g, check_single_direction(text), size, cx, y, fill,
                   tracking=tracking, mark_drop=mark_drop)


def frame(colour: str = GOLD) -> str:
    out = []
    for inset, c, sw, op in ((22, colour, 1.5, 0.45), (32, "#2A2521", 1, 0.9)):
        out.append(f'<rect x="{inset}" y="{inset}" width="{W - 2 * inset}" '
                   f'height="{H - 2 * inset}" rx="3" fill="none" stroke="{c}" '
                   f'stroke-width="{sw}" opacity="{op}"/>')
    for x, y in ((22, 22), (W - 22, 22), (22, H - 22), (W - 22, H - 22)):
        out.append(f'<circle cx="{x}" cy="{y}" r="5" fill="{colour}" opacity="0.7"/>'
                   f'<circle cx="{x}" cy="{y}" r="9.5" fill="none" '
                   f'stroke="{colour}" stroke-width="0.8" opacity="0.3"/>')
    return "\n".join(out)


DARK_BG = f"""  <defs>
    <linearGradient id="bg" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="#1B1713"/><stop offset="1" stop-color="{INK}"/>
    </linearGradient>
    <radialGradient id="glow" cx="0.5" cy="0.38" r="0.7">
      <stop offset="0" stop-color="{GOLD}" stop-opacity="0.14"/>
      <stop offset="1" stop-color="{GOLD}" stop-opacity="0"/>
    </radialGradient>
  </defs>
  <rect width="{W}" height="{H}" fill="url(#bg)"/>
  <rect width="{W}" height="{H}" fill="url(#glow)"/>"""


def svg(body: str) -> str:
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
            f'viewBox="0 0 {W} {H}">\n{body}\n</svg>\n')


# ------------------------------------------------------------------ 1 brand
def card_brand(g: Face, gb: Face) -> str:
    word = "وَرَّاق"
    size = 250
    drop = min(0.40, safe_mark_drop(g, word))
    above, below = g.extents(word, size)
    above -= drop * size
    base = 640
    rule = base + below + 50

    return svg(f"""{DARK_BG}
{frame()}
{T(g, word, size, W / 2, base, GOLD, mark_drop=drop)}
  <line x1="{W / 2 - 190}" y1="{rule}" x2="{W / 2 - 50}" y2="{rule}"
        stroke="{GOLD}" stroke-width="1" opacity="0.4"/>
  <line x1="{W / 2 + 50}" y1="{rule}" x2="{W / 2 + 190}" y2="{rule}"
        stroke="{GOLD}" stroke-width="1" opacity="0.4"/>
  <circle cx="{W / 2}" cy="{rule}" r="2.5" fill="{GOLD}" opacity="0.6"/>
{T(gb, "W A R R A Q", 34, W / 2, rule + 54, "#8C8168", tracking=2.4)}
{T(g, "الكتب العربية، مركّبة كما يليق بالكيندل", 44, W / 2, rule + 146, PARCHMENT_DIM)}
{T(g, "مجاني · بدون إنترنت · مفتوح المصدر", 32, W / 2, rule + 212, "#6E6552")}""")


# ------------------------------------------------------------------ 2 proof
def card_proof(g: Face, gb: Face, photo: pathlib.Path) -> str:
    import struct
    with open(photo, "rb") as f:
        head = f.read(33)
    pw, ph = struct.unpack(">II", head[16:24])

    box_w = W - 300
    scale = box_w / pw
    dh = ph * scale
    top = 190
    x = (W - box_w) / 2

    return svg(f"""{DARK_BG}
{frame()}
{T(gb, "على الجهاز، مش نظريًا", 48, W / 2, 118, GOLD)}
  <rect x="{x - 8}" y="{top - 8}" width="{box_w + 16}" height="{dh + 16}"
        rx="4" fill="#0A0908" opacity="0.75"/>
  <image x="{x}" y="{top}" width="{box_w}" height="{dh}"
         href="{embed(photo)}" preserveAspectRatio="xMidYMid slice"/>
  <rect x="{x}" y="{top}" width="{box_w}" height="{dh}" fill="none"
        stroke="{GOLD}" stroke-width="1" opacity="0.3"/>
{T(g, "كتاب عربي مصوّر، بعد التحويل", 40, W / 2, top + dh + 78, PARCHMENT_DIM)}
{T(g, "نص بتكبّره وبتدوّر فيه · حروف موصولة · خط أميري", 29, W / 2, top + dh + 130, "#6E6552")}""")


# --------------------------------------------------------------- 3 shaping
def card_shaping(g: Face, gb: Face) -> str:
    word = "المكتبة"
    broken = "\u200b ".join(word)
    pw, ph = W - 190, 290
    x = (W - pw) / 2
    t1, t2 = 320, 790
    inner = pw - 90

    bs = fit(g, broken, 150, inner)
    fs = fit(g, word, 150, inner)

    return svg(f"""  <rect width="{W}" height="{H}" fill="{PARCHMENT}"/>
{T(gb, "ليش الموضوع صعب", 52, W / 2, 132, INK)}
{T(g, "نفس الكلمة، بطريقتين", 34, W / 2, 194, "#6B5F52")}

{T(gb, "بدون تشكيل مسبق", 30, W / 2, t1 - 30, CLAY)}
  <rect x="{x}" y="{t1}" width="{pw}" height="{ph}" rx="8"
        fill="{PARCHMENT_WARM}" stroke="{CLAY}" stroke-width="2"/>
{T(g, broken, bs, W / 2, t1 + 190, CLAY)}
{T(g, "الحروف مقطّعة · ما بتنقرأ", 28, W / 2, t1 + ph + 54, "#6B5F52")}

{T(gb, "مع التشكيل المسبق", 30, W / 2, t2 - 30, GREEN)}
  <rect x="{x}" y="{t2}" width="{pw}" height="{ph}" rx="8"
        fill="{PARCHMENT_WARM}" stroke="{GREEN}" stroke-width="2"/>
{T(g, word, fs, W / 2, t2 + 190, GREEN)}
{T(g, "الحروف موصولة · عربي صحيح", 28, W / 2, t2 + ph + 54, "#6B5F52")}

{T(g, "الكيندل ما بيوصل الحروف بالخطوط المضمّنة،", 27, W / 2, H - 112, "#6B5F52")}
{T(g, "فوَرَّاق بيوصلها قبل ما يضمّنها", 27, W / 2, H - 68, "#6B5F52")}""")


# ------------------------------------------------------------------- 4 app
def card_app(g: Face, gb: Face, shot: pathlib.Path) -> str:
    import struct
    with open(shot, "rb") as f:
        head = f.read(33)
    sw, sh = struct.unpack(">II", head[16:24])

    box_w = W - 90
    scale = box_w / sw
    dh = sh * scale
    top = 470
    x = (W - box_w) / 2

    return svg(f"""{DARK_BG}
{frame()}
{T(gb, "بسيط: اسحب الكتاب وخلص", 46, W / 2, 250, GOLD)}
{T(g, "بيشتغل على ويندوز · مجاني بالكامل", 30, W / 2, 312, "#6E6552")}
  <rect x="{x - 6}" y="{top - 6}" width="{box_w + 12}" height="{dh + 12}"
        rx="8" fill="#0A0908" opacity="0.8"/>
  <image x="{x}" y="{top}" width="{box_w}" height="{dh}" href="{embed(shot)}"/>
{T(g, "قراءة ضوئية مجانية بدون إنترنت،", 31, W / 2, top + dh + 96, PARCHMENT_DIM)}
{T(g, "أو دقة أعلى للمسحات الصعبة", 31, W / 2, top + dh + 148, PARCHMENT_DIM)}""")


def rasterise(svg_path: pathlib.Path, png_path: pathlib.Path) -> bool:
    for exe in CHROME:
        if pathlib.Path(exe).exists():
            subprocess.run([exe, "--headless", "--disable-gpu",
                            f"--screenshot={png_path}",
                            f"--window-size={W},{H}",
                            "--hide-scrollbars",
                            svg_path.as_uri()],
                           check=False, capture_output=True, timeout=180)
            return png_path.exists()
    return False


def main() -> int:
    photo = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else None
    shot = pathlib.Path(sys.argv[2]) if len(sys.argv) > 2 else \
        ROOT / "docs" / "assets" / "screenshot-library.png"
    OUT.mkdir(parents=True, exist_ok=True)
    g, gb = Face(AMIRI), Face(AMIRI_BOLD)

    cards = [("1-brand", card_brand(g, gb)),
             ("3-shaping", card_shaping(g, gb))]
    if photo and photo.exists():
        cards.insert(1, ("2-proof", card_proof(g, gb, photo)))
    if shot.exists():
        cards.append(("4-app", card_app(g, gb, shot)))

    for name, body in cards:
        s = OUT / f"{name}.svg"
        p = OUT / f"{name}.png"
        s.write_text(body, encoding="utf-8")
        ok = rasterise(s, p)
        s.unlink()
        print(f"{'wrote' if ok else 'FAILED'} {p.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
