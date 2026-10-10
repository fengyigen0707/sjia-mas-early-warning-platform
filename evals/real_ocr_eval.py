"""真实脱敏样例 OCR 字段准确率评测（spec §3.2 / §5 ≥95%）。

样例放本地 data/real_samples/*.png（同名 .json 为人工真值 label），绝不入库。
缺样例或缺密钥 → skip 且不失败：python -m evals.real_ocr_eval
"""
import base64
import argparse
import json
import sys
from pathlib import Path

from evals.ocr_eval import score_ocr
from rareguard.ingest.ocr import parse_lab_report

DEFAULT_DIR = Path("data") / "real_samples"


def evaluate_samples(samples_dir, provider):
    samples_dir = Path(samples_dir)
    pngs = sorted(samples_dir.glob("*.png")) if samples_dir.exists() else []
    if not pngs:
        return None
    per_file, errors = [], []
    for png in pngs:
        label_path = png.with_suffix(".json")
        if not label_path.exists():
            errors.append({"file": png.name, "reason": "missing_label"})
            continue
        try:
            label = json.loads(label_path.read_text(encoding="utf-8"))
            if not isinstance(label, list) or not label or not all(isinstance(i, dict) and all(k in i for k in ("value", "unit", "ref_low", "ref_high")) and (i.get("code") or i.get("name")) for i in label):
                raise ValueError("invalid_label")
            b64 = base64.b64encode(png.read_bytes()).decode()
            parsed = parse_lab_report(b64, provider)
            metrics = score_ocr(parsed, label)
            per_file.append({"file": png.name, **metrics})
        except Exception as exc:
            # No model output, patient values or exception messages in the report.
            errors.append({"file": png.name, "reason": type(exc).__name__})
    mean = (sum(f["accuracy"] for f in per_file) / len(per_file)
            if per_file else 0.0)
    total = sum(f["total"] for f in per_file)
    matched = sum(f["matched"] for f in per_file)
    return {"files": len(per_file), "expected_files": len(pngs), "per_file": per_file,
            "errors": errors, "complete": not errors and len(per_file) == len(pngs),
            "mean_accuracy": round(mean, 4), "field_accuracy": matched / total if total else 0.0,
            "evidence_type": "real_ocr_only_not_clinical_validation"}


def main(argv):
    parser = argparse.ArgumentParser()
    parser.add_argument("samples_dir", nargs="?", default=str(DEFAULT_DIR))
    parser.add_argument("--require-real", action="store_true", help="试点验收：缺样例或密钥时失败，不能以 skip 充当通过")
    parser.add_argument("--output", help="本地报告路径（不得含真实数据或提交公开仓库）")
    args = parser.parse_args(argv[1:])
    samples_dir = Path(args.samples_dir)
    if not samples_dir.exists() or not list(samples_dir.glob("*.png")):
        print(json.dumps({"skipped": f"无真实样例：{samples_dir}"},
                         ensure_ascii=False))
        return 2 if args.require_real else 0
    from rareguard.llm.offline_provider import build_providers
    from rareguard.llm.openai_compat_provider import (
        is_configured, load_dotenv)

    load_dotenv()
    if not is_configured():
        print(json.dumps({"skipped": "未配置 MEDASSIST_LLM_*"},
                         ensure_ascii=False))
        return 2 if args.require_real else 0
    ocr, _ = build_providers(offline=False)
    r = evaluate_samples(samples_dir, ocr)
    if args.output:
        target = Path(args.output)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(r, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(r, ensure_ascii=False))
    return 0 if r and r["complete"] and r["field_accuracy"] >= 0.95 else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
