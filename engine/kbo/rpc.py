"""Warraq engine — JSON-RPC over stdio.

The desktop shell drives the conversion engine through this module. One JSON
object per line, both directions, so the shell can stream progress while a job
runs.

    engine --rpc

Design rule: this module contains NO conversion logic. It is a transport around
`kbo.cli`, so the GUI and the CLI can never diverge in behaviour.

Protocol
--------
Requests (shell -> engine), newline-delimited JSON:
    {"id":"1","method":"ping"}
    {"id":"2","method":"capabilities"}
    {"id":"3","method":"analyze","params":{"path":"C:/b.pdf"}}
    {"id":"4","method":"convert","params":{"path":"...","outDir":"..."}}
    {"id":"5","method":"cancel","params":{"jobId":"4"}}
    {"id":"6","method":"shutdown"}

Responses (engine -> shell):
    {"id":"4","event":"stage","stage":"ocr","status":"running","pct":0.6,...}
    {"id":"4","event":"metric","key":"ocrConfidence","value":95.0}
    {"id":"4","event":"warning","code":"AZURE_FALLBACK","message":"..."}
    {"id":"4","result":{...,"schemaVersion":1}}
    {"id":"4","error":{"code":"ENGINE_ERROR","message":"..."}}
"""
from __future__ import annotations

import json
import os
import queue
import sys
import threading
import traceback

SCHEMA_VERSION = 1

# Ordered pipeline stages the UI renders as a tracker. Weights are the measured
# share of wall-clock time for a scanned Arabic book (the slowest realistic
# case), used to convert stage progress into an overall percentage.
STAGES = [
    ("analyze", "Analysing pages", 0.13),
    ("clean", "Cleaning and deskewing", 0.13),
    ("ocr", "Reading text", 0.52),
    ("extract", "Rebuilding the document", 0.04),
    ("typography", "Applying typography", 0.03),
    ("build", "Building Kindle files", 0.09),
    ("qa", "Quality checks", 0.06),
]
STAGE_INDEX = {k: i for i, (k, _, _) in enumerate(STAGES)}


class Cancelled(Exception):
    """Raised inside the worker when the shell cancels a job."""


class Emitter:
    """Serialises engine -> shell messages. Thread-safe."""

    def __init__(self, out=None):
        self._out = out or sys.stdout
        self._lock = threading.Lock()
        self._peak: dict[str, float] = {}

    def send(self, obj: dict) -> None:
        line = json.dumps(obj, ensure_ascii=False, default=str)
        with self._lock:
            self._out.write(line + "\n")
            self._out.flush()

    # -- typed helpers ----------------------------------------------------
    def stage(self, job_id, stage, status, detail=None, pct=None, eta=None):
        # A completed stage is 100% of itself, so overall progress never moves
        # backwards when a stage closes.
        effective = 1.0 if status == "done" else pct
        overall = self._overall(stage, effective)
        # Progress is monotonic per job: a UI bar must never move backwards.
        prev = self._peak.get(job_id, 0.0)
        overall = max(prev, overall)
        self._peak[job_id] = overall
        msg = {"id": job_id, "event": "stage", "stage": stage,
               "status": status, "overallPct": overall}
        if detail:
            msg["detail"] = detail
        if pct is not None:
            msg["stagePct"] = round(pct, 4)
        if eta is not None:
            msg["etaSec"] = round(eta)
        self.send(msg)

    def metric(self, job_id, key, value):
        self.send({"id": job_id, "event": "metric", "key": key, "value": value})

    def warning(self, job_id, code, message):
        self.send({"id": job_id, "event": "warning", "code": code,
                   "message": message})

    def log(self, job_id, line):
        self.send({"id": job_id, "event": "log", "line": line})

    def result(self, job_id, payload):
        payload = dict(payload)
        payload["schemaVersion"] = SCHEMA_VERSION
        self.send({"id": job_id, "result": payload})

    def error(self, job_id, code, message, detail=None):
        err = {"code": code, "message": message}
        if detail:
            err["detail"] = detail
        self.send({"id": job_id, "error": err})

    @staticmethod
    def _overall(stage: str, pct: float | None) -> float:
        """Weighted overall progress so the UI bar never jumps backwards."""
        idx = STAGE_INDEX.get(stage)
        if idx is None:
            return 0.0
        done = sum(w for _, _, w in STAGES[:idx])
        cur = STAGES[idx][2] * (pct if pct is not None else 0.0)
        return round(min(1.0, done + cur), 4)


