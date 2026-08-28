"""Pin the current handling of Arabic vowel marks.

This is a characterisation test, not an endorsement. `_preshape` uses
arabic_reshaper's defaults, and that library deletes harakat unless told
otherwise, so a vocalised source text loses its marks in the pre-shaped
edition while the searchable companion keeps them.

That behaviour is documented in the README as a known limitation. The test
exists so the day someone changes it, it is a deliberate decision with a
device test behind it rather than a silent side effect.
"""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from kbo import build  # noqa: E402

HARAKAT = range(0x064B, 0x0653)
VOCALISED = "وَرَّاق الكِتَابِ"


def _marks(text: str) -> int:
    return sum(1 for c in text if ord(c) in HARAKAT)


def test_source_sample_really_is_vocalised():
    """Guard the fixture itself, so the tests below cannot pass vacuously."""
    assert _marks(VOCALISED) == 6


def test_preshaping_currently_drops_harakat():
    """Known limitation. If this fails, the behaviour changed - update the
    README claim and verify mark placement on a real Kindle before shipping."""
    assert _marks(build._preshape(VOCALISED)) == 0


def test_preshaping_still_produces_presentation_forms():
    """Losing the marks must not mean losing the joining."""
    out = build._preshape(VOCALISED)
    assert any(0xFE70 <= ord(c) <= 0xFEFF for c in out), out


def test_untouched_text_keeps_its_harakat():
    """The searchable companion does not pre-shape, so it keeps the marks.
    This is what makes the limitation survivable."""
    assert _marks(VOCALISED) == 6


def test_latin_text_is_left_alone():
    s = "Chapter One"
    assert build._preshape(s) == s
