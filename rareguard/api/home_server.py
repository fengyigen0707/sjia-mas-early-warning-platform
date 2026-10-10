"""家庭端服务装配：create_home_app + 静态页路由 + 启动入口。

启动（默认读 .env / 环境变量）：
  python -m rareguard.api.home_server               # 在线（魔搭 OpenAI 兼容端点）
  RAREGUARD_OFFLINE=1 python -m rareguard.api.home_server  # 断网演示（缓存 OCR+模板叙述）
"""
import os
from pathlib import Path

from fastapi.responses import FileResponse

from rareguard.api.home import create_home_app
from rareguard.data.store import Store
from rareguard.llm.offline_provider import build_providers
from rareguard.api.access import AccessControl

_STATIC = Path(__file__).parent / "static"


def create_served_app(store=None, offline: bool | None = None):
    if offline is None:
        offline = os.environ.get("RAREGUARD_OFFLINE") == "1"
    # Unauthenticated mode is deliberately limited to explicit synthetic demo.
    config = os.environ.get("RAREGUARD_ACCOUNTS_FILE")
    demo = os.environ.get("RAREGUARD_DEMO") == "1"
    if config:
        access = AccessControl.from_file(config, secure_cookie=os.environ.get("RAREGUARD_COOKIE_SECURE", "1") != "0")
    elif demo and offline:
        access = None
    else:
        raise RuntimeError("配置 RAREGUARD_ACCOUNTS_FILE；无认证演示须同时设置 RAREGUARD_DEMO=1 和 RAREGUARD_OFFLINE=1")
    own_store = store is None
    if own_store:
        db_path = os.environ.get("RAREGUARD_DB", "data/rareguard.db")
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        store = Store(db_path)
    ocr, narrate = build_providers(offline)
    app = create_home_app(store, ocr, narrate, access=access, cloud_ocr=not offline)
    app.state.store = store

    @app.get("/", include_in_schema=False)
    def home_page():
        return FileResponse(_STATIC / "home.html")

    return app


def main() -> None:
    import uvicorn

    host = os.environ.get("RAREGUARD_HOST", "127.0.0.1")
    if not os.environ.get("RAREGUARD_ACCOUNTS_FILE") and host != "127.0.0.1":
        raise RuntimeError("无认证演示只允许绑定 127.0.0.1")
    uvicorn.run(create_served_app(), host=host, port=8000, access_log=False)


if __name__ == "__main__":
    main()
