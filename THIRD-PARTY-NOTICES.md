# Third-party notices

Warraq bundles and depends on the components below. Copyright statements were
read from the shipped files and package metadata rather than copied from
memory.

---

## Fonts — SIL Open Font License 1.1

Full licence text: [`licenses/OFL-1.1.txt`](licenses/OFL-1.1.txt).
The OFL permits embedding these fonts in generated ebooks.

| Family | Copyright | Upstream |
|---|---|---|
| Amiri | Copyright 2010–2022 The Amiri Project Authors | https://github.com/aliftype/amiri |
| Lateef | Copyright (c) 1994–2025 SIL Global | https://software.sil.org/lateef/ |
| Markazi Text | Copyright 2017 The Markazi Text Project Authors | https://github.com/BornaIz/markazitext |
| Noto Naskh Arabic | Copyright 2022 The Noto Project Authors | https://github.com/notofonts/arabic |
| Reem Kufi | Copyright 2015–2022 The Reem Kufi Project Authors | https://github.com/aliftype/reem-kufi |
| Scheherazade New | Copyright (c) 1994–2024 SIL Global, with Reserved Font Names "Scheherazade" and "SIL" | https://software.sil.org/scheherazade/ |

"Scheherazade" and "SIL" are Reserved Font Names under the OFL. A modified
version of that font may not be distributed under those names.

---

## OCR data — Tesseract traineddata

`engine/tools/tessdata/*.traineddata` are trained models from the Tesseract OCR
project, licensed under the Apache License 2.0.

- Upstream: https://github.com/tesseract-ocr/tessdata_best
- Licence: https://www.apache.org/licenses/LICENSE-2.0

---

## Python dependencies

| Package | Licence | Note |
|---|---|---|
| PyMuPDF (fitz) | **AGPL-3.0 or Artifex commercial** | See "Copyleft obligations" below |
| OpenCV (`opencv-python-headless`) | Apache-2.0 | |
| NumPy | BSD-3-Clause | |
| fontTools | MIT | |
| arabic-reshaper | MIT | |
| Pillow | MIT-CMU | |
| PyInstaller | GPL-2.0-or-later **with bundling exception** | The exception permits distributing bundled applications under the licence of your choice |

## Shell dependencies

Tauri, React, Fluent UI and the Rust crates in `shell/src-tauri` are MIT or
MIT/Apache-2.0 dual-licensed. Run `npx license-checker` or `cargo license` for the
full resolved list.

---

## External programs — invoked, not bundled

| Program | Licence | How it is used |
|---|---|---|
| Calibre (`ebook-convert`) | GPL-3.0 | Executed as a separate process. Warraq does not link against it or redistribute it. |
| Tesseract | Apache-2.0 | Executed as a separate process. |

Keeping these as separate executables is deliberate: it avoids creating a
combined work with GPL-3.0 software.

---

## Copyleft obligations

**PyMuPDF is the binding constraint.** It is dual-licensed AGPL-3.0 or
commercial, it is imported across seven engine modules, and it is bundled into
the distributed sidecar. Distributing Warraq therefore requires either:

1. releasing Warraq under **AGPL-3.0**, which is what this project does; or
2. purchasing an **Artifex commercial licence** for PyMuPDF.

There is no third option that involves shipping PyMuPDF under a permissive
licence such as MIT or Apache-2.0.

Anyone forking Warraq, and anyone offering it as a network service, inherits
the same obligation.
