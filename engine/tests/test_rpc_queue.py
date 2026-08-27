"""Multi-job queue behaviour of the RPC layer.

The shell lets the user add several books at once. The engine converts them one
at a time, but it must accept every request, keep each job distinct, and tag
every message with the job it belongs to - otherwise the UI cannot tell the
books apart and one book's result lands on another.
"""
from __future__ import annotations

import io
import json
import threading
import time

from kbo import rpc


class Collector(io.TextIOBase):
    """Parses the engine's newline-delimited JSON output as it is written."""

    def __init__(self) -> None:
        super().__init__()
        self._buf = ""
        self._messages: list[dict] = []
        self._lock = threading.Lock()

    def write(self, s: str) -> int:
        with self._lock:
            self._buf += s
            while "\n" in self._buf:
                line, self._buf = self._buf.split("\n", 1)
                if line.strip():
                    self._messages.append(json.loads(line))
        return len(s)

    def flush(self) -> None:  # pragma: no cover - nothing to flush
        pass

    def messages(self) -> list[dict]:
        with self._lock:
            return list(self._messages)

    def terminal_ids(self) -> set:
        return {m.get("id") for m in self.messages()
                if "result" in m or "error" in m}


def _drive(requests: list[dict], expect: set[str], timeout: float = 30.0):
    """Feed `requests` to serve() and stop once `expect` jobs have finished."""
    out = Collector()

    def stdin():
        for req in requests:
            yield json.dumps(req)
        deadline = time.time() + timeout
        while time.time() < deadline and not expect <= out.terminal_ids():
            time.sleep(0.02)
        yield json.dumps({"id": "shutdown", "method": "shutdown"})

    rpc.serve(stdin=stdin(), stdout=out)
    return out


def test_queue_accepts_and_completes_several_jobs(tmp_path):
    """Two converts submitted back to back both run and stay distinct.

    Missing files are used so each job fails fast; what matters is that the
    queue drained both of them rather than dropping the second.
    """
    reqs = [
        {"id": "book-a", "method": "convert",
         "params": {"path": str(tmp_path / "a.pdf"), "outDir": str(tmp_path)}},
        {"id": "book-b", "method": "convert",
         "params": {"path": str(tmp_path / "b.pdf"), "outDir": str(tmp_path)}},
    ]
    out = _drive(reqs, expect={"book-a", "book-b"})

    terminal = {m["id"]: m for m in out.messages() if "error" in m or "result" in m}
    assert set(terminal) >= {"book-a", "book-b"}, out.messages()
    assert terminal["book-a"]["error"]["code"] == "FILE_NOT_FOUND"
    assert terminal["book-b"]["error"]["code"] == "FILE_NOT_FOUND"


def test_every_message_carries_its_job_id(tmp_path):
    """No message may be emitted without the id the UI routes on."""
    reqs = [
        {"id": "book-a", "method": "convert",
         "params": {"path": str(tmp_path / "a.pdf"), "outDir": str(tmp_path)}},
    ]
    out = _drive(reqs, expect={"book-a"})

    for msg in out.messages():
        if msg.get("event") == "ready":
            continue
        assert msg.get("id") is not None, msg
        assert msg["id"] in {"book-a", "shutdown"}, msg


def test_convert_is_acknowledged_immediately(tmp_path):
    """`convert` returns an acceptance before the job runs, so the shell can
    register the job id while the engine is still busy with an earlier book."""
    reqs = [
        {"id": "book-a", "method": "convert",
         "params": {"path": str(tmp_path / "a.pdf"), "outDir": str(tmp_path)}},
    ]
    out = _drive(reqs, expect={"book-a"})

    accepted = [m for m in out.messages() if m.get("event") == "accepted"]
    assert accepted, out.messages()
    assert accepted[0]["id"] == "book-a"


def test_queued_job_can_be_cancelled_before_it_starts(tmp_path):
    """Removing a book that is still waiting must stop it converting.

    The cancel arrives while the job is in the queue and has therefore never
    been registered as running, which previously made it uncancellable.
    """
    book = tmp_path / "b.pdf"
    book.write_bytes(b"%PDF-1.4\n")  # would fail loudly if it were ever opened

    reqs = [
        {"id": "book-a", "method": "convert",
         "params": {"path": str(tmp_path / "a.pdf"), "outDir": str(tmp_path)}},
        {"id": "book-b", "method": "convert",
         "params": {"path": str(book), "outDir": str(tmp_path)}},
        {"id": "kill", "method": "cancel", "params": {"jobId": "book-b"}},
    ]
    out = _drive(reqs, expect={"book-a", "book-b"})

    kill = [m for m in out.messages() if m.get("id") == "kill"]
    assert kill and kill[0]["result"]["cancelled"] is True

    terminal = {m["id"]: m for m in out.messages() if "error" in m or "result" in m}
    assert terminal["book-b"]["error"]["code"] == "CANCELLED", terminal["book-b"]


