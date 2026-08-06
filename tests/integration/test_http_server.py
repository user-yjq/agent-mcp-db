"""HTTP 模式集成测试（T-3.1b/T-3.2/T-3.4）：in-process ASGITransport，无需真实 socket。

覆盖：鉴权 401、工具发现与调用、资源模板、健康检查/指标、并发请求、token 不进审计。
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import pytest
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from db_assistant_mcp.config import load_config
from db_assistant_mcp.server import build_http_app

TOKEN = "test-token-for-http-tests"
BASE = "http://localhost:8000"

ALL_TOOLS = {
    "list_databases", "list_tables", "get_table_schema", "search_schema",
    "execute_query", "explain_query", "translate_sql", "refresh_schema", "ping",
}


def _write_config(tmp_path: Path) -> Path:
    cfg = tmp_path / "config.toml"
    cfg.write_text(
        f"""
[server]
mode = "read_only"
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
