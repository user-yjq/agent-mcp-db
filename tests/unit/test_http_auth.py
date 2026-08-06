"""HTTP 鉴权：T-3.2 Bearer token（fail-closed / 恒定时间比较 / stdio 不受影响）。"""

from __future__ import annotations

import pytest
from starlette.responses import JSONResponse

from db_assistant_mcp.config import HttpConfig, load_config
from db_assistant_mcp.errors import ConfigError
from db_assistant_mcp.security.http_auth import BearerTokenMiddleware, resolve_http_token

# ---------- token 解析（fail-closed） ----------

def test_token_env_missing_rejected():
    with pytest.raises(ConfigError) as exc:
        resolve_http_token(HttpConfig(token_env=None))
    assert exc.value.detail == "HTTP_TOKEN_ENV_MISSING"


def test_token_env_unset_rejected(monkeypatch):
    monkeypatch.delenv("DB_ASSISTANT_HTTP_TOKEN", raising=False)
    with pytest.raises(ConfigError) as exc:
        resolve_http_token(HttpConfig(token_env="DB_ASSISTANT_HTTP_TOKEN"))
    assert exc.value.detail == "HTTP_TOKEN_EMPTY"


def test_token_env_empty_rejected(monkeypatch):
    monkeypatch.setenv("DB_ASSISTANT_HTTP_TOKEN", "   ")
    with pytest.raises(ConfigError):
        resolve_http_token(HttpConfig(token_env="DB_ASSISTANT_HTTP_TOKEN"))


def test_token_resolved_from_env(monkeypatch):
    monkeypatch.setenv("DB_ASSISTANT_HTTP_TOKEN", "s3cr3t")
    assert resolve_http_token(HttpConfig(token_env="DB_ASSISTANT_HTTP_TOKEN")) == "s3cr3t"


def test_token_never_written_to_config_object():
    """token 只存在于运行期环境，不进入 HttpConfig 对象（不落盘）。"""
    cfg = HttpConfig(token_env="DB_ASSISTANT_HTTP_TOKEN")
    assert not hasattr(cfg, "token")
    assert cfg.token_env == "DB_ASSISTANT_HTTP_TOKEN"


# ---------- 中间件 ----------

async def _call(app, scope):
    """发送 http scope 请求，返回 (status, body)。"""
    messages: list[dict] = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        messages.append(message)

    await app(scope, receive, send)
    status = next(m["status"] for m in messages if m["type"] == "http.response.start")
    body = b"".join(m.get("body", b"") for m in messages if m["type"] == "http.response.body")
    return status, body


async def _ok_app(scope, receive, send):
    response = JSONResponse({"ok": True})
    await response(scope, receive, send)


@pytest.mark.asyncio
async def test_missing_authorization_401():
    app = BearerTokenMiddleware(_ok_app, "secret")
    scope = {"type": "http", "method": "POST", "path": "/mcp", "headers": []}
    status, _ = await _call(app, scope)
    assert status == 401


@pytest.mark.asyncio
async def test_wrong_token_401():
    app = BearerTokenMiddleware(_ok_app, "secret")
    scope = {
        "type": "http", "method": "POST", "path": "/mcp",
        "headers": [(b"authorization", b"Bearer wrong")],
    }
    status, body = await _call(app, scope)
    assert status == 401
    assert b"UNAUTHORIZED" in body


@pytest.mark.asyncio
async def test_correct_token_passes():
    app = BearerTokenMiddleware(_ok_app, "secret")
    scope = {
        "type": "http", "method": "POST", "path": "/mcp",
        "headers": [(b"authorization", b"Bearer secret")],
    }
    status, _ = await _call(app, scope)
    assert status == 200


@pytest.mark.asyncio
async def test_non_http_scope_passthrough():
    """lifespan 等非 HTTP scope 不受鉴权影响（stdio/生命周期不受影响）。"""
    entered = []

    async def lifespan_app(scope, receive, send):
        entered.append(scope["type"])
        await send({"type": "lifespan.startup.complete"})

    wrapped = BearerTokenMiddleware(lifespan_app, "secret")
    await wrapped({"type": "lifespan", "asgi": {"version": "3.0"}}, _noop_receive, _noop_send)
    assert entered == ["lifespan"]


async def _noop_receive():
    return {"type": "lifespan.startup"}


async def _noop_send(message):
    pass


# ---------- 配置解析 ----------

def _write(tmp_path, body: str):
    path = tmp_path / "config.toml"
    path.write_text(body, encoding="utf-8")
    return path


def test_http_section_defaults(tmp_path):
    path = _write(
        tmp_path,
        """
[connections.pg]
type = "postgres"
host = "h"
database = "d"
user = "u"
""",
    )
    cfg = load_config(str(path))
    assert cfg.http.token_env is None
    assert cfg.http.host == "127.0.0.1"
    assert cfg.http.port == 8000


def test_http_section_parsed(tmp_path):
    path = _write(
        tmp_path,
        """
[http]
token_env = "DB_ASSISTANT_HTTP_TOKEN"
host = "0.0.0.0"
port = 9000
[connections.pg]
type = "postgres"
host = "h"
database = "d"
user = "u"
""",
    )
    cfg = load_config(str(path))
    assert cfg.http.token_env == "DB_ASSISTANT_HTTP_TOKEN"
    assert cfg.http.host == "0.0.0.0"
    assert cfg.http.port == 9000


def test_http_port_type_mismatch(tmp_path):
    path = _write(
        tmp_path,
        """
[http]
port = "eight-thousand"
[connections.pg]
type = "postgres"
host = "h"
database = "d"
user = "u"
""",
    )
    with pytest.raises(ConfigError, match="整数"):
        load_config(str(path))


def test_http_empty_token_env_rejected(tmp_path):
    path = _write(
        tmp_path,
        """
[http]
token_env = ""
[connections.pg]
type = "postgres"
host = "h"
database = "d"
user = "u"
""",
    )
    with pytest.raises(ConfigError, match="token_env"):
        load_config(str(path))