class JobRegistry:
    """Tracks running jobs so they can be cancelled."""

    def __init__(self):
        self._jobs: dict[str, threading.Event] = {}
        self._lock = threading.Lock()

    def start(self, job_id: str) -> threading.Event:
        ev = threading.Event()
        with self._lock:
            self._jobs[job_id] = ev
        return ev

    def cancel(self, job_id: str) -> bool:
        with self._lock:
            ev = self._jobs.get(job_id)
        if ev:
            ev.set()
            return True
        return False

    def finish(self, job_id: str) -> None:
        with self._lock:
            self._jobs.pop(job_id, None)

    def active(self) -> list[str]:
        with self._lock:
            return list(self._jobs)


# ---------------------------------------------------------------- handlers
def _capabilities() -> dict:
    """Everything the shell needs to render setup and options screens."""
    from kbo import azure_ocr, build, device, kfx, ocr
    from kbo.build import EBOOK_CONVERT

    az = azure_ocr.status()
    return {
        "schemaVersion": SCHEMA_VERSION,
        "engineVersion": _engine_version(),
        "device": device.get(),
        "arabicFonts": [
            {"id": k, "description": v[2], "lineHeight": v[3],
             "hasBold": v[1] is not None, "default": k == "amiri"}
            for k, v in build.ARABIC_FONTS.items()
        ],
        "defaults": {
            "arabicFont": "amiri",
            "fontMode": "auto",
            "ocrEngine": "auto",
            "makePdf": "auto",
            "workers": max(2, min(8, (os.cpu_count() or 4))),
        },
        "tools": {
            "calibre": {"path": EBOOK_CONVERT,
                        "available": os.path.exists(EBOOK_CONVERT)},
            "tesseract": {"path": ocr.TESSERACT, "available": ocr.available(),
                          "languages": ocr.languages() if ocr.available() else []},
            "azure": {"available": az["ready"], "auth": az["auth"],
                      "endpoint": az["endpoint"], "model": az["model"],
                      "privacy": az["privacy"]},
            "kfx": kfx.status(),
        },
        "stages": [{"id": k, "label": lbl, "weight": w} for k, lbl, w in STAGES],
    }


def _engine_version() -> str:
    try:
        import kbo
        return getattr(kbo, "__version__", "0.0.0")
    except Exception:
        return "0.0.0"


def _warm_imports() -> None:
    """Preload the heavy scientific stack off the critical path."""
    try:
        import cv2          # noqa: F401
        import fitz         # noqa: F401
        import numpy        # noqa: F401

        from kbo import analyze, build, clean, extract, ocr, qa  # noqa: F401
    except Exception:
        pass


def _analyze(params: dict) -> dict:
    """Fast inspection pass that powers the Inspect screen.

    Defaults to a sampled scan so a 225-page book inspects in seconds rather
    than a minute. The conversion itself always re-analyses in full.
    """
    from kbo import analyze as anz
    from kbo import cli
    path = params["path"]
    if not os.path.exists(path):
        raise FileNotFoundError(path)

    sample = params.get("samplePages", 14)
    a = anz.analyze(path, max_pages=params.get("maxPages"),
                    sample_pages=None if params.get("full") else sample)

    # A scanned book has no text layer, so language is unknown after analysis.
    # OCR two sampled pages to answer "what language is this?" for the UI.
    if not a.get("language"):
        a["language"] = _probe_language(path, a)

    route, why = cli.decide_route(a)
    meta = cli.guess_meta(path, a)
    scale = a["page_count"] / max(a["analyzed_pages"], 1) if a["sampled"] else 1
    return {
        "path": path,
        "title": meta["title"],
        "author": meta["author"],
        "pageCount": a["page_count"],
        "analyzedPages": a["analyzed_pages"],
        "sampled": a["sampled"],
        "language": a.get("language"),
        "docType": a["doc_type"],
        "columns": a["dominant_columns"],
        "tocEntries": a["toc_entries"],
        "blankPages": int(len(a["blank_pages"]) * scale),
        "duplicatePages": int(len(a["duplicate_pages"]) * scale),
        "rotatedPages": int(len(a["rotated_pages"]) * scale),
        "noisyPages": int(len(a["noisy_pages"]) * scale),
        "skewMedian": a["skew_median"],
        "arabicRatio": a["arabic_ratio"],
        "plan": {"route": route, "reason": why,
                 "estimatedSeconds": _estimate_seconds(a, route),
                 "willUseAzure": a["doc_type"] != "text"},
        "findings": _findings(a, route),
    }


