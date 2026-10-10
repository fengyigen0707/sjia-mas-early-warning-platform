"""家庭端 API：建档 / 每日打卡 / 化验单上传 / 风险查询+叙述。

本模块是仅有的 HTTP 装配层；风险结论仍由确定性规则引擎给出，
LLM 只在叙述层出现且强制过六层管道（narrate 内部保证）。
自主性定位 L1（光谱 / 响应 meta）：risk 附带 autonomy_level / decision_owner，不改判险。
"""
from datetime import date, timedelta
from typing import Annotated, Literal
import base64
import io
import secrets
from functools import wraps

from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field, field_validator
from PIL import Image

from rareguard.analysis.mas import assess_patient
from rareguard.analysis.normalize import normalize
from rareguard.api.summary import latest_labs, render_summary_html
from rareguard.ingest.ocr import commit_confirmed, parse_dual
from rareguard.narrate import narrate
from rareguard.ts.provider import SqliteTimeSeries
from rareguard.api.access import same_origin_write

PatientId = Annotated[str, Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")]
Profile = Literal["standard", "early"]
Criteria = Annotated[int, Query(ge=1, le=4)]


def valid_date(value):
    parsed = date.fromisoformat(value)
    if value != parsed.isoformat() or parsed < date(1900, 1, 1) or parsed > date.today():
        raise ValueError("日期须为有效的 YYYY-MM-DD，且不能在未来")
    return value


class PatientIn(BaseModel):
    pid: PatientId
    name: str = Field(min_length=1, max_length=80)
    dob: str

    _date = field_validator("dob")(valid_date)


class CheckinIn(BaseModel):
    pid: PatientId
    date: str
    temp: float = Field(ge=30, le=45, allow_inf_nan=False)
    symptoms: dict[str, Annotated[float, Field(ge=0, le=10, allow_inf_nan=False)]] = Field(default_factory=dict, max_length=32)

    _date = field_validator("date")(valid_date)


class LabUploadIn(BaseModel):
    pid: PatientId
    date: str
    image_b64: str = Field(min_length=1, max_length=7_000_000)
    cloud_consent: bool = False

    _date = field_validator("date")(valid_date)


class ManualLabItem(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    value: float = Field(ge=0, allow_inf_nan=False)
    unit: str = Field(default="", max_length=40)
    ref_low: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    ref_high: float | None = Field(default=None, ge=0, allow_inf_nan=False)


class ManualLabIn(BaseModel):
    pid: PatientId
    date: str
    items: list[ManualLabItem] = Field(min_length=1, max_length=100)

    _date = field_validator("date")(valid_date)


class LoginIn(BaseModel):
    token: str = Field(min_length=1, max_length=512)


def create_home_app(store, ocr_provider, narrate_provider, *, access=None, cloud_ocr=False) -> FastAPI:
    app = FastAPI(title="RareGuard 家庭端", docs_url=None if access else "/docs",
                  redoc_url=None if access else "/redoc",
                  openapi_url=None if access else "/openapi.json")
    ts = SqliteTimeSeries(store)

    def serialized(func):
        # Small single-worker pilot: ownership checks and reads/writes are one
        # critical section, preventing delete/recreate races and shared SQLite
        # transaction interference. Cloud requests serialize too; scale later.
        @wraps(func)
        def wrapped(*args, **kwargs):
            with store.lock:
                return func(*args, **kwargs)
        return wrapped

    @app.middleware("http")
    async def protect(request, call_next):
        try:
            same_origin_write(request)
            if request.url.path.startswith("/api/home/") and access:
                request.state.user = access.principal(request)
            if request.method in {"POST", "PUT", "PATCH"}:
                chunks, size = [], 0
                async for chunk in request.stream():
                    size += len(chunk)
                    if size > 8_000_000:
                        raise HTTPException(413, "上传内容过大")
                    chunks.append(chunk)
                request._body = b"".join(chunks)
            response = await call_next(request)
        except HTTPException as exc:
            response = JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Frame-Options"] = "DENY"
        return response

    def require_patient(request, pid):
        if access and not store.owns(pid, request.state.user):
            # Same response for absent IDs and other families' IDs.
            raise HTTPException(404, "未找到可访问的患儿档案")

    def query_date(value):
        try:
            return valid_date(value)
        except ValueError:
            raise HTTPException(422, "日期无效")

    @app.get("/api/auth/status")
    def auth_status(request: Request):
        if not access:
            return {"required": False, "demo": True}
        try:
            return {"required": True, "user": access.principal(request)}
        except HTTPException:
            return {"required": True, "user": None}

    @app.post("/api/auth/login")
    def login(body: LoginIn, request: Request, response: Response):
        if not access:
            raise HTTPException(404)
        user, cookie = access.login(body.token, request.client.host if request.client else "unknown")
        response.set_cookie("rg_session", cookie, httponly=True, secure=access.secure_cookie,
                            samesite="strict", max_age=8 * 3600, path="/")
        return {"user": user}

    @app.post("/api/auth/logout")
    def logout(request: Request, response: Response):
        if access:
            access.logout(request)
        response.delete_cookie("rg_session", path="/")
        return {"ok": True}

    @app.post("/api/home/patients", status_code=201)
    @serialized
    def add_patient(body: PatientIn, request: Request):
        if access:
            if not store.create_owned_patient(body.pid, body.name, body.dob, request.state.user):
                raise HTTPException(409, "编号不可用；已有档案请使用切换功能")
        else:
            store.add_patient(body.pid, body.name, body.dob)
        return {"ok": True}

    @app.get("/api/home/patients/{pid}")
    @serialized
    def get_patient(pid: PatientId, request: Request):
        require_patient(request, pid)
        rows = store.rows("SELECT pid,name,dob FROM patient WHERE pid=?", (pid,))
        if not rows:
            raise HTTPException(404, "未找到可访问的患儿档案")
        return dict(zip(("pid", "name", "dob"), rows[0]))

    @app.get("/api/home/export/{pid}")
    @serialized
    def export(pid: PatientId, request: Request):
        require_patient(request, pid)
        if not store.rows("SELECT 1 FROM patient WHERE pid=?", (pid,)):
            raise HTTPException(404)
        return store.export_patient(pid)

    @app.delete("/api/home/patients/{pid}")
    @serialized
    def delete_patient(pid: PatientId, request: Request):
        if not access:
            raise HTTPException(403, "演示模式不开放删除")
        require_patient(request, pid)
        store.delete_owned_patient(pid, request.state.user)
        return {"ok": True, "notice": "已删除活动数据库记录；备份与审计按运营留存政策处理"}

    @app.post("/api/home/checkin")
    @serialized
    def checkin(body: CheckinIn, request: Request):
        require_patient(request, body.pid)
        store.add_checkin(body.pid, body.date, body.temp, body.symptoms)
        return {"ok": True}

    @app.post("/api/home/labs/upload")
    @serialized
    def upload_lab(body: LabUploadIn, request: Request):
        require_patient(request, body.pid)
        if access:
            if cloud_ocr and not body.cloud_consent:
                raise HTTPException(422, "请先去除姓名等身份信息并确认云端识别授权，或使用手工录入")
            try:
                data = base64.b64decode(body.image_b64, validate=True)
                if len(data) > 5_000_000:
                    raise ValueError()
                with Image.open(io.BytesIO(data)) as image:
                    if image.format not in {"PNG", "JPEG"} or image.width * image.height > 20_000_000:
                        raise ValueError()
                    image.verify()
            except Exception:
                raise HTTPException(422, "请选择有效的 PNG/JPEG 化验单图片（不超过5MB、2000万像素）")
        try:
            items = parse_dual(body.image_b64, ocr_provider)
        except Exception:
            # OCR 不可用 → 前端转结构化手工录入表单兜底（spec §2 降级路径）
            return {"items": [], "committed": 0, "ocr_error": "unavailable"}
        if access:
            # Two parses can agree on the same mistake. Never auto-commit pilot OCR.
            items = [{**item, "needs_review": True} for item in items]
            committed = 0
        else:
            committed = commit_confirmed(store, body.pid, body.date, items)
        return {"items": items, "committed": committed}

    @app.post("/api/home/labs/manual")
    @serialized
    def manual_lab(body: ManualLabIn, request: Request):
        require_patient(request, body.pid)
        results = []
        for item in body.items:
            try:
                code, value = normalize(item.name, item.value, item.unit)
                ref_low = normalize(item.name, item.ref_low, item.unit)[1] if item.ref_low is not None else None
                ref_high = normalize(item.name, item.ref_high, item.unit)[1] if item.ref_high is not None else None
                if ref_low is not None and ref_high is not None and ref_low > ref_high:
                    raise ValueError("参考区间上下限倒置")
            except Exception as exc:
                results.append({"name": item.name, "code": None,
                                "ok": False, "reason": type(exc).__name__})
                continue
            store.add_lab(body.pid, body.date, code, value,
                          ref_low, ref_high, source="manual")
            results.append({"name": item.name, "code": code,
                            "ok": True, "reason": ""})
        return {"results": results}

    @app.get("/api/home/trend/{pid}")
    @serialized
    def trend(pid: PatientId, as_of: str, request: Request, days: Annotated[int, Query(ge=1, le=366)] = 90,
              profile: Profile = "standard", min_criteria: Criteria = 2):
        require_patient(request, pid)
        query_date(as_of)
        start = date.fromisoformat(as_of) - timedelta(days=days - 1)
        points = []
        for offset in range(days):
            day = (start + timedelta(days=offset)).isoformat()
            a = assess_patient(ts, pid, day, profile=profile,
                               min_criteria=min_criteria)
            points.append({"date": day, "score": a.score, "level": a.level})
        return {"points": points}

    @app.get("/api/home/risk/{pid}")
    @serialized
    def risk(pid: PatientId, as_of: str, request: Request,
             profile: Profile = "standard", min_criteria: Criteria = 2):
        require_patient(request, pid)
        query_date(as_of)
        assessment = assess_patient(ts, pid, as_of, profile=profile,
                                    min_criteria=min_criteria)
        text, meta = narrate(assessment, narrate_provider,
                             trace_id=f"home-{secrets.token_hex(12)}")
        return {"level": assessment.level, "score": assessment.score,
                "hits": [{"rule_id": h.rule_id, "name": h.name,
                          "level": h.level} for h in assessment.hits],
                "text": text, "meta": meta,
                "autonomy_level": "L1",
                "decision_owner": "rules_engine"}

    @app.get("/api/home/summary/{pid}")
    @serialized
    def summary(pid: PatientId, as_of: str, request: Request,
                profile: Profile = "standard", min_criteria: Criteria = 2):
        require_patient(request, pid)
        query_date(as_of)
        assessment = assess_patient(ts, pid, as_of, profile=profile,
                                    min_criteria=min_criteria)
        prow = store.rows("SELECT name FROM patient WHERE pid=?", (pid,))
        name = prow[0][0] if prow else pid
        start = date.fromisoformat(as_of) - timedelta(days=89)
        points = [{"date": (start + timedelta(days=o)).isoformat(),
                   "score": assess_patient(ts, pid,
                       (start + timedelta(days=o)).isoformat(), profile=profile,
                       min_criteria=min_criteria).score}
                  for o in range(90)]
        return HTMLResponse(render_summary_html(
            pid, name, as_of, assessment.level, assessment.score,
            [{"name": h.name, "level": h.level} for h in assessment.hits],
            points, latest_labs(store, pid, as_of)))

    return app
