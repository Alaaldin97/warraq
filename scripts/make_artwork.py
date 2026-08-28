"""Generate the README artwork from the fonts the product actually embeds.

Everything here is drawn as vector paths taken from Amiri, not as SVG <text>.
GitHub will not load a webfont for an image, so live text would fall back to
whatever the viewer happens to have and the Arabic would render disconnected -
which is precisely the failure this project exists to fix. Outlining the
glyphs makes the artwork render identically everywhere, including offline.

Text is shaped with HarfBuzz rather than by hand. Arabic needs it for two
separate reasons: contextual joining (GSUB) and mark placement (GPOS). The
shadda in وَرَّاق sits above the ra at a position only the font's GPOS table
knows; guessing it would put the mark in the wrong place, which is the sort of
error this project exists to avoid making.

    pip install uharfbuzz
    python scripts/make_artwork.py

Writes to docs/assets/.
"""
from __future__ import annotations

import pathlib
import sys

from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.ttLib import TTFont

try:
    import uharfbuzz as hb
except ImportError:
    print("this script needs uharfbuzz:  pip install uharfbuzz", file=sys.stderr)
    raise SystemExit(1)

ROOT = pathlib.Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "assets"
AMIRI = ROOT / "engine" / "assets" / "Amiri-Regular.ttf"
AMIRI_BOLD = ROOT / "engine" / "assets" / "Amiri-Bold.ttf"

# Manuscript palette: oak-gall ink, aged parchment, gold leaf, clay, verdigris.
INK = "#141210"
INK_SOFT = "#4A423A"
PARCHMENT = "#F2E8D5"
PARCHMENT_WARM = "#FBF5E9"
PARCHMENT_DIM = "#B9AB90"
GOLD = "#C9A227"
CLAY = "#A6552F"
GREEN = "#1F5D3A"


class Face:
    """One font, shaped by HarfBuzz and outlined by fontTools."""

    def __init__(self, path: pathlib.Path):
        self.tt = TTFont(str(path))
        self.upem = self.tt["head"].unitsPerEm
        self.order = self.tt.getGlyphOrder()
        self.gs = self.tt.getGlyphSet()
        blob = hb.Blob.from_file_path(str(path))
        self.font = hb.Font(hb.Face(blob))

    def shape(self, text: str, features: dict | None = None):
        """Return [(glyph_id, x_offset, y_offset, x_advance)] in visual order.

        HarfBuzz emits RTL runs left-to-right already, so callers can lay the
        result out with a simple advancing pen.
        """
        buf = hb.Buffer()
        buf.add_str(text)
        buf.guess_segment_properties()
        hb.shape(self.font, buf, features)
        return [(i.codepoint, p.x_offset, p.y_offset, p.x_advance)
                for i, p in zip(buf.glyph_infos, buf.glyph_positions)]

    def outline(self, gid: int) -> str:
        name = self.order[gid]
        pen = SVGPathPen(self.gs)
        self.gs[name].draw(pen)
        return pen.getCommands()

    def notdef(self, text: str) -> list[int]:
        """Glyphs the font could not map. gid 0 is .notdef."""
        return [i for i, (g, *_ ) in enumerate(self.shape(text)) if g == 0]

    def extents(self, text: str, size: float) -> tuple[float, float]:
        """Ink height above and below the baseline, in px.

        Arabic marks sit well above the letters, and the qaf drops well below.
        Measuring rather than guessing is what keeps them inside the artboard.
        """
        from fontTools.pens.boundsPen import BoundsPen
        scale = size / self.upem
        top = bottom = 0.0
        for gid, _dx, dy, _adv in self.shape(text):
            pen = BoundsPen(self.gs)
            self.gs[self.order[gid]].draw(pen)
            if pen.bounds is None:
                continue
            _, y0, _, y1 = pen.bounds
            top = max(top, (y1 + dy) * scale)
            bottom = min(bottom, (y0 + dy) * scale)
        return top, -bottom


def width(f: Face, text: str, size: float, tracking: float = 0.0) -> float:
    scale = size / f.upem
    run = f.shape(text)
    return sum(a for _, _, _, a in run) * scale + tracking * max(0, len(run) - 1)


def draw(f: Face, text: str, size: float, x: float, y: float, fill: str,
         tracking: float = 0.0, mark_drop: float = 0.0) -> str:
    """Lay out shaped text with the baseline at y.

    `mark_drop` lowers zero-advance glyphs (the harakat) by that fraction of
    an em. Amiri aligns marks to a common height so they line up across a word
    of running text, which is correct there but leaves a visible void above a
    short letter like ra when the word is set as a large logotype. Tightening
    it is ordinary display-type practice; the value is bounded by measurement
    in main(), not guessed.
    """
    scale = size / f.upem
    drop = mark_drop * f.upem
    parts: list[str] = []
    pen = x
    for gid, dx, dy, adv in f.shape(text):
        d = f.outline(gid)
        if d:
            gx = pen + dx * scale
            # SVG y grows downward; font y grows upward, hence the flip.
            gy = y - (dy - (drop if adv == 0 else 0)) * scale
            parts.append(
                f'  <path d="{d}" fill="{fill}" '
                f'transform="translate({gx:.2f} {gy:.2f}) '
                f'scale({scale:.6f} {-scale:.6f})"/>')
        pen += adv * scale + tracking
    return "\n".join(parts)


