"""KFX support detection.

Genuine KFX requires Amazon's Kindle Previewer 3 (free, proprietary) driving the
calibre 'KFX Output' plugin. We never rename another format as .kfx.
"""
from __future__ import annotations

import glob
import os
from . import proc

from .build import EBOOK_CONVERT

PREVIEWER_PATHS = [
    os.path.expandvars(r"%LOCALAPPDATA%\Amazon\Kindle Previewer 3\Kindle Previewer 3.exe"),
    os.path.expandvars(r"%ProgramFiles%\Amazon\Kindle Previewer 3\Kindle Previewer 3.exe"),
    os.path.expandvars(r"%ProgramFiles(x86)%\Amazon\Kindle Previewer 3\Kindle Previewer 3.exe"),
]


def previewer_path() -> str | None:
    for p in PREVIEWER_PATHS:
        if os.path.exists(p):
            return p
    hits = glob.glob(os.path.expandvars(
        r"%LOCALAPPDATA%\Amazon\Kindle Previewer*\**\Kindle Previewer*.exe"),
        recursive=True)
    return hits[0] if hits else None


def kfx_plugin_installed() -> bool:
    calibre_debug = os.path.join(os.path.dirname(EBOOK_CONVERT), "calibre-debug.exe")
    if not os.path.exists(calibre_debug):
        return False
    r = proc.run([calibre_debug, "-c",
                        "from calibre.customize.ui import all_output_format_plugins;"
                        "print([p.name for p in all_output_format_plugins()])"],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace")
    return "KFX Output" in (r.stdout or "")


def status() -> dict:
    prev = previewer_path()
    plugin = kfx_plugin_installed()
    can = bool(prev) and plugin
    if can:
        reason = "Kindle Previewer 3 and the calibre KFX Output plugin are both present."
    elif prev and not plugin:
        reason = ("Kindle Previewer 3 found, but the calibre 'KFX Output' plugin is "
                  "not installed.")
    elif plugin and not prev:
        reason = ("calibre 'KFX Output' plugin found, but Amazon Kindle Previewer 3 "
                  "is not installed - the plugin cannot produce KFX without it.")
    else:
        reason = ("KFX generation needs Amazon Kindle Previewer 3 (free, proprietary, "
                  "must be installed and its licence accepted by you) plus the calibre "
                  "'KFX Output' plugin. Neither is installed.")
    return {"can_generate_kfx": can, "previewer": prev, "plugin": plugin,
            "reason": reason,
            "note": ("AZW3 is fully supported by the Kindle Oasis 9th gen; KFX mainly "
                     "adds Bookerly/Enhanced Typesetting. No file is ever renamed to "
                     ".kfx as a substitute.")}


def convert(src: str, out_kfx: str) -> dict:
    st = status()
    if not st["can_generate_kfx"]:
        return {"ok": False, "skipped": True, **st}
    r = proc.run([EBOOK_CONVERT, src, out_kfx], capture_output=True,
                       text=True, encoding="utf-8", errors="replace")
    ok = r.returncode == 0 and os.path.exists(out_kfx)
    return {"ok": ok, "skipped": False, "stdout": r.stdout[-1500:],
            "stderr": r.stderr[-1500:], **st}
