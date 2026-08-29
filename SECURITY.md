# Security policy

## Reporting a vulnerability

Please report privately, not in a public issue:
**[open a security advisory](https://github.com/Alaaldin97/warraq/security/advisories/new)**.

This is a pre-alpha side project, not a funded product. There is no bounty and
no guaranteed response time — but reports are read, and credit is given in the
fix unless you would rather stay anonymous.

## What is worth reporting

Warraq handles untrusted input and, optionally, a cloud credential. The things
that matter most:

- **Credential handling.** Anything that could send an Azure key or token
  somewhere other than the endpoint the user configured, or expose it in logs,
  RPC events, error messages, or the UI.
- **Malicious PDFs.** A crafted book that achieves code execution, escapes the
  output directory, or reads files it should not — the PDF is parsed by
  PyMuPDF/OpenCV and its metadata is embedded into generated HTML/OPF.
- **The engine bridge.** The desktop shell talks to the Python engine over
  newline-delimited JSON on stdio. Anything that lets untrusted content control
  that channel is interesting.
- **Command injection** through filenames, output paths, or CLI flags reaching
  Tesseract, Calibre, or another child process.

## Known and already documented

These are stated in the README and `docs/ARCHITECTURE.md`; no need to report
them, though a better fix is welcome:

- An Azure API key you save is written in **plaintext** to
  `%APPDATA%\Warraq\config.json`. DPAPI or Credential Manager storage is not
  implemented yet. Signing in with `az login` avoids storing a key at all.
- The desktop shell's `opener` capability is scoped to `**`. Converted books
  land beside a user-chosen PDF anywhere on disk, so a narrower scope would
  break "open output folder". Accepted deliberately.
- The engine binary is not code-signed, and there is no auto-update mechanism.

## Scope

In scope: the engine (`engine/`), the desktop shell (`shell/`), and the CI
workflow.

Out of scope: vulnerabilities in Calibre, Tesseract, Azure Document
Intelligence, or other external programs Warraq invokes — please report those
upstream. Also out of scope: the absence of a feature that was never claimed.

## Supported versions

Pre-alpha. Only the current `master` is supported; there are no maintained
release branches yet.
