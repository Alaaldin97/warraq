"""Social/teaser card for the announcement post.

Same pipeline as the README artwork - Amiri outlines, HarfBuzz shaping - but
sized for LinkedIn's 1200x627 link preview, which is also a comfortable ratio
for an in-feed image.

    python scripts/make_social.py

Writes docs/assets/social-card.svg and .png (PNG because LinkedIn will not
render an SVG upload).
"""
from __future__ import annotations

import pathlib
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from make_artwork import (  # noqa: E402
    AMIRI, AMIRI_BOLD, GOLD, INK, PARCHMENT_DIM, Face, centred, draw,
    safe_mark_drop, width,
)

ROOT = pathlib.Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "assets"


def card(g: Face, gb: Face) -> str:
    W, H = 1200, 627
    cx = W / 2

    word = "وَرَّاق"
    size = 190
    drop = min(0.40, safe_mark_drop(g, word))
    above, below = g.extents(word, size)
    above -= drop * size

    base = 300
    rule_y = base + below + 30

    frame = []
    for inset, colour, sw, op in ((18, GOLD, 1.5, 0.5), (27, "#2A2521", 1, 1)):
        frame.append(
            f'  <rect x="{inset}" y="{inset}" width="{W - 2 * inset}" '
            f'height="{H - 2 * inset}" rx="3" fill="none" stroke="{colour}" '
            f'stroke-width="{sw}" opacity="{op}"/>')

    rosettes = []
    for rx, ry in ((18, 18), (W - 18, 18), (18, H - 18), (W - 18, H - 18)):
        rosettes.append(
            f'  <circle cx="{rx}" cy="{ry}" r="5" fill="{GOLD}" opacity="0.75"/>\n'
            f'  <circle cx="{rx}" cy="{ry}" r="9.5" fill="none" stroke="{GOLD}" '
            f'stroke-width="0.8" opacity="0.35"/>')

    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}"
     viewBox="0 0 {W} {H}" role="img"
     aria-label="Warraq - Arabic books, properly typeset for Kindle">
  <defs>
    <linearGradient id="bg" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="#1B1713"/>
      <stop offset="1" stop-color="{INK}"/>
    </linearGradient>
    <radialGradient id="glow" cx="0.5" cy="0.44" r="0.66">
      <stop offset="0" stop-color="{GOLD}" stop-opacity="0.15"/>
      <stop offset="1" stop-color="{GOLD}" stop-opacity="0"/>
    </radialGradient>
  </defs>
  <rect width="{W}" height="{H}" fill="url(#bg)"/>
  <rect width="{W}" height="{H}" fill="url(#glow)"/>
{chr(10).join(frame)}
{chr(10).join(rosettes)}
{centred(g, word, size, cx, base, GOLD, mark_drop=drop)}
  <line x1="{cx - 200}" y1="{rule_y}" x2="{cx - 54}" y2="{rule_y}"
        stroke="{GOLD}" stroke-width="1" opacity="0.4"/>
  <line x1="{cx + 54}" y1="{rule_y}" x2="{cx + 200}" y2="{rule_y}"
        stroke="{GOLD}" stroke-width="1" opacity="0.4"/>
  <circle cx="{cx}" cy="{rule_y}" r="2.5" fill="{GOLD}" opacity="0.65"/>
{centred(gb, "W A R R A Q", 30, cx, rule_y + 44, "#8C8168", tracking=2.2)}
{centred(g, "Arabic books, properly typeset for Kindle", 30, cx, rule_y + 96, PARCHMENT_DIM)}
{centred(g, "free · offline · open source", 23, cx, rule_y + 138, "#6E6552")}
</svg>
"""


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    g, gb = Face(AMIRI), Face(AMIRI_BOLD)
    svg = card(g, gb)
    svg_path = OUT / "social-card.svg"
    svg_path.write_text(svg, encoding="utf-8")
    print(f"wrote {svg_path.relative_to(ROOT)}")

    # LinkedIn will not take an SVG, so rasterise. Chrome is already a
    # dependency of the Tauri build, so use it rather than adding cairo.
    png = OUT / "social-card.png"
    for exe in (r"C:\Program Files\Google\Chrome\Application\chrome.exe",
                r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"):
        if pathlib.Path(exe).exists():
            subprocess.run([exe, "--headless", "--disable-gpu",
                            "--screenshot=" + str(png), "--window-size=1200,627",
                            "--default-background-color=00000000",
                            svg_path.as_uri()], check=False,
                           capture_output=True, timeout=120)
            if png.exists():
                print(f"wrote {png.relative_to(ROOT)}")
                return 0
    print("no Chrome/Edge found - SVG written, rasterise it manually",
          file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
