"""Azure AI Document Intelligence OCR engine.

Best-in-class Arabic OCR. Uses the prebuilt-read model, authenticated with the
signed-in user's Entra ID token (works when the resource has local auth /
API keys disabled, which is the secure default in Microsoft tenants).

Privacy: page images are uploaded to the configured Azure resource. The caller
must obtain explicit user consent before enabling this engine.
"""
from __future__ import annotations

import base64
import json
import os
from . import proc
import time
import urllib.error
import urllib.request

API_VERSION = "2024-11-30"
MODEL = "prebuilt-read"

ENDPOINT_ENV = "KBO_AZURE_DI_ENDPOINT"
KEY_ENV = "KBO_AZURE_DI_KEY"


# ------------------------------------------------------------------ auth
def _az(args: list[str]) -> str:
    r = proc.run(["az"] + args, capture_output=True, text=True,
                       shell=True, timeout=120)
    return (r.stdout or "").strip()


def get_token() -> str | None:
    t = _az(["account", "get-access-token", "--resource",
             "https://cognitiveservices.azure.com", "--query", "accessToken",
             "-o", "tsv"])
    return t if t and len(t) > 100 else None


def endpoint() -> str | None:
    return os.environ.get(ENDPOINT_ENV)


def available() -> bool:
    return bool(endpoint()) and (bool(os.environ.get(KEY_ENV)) or bool(get_token()))


def _headers() -> dict:
    h = {"Content-Type": "application/octet-stream"}
    key = os.environ.get(KEY_ENV)
    if key:
        h["Ocp-Apim-Subscription-Key"] = key
    else:
        h["Authorization"] = "Bearer " + (get_token() or "")
    return h


# ------------------------------------------------------------------ core
def analyze_pdf(pdf_bytes: bytes, timeout: int = 600, retries: int = 4) -> dict:
    """Run prebuilt-read over a PDF (or image) and return the raw result.

    Retries on transient network/throttling errors - large uploads over a VPN
    occasionally get their connection reset.
    """
    ep = endpoint()
    if not ep:
        return {"ok": False, "reason": f"{ENDPOINT_ENV} not set"}
    url = (f"{ep.rstrip('/')}/documentintelligence/documentModels/{MODEL}:analyze"
           f"?api-version={API_VERSION}")

    last = ""
    for attempt in range(retries):
        if attempt:
            time.sleep(min(4 * 2 ** attempt, 45))
        try:
            req = urllib.request.Request(url, data=pdf_bytes, headers=_headers(),
                                         method="POST")
            with urllib.request.urlopen(req, timeout=300) as resp:
                op = resp.headers.get("Operation-Location")
        except urllib.error.HTTPError as e:
            body = e.read()[:300]
            last = f"HTTP {e.code}: {body!r}"
            if e.code in (408, 429, 500, 502, 503, 504):
                continue
            return {"ok": False, "reason": last}
        except (urllib.error.URLError, ConnectionError, TimeoutError, OSError) as e:
            last = f"network: {e}"
            continue
        if not op:
            last = "no Operation-Location returned"
            continue

        deadline = time.time() + timeout
        while time.time() < deadline:
            time.sleep(2)
            try:
                g = urllib.request.Request(op, headers={
                    k: v for k, v in _headers().items() if k != "Content-Type"})
                with urllib.request.urlopen(g, timeout=90) as resp:
                    data = json.loads(resp.read())
            except (urllib.error.URLError, ConnectionError, TimeoutError,
                    OSError) as e:
                last = f"poll network: {e}"
                continue
            st = data.get("status")
            if st == "succeeded":
                return {"ok": True, "result": data.get("analyzeResult", {})}
            if st == "failed":
                return {"ok": False, "reason": json.dumps(data)[:500]}
        last = f"timed out after {timeout}s"
    return {"ok": False, "reason": last}


def pages_from_result(res: dict) -> list[dict]:
    """Convert Document Intelligence output into our line format."""
    from . import arabic
    out = []
    for p in res.get("pages", []):
        unit_scale = 72.0 if p.get("unit") == "inch" else 1.0
        lines = []
        for ln in p.get("lines", []):
            txt = ln.get("content", "")
            if not txt.strip():
                continue
            poly = ln.get("polygon") or []
            xs = poly[0::2] or [0]
            ys = poly[1::2] or [0]
            bbox = [min(xs) * unit_scale, min(ys) * unit_scale,
                    max(xs) * unit_scale, max(ys) * unit_scale]
            clean, _ = arabic.clean_line(txt, allow_reversal=False)
            lines.append({"text": clean, "bbox": bbox,
                          "size": bbox[3] - bbox[1],
                          "conf": 100.0, "bold": False, "fonts": []})
        out.append({"page": p.get("pageNumber", len(out) + 1), "lines": lines,
                    "angle": p.get("angle", 0),
                    "width": p.get("width"), "height": p.get("height")})
    return out


def mean_confidence(res: dict) -> float:
    """Average word confidence reported by the service."""
    confs = [w.get("confidence", 0) for p in res.get("pages", [])
             for w in p.get("words", [])]
    return round(sum(confs) / len(confs) * 100, 1) if confs else 0.0


def ocr_pdf_file(path: str, timeout: int = 900) -> dict:
    with open(path, "rb") as f:
        data = f.read()
    r = analyze_pdf(data, timeout)
    if not r["ok"]:
        return r
    res = r["result"]
    return {"ok": True, "pages": pages_from_result(res),
            "mean_conf": mean_confidence(res),
            "content": res.get("content", ""),
            "languages": [l.get("locale") for l in res.get("languages", [])][:5]}


def status() -> dict:
    ep = endpoint()
    return {"endpoint": ep,
            "auth": ("key" if os.environ.get(KEY_ENV)
                     else ("entra-token" if get_token() else "none")),
            "ready": available(),
            "model": MODEL, "api_version": API_VERSION,
            "privacy": ("Page images are sent to this Azure resource for "
                        "recognition. Requires explicit user consent.")}
