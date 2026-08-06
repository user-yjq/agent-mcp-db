"""安全回归（T-4.4）：HTTP 鉴权绕过尝试全部 401，且未授权请求不执行任何工具。"""

from __future__ import annotations

import httpx
import pytest
from starlette.responses import JSONResponse

from db_assistant_mcp.config import load_config
from db_assistant_mcp.security.http_auth import BearerTokenMiddleware
from db_assistant_mcp.server import build_http_app

TOKEN = "correct-horse-battery-staple"
BASE = "http://localhost:8000"


def _write_config(tmp_path) -> str:
    cfg = tmp_path / "config.toml"
    cfg.write_text(
        f"""
[audit]
output = "file"
path = "{tmp_path / 'audit.log'}"
[semantic]
[metrics]
enabled = false
[http]
token_env = "DB_ASSISTANT_HTTP_TOKEN"
[connections.demo]
type = "postgres"
host = "127.0.0.1"
port = 5432
database = "orders"
user = "svc"
""",
        encoding="utf-8",
    )
    return str(cfg)


@pytest.fixture
def http_app(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_ASSISTANT_HTTP_TOKEN", TOKEN)
    return build_http_app(load_config(_write_config(tmp_path)))


@pytest.mark.parametrize(
    "auth_header",
    [
        None,                          # 缺失
        "Bearer wrong",                # 错误 token
        "bearer correct-horse-battery-staple",  # 小写 scheme
        "Bearer  correct-horse-battery-staple",  # 多余空格
        "Bearer correct-horse-battery",  # 前缀截断
        "Token correct-horse-battery-staple",  # 错误 scheme
        "correct-horse-battery-staple",  # 无 scheme
        "Bearer correct-horse-battery-staple extra",  # 尾部追加
        "",                            # 空头
    ],
)
@pytest.mark.asyncio
async def test_http_auth_bypass_attempts_rejected(http_app, auth_header):
    headers = {"Authorization": auth_header} if auth_header is not None else {}
    transport = httpx.ASGITransport(app=http_app)
    async with httpx.AsyncClient(transport=transport, base_url=BASE) as client:
        resp = await client.post(
            "/mcp",
            headers=headers,
            json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        )
        assert resp.status_code == 401


@pytest.mark.asyncio
async def test_unauthorized_request_never_reaches_tool():
    """中间件在进入 MCP 前短路：未授权请求绝不执行任何工具/初始化。"""
    calls: list[str] = []

    async def spy_app(scope, receive, send):
        calls.append(scope["path"])
        response = JSONResponse({"ok": True})
        await response(scope, receive, send)

    wrapped = BearerTokenMiddleware(spy_app, TOKEN)
    transport = httpx.ASGITransport(app=wrapped)
    async with httpx.AsyncClient(transport=transport, base_url=BASE) as client:
        resp = await client.post(
            "/mcp",
            headers={"Authorization": "Bearer wrong"},
            json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        )
        assert resp.status_code == 401
    assert calls == []  # 内部 app 从未被调用


@pytest.mark.asyncio
async def test_correct_token_response_contains_no_token_echo(http_app):
    raw = http_app.app  # 中间件内部的 starlette app
    async with raw.router.lifespan_context(raw):
        transport = httpx.ASGITransport(app=http_app)
        async with httpx.AsyncClient(transport=transport, base_url=BASE) as client:
            resp = await client.post(
                "/mcp",
                headers={"Authorization": f"Bearer {TOKEN}", "Accept": "application/json, text/event-stream"},
                json={"jsonrpc": "2.0", "id": 1, "method": "initialize",
                      "params": {"protocolVersion": "2025-03-26", "capabilities": {},
                                 "clientInfo": {"name": "t", "version": "1"}}},
            )
            assert resp.status_code == 200
            assert TOKEN not in resp.text
