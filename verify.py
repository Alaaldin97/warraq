"""End-to-end verification of everything Warraq can currently do.

Runs the engine through its real interfaces and prints a pass/fail summary, so
the state of the project can be confirmed in one command.
"""
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ENGINE = ROOT / "engine"

results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  — {detail}" if detail else ""),
          flush=True)


def run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", **kw)


print("\n=== 1. Regression suite ===")
r = run([sys.executable, "-m", "pytest", "tests", "-q"], cwd=ENGINE)
passed = "passed" in (r.stdout or "")
n = ""
for line in (r.stdout or "").splitlines():
    if "passed" in line:
        n = line.strip()
check("engine regression tests", passed and r.returncode == 0, n)

print("\n=== 2. JSON-RPC contract ===")
p = subprocess.Popen([sys.executable, "-u", "-m", "kbo.cli", "--rpc"],
                     cwd=ENGINE, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                     stderr=subprocess.DEVNULL, text=True, encoding="utf-8",
                     bufsize=1)


def call(obj):
    p.stdin.write(json.dumps(obj) + "\n")
    p.stdin.flush()
    return json.loads(p.stdout.readline())


t0 = time.time()
ready = json.loads(p.stdout.readline())
check("engine ready banner", ready.get("event") == "ready",
      f"{time.time()-t0:.2f}s, schema v{ready.get('schemaVersion')}")

pong = call({"id": "1", "method": "ping"})
check("ping", pong.get("result", {}).get("pong") is True)

caps = call({"id": "2", "method": "capabilities"}).get("result", {})
check("capabilities", bool(caps), f"engine {caps.get('engineVersion')}")
check("Amiri is the default font",
      caps.get("defaults", {}).get("arabicFont") == "amiri")
check("font profiles available",
      len(caps.get("arabicFonts", [])) >= 5,
      ", ".join(f["id"] for f in caps.get("arabicFonts", [])))
tools = caps.get("tools", {})
check("Calibre detected", tools.get("calibre", {}).get("available", False))
check("Tesseract + Arabic",
      "ara" in tools.get("tesseract", {}).get("languages", []))
check("Azure Document Intelligence",
      tools.get("azure", {}).get("available", False),
      tools.get("azure", {}).get("auth", ""))

bad = call({"id": "3", "method": "analyze", "params": {"path": "nope.pdf"}})
check("clean error on missing file",
      bad.get("error", {}).get("code") == "FILE_NOT_FOUND")

p.stdin.write('{"id":"z","method":"shutdown"}\n')
p.stdin.flush()
p.wait(timeout=15)

print("\n=== 3. Frozen sidecar ===")
exe = ENGINE / "dist" / "warraq-engine" / "warraq-engine.exe"
if exe.exists():
    size = sum(f.stat().st_size for f in exe.parent.rglob("*") if f.is_file())
    t0 = time.time()
    sp = subprocess.Popen([str(exe), "--rpc"], cwd=ENGINE,
                          stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                          stderr=subprocess.DEVNULL, text=True,
                          encoding="utf-8", bufsize=1)
    banner = json.loads(sp.stdout.readline())
    cold = time.time() - t0
    check("frozen sidecar starts", banner.get("event") == "ready",
          f"{cold:.2f}s cold start, {size/1024/1024:.0f} MB bundle")
    sp.stdin.write('{"id":"z","method":"shutdown"}\n')
    sp.stdin.flush()
    sp.wait(timeout=15)
else:
    check("frozen sidecar built", False, "not built (run PyInstaller)")

print("\n=== 4. Shell ===")
shell = ROOT / "shell"
check("Tauri scaffold", (shell / "src-tauri" / "Cargo.toml").exists())
check("Rust engine bridge", (shell / "src-tauri" / "src" / "engine.rs").exists())
check("React app", (shell / "src" / "App.tsx").exists())
check("typed engine client", (shell / "src" / "engine.ts").exists())
r = run(["cmd", "/c", "npx", "tsc", "--noEmit"], cwd=shell)
check("TypeScript compiles", r.returncode == 0,
      (r.stdout or r.stderr or "").strip()[:80])

msvc = list(Path("C:/Program Files (x86)/Microsoft Visual Studio").rglob("link.exe")) \
    if Path("C:/Program Files (x86)/Microsoft Visual Studio").exists() else []
check("MSVC linker (needed for cargo build)", bool(msvc),
      "" if msvc else "BLOCKED — needs an admin UAC prompt")

print("\n" + "=" * 62)
ok = sum(1 for _, o, _ in results if o)
print(f"{ok}/{len(results)} checks passed")
failed = [n for n, o, _ in results if not o]
if failed:
    print("outstanding: " + ", ".join(failed))
print("=" * 62)
