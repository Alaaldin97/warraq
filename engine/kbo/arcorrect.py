"""Arabic OCR error correction.

Scanned Arabic OCR makes systematic, repeatable mistakes: dots dropped or added
(ج/ح/خ, ب/ت/ث, د/ذ, ر/ز, س/ش, ص/ض, ط/ظ, ع/غ, ف/ق), hamza forms confused, and
letters occasionally lost inside long words.

Rather than guessing, we correct only where the evidence is strong:

1. Build a corroboration lexicon from the whole book. A form the engine produced
   many times across many pages is almost certainly right; a form seen once is
   suspect.
2. For each rare form, look for a frequent form that is one "confusable" edit
   away. If exactly one such candidate dominates, adopt it.
3. Never touch a word that is already frequent, and never change word length
   beyond a single-character edit. Meaning is preserved, not rewritten.
"""
from __future__ import annotations

import collections
import re

ARABIC_WORD = re.compile(r"[\u0621-\u064A\u0640]+")

# Letters that differ only by dots/hamza - the dominant OCR failure mode.
CONFUSION_GROUPS = [
    "\u0628\u062A\u062B\u0646\u064A",          # ب ت ث ن ي
    "\u062C\u062D\u062E",                      # ج ح خ
    "\u062F\u0630",                            # د ذ
    "\u0631\u0632",                            # ر ز
    "\u0633\u0634",                            # س ش
    "\u0635\u0636",                            # ص ض
    "\u0637\u0638",                            # ط ظ
    "\u0639\u063A",                            # ع غ
    "\u0641\u0642",                            # ف ق
    "\u0647\u0629",                            # ه ة
    "\u0627\u0623\u0625\u0622\u0621",          # ا أ إ آ ء
    "\u0648\u0624",                            # و ؤ
    "\u064A\u0649\u0626",                      # ي ى ئ
    "\u0643\u0644",                            # ك ل (shape confusion)
]
CONFUSABLE: dict[str, set[str]] = {}
for grp in CONFUSION_GROUPS:
    for ch in grp:
        CONFUSABLE.setdefault(ch, set()).update(set(grp) - {ch})


def _one_edit_variants(word: str):
    """Words one confusable-substitution away."""
    for i, ch in enumerate(word):
        for alt in CONFUSABLE.get(ch, ()):
            yield word[:i] + alt + word[i + 1:]


def _one_insert_variants(word: str, alphabet: str):
    """OCR sometimes drops a letter; try re-inserting one."""
    for i in range(len(word) + 1):
        for ch in alphabet:
            yield word[:i] + ch + word[i:]


def build_lexicon(texts) -> collections.Counter:
    lex = collections.Counter()
    for t in texts:
        lex.update(ARABIC_WORD.findall(t))
    return lex


class ArabicCorrector:
    def __init__(self, lexicon: collections.Counter, *,
                 trusted_min: int = 4, suspect_max: int = 2,
                 dominance: int = 6, allow_insertion: bool = True,
                 protect_known: bool = True):
        self.lex = lexicon
        self.trusted_min = trusted_min
        self.suspect_max = suspect_max
        self.dominance = dominance
        self.allow_insertion = allow_insertion
        # Words that are common enough elsewhere in the book are real words:
        # never "correct" them, or valid text gets corrupted
        # (e.g. شبع -> سبع, فرش -> فرس).
        self.protect_known = protect_known
        self.alphabet = "".join(sorted({c for w in lexicon for c in w}))
        self._cache: dict[str, str] = {}
        self.stats = collections.Counter()

    def _best(self, word: str) -> str | None:
        count = self.lex.get(word, 0)
        cands = {}
        for v in _one_edit_variants(word):
            c = self.lex.get(v, 0)
            if c >= self.trusted_min:
                cands[v] = c
        if not cands and self.allow_insertion and len(word) >= 3:
            for v in _one_insert_variants(word, self.alphabet):
                c = self.lex.get(v, 0)
                if c >= self.trusted_min * 3:
                    cands[v] = c
        if not cands:
            return None
        best, bc = max(cands.items(), key=lambda kv: kv[1])
        # require the replacement to dominate both the original and any rival
        rivals = sorted(cands.values(), reverse=True)
        if bc < max(count * self.dominance, self.trusted_min):
            return None
        if len(rivals) > 1 and rivals[1] * 2 > bc:
            return None          # ambiguous - leave the text alone
        return best

    def correct_word(self, word: str) -> str:
        if word in self._cache:
            return self._cache[word]
        out = word
        count = self.lex.get(word, 0)
        # Only touch words that are BOTH rare and short-ish. A rare long word is
        # usually a real uncommon word, not an OCR slip.
        if 3 <= len(word) <= 8 and count <= self.suspect_max:
            cand = self._best(word)
            if cand:
                out = cand
                self.stats[(word, cand)] += 1
        self._cache[word] = out
        return out

    def correct_text(self, text: str) -> str:
        return ARABIC_WORD.sub(lambda m: self.correct_word(m.group(0)), text)

    def summary(self, top: int = 25):
        total = sum(self.stats.values())
        return {"corrections": total,
                "distinct": len(self.stats),
                "examples": [(a, b, n) for (a, b), n in self.stats.most_common(top)]}
