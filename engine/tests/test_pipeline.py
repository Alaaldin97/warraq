"""Tests for routing, scoring and the JSON-RPC contract."""
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from kbo import cli, rpc, score  # noqa: E402


def _analysis(**over):
    base = {
        "doc_type": "text", "language": "en", "dominant_columns": 1,
        "text_page_ratio": 0.98, "image_backed_ratio": 0.0,
        "page_count": 100, "analyzed_pages": 100, "sampled": False,
        "toc": [], "toc_entries": 0, "arabic_ratio": 0.0,
        "blank_pages": [], "duplicate_pages": [], "rotated_pages": [],
        "noisy_pages": [], "skew_median": 0.0,
    }
    base.update(over)
    return base


class TestRouting:
    def test_native_text_reflows(self):
        route, _ = cli.decide_route(_analysis())
        assert route == "reflow"

    def test_dense_multicolumn_stays_fixed(self):
        route, _ = cli.decide_route(_analysis(dominant_columns=3))
        assert route == "fixed"

    def test_scanned_goes_to_ocr(self):
        route, _ = cli.decide_route(
            _analysis(doc_type="scanned", text_page_ratio=0.0))
        assert route == "ocr"

    def test_junk_text_layer_is_re_ocred(self):
        """archive.org scans carry a poor OCR layer that must not be trusted.

        Trusting it produced 48% content coverage and scrambled reading order.
        """
        route, reason = cli.decide_route(_analysis(doc_type="text_over_scan"))
        assert route == "ocr"
        assert "OCR" in reason or "re-run" in reason.lower()


class TestScoring:
    def test_prefers_coherent_text(self):
        good = ("في البيت رجل من الناس وقال الرجل إن هذا من الأمر الذي "
                "لا يعرفه أحد من الناس في هذا البيت وقال إن الأمر كذلك") * 6
        junk = "خ ط ب ه ن ل ك ي ٪٪ ﴿ ؟؟ ززز ححح ططط" * 20
        assert (score.score_text(good, "ar")["score"]
                > score.score_text(junk, "ar")["score"])

    def test_empty_scores_zero(self):
        assert score.score_text("", "ar")["score"] == 0.0

    def test_pick_best_returns_winner(self):
        good = "في البيت رجل من الناس وقال الرجل إن هذا الأمر من الناس " * 8
        junk = "ز ح ط ٪ ؟ خ ه ن" * 30
        winner, scores = score.pick_best({"a": good, "b": junk}, "ar")
        assert winner == "a"
        assert set(scores) == {"a", "b"}


class TestProgressWeights:
    def test_weights_sum_to_one(self):
        total = sum(w for _, _, w in rpc.STAGES)
        assert abs(total - 1.0) < 1e-6, total

    def test_progress_is_monotonic_across_stages(self):
        prev = -1.0
        for stage, _, _ in rpc.STAGES:
            pct = rpc.Emitter._overall(stage, 0.0)
            assert pct >= prev, stage
            prev = pct

    def test_final_stage_completes_at_one(self):
        last = rpc.STAGES[-1][0]
        assert abs(rpc.Emitter._overall(last, 1.0) - 1.0) < 1e-6


class TestQualityScore:
    def test_failed_gate_scores_low(self):
        s = rpc._quality_score({"qa_gate": "FAIL", "qa": {}, "ocr": {}})
        assert s < 70

    def test_clean_arabic_run_scores_high(self):
        s = rpc._quality_score({
            "qa_gate": "PASS",
            "ocr": {"mean_conf": 95.0},
            "qa": {"azw3": {"coverage": {"vocab_recall": 0.99},
                            "arabic": {"ok": True}, "font": {}}},
            "analysis": {"page_count": 225},
            "manual_review_pages": [],
        })
        assert s >= 90

    def test_subsetted_font_is_heavily_penalised(self):
        s = rpc._quality_score({
            "qa_gate": "PASS",
            "ocr": {"mean_conf": 95.0},
            "qa": {"azw3": {"coverage": {"vocab_recall": 0.99},
                            "arabic": {"ok": True},
                            "font": {"subset_suspected": True}}},
            "analysis": {"page_count": 225},
            "manual_review_pages": [],
        })
        assert s < 85

    def test_score_is_bounded(self):
        for gate in ("PASS", "WARN", "FAIL"):
            s = rpc._quality_score({"qa_gate": gate, "qa": {}, "ocr": {}})
            assert 0 <= s <= 100


class TestRpcProtocol:
    @pytest.fixture(scope="class")
    @staticmethod
    def engine():
        p = subprocess.Popen(
            [sys.executable, "-u", "-m", "kbo.cli", "--rpc"],
            cwd=str(ROOT), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, text=True, encoding="utf-8", bufsize=1)
        json.loads(p.stdout.readline())          # ready banner
        yield p
        try:
            p.stdin.write('{"id":"z","method":"shutdown"}\n')
            p.stdin.flush()
            p.wait(timeout=10)
        except Exception:
            p.kill()

    @staticmethod
    def _call(p, obj):
        p.stdin.write(json.dumps(obj) + "\n")
        p.stdin.flush()
        return json.loads(p.stdout.readline())

    def test_ping(self, engine):
        assert self._call(engine, {"id": "1", "method": "ping"})["result"]["pong"]

    def test_capabilities_shape(self, engine):
        r = self._call(engine, {"id": "2", "method": "capabilities"})["result"]
        assert r["schemaVersion"] == rpc.SCHEMA_VERSION
        assert r["defaults"]["arabicFont"] == "amiri"
        assert any(f["default"] for f in r["arabicFonts"])
        assert {"calibre", "tesseract", "azure", "kfx"} <= set(r["tools"])

    def test_amiri_is_flagged_default(self, engine):
        r = self._call(engine, {"id": "3", "method": "capabilities"})["result"]
        default = [f["id"] for f in r["arabicFonts"] if f["default"]]
        assert default == ["amiri"]

    def test_unknown_method_errors_cleanly(self, engine):
        r = self._call(engine, {"id": "4", "method": "nope"})
        assert r["error"]["code"] == "UNKNOWN_METHOD"

    def test_missing_file_errors_cleanly(self, engine):
        r = self._call(engine, {"id": "5", "method": "analyze",
                                "params": {"path": "does_not_exist.pdf"}})
        assert r["error"]["code"] == "FILE_NOT_FOUND"

    def test_stdout_carries_only_json(self, engine):
        """Any stray print() in the engine would corrupt the stream."""
        for i in range(5):
            r = self._call(engine, {"id": str(i), "method": "ping"})
            assert "result" in r


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
