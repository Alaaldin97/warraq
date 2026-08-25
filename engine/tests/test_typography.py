"""Regression tests for the Arabic typography subsystem.

Amiri correctness is a product requirement, not a preference. These tests
enforce it mechanically.
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from kbo import build, fontfix  # noqa: E402

ASSETS = ROOT / "assets"


class TestFontRegistry:
    def test_amiri_is_the_default(self):
        assert "amiri" in build.ARABIC_FONTS
        # every profile carries (regular, bold, description, line-height)
        for name, spec in build.ARABIC_FONTS.items():
            assert len(spec) == 4, name
            assert isinstance(spec[3], float), name

    def test_amiri_has_bold(self):
        assert build.ARABIC_FONTS["amiri"][1] is not None

    def test_amiri_line_height_tuned(self):
        # Amiri needs more leading than Noto; a shared value would look wrong
        assert build.ARABIC_FONTS["amiri"][3] > build.ARABIC_FONTS["notonaskh"][3]


class TestAmiriIntegrity:
    def test_amiri_files_present(self):
        for f in ("Amiri-Regular.ttf", "Amiri-Bold.ttf"):
            assert (ASSETS / f).exists(), f

    def test_amiri_is_not_subsetted(self):
        """A subsetted Arabic font renders as disconnected letters.

        Full Amiri is ~420 KB; Calibre's subsetter produced ~90 KB and broke
        letter joining on the device.
        """
        for f in ("Amiri-Regular.ttf", "Amiri-Bold.ttf"):
            assert (ASSETS / f).stat().st_size > 200_000, f

    def test_amiri_covers_presentation_forms(self):
        """Pre-shaped text needs presentation forms in the cmap."""
        cps = fontfix.font_codepoints(str(ASSETS / "Amiri-Regular.ttf"))
        present = sum(1 for c in range(0xFE70, 0xFF00) if c in cps)
        assert present >= 120, f"only {present}/144 presentation forms"

    def test_amiri_has_lam_alef_ligatures(self):
        cps = fontfix.font_codepoints(str(ASSETS / "Amiri-Regular.ttf"))
        for cp in (0xFEFB, 0xFEF7, 0xFEF9, 0xFEF5):
            assert cp in cps, f"missing U+{cp:04X}"


class TestFontFlattening:
    @pytest.mark.parametrize("font", [
        "ScheherazadeNew-Regular.ttf", "Lateef-Regular.ttf",
    ])
    def test_flattening_makes_font_usable(self, font, tmp_path):
        """Most Arabic fonts map only base letters and rely on GSUB.

        Flattening injects direct cmap entries so pre-shaped text renders.
        """
        src = ASSETS / "fonts" / font
        if not src.exists():
            pytest.skip(f"{font} not installed")
        before = fontfix.font_codepoints(str(src))
        n_before = sum(1 for c in range(0xFE70, 0xFF00) if c in before)

        flat, info = fontfix.ensure_preshape_font(str(src), str(tmp_path))
        after = fontfix.font_codepoints(flat)
        n_after = sum(1 for c in range(0xFE70, 0xFF00) if c in after)

        assert n_after > n_before + 100, f"{font}: {n_before} -> {n_after}"

    def test_ligature_fallback_covers_the_gap(self, tmp_path):
        """Fonts lacking lam-alef glyphs get a decomposition fallback."""
        src = ASSETS / "fonts" / "ScheherazadeNew-Regular.ttf"
        if not src.exists():
            pytest.skip("Scheherazade not installed")
        import arabic_reshaper
        flat, _ = fontfix.ensure_preshape_font(str(src), str(tmp_path))
        shaped = arabic_reshaper.reshape("لا إله إلا الله ولا حول ولا قوة")
        adapted, info = fontfix.adapt_text_to_font(shaped, flat)
        assert info["unrenderable"] == 0, info["missing_sample"]

    def test_amiri_needs_no_adaptation(self, tmp_path):
        import arabic_reshaper
        shaped = arabic_reshaper.reshape("لا إله إلا الله")
        _, info = fontfix.adapt_text_to_font(
            shaped, str(ASSETS / "Amiri-Regular.ttf"))
        assert info["unrenderable"] == 0
        assert info["ligatures_decomposed"] == 0


class TestCss:
    def test_rtl_css_sets_direction(self):
        css = build._css(rtl=True, embed_font=True)
        assert "direction: rtl" in css

    def test_embeds_named_font(self):
        css = build._css(rtl=True, embed_font=True, family="KBOArabic",
                         files=("Amiri-Regular.ttf", "Amiri-Bold.ttf"))
        assert "Amiri-Regular.ttf" in css and "@font-face" in css

    def test_native_mode_embeds_nothing(self):
        css = build._css(rtl=True, embed_font=False)
        assert "@font-face" not in css

    def test_english_is_ltr(self):
        assert "direction: ltr" in build._css(rtl=False)


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