def _probe_language(path: str, a: dict) -> str | None:
    """OCR a couple of sampled pages to name the language of a scanned book."""
    try:
        import cv2
        import fitz

        from kbo import analyze as anz
        from kbo import arabic, clean, ocr
        if not ocr.available():
            return None
        doc = fitz.open(path)
        n = doc.page_count
        picks = [i for i in (int(n * 0.25), int(n * 0.6)) if 0 <= i < n]
        texts = []
        for i in picks:
            info = anz.analyze_page(doc[i])
            g = anz.render_gray(doc[i], 200)
            g = clean.clean_page(g, info)
            r = ocr.ocr_page(g, "ara+eng", psm=3)
            texts.append(" ".join(l["text"] for l in r["lines"]))
        doc.close()
        joined = "\n".join(texts)
        return arabic.classify_language(joined) if joined.strip() else None
    except Exception:
        return None


def _findings(a: dict, route: str) -> list[str]:
    """Plain-language observations for the Inspect screen."""
    out = []
    lang = {"ar": "Arabic", "en": "English",
            "bilingual": "Arabic and English"}.get(a.get("language"))
    dt = {"text": "with a real text layer",
          "scanned": "scanned images — no text layer",
          "text_over_scan": "scanned, with a poor existing text layer",
          "mixed": "part text, part scanned"}.get(a["doc_type"], "")
    if lang:
        out.append(f"{lang}, {dt}")
    elif dt:
        out.append(dt.capitalize())
    cols = a["dominant_columns"]
    col_txt = "Single column" if cols == 1 else f"{cols} columns"
    if a["toc_entries"]:
        out.append(f"{col_txt} · {a['toc_entries']} chapter bookmarks found")
    else:
        out.append(f"{col_txt} · no bookmarks — chapters will be detected")
    if a["skew_median"] > 0.15:
        out.append(f"Slight scan tilt ({a['skew_median']}°) — will be corrected")
    if a["blank_pages"]:
        out.append(f"{len(a['blank_pages'])} blank page(s) will be removed")
    if a["duplicate_pages"]:
        out.append(f"{len(a['duplicate_pages'])} duplicate page(s) will be removed")
    if a["noisy_pages"]:
        out.append(f"{len(a['noisy_pages'])} page(s) need noise cleanup")
    return out


def _estimate_seconds(a: dict, route: str) -> int:
    """Calibrated against measured runs: 271p text=59s, 225p scan=587s."""
    n = a["page_count"]
    if route == "reflow":
        return int(max(10, n * 0.22))
    return int(max(30, n * 2.6))


def _convert(job_id: str, params: dict, emit: Emitter,
             cancel: threading.Event) -> dict:
    """Run the full pipeline, translating engine logs into stage events."""
    from kbo import cli

    stage_state = {"current": None}

    def on_log(line: str) -> None:
        if cancel.is_set():
            raise Cancelled()
        emit.log(job_id, line)
        st, detail, pct = _classify(line)
        if st:
            if stage_state["current"] and stage_state["current"] != st:
                emit.stage(job_id, stage_state["current"], "done")
            stage_state["current"] = st
            emit.stage(job_id, st, "running", detail, pct)

    original_log = cli.log
    cli.log = on_log
    try:
        res = cli.run(
            params["path"],
            params["outDir"],
            max_pages=params.get("maxPages"),
            force_route=params.get("forceRoute", "auto"),
            aggressive=params.get("aggressiveClean", False),
            make_pdf=params.get("makePdf", "auto"),
            workers=params.get("workers", 4),
            font_mode=params.get("fontMode", "auto"),
            ocr_engine=params.get("ocrEngine", "auto"),
            arabic_font=params.get("arabicFont", "amiri"),
        )
    finally:
        cli.log = original_log

    if stage_state["current"]:
        emit.stage(job_id, stage_state["current"], "done")

    for w in res.get("warnings", []):
        emit.warning(job_id, "PIPELINE_WARNING", w)
    return _shape_result(res)