def centred(f: Face, text: str, size: float, cx: float, y: float, fill: str,
            tracking: float = 0.0, mark_drop: float = 0.0) -> str:
    w = width(f, text, size, tracking)
    return draw(f, text, size, cx - w / 2, y, fill, tracking=tracking,
                mark_drop=mark_drop)


def safe_mark_drop(f: Face, text: str, clearance: float = 90.0) -> float:
    """Largest drop, in em, that keeps every mark clear of the letters it sits
    over. Measured from real glyph bounds so the wordmark cannot silently
    collide if the text or the font changes."""
    from fontTools.pens.boundsPen import BoundsPen

    items = []
    pen = 0.0
    for gid, dx, dy, adv in f.shape(text):
        bp = BoundsPen(f.gs)
        f.gs[f.order[gid]].draw(bp)
        if bp.bounds:
            x0, y0, x1, y1 = bp.bounds
            items.append((adv == 0, pen + dx + x0, pen + dx + x1,
                          y0 + dy, y1 + dy))
        pen += adv

    bases = [i for i in items if not i[0]]
    room = None
    for is_mark, mx0, mx1, my0, _my1 in items:
        if not is_mark:
            continue
        ceiling = max((b[4] for b in bases
                       if not (b[2] < mx0 - 20 or b[1] > mx1 + 20)), default=0.0)
        r = my0 - ceiling - clearance
        room = r if room is None else min(room, r)
    return max(0.0, (room or 0.0) / f.upem)


def fit(f: Face, text: str, size: float, max_w: float,
        tracking: float = 0.0) -> float:
    """Shrink `size` until the run fits `max_w`. Layout here is manual, so
    nothing else will catch an overflow."""
    while size > 8 and width(f, text, size, tracking) > max_w:
        size -= 1
    return size


