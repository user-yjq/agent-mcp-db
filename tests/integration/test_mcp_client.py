"""T-7.1 MCP 客户端集成测试：真实 stdio 启动 server，验证工具发现与调用。"""

from __future__ import annotations

import json
import os
import sys

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from db_assistant_mcp.config import ConnectionConfig, save_connection


@pytest.fixture
def server_params(tmp_path):
    cfg = tmp_path / "config.toml"
    conn = ConnectionConfig(
        name="demo",
        type="postgres",
        host="127.0.0.1",
        port=5432,
        database="app",
        user="svc",
        password_env="DEMO_PW",
        mode="read_only",
    )
    save_connection(cfg, conn)
    os.environ["DEMO_PW"] = "x"
    return StdioServerParameters(
        command=sys.executable,
        args=["-m", "db_assistant_mcp"],
        env={**os.environ, "DB_ASSISTANT_CONFIG": str(cfg)},
    )


@pytest.mark.asyncio
async def test_tool_discovery_and_list_databases(server_params):
    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            names = {t.name for t in tools.tools}
            assert {
                "list_databases", "list_tables", "get_table_schema",
                "search_schema", "execute_query", "explain_query",
                "translate_sql", "refresh_schema", "ping",
            } <= names

            result = await session.call_tool("list_databases", {})
            text = result.content[0].text
            payload = json.loads(text)
            assert payload["databases"][0]["name"] == "demo"


@pytest.mark.asyncio
async def test_resource_templates_available(server_params):
    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            templates = await session.list_resource_templates()
            uris = [t.uriTemplate for t in templates.resourceTemplates]
            assert "db://{name}/schema" in uris
            assert "db://{name}/tables" in uris
            assert "db://{name}/semantic" in uris


@pytest.mark.asyncio
async def test_semantic_resource_readable(server_params):
    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.read_resource("db://demo/semantic")
            content = result.contents[0].text
            payload = json.loads(content)
            assert "terms" in payload


@pytest.mark.asyncio
async def test_ping_unreachable_returns_unhealthy(server_params):
    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool("ping", {"connection": "demo"})
            payload = json.loads(result.content[0].text)
            assert payload["healthy"] is False
            assert payload["connections"]["demo"]["ok"] is False


@pytest.mark.asyncio
async def test_unknown_connection_returns_structured_error(server_params):
    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool("execute_query", {
                "connection": "nope", "sql": "SELECT 1",
            })
            payload = json.loads(result.content[0].text)
            assert payload["error"]["error"] == "CONNECTION_FAILED"


@pytest.mark.asyncio
async def test_rejected_sql_returns_security_error(server_params):
    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool("execute_query", {
                "connection": "demo", "sql": "DROP TABLE users",
            })
            payload = json.loads(result.content[0].text)
            assert payload["error"]["error"] == "SECURITY_REJECTED"
            assert "DROP" not in payload["error"]["message"]
