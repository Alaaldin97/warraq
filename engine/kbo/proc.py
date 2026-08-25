"""Safe subprocess invocation for the engine.

Every external tool (Calibre, Tesseract, az CLI) must be launched with stdin
detached. When the engine runs as an RPC sidecar its own stdin is the JSON
command pipe; a child that inherits it and reads from it blocks forever, and
the whole conversion deadlocks with the child at 0% CPU.

This bit us with `ebook-convert` during RPC bring-up. Route every child process
through `run()` so it cannot happen again.
"""
from __future__ import annotations

import subprocess

# Do not pop a console window for child processes on Windows.
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def run(cmd, **kw):
    """subprocess.run with stdin detached and no console flash."""
    kw.setdefault("stdin", subprocess.DEVNULL)
    if _NO_WINDOW and "creationflags" not in kw:
        kw["creationflags"] = _NO_WINDOW
    return subprocess.run(cmd, **kw)


def popen(cmd, **kw):
    """subprocess.Popen with the same protections."""
    kw.setdefault("stdin", subprocess.DEVNULL)
    if _NO_WINDOW and "creationflags" not in kw:
        kw["creationflags"] = _NO_WINDOW
    return subprocess.Popen(cmd, **kw)
