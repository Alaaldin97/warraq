"""Arabic text handling: presentation-form normalization, visual->logical order
recovery, RTL block ordering and shaping/diacritic validation.

PDF text layers frequently store Arabic as Unicode *presentation forms*
(U+FB50-FDFF, U+FE70-FEFF) and often in *visual* order, which yields
disconnected, reversed text if used as-is. This module repairs that.
"""
from __future__ import annotations

import re
import unicodedata

ARABIC_BLOCKS = (
    (0x0600, 0x06FF),   # Arabic
    (0x0750, 0x077F),   # Arabic Supplement
    (0x08A0, 0x08FF),   # Arabic Extended-A
    (0xFB50, 0xFDFF),   # Arabic Presentation Forms-A
    (0xFE70, 0xFEFF),   # Arabic Presentation Forms-B
)
PRESENTATION_BLOCKS = ((0xFB50, 0xFDFF), (0xFE70, 0xFEFF))

# Harakat / tashkeel that must be preserved, never stripped.
DIACRITICS = set("\u064B\u064C\u064D\u064E\u064F\u0650\u0651\u0652\u0653\u0654"
                 "\u0655\u0656\u0657\u0658\u0670\u06D6\u06D7\u06D8\u06D9\u06DA"
                 "\u06DB\u06DC\u06DF\u06E0\u06E1\u06E2\u06E3\u06E4\u06E7\u06E8"
                 "\u06EA\u06EB\u06EC\u06ED")

TATWEEL = "\u0640"
BIDI_CONTROLS = "\u202A\u202B\u202C\u202D\u202E\u2066\u2067\u2068\u2069\u200E\u200F\u061C"

# High-frequency Arabic function words used to score reading direction.
COMMON_WORDS = [
    "\u0641\u064a", "\u0645\u0646", "\u0639\u0644\u0649", "\u0625\u0644\u0649",
    "\u0627\u0644\u0630\u064a", "\u0627\u0644\u062a\u064a", "\u0639\u0646",
    "\u0623\u0646", "\u0625\u0646", "\u0644\u0627", "\u0645\u0627", "\u0642\u062f",
    "\u0643\u0627\u0646", "\u0647\u0630\u0627", "\u0630\u0644\u0643",
    "\u0627\u0644\u0644\u0647", "\u0628\u0639\u062f", "\u0642\u0628\u0644",
    "\u0628\u064a\u0646", "\u0627\u0644\u0639\u0631\u0628\u064a\u0629",
]
DEF_ARTICLE = "\u0627\u0644"   # ال
TA_MARBUTA = "\u0629"
ALEF_MAQSURA = "\u0649"


def _in(ch: str, blocks) -> bool:
    o = ord(ch)
    return any(a <= o <= b for a, b in blocks)


def is_arabic_char(ch: str) -> bool:
    return _in(ch, ARABIC_BLOCKS)


def arabic_ratio(text: str) -> float:
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return 0.0
    return sum(1 for c in letters if is_arabic_char(c)) / len(letters)


def latin_ratio(text: str) -> float:
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return 0.0
    return sum(1 for c in letters if "a" <= c.lower() <= "z") / len(letters)


# Characters in the presentation-forms blocks that are NOT broken shaping:
# U+FD3E/U+FD3F are the ornate parentheses used to quote Qur'anic text, and
# U+FDFD/U+FDF2 etc. are standalone religious ligatures. They are correct
# typography and must be preserved.
LEGIT_PRESENTATION = {
    "\uFD3E", "\uFD3F",              # ornate left/right parenthesis
    "\uFDFD",                        # BASMALA
    "\uFDF2",                        # ALLAH
    "\uFDFA", "\uFDFB",              # SALLALLAHOU ALAYHE WASALLAM / JALLAJALALOUHOU
    "\uFDFC",                        # RIAL SIGN
}


def has_presentation_forms(text: str) -> bool:
    """True only for presentation forms that indicate broken shaping."""
    return any(_in(c, PRESENTATION_BLOCKS) and c not in LEGIT_PRESENTATION
               for c in text)


def diacritic_count(text: str) -> int:
    return sum(1 for c in text if c in DIACRITICS)


def normalize_presentation_forms(text: str) -> str:
    """Map Arabic presentation forms back to base letters, preserving diacritics.

    NFKC decomposes contextual forms (U+FEDF LAM INITIAL FORM -> U+0644) and
    lam-alef ligatures (U+FEFB -> U+0644 U+0627). Applied per character so
    surrounding non-Arabic text is untouched. Legitimate ornate/religious
    ligatures are left alone.
    """
    return "".join(
        unicodedata.normalize("NFKC", ch)
        if _in(ch, PRESENTATION_BLOCKS) and ch not in LEGIT_PRESENTATION else ch
        for ch in text
    )


def strip_bidi_controls(text: str) -> str:
    return "".join(c for c in text if c not in BIDI_CONTROLS)


