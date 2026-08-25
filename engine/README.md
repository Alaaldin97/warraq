# Kindle Oasis Book Optimizer — prototype (v0.9, pending approval)

Converts PDF books into clean, readable files optimised for the
**Kindle Oasis 9th Generation (2017), 7", 300 ppi, 1264x1680 px**.

> Status: prototype under test.
> Outputs verified on device.

## Install / requirements

| Component | Purpose | Install |
|---|---|---|
| Calibre 9.x | AZW3 generation, metadata, QA round-trip | `winget install calibre.calibre` |
| Tesseract 5.x | OCR (English + Arabic) | `winget install UB-Mannheim.TesseractOCR` |
| `tools/tessdata` | `ara` + `eng` from `tessdata_best`, **plus the `configs/` folder copied from the Tesseract install** (without it, TSV output silently degrades to plain text) | see `setup.ps1` |
| Python 3.11+ | pipeline | `pip install pymupdf opencv-python-headless pillow numpy` |
| `assets/Amiri-*.ttf` | embedded Arabic font (SIL OFL, embeddable) | Google Fonts |
| k2pdfopt *(optional)* | alternative crop/reflow engine | manual download from willus.com (captcha-gated; drop `k2pdfopt.exe` into `tools\`) |
| Kindle Previewer 3 + calibre "KFX Output" plugin *(optional)* | genuine KFX output | user must install and accept Amazon's licence |

## Usage

```powershell
$env:PYTHONIOENCODING='utf-8'
python -m kbo.cli <input.pdf> --out <output-dir> [--workers 6]
                  [--max-pages N] [--force-route auto|reflow|ocr|fixed]
                  [--aggressive-clean] [--make-pdf auto|always|never]
```

## Pipeline

```
analyze -> route -> (clean -> OCR) -> extract/repair -> build -> QA -> report
```

| Module | Responsibility |
|---|---|
| `device.py` | Oasis 9 screen geometry, greyscale depth, calibre profile |
| `analyze.py` | Per page: text layer, language, columns, skew, margins, noise, blank/duplicate detection, rotation, image DPI, repeated headers/footers |
| `clean.py` | Scan-border removal, deskew, speckle denoise, shading flattening + CLAHE, content crop, fit-to-screen, 16-level greyscale |
| `ocr.py` | Tesseract wrapper: per-word confidence, searchable-PDF layer, OSD orientation, language-aware confidence thresholds (Arabic 78, Latin 70) |
| `arabic.py` | Presentation-form normalisation, visual→logical order recovery, cluster-safe reversal, tatweel removal, diacritic preservation, shaping validation |
| `extract.py` | Column reconstruction, reading order, running-head/page-number removal, heading + footnote classification, paragraph reassembly, hyphen repair, bookmark-driven TOC |
| `build.py` | Reflowable HTML→AZW3, pre-paginated fixed-layout EPUB→AZW3 (CBZ fallback), device-sized PDF |
| `kfx.py` | Honest KFX capability detection — never renames another format `.kfx` |
| `qa.py` | Magic-byte format check, metadata read-back, round-trip text extraction, content-coverage vs source, Arabic shaping validation |
| `report.py` | The conversion report |
| `cli.py` | Routing and orchestration |

## Routing rules

| Source | Route | Output |
|---|---|---|
| Native text, 1–2 columns | reflow | reflowable AZW3 |
| Native text, 3+ columns | fixed | page-exact AZW3 + PDF |
| Scanned, Latin, OCR ≥ 70 | ocr-reflow | reflowable AZW3 (+PDF) |
| Scanned, Arabic, OCR ≥ 78 | ocr-reflow **and** fixed | both AZW3 variants + PDF |
| OCR poor, or Arabic marginal | fixed | page images preserved, never corrupted text |

## Known traps (learned during build)

1. A custom `TESSDATA_PREFIX` directory must contain `configs/` — otherwise
   `tsv` output mode silently returns plain text and confidence is reported as 0.
2. Never detect language from page 1 — covers mislead. Sample ~5 pages spread
   through the book.
3. `--no-default-epub-cover` is not a valid AZW3 output option in calibre 9.
4. argparse: a positional named `pdf` and an option `--pdf` collide on the same
   dest and silently break the flag. Option is `--make-pdf`.
5. Paragraph splitting must be vetoed when the previous line has no terminal
   punctuation and the next starts lowercase, or sentences get cut in half.
6. Arabic PDFs often store presentation forms in visual order; normalise with
   per-character NFKC and score-based reversal detection before anything else.

## Legal / safety

- No DRM circumvention, ever. DRM-protected files are rejected.
- Fonts are SIL OFL (embeddable). k2pdfopt and Kindle Previewer are downloaded
  by the user, not scripted around their captcha/licence gates.
- KFX is only produced when a genuine Kindle Previewer + plugin toolchain exists.
