"""Provision a private pilot credential; run locally and hand it to the family.

Prints a one-time secret only to the operator's local terminal. The accounts file
stores hashes, is never overwritten without checking the account, and is ignored
by git when kept under data/private/.
"""
import argparse
import hashlib
import json
import os
import re
import secrets
from pathlib import Path


def provision(path, user):
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", user):
        raise ValueError("账号须为1–64位英文、数字、下划线或连字符")
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    accounts = json.loads(path.read_text()) if path.exists() else {}
    if user in accounts:
        raise ValueError("账号已存在；凭证轮换须由运营单独操作")
    token = secrets.token_urlsafe(32)
    accounts[user] = {"token_sha256": hashlib.sha256(token.encode()).hexdigest()}
    temporary = path.with_name(path.name + "." + secrets.token_hex(8) + ".tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as out:
        json.dump(accounts, out, indent=2)
    os.replace(temporary, path)
    return token


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("user")
    parser.add_argument("--file", default="data/private/accounts.json")
    args = parser.parse_args()
    print("请私下交给该家庭，勿截图公开或提交仓库：\n" + provision(args.file, args.user))