def _classify(line: str) -> tuple[str | None, str | None, float | None]:
    """Map an engine log line onto (stage, detail, stagePct)."""
    import re
    low = line.lower()
    if "analyzing" in low:
        return "analyze", None, 0.05
    if "route:" in low:
        return "analyze", None, 1.0
    if "rendering + cleaning" in low:
        return "clean", None, 0.05
    if "pages prepared" in low:
        return "clean", None, 1.0
    if "azure di chunk" in low:
        m = re.search(r"chunk (\d+)/(\d+)", line)
        if m:
            i, n = int(m.group(1)), int(m.group(2))
            return "ocr", f"Azure AI · chunk {i} of {n}", i / max(n, 1) * 0.8
        return "ocr", "Azure AI", 0.1
    if "tesseract (" in low or "ocr (" in low:
        return "ocr", "Offline OCR", 0.85
    if "mean confidence" in low:
        return "ocr", None, 1.0
    if "engine scores" in low:
        return "ocr", "Comparing engines", 0.95
    if "-> " in low and "blocks" in low:
        return "extract", None, 1.0
    if "converting to azw3" in low:
        return "typography", None, 1.0
    if "also built" in low or "also building" in low or "fixed-layout" in low:
        return "build", "Extra editions", 0.6
    if "optimized pdf" in low:
        return "build", "Kindle PDF", 0.85
    if "running qa" in low:
        return "qa", None, 0.3
    if "qa gate" in low:
        return "qa", None, 0.9
    if "done in" in low:
        return "qa", None, 1.0
    return None, None, None


def _shape_result(res: dict) -> dict:
    """Reshape the engine result into the UI-facing contract."""
    a = res.get("analysis") or {}
    qa_ = res.get("qa", {})
    primary = qa_.get("azw3", {})
    ocr_ = res.get("ocr", {})
    st = res.get("extract_stats", {})

    files = []
    labels = {
        "azw3": ("Kindle edition", True),
        "azw3_searchable": ("Searchable edition", False),
        "azw3_fixed": ("Page-exact edition", False),
        "pdf": ("Kindle-sized PDF", False),
        "report": ("Conversion report", False),
    }
    for key, path in res.get("outputs", {}).items():
        label, primary_flag = labels.get(key, (key, False))
        files.append({
            "kind": key, "label": label, "path": path,
            "name": os.path.basename(path),
            "sizeBytes": os.path.getsize(path) if os.path.exists(path) else 0,
            "recommended": primary_flag,
        })

    typography = None
    if res.get("rtl"):
        font = primary.get("font", {})
        arb = primary.get("arabic", {})
        typography = {
            "font": res.get("arabic_font", "amiri"),
            "fontEmbedded": bool(font.get("embedded")),
            "fontIntact": not font.get("subset_suspected", False),
            "preShaped": res.get("font_mode") == "preshape",
            "rtlValidated": True,
            "shapingValid": arb.get("ok", False),
            "wordsChecked": arb.get("words", 0),
            "issues": arb.get("issues", []),
        }

    cov = primary.get("coverage") or {}
    return {
        "jobId": None,
        "input": res.get("input"),
        "title": res.get("meta", {}).get("title"),
        "author": res.get("meta", {}).get("author"),
        "language": a.get("language"),
        "rtl": res.get("rtl", False),
        "route": res.get("route"),
        "routeReason": res.get("route_reason"),
        "qualityGate": res.get("qa_gate"),
        "qualityScore": _quality_score(res),
        "ocr": {
            "used": ocr_.get("used", False),
            "engine": ocr_.get("engine"),
            "confidence": ocr_.get("mean_conf"),
            "verdict": ocr_.get("verdict"),
            "pagesOcred": ocr_.get("pages_ocred"),
        },
        "typography": typography,
        "content": {
            "tokenRatio": cov.get("token_ratio"),
            "vocabRecall": cov.get("vocab_recall"),
            "chapters": st.get("headings", 0) + st.get("toc_headings", 0),
            "headingsDetected": st.get("headings", 0),
            "headingsFromBookmarks": st.get("toc_headings", 0),
            "footnotes": st.get("footnotes", 0),
            "images": st.get("images", 0),
            "pagesKeptAsImage": st.get("pages_kept_as_image", 0),
        },
        "reviewPages": res.get("manual_review_pages", []),
        "warnings": res.get("warnings", []),
        "kfx": res.get("kfx", {}),
        "files": files,
        "previews": res.get("previews", []),
        "elapsedSeconds": res.get("elapsed_sec"),
    }


