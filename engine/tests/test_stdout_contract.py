"""The RPC contract: stdout carries newline-delimited JSON and nothing else.

This is the one invariant the desktop shell depends on to talk to the engine.
It is also easy to break from a distance: any imported library that prints a
banner or a deprecation notice at import time writes to the same file
descriptor and corrupts the stream before the engine has run a line of its
own code.

That is not hypothetical. PyMuPDF 1.28 prints

    warning: The `fitz` API is deprecated ... Use `import pymupdf` instead.

to stdout when imported under the old name, which is why the engine imports
`pymupdf as fitz` rather than `fitz`. On a developer machine pinned to an
older PyMuPDF the problem is invisible, so this test spawns a real process
and inspects the raw first bytes instead of trusting the local environment.
"""
from __future__ import annotations

import json
import pathlib
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]


def _spawn():
    return subprocess.Popen(
        [sys.executable, "-u", "-m", "kbo.cli", "--rpc"],
        cwd=str(ROOT),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        bufsize=1,
    )


def _shutdown(p):
    try:
        p.stdin.write('{"id":"z","method":"shutdown"}\n')
        p.stdin.flush()
        p.wait(timeout=10)
    except Exception:
        p.kill()


def test_first_stdout_line_is_the_json_ready_banner():
    """Nothing may reach stdout before the protocol's own first message."""
    p = _spawn()
    try:
        first = p.stdout.readline()
        try:
            msg = json.loads(first)
        except json.JSONDecodeError:
            pytest.fail(
                "stdout was polluted before the ready banner. The first line "
                f"was not JSON: {first!r}. Something imported by the engine "
                "printed to stdout - check for a library banner or a "
                "deprecation warning at import time."
            )
        assert msg.get("event") == "ready", msg
        assert "schemaVersion" in msg, msg
    finally:
        _shutdown(p)


def test_every_stdout_line_of_a_session_parses_as_json():
    """Exercise a few methods and require the whole stream to stay clean."""
    p = _spawn()
    lines: list[str] = []
    try:
        lines.append(p.stdout.readline())
        for i, method in enumerate(("ping", "capabilities", "getSettings")):
            p.stdin.write(json.dumps({"id": str(i), "method": method}) + "\n")
            p.stdin.flush()
            lines.append(p.stdout.readline())
    finally:
        _shutdown(p)

    for line in lines:
        assert line.strip(), "engine closed stdout early"
        try:
            json.loads(line)
        except json.JSONDecodeError:
            pytest.fail(f"non-JSON line on stdout: {line!r}")


def test_engine_does_not_import_pymupdf_under_the_deprecated_name():
    """Guard the root cause directly, so a future edit cannot reintroduce it.

    Importing `fitz` is what triggers PyMuPDF's stdout warning; importing
    `pymupdf` does not.
    """
    offenders = []
    for path in sorted((ROOT / "kbo").glob("*.py")):
        for n, line in enumerate(
                path.read_text(encoding="utf-8").splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("import fitz") or \
                    stripped.startswith("from fitz "):
                offenders.append(f"{path.name}:{n}: {stripped}")
    assert not offenders, (
        "import PyMuPDF as `import pymupdf as fitz`; the bare `fitz` name is "
        "deprecated and prints a warning to stdout, which corrupts the RPC "
        "stream:\n  " + "\n  ".join(offenders)
    )
