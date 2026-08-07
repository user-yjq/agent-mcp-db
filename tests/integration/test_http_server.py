"""HTTP 模式集成测试（T-3.1b/T-3.2/T-3.4）：in-process ASGITransport，无需真实 socket。

覆盖：鉴权 401、工具发现与调用、资源模板、健康检查/指标、并发请求、token 不进审计。
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
import pytest
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from db_assistant_mcp.config import load_config
from db_assistant_mcp.drivers.base import DatabaseConnection
from db_assistant_mcp.security.http_auth import BearerTokenMiddleware
from db_assistant_mcp.server import _attach_observability_routes, build_http_app, create_server

TOKEN = "test-token-for-http-tests"
BASE = "http://localhost:8000"

ALL_TOOLS = {
    "list_databases", "list_tables", "get_table_schema", "search_schema",
    "execute_query", "explain_query", "translate_sql", "refresh_schema", "ping",
}


def _write_config(tmp_path: Path, *, max_concurrent: int | None = None) -> Path:
    cfg = tmp_path / "config.toml"
    server_section = f"[server]\nmode = \"read_only\"\nmax_concurrent = {max_concurrent}\n" if max_concurrent else ""
    cfg.write_text(
        f"""
{server_section}
[audit]
output = "file"
path = "{tmp_path / 'audit.log'}"
[semantic]
[metrics]
enabled = false
[http]
token_env = "DB_ASSISTANT_HTTP_TOKEN"
host = "127.0.0.1"
port = 18000
[connections.demo]
type = "postgres"
host = "127.0.0.1"
port = 5432
database = "orders"
user = "svc"
connect_timeout_sec = 1
""",
        encoding="utf-8",
    )
    return cfg


@pytest.fixture
def http_app(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_ASSISTANT_HTTP_TOKEN", TOKEN)
    app_config = load_config(str(_write_config(tmp_path)))
    wrapped = build_http_app(app_config)
    return wrapped, wrapped.app, tmp_path


@asynccontextmanager
async def _session(http_app) -> AsyncIterator[ClientSession]:
    wrapped, raw, _tmp = http_app
    http_client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=wrapped),
        base_url=BASE,
        headers={"Authorization": f"Bearer {TOKEN}"},
        follow_redirects=True,
    )
    async with raw.router.lifespan_context(raw):
        async with streamable_http_client(f"{BASE}/mcp", http_client=http_client) as (read, write, _get_session_id):
            async with ClientSession(read, write) as session:
                await session.initialize()
                yield session


# ---------- 鉴权 ----------

@pytest.mark.asyncio
async def test_http_missing_or_wrong_token_401(http_app):
    wrapped, _raw, _tmp = http_app
    transport = httpx.ASGITransport(app=wrapped)
    payload = {
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}},
    }
    async with httpx.AsyncClient(transport=transport, base_url=BASE) as client:
        resp = await client.post("/mcp", json=payload)
        assert resp.status_code == 401
        resp = await client.post("/mcp", headers={"Authorization": "Bearer wrong"}, json=payload)
        assert resp.status_code == 401


@pytest.mark.asyncio
async def test_http_healthz_metrics_protected(http_app):
    wrapped, _raw, _tmp = http_app
    transport = httpx.ASGITransport(app=wrapped)
    async with httpx.AsyncClient(transport=transport, base_url=BASE) as client:
        assert (await client.get("/healthz")).status_code == 401
        assert (await client.get("/metrics")).status_code == 401
        headers = {"Authorization": f"Bearer {TOKEN}"}
        metrics = await client.get("/metrics", headers=headers)
        assert metrics.status_code == 200
        assert "promhttp" in metrics.text or "db_assistant" in metrics.text


# ---------- 工具/资源 e2e ----------

@pytest.mark.asyncio
async def test_http_tool_discovery_and_call(http_app):
    async with _session(http_app) as session:
        tools = await session.list_tools()
        names = {t.name for t in tools.tools}
        assert ALL_TOOLS <= names

        result = await session.call_tool("list_databases", {})
        text = result.content[0].text
        assert "demo" in text


@pytest.mark.asyncio
async def test_http_resource_templates(http_app):
    async with _session(http_app) as session:
        templates = await session.list_resource_templates()
        uris = {t.uriTemplate for t in templates.resourceTemplates}
        assert {"db://{name}/schema", "db://{name}/tables", "db://{name}/semantic"} <= uris


@pytest.mark.asyncio
async def test_http_concurrent_tool_calls(http_app):
    async with _session(http_app) as session:
        results = await asyncio.gather(*[session.call_tool("list_databases", {}) for _ in range(5)])
        for result in results:
            assert "demo" in result.content[0].text


# ---------- 安全 ----------

@pytest.mark.asyncio
async def test_http_token_not_leaked_to_audit(http_app):
    async with _session(http_app) as session:
        await session.call_tool("list_databases", {})
    _wrapped, _raw, tmp = http_app
    audit_path = tmp / "audit.log"
    if audit_path.exists():
        content = audit_path.read_text(encoding="utf-8")
        assert TOKEN not in content


@pytest.mark.asyncio
async def test_http_healthz_reports_unhealthy_when_db_unreachable(http_app):
    """demo 连接不可达时 /healthz 应返回 503 且 status=unhealthy（带 token 访问）。"""
    wrapped, _raw, _tmp = http_app
    transport = httpx.ASGITransport(app=wrapped)
    async with httpx.AsyncClient(transport=transport, base_url=BASE) as client:
        resp = await client.get("/healthz", headers={"Authorization": f"Bearer {TOKEN}"})
        assert resp.status_code == 503
        payload = resp.json()
        assert payload["status"] == "unhealthy"
        assert payload["connections"]["demo"]["ok"] is False


class _StubConn(DatabaseConnection):
    """可注入故障的假连接（仅用于健康检查相关行为验证）。"""

    dialect = "postgres"

    def __init__(self, *, ping_ok: bool = True) -> None:
        self.ping_ok = ping_ok

    async def close(self) -> None:
        pass

    async def connect(self) -> None:
        pass

    async def is_valid(self) -> bool:
        return True

    async def ping(self) -> float:
        if not self.ping_ok:
            raise ConnectionRefusedError("connection refused")
        return 1.0

    async def fetch(self, sql: str, timeout: float) -> tuple[list[str], list[list[Any]]]:
        return ["one"], [[1]]

    async def list_tables(self) -> list[dict[str, Any]]:
        return []

    async def table_schema(self, table: str) -> dict[str, Any]:
        return {"columns": [], "indexes": [], "foreign_keys": []}

    async def search_schema(self, keyword: str) -> dict[str, list[dict[str, str]]]:
        return {"tables": [], "columns": []}

    async def explain(self, sql: str, analyze: bool, timeout: float) -> dict[str, Any]:
        return {}


def _build_http_with_registry(tmp_path: Path, monkeypatch, *, max_concurrent: int | None = None):
    """按 build_http_app 同样装配，但暴露 registry 供测试注入连接故障。"""
    monkeypatch.setenv("DB_ASSISTANT_HTTP_TOKEN", TOKEN)
    app_config = load_config(str(_write_config(tmp_path, max_concurrent=max_concurrent)))
    mcp = create_server(app_config)
    app = mcp.streamable_http_app()
    _attach_observability_routes(app, mcp._registry)  # type: ignore[attr-defined]
    return BearerTokenMiddleware(app, TOKEN), mcp._registry  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_healthz_pool_exhausted_returns_503_with_detail(tmp_path, monkeypatch):
    """B-1：连接池耗尽时 /healthz 快速返回 503 且带 连接池耗尽 明细（不被池队列阻塞）。"""
    wrapped, registry = _build_http_with_registry(tmp_path, monkeypatch, max_concurrent=1)
    pool = registry.get("demo").pool
    await pool._sem.acquire()  # 占用唯一槽位模拟池耗尽
    try:
        transport = httpx.ASGITransport(app=wrapped)
        async with httpx.AsyncClient(transport=transport, base_url=BASE) as client:
            resp = await client.get("/healthz", headers={"Authorization": f"Bearer {TOKEN}"})
        assert resp.status_code == 503
        payload = resp.json()
        assert payload["status"] == "unhealthy"
        assert payload["connections"]["demo"]["ok"] is False
        assert "连接池耗尽" in payload["connections"]["demo"]["error"]
    finally:
        pool._sem.release()


@pytest.mark.asyncio
async def test_healthz_ping_failure_returns_503_with_detail(tmp_path, monkeypatch):
    """B-1：连接降级（ping 失败）时 /healthz 返回 503 且连接明细 ok=False。"""
    wrapped, registry = _build_http_with_registry(tmp_path, monkeypatch)
    pool = registry.get("demo").pool
    pool._make_conn = lambda: _StubConn(ping_ok=False)  # type: ignore[method-assign]

    transport = httpx.ASGITransport(app=wrapped)
    async with httpx.AsyncClient(transport=transport, base_url=BASE) as client:
        resp = await client.get("/healthz", headers={"Authorization": f"Bearer {TOKEN}"})
    assert resp.status_code == 503
    payload = resp.json()
    assert payload["status"] == "unhealthy"
    assert payload["connections"]["demo"]["ok"] is False
    assert "connection refused" in payload["connections"]["demo"]["error"]


@pytest.mark.asyncio
async def test_healthz_timeout_returns_503_with_message(tmp_path, monkeypatch):
    """B-1：健康检查超时（wait_for 到期）时返回 503 与明确提示，不挂起。"""
    wrapped, registry = _build_http_with_registry(tmp_path, monkeypatch)

    async def _slow_ping(name=None):
        raise TimeoutError("simulated health check timeout")

    monkeypatch.setattr(registry, "ping", _slow_ping)

    transport = httpx.ASGITransport(app=wrapped)
    async with httpx.AsyncClient(transport=transport, base_url=BASE) as client:
        resp = await client.get("/healthz", headers={"Authorization": f"Bearer {TOKEN}"})
    assert resp.status_code == 503
    payload = resp.json()
    assert payload["status"] == "unhealthy"
    assert payload["error"] == "health check timeout"
