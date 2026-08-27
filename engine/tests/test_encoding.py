"""Regression: Arabic filenames must survive the RPC channel intact.

A file named in Arabic arrived at the engine as mojibake because Python
defaulted stdin to the Windows ANSI codepage, and the conversion failed with
FILE_NOT_FOUND. This test spawns the engine the way the desktop shell does -
with no encoding environment set - and proves the path round-trips.
"""
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

ARABIC_NAME = "مع الناس_Foulabook.com_.pdf"
MIXED_NAME = "كتاب Test 2026 مع الناس.pdf"


def _sample_pdf() -> Path | None:
    for cand in (ROOT / "testdata" / "en_holmes.pdf",
                 ROOT.parent.parent / "KindleOptimizer" / "input" / "en_holmes.pdf"):
        if cand.exists():
            return cand
    return None


@pytest.fixture(scope="module")
def engine():
    # Deliberately strip the encoding hints so we reproduce how the Rust shell
    # used to launch the engine.
    env = {k: v for k, v in __import__("os").environ.items()
           if k not in ("PYTHONIOENCODING", "PYTHONUTF8")}
    p = subprocess.Popen(
        [sys.executable, "-u", "-m", "kbo.cli", "--rpc"],
        cwd=str(ROOT), env=env,
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        text=True, encoding="utf-8", bufsize=1)
    json.loads(p.stdout.readline())          # ready banner
    yield p
    try:
        p.stdin.write('{"id":"z","method":"shutdown"}\n')
        p.stdin.flush()
        p.wait(timeout=10)
    except Exception:
        p.kill()


def _call(p, obj):
    p.stdin.write(json.dumps(obj, ensure_ascii=False) + "\n")
    p.stdin.flush()
    return json.loads(p.stdout.readline())


@pytest.mark.parametrize("name", [ARABIC_NAME, MIXED_NAME])
def test_arabic_filename_round_trips(engine, tmp_path, name):
    src = _sample_pdf()
    if src is None:
        pytest.skip("no sample PDF available")
    target = tmp_path / name
    shutil.copy2(src, target)

    r = _call(engine, {"id": "ar", "method": "analyze",
                       "params": {"path": str(target), "samplePages": 4}})

    assert "error" not in r, r.get("error")
    # The engine must echo the path back byte-identical, not mojibake.
    assert r["result"]["path"] == str(target)
    assert r["result"]["pageCount"] > 0


def test_missing_file_still_reports_cleanly(engine):
    r = _call(engine, {"id": "x", "method": "analyze",
                       "params": {"path": "لا يوجد.pdf"}})
    assert r["error"]["code"] == "FILE_NOT_FOUND"
    # the message should contain the real Arabic name, not mojibake
    assert "لا يوجد" in r["error"]["message"]


def test_cli_logs_arabic_on_a_legacy_codepage(tmp_path):
    """The CLI must not crash when stdout cannot represent Arabic.

    Running `python -m kbo.cli` on a book with an Arabic filename raised
    UnicodeEncodeError from the very first progress line, because the Windows
    console defaults to a legacy code page. The RPC path had been fixed; the
    CLI had not, so command-line users hit a crash on ordinary Arabic titles.
    """
    script = (
        "import sys, io\n"
        # Force the failure mode: a stream that cannot encode Arabic and,
        # like a real console handle, cannot be reconfigured to UTF-8.
        "class Legacy(io.TextIOBase):\n"
        "    encoding = 'cp1252'\n"
        "    def write(self, s):\n"
        "        s.encode('cp1252')\n"
        "        return len(s)\n"
        "    def reconfigure(self, **kw):\n"
        "        raise ValueError('cannot reconfigure')\n"
        "sys.stdout = Legacy()\n"
        "from kbo.cli import log\n"
        "log('Analyzing مع الناس.pdf ...')\n"
        "sys.stderr.write('SURVIVED\\n')\n"
    )
    r = subprocess.run([sys.executable, "-c", script], cwd=ROOT,
                       capture_output=True, text=True, timeout=120)
    assert "UnicodeEncodeError" not in r.stderr, r.stderr
    assert r.returncode == 0, r.stderr
    assert "SURVIVED" in r.stderr


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
