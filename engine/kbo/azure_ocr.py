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
import pathlib
import shutil
from . import proc
import time
import urllib.error
import urllib.parse
import urllib.request

API_VERSION = "2024-11-30"
MODEL = "prebuilt-read"

ENDPOINT_ENV = "KBO_AZURE_DI_ENDPOINT"
KEY_ENV = "KBO_AZURE_DI_KEY"


# ---------------------------------------------------------------- config
# The engine is spawned by the desktop shell, which inherits whatever
# environment the shell itself was launched from. That makes ambient
# environment variables an unreliable place to keep the Azure endpoint: a
# shell started before the variable was defined silently falls back to
# offline OCR. The config file below is the durable source of truth, with
# environment variables kept as an override for CLI use and CI.
def config_path() -> pathlib.Path:
    override = os.environ.get("KBO_CONFIG")
    if override:
        return pathlib.Path(override)
    appdata = os.environ.get("APPDATA")
    if appdata:
        return pathlib.Path(appdata) / "Warraq" / "config.json"
    return pathlib.Path.home() / ".config" / "warraq" / "config.json"


def read_config() -> dict:
    try:
        with open(config_path(), "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def write_config(values: dict) -> pathlib.Path:
    """Persist settings. A key whose value is None is left alone; a key whose
    value is the empty string is removed, which is how the UI clears a field."""
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    merged = read_config()
    for k, v in values.items():
        if v is None:
            continue
        if isinstance(v, str) and not v.strip():
            merged.pop(k, None)
        else:
            merged[k] = v.strip() if isinstance(v, str) else v
    with open(path, "w", encoding="utf-8") as f:
        json.dump(merged, f, ensure_ascii=False, indent=2)
    return path


def _setting(env_name: str, config_key: str) -> str | None:
    value = os.environ.get(env_name) or read_config().get(config_key)
    value = (value or "").strip()
    return value or None


# ------------------------------------------------------------------ auth
def _az(args: list[str]) -> str:
    """Invoke the Azure CLI.

    Resolved through shutil.which rather than shell=True. On Windows `az` is a
    .cmd, which CreateProcess cannot launch directly, and the obvious fix --
    shell=True -- routes through cmd.exe, which resolves an unqualified name
    from the current working directory before PATH. That turns a planted
    az.bat in the CWD into code execution. which() applies PATHEXT and a safe
    search order, so the shell is not needed at all.
    """
    exe = shutil.which("az")
    if not exe:
        return ""
    r = proc.run([exe] + args, capture_output=True, text=True,
                 shell=False, timeout=120)
    return (r.stdout or "").strip()


def get_token() -> str | None:
    t = _az(["account", "get-access-token", "--resource",
             "https://cognitiveservices.azure.com", "--query", "accessToken",
             "-o", "tsv"])
    return t if t and len(t) > 100 else None


def endpoint() -> str | None:
    return _setting(ENDPOINT_ENV, "azureEndpoint")


def api_key() -> str | None:
    return _setting(KEY_ENV, "azureKey")


def available() -> bool:
    return bool(endpoint()) and (bool(api_key()) or bool(get_token()))


def _headers() -> dict:
    h = {"Content-Type": "application/octet-stream"}
    key = api_key()
    if key:
        h["Ocp-Apim-Subscription-Key"] = key
    else:
        h["Authorization"] = "Bearer " + (get_token() or "")
    return h


class _NoCrossHostRedirect(urllib.request.HTTPRedirectHandler):
    """Refuse redirects that would carry credentials to another host.

    urllib replays the original request headers across redirects, including
    Ocp-Apim-Subscription-Key and Authorization. A redirect to an attacker
    host would therefore hand over the credential, so anything that leaves
    the original host is rejected outright.
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not _same_host(req.full_url, newurl):
            raise urllib.error.HTTPError(
                req.full_url, code,
                "cross-host redirect refused (would leak credentials)",
                headers, fp)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_opener = urllib.request.build_opener(_NoCrossHostRedirect)


def _same_host(a: str, b: str) -> bool:
    pa, pb = urllib.parse.urlsplit(a), urllib.parse.urlsplit(b)
    return (pa.scheme == pb.scheme
            and (pa.hostname or "").lower() == (pb.hostname or "").lower()
            and pa.port == pb.port)


# ------------------------------------------------------------------ core
def analyze_pdf(pdf_bytes: bytes, timeout: int = 600, retries: int = 4) -> dict:
    """Run prebuilt-read over a PDF (or image) and return the raw result.

    Retries on transient network/throttling errors - large uploads over a VPN
    occasionally get their connection reset.
    """
    ep = endpoint()
    if not ep:
        return {"ok": False, "reason": f"{ENDPOINT_ENV} not set"}
    if urllib.parse.urlsplit(ep).scheme != "https":
        return {"ok": False,
                "reason": "endpoint must use https - refusing to send the "
                          "credential over an unencrypted connection"}
    url = (f"{ep.rstrip('/')}/documentintelligence/documentModels/{MODEL}:analyze"
           f"?api-version={API_VERSION}")

    last = ""
    for attempt in range(retries):
        if attempt:
            time.sleep(min(4 * 2 ** attempt, 45))
        try:
            req = urllib.request.Request(url, data=pdf_bytes, headers=_headers(),
                                         method="POST")
            with _opener.open(req, timeout=300) as resp:
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
        # The poll URL comes back from the server, and we re-send the
        # credential to it. Only follow it if it stayed on the resource we
        # were configured to talk to.
        if not _same_host(url, op):
            return {"ok": False,
                    "reason": "Operation-Location pointed at an unexpected "
                              "host - refusing to send the credential there"}

        deadline = time.time() + timeout
        while time.time() < deadline:
            time.sleep(2)
            try:
                g = urllib.request.Request(op, headers={
                    k: v for k, v in _headers().items() if k != "Content-Type"})
                with _opener.open(g, timeout=90) as resp:
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
            "auth": ("key" if api_key()
                     else ("entra-token" if get_token() else "none")),
            "ready": available(),
            "configPath": str(config_path()),
            "model": MODEL, "api_version": API_VERSION,
            "privacy": ("Page images are sent to this Azure resource for "
                        "recognition. Requires explicit user consent.")}


def test_connection(timeout: int = 60) -> dict:
    """Send one tiny page to the configured resource and report what happened.

    "Saved" is not the same as "works": the endpoint may be misspelt, the
    resource may be the wrong kind, or the signed-in identity may lack the
    Cognitive Services User role. Those failures should surface in settings,
    not halfway through a book.
    """
    ep = endpoint()
    if not ep:
        return {"ok": False, "reason": "No endpoint configured.",
                "hint": "Paste the endpoint URL from your Azure resource."}
    if not (api_key() or get_token()):
        return {"ok": False,
                "reason": "No credentials. Run 'az login', or supply an API key.",
                "hint": "Warraq uses your signed-in Azure identity by default."}

    # A 1-page PDF built inline: no fixture on disk, no dependency on a book.
    pdf = (b"%PDF-1.4\n"
           b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
           b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
           b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 200 100]"
           b"/Contents 4 0 R/Resources<</Font<</F1 5 0 R>>>>>>endobj\n"
           b"4 0 obj<</Length 44>>stream\n"
           b"BT /F1 24 Tf 20 40 Td (Warraq test) Tj ET\n"
           b"endstream endobj\n"
           b"5 0 obj<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>endobj\n"
           b"trailer<</Root 1 0 R>>\n")

    r = analyze_pdf(pdf, timeout=timeout, retries=1)
    if r.get("ok"):
        return {"ok": True,
                "endpoint": ep,
                "auth": "key" if api_key() else "entra-token",
                "reason": "Connected. Azure will be used for scanned books."}

    reason = str(r.get("reason", "unknown error"))
    hint = "Check the endpoint URL and that the resource is running."
    low = reason.lower()
    if "401" in reason or "unauthor" in low:
        hint = ("Credentials were rejected. Run 'az login', or check the API "
                "key. Entra sign-in also needs the 'Cognitive Services User' "
                "role on the resource.")
    elif "403" in reason or "forbidden" in low:
        hint = ("Access denied. The signed-in identity needs the 'Cognitive "
                "Services User' role on this resource.")
    elif "404" in reason:
        hint = ("Endpoint reachable but the model was not found. Confirm this "
                "is an Azure AI Services or Document Intelligence resource.")
    elif "getaddrinfo" in low or "name or service" in low or "dns" in low:
        hint = "Endpoint hostname could not be resolved. Check for a typo."
    elif "timed out" in low or "timeout" in low:
        hint = "The resource did not respond in time. Check network access."
    return {"ok": False, "reason": reason[:400], "hint": hint}
