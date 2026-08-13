"""B-3 MCP 层重连 e2e：execute_query 在连接中断时自动重连（进程内，无需真实 DB/socket）。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from db_assistant_mcp.config import load_config
from db_assistant_mcp.drivers.base import DatabaseConnection
from db_assistant_mcp.server import create_server


class StubConn(DatabaseConnection):
    """可注入故障的假连接：首次 fetch 可抛连接中断。"""

    dialect = "postgres"

    def __init__(self, fail_first: bool = False) -> None:
        self.fail_first = fail_first
        self.calls = 0
        self.closed = False

    async def close(self) -> None:
        self.closed = True

    async def connect(self) -> None:
        self.closed = False

    async def is_valid(self) -> bool:
        return True

    async def ping(self) -> float:
        return 1.0

    async def fetch(self, sql: str, timeout: float) -> tuple[list[str], list[list[Any]]]:
        self.calls += 1
        if self.fail_first and self.calls == 1:
            raise ConnectionResetError("connection reset")
        return ["one"], [[1]]

    async def list_tables(self) -> list[dict[str, Any]]:
        return []

    async def table_schema(self, table: str) -> dict[str, Any]:
        return {"columns": [], "indexes": [], "foreign_keys": []}

    async def search_schema(self, keyword: str) -> dict[str, list[dict[str, str]]]:
        return {"tables": [], "columns": []}

    async def explain(self, sql: str, analyze: bool, timeout: float) -> dict[str, Any]:
        return {}


def _write_config(tmp_path: Path, *, audit_output: str = "stdout") -> Path:
    cfg = tmp_path / "config.toml"
    cfg.write_text(
        f"""
[audit]
output = "{audit_output}"
path = "{tmp_path / 'audit.log'}"
[semantic]
[metrics]
enabled = false
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


def _build_server(tmp_path: Path, *, audit_output: str = "stdout"):
    app_config = load_config(str(_write_config(tmp_path, audit_output=audit_output)))
    return create_server(app_config)


def _patch_pool(mcp, *, first_fails: bool, second_fails: bool = False):
    """把 demo 连接池的建连工厂替换为可注入故障的假连接，返回 (created 计数, first, second)。"""
    registry = mcp.registry
    pool = registry.get("demo").pool
    first = StubConn(fail_first=first_fails)
    second = StubConn(fail_first=second_fails)
    created = {"n": 0}

    def make():
        created["n"] += 1
        return first if created["n"] == 1 else second

    pool._make_conn = make  # type: ignore[method-assign]
    return created, first, second


@pytest.mark.asyncio
async def test_execute_query_reconnects_after_mid_query_reset(tmp_path):
    """连接中断（ConnectionResetError）时自动重建连接并成功返回，审计记录 allowed=True。"""
    mcp = _build_server(tmp_path, audit_output="file")
    created, first, second = _patch_pool(mcp, first_fails=True)

    result = await mcp.call_tool("execute_query", {"connection": "demo", "sql": "SELECT 1"})
    payload = json.loads(result[0].text)

    assert created["n"] == 2  # 首次连接中断后自动重建
    assert first.closed  # 失效连接被丢弃
    assert not second.closed
    assert payload["columns"] == ["one"]
    assert payload["rows"] == [[1]]
    assert payload["row_count"] == 1
    assert payload["truncated"] is False

    audit_path = tmp_path / "audit.log"
    entries = [json.loads(line) for line in audit_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(entries) == 1
    assert entries[0]["tool"] == "execute_query"
    assert entries[0]["allowed"] is True
    assert entries[0]["rows"] == 1


@pytest.mark.asyncio
async def test_execute_query_returns_structured_error_when_reconnect_fails(tmp_path):
    """重连也失败时返回结构化错误（不崩溃），审计记录 allowed=False。"""
    mcp = _build_server(tmp_path, audit_output="file")
    created, first, second = _patch_pool(mcp, first_fails=True, second_fails=True)

    result = await mcp.call_tool("execute_query", {"connection": "demo", "sql": "SELECT 1"})
    payload = json.loads(result[0].text)

    assert created["n"] == 2
    assert first.closed
    assert second.closed
    assert "error" in payload
    assert payload["error"]["message"] == "执行查询时发生内部错误"

    audit_path = tmp_path / "audit.log"
    entries = [json.loads(line) for line in audit_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(entries) == 1
    assert entries[0]["tool"] == "execute_query"
    assert entries[0]["allowed"] is False


@pytest.mark.asyncio
async def test_audit_stdout_downgraded_to_stderr_in_stdio(tmp_path, capsys):
    """C-3: stdio 模式下 audit output=stdout 自动降级 stderr，stdout 协议流不被污染。"""
    mcp = _build_server(tmp_path, audit_output="stdout")
    _patch_pool(mcp, first_fails=False)

    result = await mcp.call_tool("execute_query", {"connection": "demo", "sql": "SELECT 1"})
    assert json.loads(result[0].text)["row_count"] == 1

    captured = capsys.readouterr()
    assert captured.out == ""  # stdout 干净：只有 JSON-RPC 协议流
    assert "allowed" in captured.err  # 审计实际写入 stderr