def _score_logical(text: str) -> float:
    """Higher score => text reads as correctly ordered logical Arabic."""
    if not text.strip():
        return 0.0
    score = 0.0
    for w in COMMON_WORDS:
        score += text.count(w) * 3.0
    words = re.findall(r"[\u0600-\u06FF]+", text)
    if words:
        # 'ال' is a prefix: it starts words, it does not end them.
        score += sum(1 for w in words if w.startswith(DEF_ARTICLE)) * 1.5
        score -= sum(1 for w in words if w.endswith(DEF_ARTICLE[::-1])) * 1.5
        # ta marbuta / alef maqsura are word-final letters.
        score += sum(1 for w in words if w.endswith(TA_MARBUTA)) * 1.0
        score += sum(1 for w in words if w.endswith(ALEF_MAQSURA)) * 0.7
        score -= sum(1 for w in words if w.startswith(TA_MARBUTA)) * 1.0
    return score


def _reverse_preserving_clusters(text: str) -> str:
    """Reverse by grapheme cluster so combining marks stay attached to letters."""
    clusters, cur = [], ""
    for ch in text:
        if (unicodedata.combining(ch) or ch in DIACRITICS) and cur:
            cur += ch
        else:
            if cur:
                clusters.append(cur)
            cur = ch
    if cur:
        clusters.append(cur)
    return "".join(reversed(clusters))


def fix_visual_order(text: str) -> tuple[str, bool]:
    """Detect visual-order Arabic and restore logical order.

    Returns (text, was_reversed). Latin/number runs are re-reversed so mixed
    Arabic-English passages remain readable.
    """
    if arabic_ratio(text) < 0.2:
        return text, False
    forward = _score_logical(text)
    reversed_text = _reverse_preserving_clusters(text)
    backward = _score_logical(reversed_text)
    if backward > max(forward * 1.4, forward + 6.0):
        fixed = re.sub(r"[A-Za-z0-9][A-Za-z0-9 .,:/@\-_]*[A-Za-z0-9]",
                       lambda m: m.group(0)[::-1], reversed_text)
        return fixed, True
    return text, False


def clean_line(text: str, allow_reversal: bool = True) -> tuple[str, dict]:
    """Full per-line repair. Returns (clean_text, flags).

    `allow_reversal` must be False for OCR-derived text: Tesseract already
    emits logical order, so re-ordering it would corrupt correct text.
    """
    flags = {"presentation_forms": False, "reversed": False,
             "diacritics": 0, "tatweel_removed": 0}
    if not text:
        return text, flags
    t = strip_bidi_controls(text)
    if has_presentation_forms(t):
        flags["presentation_forms"] = True
        t = normalize_presentation_forms(t)
    if allow_reversal:
        t, rev = fix_visual_order(t)
        flags["reversed"] = rev
    n_tat = t.count(TATWEEL)          # justification filler, carries no meaning
    if n_tat:
        t = t.replace(TATWEEL, "")
        flags["tatweel_removed"] = n_tat
    flags["diacritics"] = diacritic_count(t)
    t = re.sub(r"[ \t]{2,}", " ", t).strip()
    return t, flags


def is_rtl_text(text: str) -> bool:
    return arabic_ratio(text) >= 0.4


def classify_language(text: str) -> str:
    ar, la = arabic_ratio(text), latin_ratio(text)
    if ar >= 0.85:
        return "ar"
    if la >= 0.85:
        return "en"
    if ar >= 0.15 and la >= 0.15:
        return "bilingual"
    return "ar" if ar > la else "en"


def validate_shaping(text: str, preshaped: bool = False) -> dict:
    """Post-conversion sanity check for Arabic text quality.

    `preshaped` means the text was intentionally written as presentation forms
    so a custom embedded font renders correctly on the Kindle Oasis. In that
    mode presentation forms are expected, and it is *unshaped* base letters
    that would indicate a problem.
    """
    words = re.findall(r"[\u0600-\u06FF\uFB50-\uFDFF\uFE70-\uFEFF]+", text)
    issues = []
    if preshaped:
        pres = sum(1 for c in text
                   if _in(c, PRESENTATION_BLOCKS) and c not in LEGIT_PRESENTATION)
        base = sum(1 for c in text if 0x0621 <= ord(c) <= 0x064A)
        if pres == 0 and base > 50:
            issues.append("expected_preshaped_text_but_found_none")
        elif pres > 0 and base > pres * 0.6:
            issues.append("many_unshaped_letters")
    else:
        if has_presentation_forms(text):
            issues.append("residual_presentation_forms")
        if words:
            singles = sum(1 for w in words if len(w) == 1)
            if singles / len(words) > 0.45:
                issues.append("disconnected_letters")
    if TATWEEL in text:
        issues.append("tatweel_present")
    if any(c in text for c in BIDI_CONTROLS):
        issues.append("bidi_control_chars")
    return {"words": len(words), "logical_score": round(_score_logical(text), 1),
            "preshaped": preshaped, "issues": issues, "ok": not issues}
