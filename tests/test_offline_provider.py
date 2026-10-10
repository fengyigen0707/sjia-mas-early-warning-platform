import json

import pytest
from fastapi.testclient import TestClient

from rareguard.api.home_server import create_served_app
from rareguard.data.store import Store
from rareguard.llm.offline_provider import (CachedOCR, OfflineNarrate,
                                            RecordingOCR)
from rareguard.llm.provider import BaseLLMProvider, LLMResponse


def _msgs(image_b64):
    return [
        {"role": "system", "content": "OCR-LAB"},
        {"role": "user", "content": [
            {"type": "image_url",
             "image_url": {"url": f"data:image/png;base64,{image_b64}"}},
            {"type": "text", "text": "解析这张化验单"},
        ]},
    ]


class InnerOCR(BaseLLMProvider):
    name = "inner"

    def chat(self, messages, tools=None):
        return LLMResponse(text=json.dumps({"items": []}), model=self.name)


def test_recording_writes_cache_and_cached_reads(tmp_path):
    d = str(tmp_path)
    inner = InnerOCR()
    r1 = RecordingOCR(inner, d).chat(_msgs("AAAA"))
    cached = CachedOCR(d)
    r2 = cached.chat(_msgs("AAAA"))
    assert r2.text == r1.text
    with pytest.raises(RuntimeError):
        cached.chat(_msgs("BBBB"))


def test_offline_narrate_always_raises():
    with pytest.raises(RuntimeError):
        OfflineNarrate().chat([{"role": "user", "content": "x"}])


def test_served_app_offline_end_to_end(tmp_path, monkeypatch):
    monkeypatch.setenv("RAREGUARD_DEMO", "1")
    store = Store(str(tmp_path / "s.db"))
    app = create_served_app(store=store, offline=True)
    with TestClient(app) as c:
        assert c.get("/").status_code == 200
        c.post("/api/home/patients",
               json={"pid": "P99", "name": "小明", "dob": "2015-01-01"})
        up = c.post("/api/home/labs/upload", json={
            "pid": "P99", "date": "2026-03-01", "image_b64": "AAAA"})
        assert up.json()["ocr_error"] == "unavailable"
        c.post("/api/home/labs/manual", json={
            "pid": "P99", "date": "2026-03-01",
            "items": [{"name": "血清铁蛋白", "value": 1200,
                       "unit": "ng/mL"}]})
        risk = c.get("/api/home/risk/P99?as_of=2026-03-01").json()
        assert risk["level"] in ("yellow", "red")
        assert risk["meta"]["source"] == "template"
        assert "医生诊断" in risk["text"]
    store.close()


def test_home_page_contains_four_screens(tmp_path, monkeypatch):
    monkeypatch.setenv("RAREGUARD_DEMO", "1")
    store = Store(str(tmp_path / "p.db"))
    app = create_served_app(store=store, offline=True)
    with TestClient(app) as c:
        html = c.get("/").text
    for s in ("仪表盘", "拍照录入", "每日打卡", "预警详情", "请以医生诊断为准"):
        assert s in html
    assert "URLSearchParams" in html  # ?pid= 深链：可分享/无头取证自动选中患儿
    assert "https://" not in html and "http://" not in html  # 零外部依赖
    store.close()
