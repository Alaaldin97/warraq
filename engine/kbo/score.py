"""Score OCR text quality without a ground truth.

Used to decide, per book, whether an existing PDF text layer (e.g. produced by
Adobe Acrobat) is better than re-running Tesseract - and to pick a winner
between two candidate texts.

The score blends signals that correlate with real recognition quality:
  * dictionary-free plausibility: share of tokens that recur in the document
  * function-word coverage: real prose uses common words heavily
  * junk rate: isolated letters, stray symbols, impossible characters
  * word-length sanity
"""
from __future__ import annotations

import collections
import re

from . import arabic

AR_WORD = re.compile(r"[\u0621-\u064A]{2,}")
EN_WORD = re.compile(r"[A-Za-z]{2,}")
JUNK = re.compile(r"[^\w\s\u0600-\u06FF.,;:!?'\"()\[\]\-\u060C\u061B\u061F\u066A-\u066D\u2018\u2019\u201C\u201D\u2013\u2014/%&@#]")

AR_FUNCTION = set(arabic.COMMON_WORDS) | {
    "\u0627\u0644\u0649", "\u0647\u0630\u0647", "\u0647\u0648", "\u0647\u064a",
    "\u0643\u0644", "\u0628\u0639\u0636", "\u063a\u064a\u0631", "\u0644\u0645",
    "\u0644\u0646", "\u0642\u0627\u0644", "\u0639\u0646\u062f", "\u0644\u0648",
    "\u062b\u0645", "\u0625\u0630\u0627", "\u062d\u062a\u0649", "\u0628\u0644",
}
EN_FUNCTION = {"the", "and", "of", "to", "in", "that", "he", "was", "it", "for",
               "with", "as", "his", "on", "be", "at", "by", "had", "not", "but"}


def score_text(text: str, lang: str = "ar") -> dict:
    """Return a quality score in 0..100 plus the component signals."""
    if not text or not text.strip():
        return {"score": 0.0, "tokens": 0, "note": "empty"}

    words = AR_WORD.findall(text) if lang.startswith("ar") else EN_WORD.findall(text)
    if not words:
        return {"score": 0.0, "tokens": 0, "note": "no words"}

    freq = collections.Counter(words)
    n = len(words)

    # 1. Recurrence: OCR garbage is unique; real vocabulary repeats.
    recurring = sum(c for w, c in freq.items() if c >= 3) / n

    # 2. Function words: prose is dense with them; garbage is not.
    fn = AR_FUNCTION if lang.startswith("ar") else EN_FUNCTION
    fn_rate = sum(freq[w] for w in fn if w in freq) / n

    # 3. Junk characters per 1000 chars.
    junk_rate = len(JUNK.findall(text)) / max(len(text), 1)

    # 4. Isolated single letters (classic broken recognition).
    singles = len(re.findall(r"(?<!\S)[\u0621-\u064AA-Za-z](?!\S)", text)) / n

    # 5. Mean word length sanity (Arabic ~4-6, English ~4-5).
    avg_len = sum(len(w) for w in words) / n
    len_ok = 1.0 if 3.0 <= avg_len <= 7.5 else max(0.0, 1 - abs(avg_len - 5) / 5)

    score = (
        recurring * 34 +
        min(fn_rate / 0.16, 1.0) * 34 +
        max(0.0, 1 - junk_rate * 60) * 14 +
        max(0.0, 1 - singles * 14) * 10 +
        len_ok * 8
    )
    return {
        "score": round(min(100.0, max(0.0, score)), 1),
        "tokens": n,
        "distinct": len(freq),
        "recurring_rate": round(recurring, 3),
        "function_word_rate": round(fn_rate, 3),
        "junk_rate": round(junk_rate, 4),
        "isolated_letter_rate": round(singles, 3),
        "avg_word_len": round(avg_len, 2),
    }


def pick_best(candidates: dict[str, str], lang: str = "ar") -> tuple[str, dict]:
    """candidates: {name: text}. Returns (winner_name, {name: score_dict})."""
    scores = {k: score_text(v, lang) for k, v in candidates.items()}
    winner = max(scores, key=lambda k: scores[k]["score"])
    return winner, scores
