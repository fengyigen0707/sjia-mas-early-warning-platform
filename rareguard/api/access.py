"""Small pilot access control: separate family credentials and signed sessions.

Credentials come from a private operator-managed file, never from patient IDs.
This is a pilot mechanism, not a public registration/identity verification system.
"""
import hashlib
import hmac
import json
import secrets
import threading
import time
from pathlib import Path

from fastapi import HTTPException, Request


class AccessControl:
    def __init__(self, accounts: dict, *, secure_cookie: bool = True):
        if not accounts:
            raise ValueError("At least one family account is required")
        self.accounts = accounts
        self.secure_cookie = secure_cookie
        self.key = secrets.token_bytes(32)
        self.sessions = {}
        self.lock = threading.Lock()
        self.failures = {}
        hashes = set()
        for user, record in accounts.items():
            digest = record.get("token_sha256", "")
            if (not user or len(user) > 64 or len(digest) != 64
                    or any(c not in "0123456789abcdef" for c in digest)
                    or digest in hashes):
                raise ValueError("Invalid account or duplicate credential")
            hashes.add(digest)

    @classmethod
    def from_file(cls, path, *, secure_cookie=True):
        accounts = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls(accounts, secure_cookie=secure_cookie)

    def authenticate(self, token):
        if not token or not 32 <= len(token) <= 512:
            return None
        digest = hashlib.sha256(token.encode()).hexdigest()
        for user, record in self.accounts.items():
            if hmac.compare_digest(digest, record["token_sha256"]):
                return user
        return None

    def login(self, token, client):
        now = time.time()
        with self.lock:
            self.failures = {k: v for k, v in self.failures.items() if v[1] > now}
            failures, until = self.failures.get(client, (0, now + 300))
            if failures >= 10:
                raise HTTPException(429, "尝试过多，请五分钟后重试")
            user = self.authenticate(token)
            if user is None:
                self.failures[client] = (failures + 1, until)
                raise HTTPException(401, "访问凭证无效")
            self.failures.pop(client, None)
            self.sessions = {k: v for k, v in self.sessions.items() if v[1] > now}
            session = secrets.token_urlsafe(32)
            self.sessions[session] = (user, now + 8 * 3600)
        signature = hmac.new(self.key, session.encode(), hashlib.sha256).hexdigest()
        return user, session + "." + signature

    def principal(self, request: Request):
        auth = request.headers.get("authorization", "")
        if auth.startswith("Bearer "):
            user = self.authenticate(auth[7:])
            if user:
                return user
        cookie = request.cookies.get("rg_session", "")
        session, _, sig = cookie.partition(".")
        expected = hmac.new(self.key, session.encode(), hashlib.sha256).hexdigest()
        with self.lock:
            record = self.sessions.get(session)
        if record and hmac.compare_digest(sig, expected) and record[1] > time.time():
            return record[0]
        raise HTTPException(401, "请先登录")

    def logout(self, request):
        session = request.cookies.get("rg_session", "").partition(".")[0]
        with self.lock:
            self.sessions.pop(session, None)


def same_origin_write(request: Request):
    """Block browser cross-site mutations, including session creation."""
    if request.method in {"GET", "HEAD", "OPTIONS"}:
        return
    if request.headers.get("sec-fetch-site") == "cross-site":
        raise HTTPException(403, "不允许跨站请求")
    origin = request.headers.get("origin")
    # Reverse proxies must forward the external Host and use HTTPS cookie mode.
    if origin and origin.rstrip("/") != str(request.base_url).rstrip("/"):
        raise HTTPException(403, "不允许跨站请求")
