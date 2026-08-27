"""Frozen entry point for the Warraq conversion engine.

The desktop shell spawns this executable with `--rpc` and drives it over
JSON-RPC on stdio. All other CLI arguments are handled by kbo.cli, so the
frozen binary and the source CLI behave identically.
"""
import multiprocessing
import os
import sys


def _bundle_dir() -> str:
    """Directory holding bundled assets, frozen or not."""
    return getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))


def _configure_bundled_paths() -> None:
    """Point the engine at bundled resources unless the user overrode them."""
    root = _bundle_dir()
    tessdata = os.path.join(root, "tools", "tessdata")
    if os.path.isdir(tessdata) and not os.environ.get("KBO_TESSDATA"):
        os.environ["KBO_TESSDATA"] = tessdata
    os.environ.setdefault("KBO_ASSETS", os.path.join(root, "assets"))
    # The RPC channel carries Arabic filenames and text. Windows defaults the
    # standard streams to the ANSI codepage, which mangles them, so force UTF-8
    # on all three regardless of how the process was launched.
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def main() -> int:
    multiprocessing.freeze_support()
    _configure_bundled_paths()
    # RPC mode imports only the transport, so the shell sees "ready" in well
    # under a second. The heavy imaging stack is warmed in the background.
    if len(sys.argv) > 1 and sys.argv[1] == "--rpc":
        from kbo.rpc import serve
        return serve()
    from kbo.cli import main as cli_main
    return cli_main()


if __name__ == "__main__":
    sys.exit(main())
