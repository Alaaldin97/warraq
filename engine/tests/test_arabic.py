"""Regression tests for Arabic text correctness.

Every test here encodes a bug that actually shipped and was caught on-device or
in QA. They are the guardrails from docs/ARCHITECTURE.md Appendix A.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from kbo import arabic  # noqa: E402

# A real sentence from the test corpus, in correct logical order.
GOOD = "ويغدو الناس كأنهم أخوة في أسرة واحدة أو رفاق في مدرسة داخلية"


class TestPresentationForms:
    def test_normalises_contextual_forms(self):
        # U+FEDF is LAM INITIAL FORM -> must fold to plain LAM U+0644
        assert arabic.normalize_presentation_forms("\uFEDF") == "\u0644"

    def test_expands_lam_alef_ligature(self):
        # U+FEFB LAM WITH ALEF ISOLATED -> two base letters
        assert arabic.normalize_presentation_forms("\uFEFB") == "\u0644\u0627"

    def test_detects_broken_shaping(self):
        assert arabic.has_presentation_forms("\uFEDF") is True

    def test_ornate_quran_parens_are_not_breakage(self):
        """U+FD3E/FD3F are legitimate Quranic quotation marks.

        Flagging them as broken shaping caused a false QA failure on a real
        book that was otherwise perfect.
        """
        assert arabic.has_presentation_forms("\uFD3Eالنص\uFD3F") is False

    def test_ornate_parens_survive_normalisation(self):
        assert arabic.normalize_presentation_forms("\uFD3E") == "\uFD3E"


class TestVisualOrder:
    def test_repairs_reversed_text(self):
        reversed_text = arabic._reverse_preserving_clusters(GOOD)
        fixed, was_reversed = arabic.fix_visual_order(reversed_text)
        assert was_reversed is True
        assert fixed == GOOD

    def test_leaves_correct_text_alone(self):
        fixed, was_reversed = arabic.fix_visual_order(GOOD)
        assert was_reversed is False
        assert fixed == GOOD

    def test_never_reverses_ocr_output(self):
        """OCR engines already emit logical order.

        Re-reversing it corrupted correct Arabic in an early build.
        """
        _, flags = arabic.clean_line(GOOD, allow_reversal=False)
        assert flags["reversed"] is False

    def test_preserves_diacritics_when_reversing(self):
        text = "كُلِّ سُنْبُلَةٍ مِئَةُ حَبَّةٍ"
        before = arabic.diacritic_count(text)
        rev = arabic._reverse_preserving_clusters(text)
        assert arabic.diacritic_count(rev) == before


class TestLanguageClassification:
    def test_pure_arabic(self):
        assert arabic.classify_language(GOOD) == "ar"

    def test_pure_english(self):
        assert arabic.classify_language("The quick brown fox jumps") == "en"

    def test_bilingual(self):
        assert arabic.classify_language(GOOD + " Microsoft Azure") == "bilingual"


class TestShapingValidation:
    def test_clean_logical_text_passes(self):
        assert arabic.validate_shaping(GOOD)["ok"] is True

    def test_broken_shaping_fails(self):
        assert arabic.validate_shaping("\uFEDF\uFEDF\uFEDF")["ok"] is False

    def test_preshaped_mode_accepts_presentation_forms(self):
        """Pre-shaping is intentional for embedded fonts on the Oasis."""
        import arabic_reshaper
        shaped = arabic_reshaper.reshape(GOOD)
        assert arabic.validate_shaping(shaped, preshaped=True)["ok"] is True

    def test_preshaped_mode_rejects_unshaped_text(self):
        r = arabic.validate_shaping(GOOD * 5, preshaped=True)
        assert r["ok"] is False

    def test_tatweel_flagged(self):
        assert "tatweel_present" in arabic.validate_shaping("كــتاب")["issues"]


class TestCleanLine:
    def test_removes_tatweel(self):
        out, flags = arabic.clean_line("كــتاب")
        assert "\u0640" not in out
        assert flags["tatweel_removed"] == 2

    def test_strips_bidi_controls(self):
        out, _ = arabic.clean_line("\u202Bنص\u202C")
        assert "\u202B" not in out and "\u202C" not in out

    def test_empty_is_safe(self):
        assert arabic.clean_line("")[0] == ""


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
