import json

from evals.real_ocr_eval import evaluate_samples
from evals.real_ocr_eval import main
from rareguard.llm.provider import BaseLLMProvider, LLMResponse


class _MatchOCR(BaseLLMProvider):
    name = "m"

    def chat(self, messages, tools=None):
        return LLMResponse(text=json.dumps({"items": [
            {"name": "Serum Ferritin", "value": 1200, "unit": "ng/mL",
             "ref_low": 15, "ref_high": 150}]}), model=self.name)


def _sample(tmp):
    (tmp / "lab1.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    label = [{"name": "血清铁蛋白", "code": "ferritin", "value": 1200,
              "unit": "ng/mL", "ref_low": 15, "ref_high": 150}]
    (tmp / "lab1.json").write_text(
        json.dumps(label, ensure_ascii=False), encoding="utf-8")


def test_evaluate_samples_full_accuracy(tmp_path):
    _sample(tmp_path)
    r = evaluate_samples(tmp_path, _MatchOCR())
    assert r["files"] == 1 and r["mean_accuracy"] == 1.0


def test_evaluate_samples_empty_returns_none(tmp_path):
    assert evaluate_samples(tmp_path, _MatchOCR()) is None


def test_evaluate_samples_scores_mismatch_below_one(tmp_path):
    (tmp_path / "lab1.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    label = [{"name": "血清铁蛋白", "code": "ferritin", "value": 999,
              "unit": "ng/mL", "ref_low": 15, "ref_high": 150}]
    (tmp_path / "lab1.json").write_text(
        json.dumps(label, ensure_ascii=False), encoding="utf-8")
    r = evaluate_samples(tmp_path, _MatchOCR())
    assert 0.0 <= r["mean_accuracy"] < 1.0


def test_require_real_does_not_pass_missing_samples(tmp_path):
    assert main(["eval", str(tmp_path), "--require-real"]) == 2


def test_missing_label_is_an_incomplete_report(tmp_path):
    _sample(tmp_path)
    (tmp_path / "unlabeled.png").write_bytes(b"png")
    r = evaluate_samples(tmp_path, _MatchOCR())
    assert r["files"] == 1 and r["expected_files"] == 2
    assert not r["complete"] and r["errors"]


def test_empty_labels_cannot_score_as_perfect(tmp_path):
    _sample(tmp_path)
    (tmp_path / "lab1.json").write_text("[]")
    r = evaluate_samples(tmp_path, _MatchOCR())
    assert not r["complete"] and r["field_accuracy"] == 0


def test_provider_failures_are_recorded_without_patient_data(tmp_path):
    _sample(tmp_path)
    class Fail:
        def chat(self, messages):
            raise RuntimeError("sensitive patient data")
    r = evaluate_samples(tmp_path, Fail())
    assert not r["complete"]
    assert "sensitive patient data" not in json.dumps(r)
