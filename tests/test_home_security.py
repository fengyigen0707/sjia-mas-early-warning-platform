"""Pilot safety regression: two families, failed auth, OCR review, and invalid input."""
import base64
import hashlib
import io
import json

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from rareguard.api.access import AccessControl
from rareguard.api.home import create_home_app
from rareguard.api.home_server import create_served_app
from rareguard.data.store import Store
from rareguard.llm.provider import LLMResponse

TOKEN_A = "test-family-a-credential-with-at-least-32-characters"
TOKEN_B = "test-family-b-credential-with-at-least-32-characters"


class OCR:
    def chat(self, messages):
        return LLMResponse(text=json.dumps({"items": [{"name": "甘油三酯", "value": 177.14, "unit": "mg/dL"}]}), model="mock")


@pytest.fixture
def secure(tmp_path):
    store = Store(str(tmp_path / "secure.db"))
    access = AccessControl({user: {"token_sha256": hashlib.sha256(token.encode()).hexdigest()}
                            for user, token in [("family-a", TOKEN_A), ("family-b", TOKEN_B)]}, secure_cookie=False)
    app = create_home_app(store, OCR(), None, access=access, cloud_ocr=True)
    with TestClient(app) as c:
        c.store = store
        c.headers["Authorization"] = "Bearer " + TOKEN_A
        assert c.post("/api/home/patients", json={"pid": "PA", "name": "测试", "dob": "2015-01-01"}).status_code == 201
        c.headers["Authorization"] = "Bearer " + TOKEN_B
        assert c.post("/api/home/patients", json={"pid": "PB", "name": "测试", "dob": "2015-01-01"}).status_code == 201
        c.headers["Authorization"] = "Bearer " + TOKEN_A
        yield c
    store.close()


@pytest.mark.parametrize("path", ["patients/PA", "risk/PA?as_of=2026-03-01", "trend/PA?as_of=2026-03-01", "summary/PA?as_of=2026-03-01", "export/PA"])
def test_no_auth_and_other_family_cannot_read(secure, path):
    secure.headers.pop("Authorization")
    assert secure.get("/api/home/" + path).status_code == 401
    secure.headers["Authorization"] = "Bearer " + TOKEN_B
    assert secure.get("/api/home/" + path).status_code == 404


@pytest.mark.parametrize("path,body", [
    ("checkin", {"pid":"PA", "date":"2026-03-01", "temp":38}),
    ("labs/manual", {"pid":"PA", "date":"2026-03-01", "items":[{"name":"crp", "value":10, "unit":"mg/L"}]}),
    ("labs/upload", {"pid":"PA", "date":"2026-03-01", "image_b64":"AAAA"}),
])
def test_other_family_cannot_write(secure, path, body):
    secure.headers["Authorization"] = "Bearer " + TOKEN_B
    assert secure.post("/api/home/" + path, json=body).status_code == 404


def test_existing_patient_cannot_be_overwritten_or_claimed(secure):
    body = {"pid":"PA", "name":"changed", "dob":"2010-01-01"}
    assert secure.post("/api/home/patients", json=body).status_code == 409
    secure.headers["Authorization"] = "Bearer " + TOKEN_B
    assert secure.post("/api/home/patients", json=body).status_code == 409
    secure.store.add_patient("OLD", "legacy", "2015-01-01")
    body["pid"] = "OLD"
    assert secure.post("/api/home/patients", json=body).status_code == 409


def test_session_login_logout_revokes_cookie(secure):
    secure.headers.pop("Authorization")
    r = secure.post("/api/auth/login", json={"token":TOKEN_A})
    assert r.status_code == 200
    assert "httponly" in r.headers["set-cookie"].lower()
    assert "samesite=strict" in r.headers["set-cookie"].lower()
    old = secure.cookies.get("rg_session")
    assert secure.get("/api/home/patients/PA").status_code == 200
    secure.post("/api/auth/logout")
    secure.cookies.set("rg_session", old)
    assert secure.get("/api/home/patients/PA").status_code == 401


def test_session_tampering_and_login_throttle(secure):
    secure.headers.pop("Authorization")
    secure.cookies.set("rg_session", "forged.signature")
    assert secure.get("/api/home/patients/PA").status_code == 401
    for _ in range(10):
        assert secure.post("/api/auth/login", json={"token":"wrong"}).status_code == 401
    assert secure.post("/api/auth/login", json={"token":TOKEN_A}).status_code == 429


def test_cross_site_writes_blocked_and_no_cache(secure):
    r = secure.post("/api/auth/login", headers={"Origin":"https://evil.example"}, json={"token":TOKEN_A})
    assert r.status_code == 403
    r = secure.delete("/api/home/patients/PA", headers={"Sec-Fetch-Site":"cross-site"})
    assert r.status_code == 403
    assert secure.get("/api/home/patients/PA").headers["cache-control"] == "no-store"
    assert secure.get("/docs").status_code == 404