def test_registry_remembers_cancels_for_jobs_that_have_not_started():
    """The pre-cancel contract the queued-book case depends on.

    Asserted directly rather than through `serve()` because whether the worker
    has already picked the job up is a matter of thread scheduling.
    """
    reg = rpc.JobRegistry()

    assert reg.cancel("later") is True          # never started
    assert reg.start("later").is_set()          # starts already cancelled

    # The flag is one-shot: finishing clears it so the id can be reused.
    reg.finish("later")
    assert not reg.start("later").is_set()


def test_registry_cancels_a_running_job():
    reg = rpc.JobRegistry()
    ev = reg.start("running")
    assert not ev.is_set()
    assert reg.cancel("running") is True
    assert ev.is_set()


def test_cancelling_an_unknown_job_is_harmless(tmp_path):
    """A cancel for a job that already finished must not block a later reuse."""
    reqs = [
        {"id": "ghost", "method": "cancel", "params": {"jobId": "ghost"}},
        {"id": "book-a", "method": "convert",
         "params": {"path": str(tmp_path / "a.pdf"), "outDir": str(tmp_path)}},
    ]
    out = _drive(reqs, expect={"book-a"})
    terminal = {m["id"]: m for m in out.messages() if "error" in m or "result" in m}
    assert terminal["book-a"]["error"]["code"] == "FILE_NOT_FOUND"


# ------------------------------------------------------------- settings
def test_settings_round_trip(tmp_path, monkeypatch):
    """The settings screen must be able to switch OCR modes.

    Choosing Azure writes an endpoint; choosing offline clears it. Clearing has
    to actually remove the key rather than leave the old value in place, which
    is why an empty string is meaningful here.
    """
    monkeypatch.setenv("KBO_CONFIG", str(tmp_path / "config.json"))
    monkeypatch.delenv("KBO_AZURE_DI_ENDPOINT", raising=False)
    monkeypatch.delenv("KBO_AZURE_DI_KEY", raising=False)

    from kbo import rpc as rpc_mod

    assert rpc_mod._get_settings()["ocrMode"] == "offline"

    saved = rpc_mod._set_settings({
        "azureEndpoint": "https://example.cognitiveservices.azure.com/",
        "azureKey": "secret-value",
    })
    assert saved["saved"] is True
    assert saved["ocrMode"] == "azure"
    assert saved["hasAzureKey"] is True
    # The key must never be echoed back to the UI.
    assert "secret-value" not in json.dumps(saved)

    cleared = rpc_mod._set_settings({"azureEndpoint": "", "azureKey": ""})
    assert cleared["ocrMode"] == "offline"
    assert cleared["azureEndpoint"] == ""
    assert cleared["hasAzureKey"] is False


def test_endpoint_without_scheme_is_accepted(tmp_path, monkeypatch):
    """Users paste hostnames from the portal without the scheme."""
    monkeypatch.setenv("KBO_CONFIG", str(tmp_path / "config.json"))
    monkeypatch.delenv("KBO_AZURE_DI_ENDPOINT", raising=False)
    from kbo import rpc as rpc_mod

    s = rpc_mod._set_settings(
        {"azureEndpoint": "  example.cognitiveservices.azure.com  "})
    assert s["azureEndpoint"] == "https://example.cognitiveservices.azure.com"


def test_azure_test_reports_missing_endpoint(tmp_path, monkeypatch):
    """A connection test with nothing configured explains itself."""
    monkeypatch.setenv("KBO_CONFIG", str(tmp_path / "config.json"))
    monkeypatch.delenv("KBO_AZURE_DI_ENDPOINT", raising=False)
    monkeypatch.delenv("KBO_AZURE_DI_KEY", raising=False)
    from kbo import azure_ocr

    r = azure_ocr.test_connection()
    assert r["ok"] is False
    assert "endpoint" in r["reason"].lower()
    assert r["hint"]
