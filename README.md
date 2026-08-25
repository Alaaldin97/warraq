# Warraq

**Arabic PDF → Kindle-ready ebooks, for Windows.**

Turns scanned Arabic books into properly typeset Kindle editions: correct
right-to-left flow, correct letter joining, preserved diacritics, and
professional Amiri typography — with an automated quality report that proves it.

> Status: **pre-alpha**. The conversion engine is validated and production-grade;
> the desktop shell is under construction.
> See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for the full specification.

---

## Repository layout

```
warraq/
├── engine/            Python conversion engine (the product's core asset)
│   ├── kbo/           19 modules — analysis, cleanup, OCR, typography, build, QA
│   ├── assets/        Amiri and the other Arabic font profiles (SIL OFL)
│   ├── tools/         Tesseract language data
│   ├── tests/         55 regression tests
│   ├── engine.spec    PyInstaller bundle definition
│   └── engine_main.py Frozen entry point
├── shell/             Tauri 2 desktop app
│   ├── src-tauri/     Rust: engine sidecar bridge, credentials, updater
│   └── src/           React 18 + Fluent UI v9
├── docs/              Architecture and decision records
└── installer/         WiX packaging (Phase 5)
```

**Design rule:** the shell contains *zero* conversion logic. Everything that
affects output quality lives in the engine, so the GUI and the CLI can never
diverge.

---

## Prerequisites

| Requirement | Why | Install |
|---|---|---|
| Python 3.12+ | Engine runtime | python.org |
| Node 20+ | Shell build | nodejs.org |
| Rust (stable) | Tauri shell | `winget install Rustlang.Rustup` |
| MSVC Build Tools | Rust linker on Windows | `winget install Microsoft.VisualStudio.2022.BuildTools` with the VCTools workload |
| Calibre 9 | AZW3 generation | `winget install calibre.calibre` |
| Tesseract 5 | Offline OCR | `winget install UB-Mannheim.TesseractOCR` |
| Azure AI (optional) | Best-in-class Arabic OCR | any AIServices or Document Intelligence resource |

```powershell
pip install pymupdf opencv-python-headless numpy fonttools arabic-reshaper pyinstaller pytest
```

---

## Running the engine

```powershell
cd engine
$env:KBO_AZURE_DI_ENDPOINT = "https://<your-resource>.cognitiveservices.azure.com/"

# one book
python -m kbo.cli book.pdf --out out\book --workers 6

# a folder of books
python -m kbo.batch --workers 6

# JSON-RPC mode (what the desktop shell speaks)
python -m kbo.cli --rpc
```

Arabic books automatically get Amiri, pre-shaped, plus a searchable companion.
No flags required.

### Useful options

| Flag | Default | Purpose |
|---|---|---|
| `--arabic-font` | `amiri` | Typeface profile |
| `--font-mode` | `auto` | `auto` pre-shapes Arabic, leaves English native |
| `--ocr-engine` | `auto` | `azure`, `tesseract`, or auto-select |
| `--make-pdf` | `auto` | Also emit a Kindle-sized PDF |
| `--force-route` | `auto` | `reflow`, `ocr`, `fixed` |
| `--max-pages` | all | Page-limited trial run |

---

## Running the shell

```powershell
cd shell
npm install
npm run tauri dev
```

The shell locates the engine in this order: bundled next to the app → the
PyInstaller build in `engine/dist/` → the Python source in `engine/`.

---

## Building for distribution

```powershell
cd engine
python -m PyInstaller engine.spec --noconfirm   # -> engine/dist/warraq-engine/

cd ..\shell
npm run tauri build
```

**The engine must be built as `--onedir`, never `--onefile`.** A onefile bundle
extracts `python3xx.dll` to `%TEMP%` at launch, which Windows Application
Control blocks on managed corporate devices.

---

## Tests

```powershell
cd engine
python -m pytest tests -q          # 55 tests
```

The suite encodes every bug that has ever shipped, most notably:

- Arabic presentation-form handling and visual→logical order recovery
- Ornate Quranic parentheses `﴿ ﴾` are legitimate typography, not breakage
- Amiri must never be subsetted (a subsetted face breaks letter joining)
- Font flattening makes any Arabic font renderable with pre-shaped text
- OCR output must never be re-sorted by Y coordinate (it destroys column order)
- Progress reporting must be monotonic

There is also an **on-device Kindle regression suite** that cannot be automated.
Three separate bugs were only discoverable on real hardware. Run it before every
release.

---

## Quality gate

Every conversion is verified before delivery:

| Check | Failure action |
|---|---|
| Format magic bytes | Fail |
| Metadata read-back | Warn |
| Content coverage vs source | Fail below 70% vocabulary recall |
| Arabic shaping validation | Fail |
| Embedded font not subsetted | Fail |
| RTL direction | Fail |

A failed gate does not mean a failed conversion — the engine automatically
builds the page-exact edition instead and recommends it.

---

## Licensing notes

- Arabic fonts (Amiri, Noto Naskh, Scheherazade, Lateef, Markazi) are SIL OFL;
  embedding is permitted. Licence texts ship in `engine/assets/`.
- **Calibre is GPL v3.** It is invoked as a separate executable and must never
  be linked in-process. See risk R4 in the architecture document.
- No DRM circumvention exists anywhere in this codebase, by design.
