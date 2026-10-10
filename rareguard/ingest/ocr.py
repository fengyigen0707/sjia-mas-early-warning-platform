"""化验单 OCR 解析：LLM 输出强制 JSON 协议 + 双通道一致性校验。

同一张化验单独立解析两次，归一化后 (code, value) 不一致的条目置
needs_review，未经人工确认不得入库（spec §3.3）。
"""
import base64
import math

from rareguard.analysis.normalize import normalize, UnknownIndicator, UnitConversionError
from rareguard.llm.provider import extract_json

OCR_SYSTEM = "OCR-LAB"

_PROMPT = (
    f"{OCR_SYSTEM}\n你是化验单结构化解析器。仅输出 JSON：{{\"items\":["
    "{{\"name\":..., \"value\":数值, \"unit\":..., "
    "\"ref_low\":数值或null, \"ref_high\":数值或null}}]}}，"
    "包含化验单上全部检验项目，不要遗漏或编造。"
)


def parse_lab_report(image_b64: str, provider) -> list[dict]:
    messages = [
        {"role": "system", "content": _PROMPT},
        {"role": "user", "content": [
            {"type": "image_url",
             "image_url": {"url": f"data:image/png;base64,{image_b64}"}},
            {"type": "text", "text": "解析这张化验单"},
        ]},
    ]
    resp = provider.chat(messages)
    raw = extract_json(resp.text).get("items", [])
    return [_normalize_item(i) for i in raw]


def _normalize_item(item: dict) -> dict:
    def finite_number(value):
        try:
            number = float(value)
            return number if math.isfinite(number) and number >= 0 else None
        except (ValueError, TypeError):
            return None
    out = {"name": str(item.get("name", ""))[:100], "code": None, "value": None,
           "raw_value": finite_number(item.get("value")),
           "unit": str(item.get("unit", ""))[:40], "ref_low": finite_number(item.get("ref_low")),
           "ref_high": finite_number(item.get("ref_high")), "needs_review": True}
    try:
        code, value = normalize(str(item.get("name", "")),
                                float(item["value"]),
                                str(item.get("unit", "")))
        out.update(code=code, value=value, needs_review=False)
    except (UnknownIndicator, UnitConversionError, KeyError, TypeError, ValueError):
        pass
    return out


def parse_dual(image_b64: str, provider) -> list[dict]:
    a = parse_lab_report(image_b64, provider)
    b = parse_lab_report(image_b64, provider)
    b_index = {}
    for item in b:
        key = (item["code"] or item["name"])
        b_index.setdefault(key, []).append(item)
    merged = []
    for item in a:
        key = item["code"] or item["name"]
        pool = b_index.get(key, [])
        agree = pool and item["value"] is not None and any(
            abs(p["value"] - item["value"]) < 1e-6
            for p in pool if p["value"] is not None)
        merged.append({**item,
                       "needs_review": item["needs_review"] or not agree})
    return merged


def commit_confirmed(store, pid: str, date: str, items: list[dict]) -> int:
    n = 0
    for item in items:
        if item["needs_review"] or not item["code"]:
            continue
        store.add_lab(pid, date, item["code"], item["value"],
                      item["ref_low"], item["ref_high"], source="ocr")
        n += 1
    return n