def _quality_score(res: dict) -> int:
    """0-100 headline score. Deliberately conservative and explainable."""
    gate = res.get("qa_gate")
    if gate == "FAIL":
        base = 55
    elif gate == "WARN":
        base = 78
    else:
        base = 90

    ocr_ = res.get("ocr", {})
    conf = ocr_.get("mean_conf")
    if conf:
        base += 6 if conf >= 93 else (3 if conf >= 85 else 0)

    q = res.get("qa", {}).get("azw3", {})
    cov = (q.get("coverage") or {}).get("vocab_recall")
    if cov is not None:
        base += 4 if cov >= 0.97 else (2 if cov >= 0.90 else -4)

    arb = q.get("arabic")
    if arb and not arb.get("ok", True):
        base -= 15
    if q.get("font", {}).get("subset_suspected"):
        base -= 20

    review = len(res.get("manual_review_pages", []))
    pages = (res.get("analysis") or {}).get("page_count") or 1
    if review:
        base -= min(8, int(review / pages * 40))
    return max(0, min(100, int(base)))


# ---------------------------------------------------------------- server
def serve(stdin=None, stdout=None) -> int:
    stdin = stdin or sys.stdin
    # The protocol owns stdout. Any stray print() inside the engine would
    # corrupt the JSON stream, so we capture the real stdout for the emitter
    # and redirect everything else to stderr.
    real_stdout = stdout or sys.stdout
    if stdout is None:
        sys.stdout = sys.stderr

    emit = Emitter(real_stdout)
    jobs = JobRegistry()
    work: queue.Queue = queue.Queue()

    def worker():
        while True:
            item = work.get()
            if item is None:
                return
            job_id, params = item
            cancel = jobs.start(job_id)
            try:
                payload = _convert(job_id, params, emit, cancel)
                payload["jobId"] = job_id
                emit.result(job_id, payload)
            except Cancelled:
                emit.error(job_id, "CANCELLED", "Conversion cancelled.")
            except FileNotFoundError as e:
                emit.error(job_id, "FILE_NOT_FOUND", str(e))
            except Exception as e:
                emit.error(job_id, "ENGINE_ERROR", str(e),
                           traceback.format_exc()[-2000:])
            finally:
                jobs.finish(job_id)
                work.task_done()

    threading.Thread(target=worker, daemon=True, name="warraq-worker").start()

    # Announce readiness before importing OpenCV/PyMuPDF/NumPy. Those take
    # several seconds from a frozen bundle, and the shell should be able to
    # paint its UI immediately. Warm them in the background so the first real
    # request does not pay the cost either.
    emit.send({"event": "ready", "schemaVersion": SCHEMA_VERSION,
               "engineVersion": _engine_version()})
    threading.Thread(target=_warm_imports, daemon=True,
                     name="warraq-warmup").start()

    for raw in stdin:
        raw = raw.strip()
        if not raw:
            continue
        try:
            req = json.loads(raw)
        except json.JSONDecodeError as e:
            emit.error(None, "BAD_REQUEST", f"invalid JSON: {e}")
            continue

        rid = req.get("id")
        method = req.get("method")
        params = req.get("params") or {}
        try:
            if method == "ping":
                emit.send({"id": rid, "result": {"pong": True}})
            elif method == "capabilities":
                emit.send({"id": rid, "result": _capabilities()})
            elif method == "analyze":
                emit.send({"id": rid, "result": _analyze(params)})
            elif method == "convert":
                work.put((rid, params))
                emit.send({"id": rid, "event": "accepted",
                           "activeJobs": jobs.active()})
            elif method == "cancel":
                ok = jobs.cancel(params.get("jobId"))
                emit.send({"id": rid, "result": {"cancelled": ok}})
            elif method == "shutdown":
                emit.send({"id": rid, "result": {"bye": True}})
                return 0
            else:
                emit.error(rid, "UNKNOWN_METHOD", f"unknown method: {method}")
        except FileNotFoundError as e:
            emit.error(rid, "FILE_NOT_FOUND", str(e))
        except Exception as e:
            emit.error(rid, "ENGINE_ERROR", str(e),
                       traceback.format_exc()[-2000:])
    return 0


if __name__ == "__main__":
    sys.exit(serve())