# --------------------------------------------------------------- banner
def banner(g: Face, gb: Face) -> str:
    W = 1280
    cx = W / 2
    # وَرَّاق - the shadda on the ra is what makes this warrāq, the copyist,
    # rather than warāq. HarfBuzz places the marks from the font's GPOS table.
    word = "وَرَّاق"
    word_size = 186
    word_base = 210
    # Amiri sets marks on a common height so they align across running text.
    # Over a short letter like ra that leaves a void at logotype size, so the
    # marks are tightened - clamped to what measurement says is collision-free.
    drop = min(0.40, safe_mark_drop(g, word))
    above, below = g.extents(word, word_size)
    above -= drop * word_size          # the marks moved down
    # The qaf descends a long way; keep the rule clear of it.
    rule_y = word_base + below + 26

    # Derive the canvas from measured ink rather than a guessed height, so a
    # tall mark or a deep descender can never be clipped by the frame.
    top_gap = 46
    word_base = max(word_base, top_gap + above)
    rule_y = word_base + below + 26
    H = int(rule_y + 84 + 46)

    frame = []
    for inset, colour, sw, op in ((14, GOLD, 1.5, 0.55), (22, "#2A2521", 1, 1)):
        frame.append(
            f'  <rect x="{inset}" y="{inset}" width="{W - 2 * inset}" '
            f'height="{H - 2 * inset}" rx="3" fill="none" stroke="{colour}" '
            f'stroke-width="{sw}" opacity="{op}"/>')

    rosettes = []
    for rx, ry in ((14, 14), (W - 14, 14), (14, H - 14), (W - 14, H - 14)):
        rosettes.append(
            f'  <circle cx="{rx}" cy="{ry}" r="5" fill="{GOLD}" opacity="0.8"/>\n'
            f'  <circle cx="{rx}" cy="{ry}" r="9" fill="none" stroke="{GOLD}" '
            f'stroke-width="0.8" opacity="0.4"/>')

    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}"
     viewBox="0 0 {W} {H}" role="img"
     aria-label="Warraq - Arabic books, properly typeset for Kindle">
  <defs>
    <linearGradient id="bg" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="#191512"/>
      <stop offset="1" stop-color="{INK}"/>
    </linearGradient>
    <radialGradient id="glow" cx="0.5" cy="0.40" r="0.62">
      <stop offset="0" stop-color="{GOLD}" stop-opacity="0.13"/>
      <stop offset="1" stop-color="{GOLD}" stop-opacity="0"/>
    </radialGradient>
  </defs>
  <rect width="{W}" height="{H}" fill="url(#bg)"/>
  <rect width="{W}" height="{H}" fill="url(#glow)"/>
{chr(10).join(frame)}
{chr(10).join(rosettes)}
{centred(g, word, word_size, cx, word_base, GOLD, mark_drop=drop)}
  <line x1="{cx - 190}" y1="{rule_y}" x2="{cx - 52}" y2="{rule_y}"
        stroke="{GOLD}" stroke-width="1" opacity="0.45"/>
  <line x1="{cx + 52}" y1="{rule_y}" x2="{cx + 190}" y2="{rule_y}"
        stroke="{GOLD}" stroke-width="1" opacity="0.45"/>
  <circle cx="{cx}" cy="{rule_y}" r="2.5" fill="{GOLD}" opacity="0.7"/>
{centred(gb, "W A R R A Q", 31, cx, rule_y + 40, "#8C8168", tracking=2.0)}
{centred(g, "Arabic books, properly typeset for Kindle", 26, cx, rule_y + 84, PARCHMENT_DIM)}
</svg>
"""


# ------------------------------------------------------- shaping compare
def shaping(g: Face, gb: Face) -> str:
    """The one image that explains why this project exists.

    Left: the isolated forms a reader gets when the device cannot shape an
    embedded font. Right: the same word shaped properly, which is what Warraq
    writes into the file.
    """
    W, H = 1280, 440
    word = "المكتبة"                     # "the library"
    # Zero-width space between letters suppresses joining, which is what
    # unshaped output looks like on the device. HarfBuzz still orders the run
    # right-to-left, so the string must NOT be reversed here.
    broken = "\u200b ".join(word)

    pw, ph = 552, 200
    lx, rx = 56, W - 56 - pw
    lcx, rcx = lx + pw / 2, rx + pw / 2
    top = 116
    inner = pw - 48                      # keep clear of the panel border

    broken_size = fit(g, broken, 104, inner)
    fixed_size = fit(g, word, 104, inner)

    panels = []
    for px, colour in ((lx, CLAY), (rx, GREEN)):
        panels.append(
            f'  <rect x="{px}" y="{top}" width="{pw}" height="{ph}" rx="6" '
            f'fill="{PARCHMENT_WARM}" stroke="{colour}" stroke-width="1.5"/>')

    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}"
     viewBox="0 0 {W} {H}" role="img"
     aria-label="The Arabic word al-maktaba shown twice: without shaping the letters stand apart and are unreadable; with shaping they join correctly">
  <rect width="{W}" height="{H}" fill="{PARCHMENT}"/>
{centred(gb, "The same Arabic word, written two ways", 31, W / 2, 62, INK)}
{chr(10).join(panels)}
{centred(gb, "WITHOUT PRE-SHAPING", 23, lcx, top - 20, CLAY, tracking=1.2)}
{centred(gb, "WITH PRE-SHAPING", 23, rcx, top - 20, GREEN, tracking=1.2)}
{centred(g, broken, broken_size, lcx, top + 130, CLAY)}
{centred(g, word, fixed_size, rcx, top + 130, GREEN)}
{centred(g, "letters stand apart - unreadable", 22, lcx, top + ph + 36, INK_SOFT)}
{centred(g, "letters join - correct Arabic", 22, rcx, top + ph + 36, INK_SOFT)}
{centred(g, "Kindle firmware does not shape embedded fonts, so Warraq shapes the text before embedding it", 21, W / 2, H - 26, INK_SOFT)}
</svg>
"""


def main() -> int:
    for f in (AMIRI, AMIRI_BOLD):
        if not f.exists():
            print(f"missing font: {f}", file=sys.stderr)
            return 1

    OUT.mkdir(parents=True, exist_ok=True)
    g, gb = Face(AMIRI), Face(AMIRI_BOLD)

    # Fail loudly rather than emitting artwork with holes in it.
    checks = [
        (g, "وَرَّاق"),
        (g, "المكتبة"),
        (g, "Arabic books, properly typeset for Kindle"),
        (g, "Kindle firmware does not shape embedded fonts, "
            "so Warraq shapes the text before embedding it"),
        (gb, "The same Arabic word, written two ways"),
        (gb, "W A R R A Q"),
    ]
    for face, probe in checks:
        missing = face.notdef(probe)
        if missing:
            print(f"font cannot render {probe!r} (notdef at {missing})",
                  file=sys.stderr)
            return 1

    # The shadda is the whole point of the wordmark; prove it survived shaping.
    marks = [gid for gid, _, _, adv in g.shape("وَرَّاق") if adv == 0]
    if len(marks) < 3:
        print("expected the fatha/shadda marks in وَرَّاق, got "
              f"{len(marks)} zero-advance glyphs", file=sys.stderr)
        return 1

    for name, svg in (("banner.svg", banner(g, gb)),
                      ("shaping.svg", shaping(g, gb))):
        (OUT / name).write_text(svg, encoding="utf-8")
        print(f"wrote {(OUT / name).relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