def test_export_delete_and_other_family_retained(secure):
    secure.post("/api/home/checkin", json={"pid":"PA", "date":"2026-03-01", "temp":38})
    secure.post("/api/home/labs/manual", json={"pid":"PA", "date":"2026-03-01", "items":[{"name":"crp", "value":10, "unit":"mg/L"}]})
    assert len(secure.get("/api/home/export/PA").json()["labs"]) == 1
    secure.headers["Authorization"] = "Bearer " + TOKEN_B
    assert secure.delete("/api/home/patients/PA").status_code == 404
    secure.headers["Authorization"] = "Bearer " + TOKEN_A
    assert secure.delete("/api/home/patients/PA").status_code == 200
    assert secure.get("/api/home/export/PA").status_code == 404
    assert secure.store.rows("SELECT * FROM lab WHERE pid='PA'") == []
    assert secure.store.rows("SELECT * FROM checkin WHERE pid='PA'") == []
    assert secure.store.owns("PB", "family-b")


def test_ocr_requires_consent_valid_image_and_human_confirmation(secure):
    buf = io.BytesIO()
    Image.new("RGB", (20,20)).save(buf, format="PNG")
    body = {"pid":"PA", "date":"2026-03-01", "image_b64":base64.b64encode(buf.getvalue()).decode()}
    assert secure.post("/api/home/labs/upload", json=body).status_code == 422
    body["cloud_consent"] = True
    r = secure.post("/api/home/labs/upload", json=body)
    assert r.status_code == 200
    item = r.json()["items"][0]
    assert item["value"] == pytest.approx(2)
    assert item["raw_value"] == 177.14
    assert item["needs_review"] and r.json()["committed"] == 0
    assert not secure.store.rows("SELECT * FROM lab")
    r = secure.post("/api/home/labs/manual", json={"pid":"PA", "date":"2026-03-01", "items":[{"name":item["name"], "value":item["raw_value"], "unit":item["unit"]}]})
    assert r.json()["results"][0]["ok"]
    assert secure.store.rows("SELECT value FROM lab")[0][0] == pytest.approx(2)
    body["image_b64"] = "AAAA"
    assert secure.post("/api/home/labs/upload", json=body).status_code == 422


@pytest.mark.parametrize("query", ["as_of=bad", "as_of=2026-02-30", "as_of=9999-01-01", "as_of=2026-03-01&days=0", "as_of=2026-03-01&days=1000000", "as_of=2026-03-01&profile=unknown", "as_of=2026-03-01&min_criteria=0"])
def test_invalid_query_rejected(secure, query):
    assert secure.get("/api/home/trend/PA?" + query).status_code == 422


@pytest.mark.parametrize("override", [{"temp":100}, {"temp":-1}, {"symptoms":{"pain":11}}, {"date":"2026-02-30"}])
def test_invalid_checkin_rejected(secure, override):
    body = {"pid":"PA", "date":"2026-03-01", "temp":38, **override}
    assert secure.post("/api/home/checkin", json=body).status_code == 422


def test_summary_escapes_patient_name(secure):
    secure.store.conn.execute("UPDATE patient SET name=? WHERE pid='PA'", ('<script>alert(1)</script>',))
    secure.store.conn.commit()
    html = secure.get("/api/home/summary/PA?as_of=2026-03-01").text
    assert '<script>alert(1)</script>' not in html
    assert '&lt;script&gt;' in html


def test_zero_values_do_not_crash_trends(secure):
    for d,v in [("2026-03-01",0),("2026-03-02",40),("2026-03-03",50)]:
        secure.store.add_lab("PA", d, "crp", v, None, None)
    assert secure.get("/api/home/risk/PA?as_of=2026-03-03").status_code == 200


def test_server_refuses_implicit_unprotected_mode(tmp_path, monkeypatch):
    monkeypatch.delenv("RAREGUARD_ACCOUNTS_FILE", raising=False)
    monkeypatch.delenv("RAREGUARD_DEMO", raising=False)
    with pytest.raises(RuntimeError):
        create_served_app(offline=True)


def test_large_request_body_is_rejected_before_parsing(secure):
    assert secure.post('/api/home/checkin', content=b'x'*8_000_001).status_code == 413


def test_reference_interval_uses_same_unit_conversion(secure):
    r=secure.post('/api/home/labs/manual', json={'pid':'PA','date':'2026-03-01','items':[{'name':'crp','value':2,'unit':'mg/dL','ref_low':0,'ref_high':1}]})
    assert r.json()['results'][0]['ok']
    assert secure.store.rows("SELECT value,ref_low,ref_high FROM lab WHERE pid='PA'") == [(20,0,10)]


def test_account_creation_stores_hash_only_and_refuses_overwrite(tmp_path):
    from scripts.create_family_account import provision
    path=tmp_path/'accounts.json'
    token=provision(path,'family-a')
    assert token not in path.read_text()
    assert AccessControl.from_file(path).authenticate(token)=='family-a'
    with pytest.raises(ValueError):
        provision(path,'family-a')


def test_nonfinite_ocr_values_are_safe_review_items():
    from rareguard.ingest.ocr import _normalize_item
    item=_normalize_item({'name':'crp','value':'NaN','unit':'mg/L','ref_high':float('inf')})
    assert item['needs_review'] and item['raw_value'] is None and item['ref_high'] is None
    json.dumps(item, allow_nan=False)
