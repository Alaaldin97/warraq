# Warraq engine

Converts PDF books into clean, readable files optimised for the
**Kindle Oasis 9th Generation (2017), 7", 300 ppi, 1264x1680 px**.

This is the engine only. For installation, the offline-vs-Azure choice, and
troubleshooting, see the [main README](../README.md).

> Status: v0.9. Validated on real hardware; interfaces may still change.

## Install / requirements

| Component | Purpose | Install |
|---|---|---|
| Calibre 9.x | AZW3 generation, metadata, QA round-trip | `winget install calibre.calibre` |
| Tesseract 5.x | Offline OCR (English + Arabic) | `winget install UB-Mannheim.TesseractOCR` |
| `tools/tessdata` | Bundled `ara` + `eng` from `tessdata_best`, **plus the `configs/` folder** (without it, TSV output silently degrades to plain text and confidence reads 0) | ships with the repo |
| Python 3.12+ | pipeline | `pip install -r requirements.txt` |
| `assets/Amiri-*.ttf` | embedded Arabic font (SIL OFL, embeddable) | ships with the repo |
| Azure Document Intelligence *(optional)* | better Arabic OCR on scans; bring your own resource | see [main README](../README.md#setting-up--connecting-your-own-azure) |
| k2pdfopt *(optional)* | alternative crop/reflow engine | manual download from willus.com (captcha-gated; drop `k2pdfopt.exe` into `tools\`) |
| Kindle Previewer 3 + calibre "KFX Output" plugin *(optional)* | genuine KFX output | user must install and accept Amazon's licence |

Tesseract is located at `C:\Program Files\Tesseract-OCR\tesseract.exe`; `PATH` is
not searched. Override with `KBO_TESSERACT`.

## Usage

```powershell
$env:PYTHONIOENCODING='utf-8'
python -m kbo.cli <input.pdf> --out <output-dir> [--workers 6]
                  [--max-pages N] [--force-route auto|reflow|ocr|fixed]
                  [--aggressive-clean] [--make-pdf auto|always|never]
                  [--ocr-engine auto|azure|tesseract]
                  [--font-mode auto|embed|native|preshape]
                  [--arabic-font amiri|lateef|markazi|notonaskh|scheherazade]

# batch a folder
python -m kbo.batch --workers 6

# JSON-RPC over stdio (what the desktop shell speaks)
python -m kbo.cli --rpc
```

`--rpc` is handled before argument parsing, so it does not appear in `--help`.

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
| `azure_ocr.py` | Optional Azure Document Intelligence client: config/env resolution, Entra token or API key auth, chunked upload, connection test. Falls back to Tesseract on any failure |
| `rpc.py` | Newline-delimited JSON-RPC server: job queue, stage/progress events, settings |
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
