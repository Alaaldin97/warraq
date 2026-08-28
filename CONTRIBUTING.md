# Contributing to Warraq

Thank you for considering a contribution. Warraq exists to make Arabic books
read properly on e-readers, and help with that is genuinely welcome.

## How decisions are made

Warraq is maintained by its author. **Every change is merged only after his
review and approval** — `main` is protected, and pull requests require an
approving review from a code owner. This is not a formality: Arabic typography
has failure modes that are invisible unless you know to look for them, so
changes are read closely.

Please open an issue to discuss anything substantial *before* writing code. A
rejected pull request is a waste of your time, and that is avoidable.

## Ground rules for this codebase

1. **The engine owns all conversion decisions.** The desktop shell renders what
   the engine reports and never re-implements pipeline logic. If the GUI and the
   CLI can disagree about anything, that is a bug.
2. **Never silently degrade output.** If OCR confidence is poor, keep the page
   image rather than emit corrupted Arabic — and say so in the report.
3. **Never mislabel a format.** If genuine KFX cannot be produced, say so.
4. **Arabic correctness is verified, not assumed.** Changes touching shaping,
   right-to-left flow, diacritics or font embedding need evidence: a before and
   after, ideally photographed on a device.
5. **No DRM circumvention**, and no uploading a user's material anywhere without
   explicit consent.

## Before you open a pull request

```bash
# engine
cd engine && python -m pytest -q

# shell
cd shell && npx tsc --noEmit && .\build.ps1 check
```

Both must pass. If you change conversion behaviour, add a regression test —
`engine/tests/` has examples, including RPC-level tests.

Do not commit books. `engine/testdata/*.pdf` is gitignored deliberately:
scanned books are usually still in copyright and are not ours to redistribute.

## README artwork

`docs/assets/*.svg` is generated, not hand-drawn. Do not edit the SVGs
directly — change `scripts/make_artwork.py` and regenerate:

```bash
pip install uharfbuzz
python scripts/make_artwork.py
```

The Arabic in those images is drawn as **outlined paths** taken from the Amiri
font that ships in `engine/assets/`. That is deliberate: GitHub will not load a
webfont for an image, so live `<text>` would fall back to whatever the reader
happens to have installed and the Arabic would render disconnected — the exact
bug this project exists to fix. Outlining keeps the artwork correct everywhere,
including offline and in forks.

Shaping goes through HarfBuzz rather than being done by hand, because Arabic
needs both contextual joining (GSUB) and mark placement (GPOS). The shadda in
وَرَّاق sits at a position only the font knows; guessing it would put the mark in
the wrong place.

The wordmark lowers the harakat slightly, because Amiri aligns marks to a
common height for running text and that leaves a visible void above a short
letter like ra at logotype size. The amount is clamped by `safe_mark_drop()`,
which measures real glyph bounds, so the marks cannot collide with the letters
if the text or the font changes.

Output is deterministic, so regenerating without changing the script produces
byte-identical files and no spurious diff.

## Licensing of contributions

Warraq is licensed under **AGPL-3.0** (see [`LICENSE`](LICENSE)). This is not a
stylistic choice — PyMuPDF, which the engine depends on heavily, is AGPL-3.0
unless a commercial licence is bought, so the combined work must be AGPL-3.0.
See [`THIRD-PARTY-NOTICES.md`](THIRD-PARTY-NOTICES.md).

By submitting a pull request you agree that your contribution is licensed under
AGPL-3.0, and you confirm you have the right to submit it. Sign your commits
off to state this explicitly:

```bash
git commit -s -m "your message"
```

This adds a `Signed-off-by` line, certifying the
[Developer Certificate of Origin](https://developercertificate.org/).

If you contribute on behalf of an employer, please make sure you are permitted
to do so before you submit.

## Reporting bugs

A useful Arabic-typography bug report includes:

- the conversion report from the output folder,
- which font mode and OCR engine were used,
- a photograph of the device screen when the problem is visual,
- the source PDF **only if** you are free to share it.
