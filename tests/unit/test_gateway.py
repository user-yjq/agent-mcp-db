"""安全网关：行数限制（LIMIT-001..003）、超时（TIMEOUT-001..002）、审计联动。"""

from __future__ import annotations

import asyncio
import re
from typing import Any

import pytest

from db_assistant_mcp.config import AuditConfig, ConnectionConfig, ServerConfig
from db_assistant_mcp.drivers.base import DatabaseConnection
from db_assistant_mcp.errors import QueryTimeoutError, SecurityRejectedError
from db_assistant_mcp.security.audit import AuditLogger
from db_assistant_mcp.security.gateway import SecurityGateway


class FakeConnection(DatabaseConnection):
    dialect = "postgres"

    def __init__(self, rows: list[list[Any]] | None = None, columns: list[str] | None = None,
                 sleep_sec: float = 0, fail_with: Exception | None = None) -> None:
        self.rows = rows or [[i, f"r{i}"] for i in range(200)]
        self.columns = columns or ["id", "name"]
        self.sleep_sec = sleep_sec
        self.fail_with = fail_with
        self.executed_sql: list[str] = []
        self.closed = False

    async def close(self) -> None:
        self.closed = True

    async def is_valid(self) -> bool:
        return not self.closed

    async def ping(self) -> float:
        return 1.0

    async def fetch(self, sql: str, timeout: float) -> tuple[list[str], list[list[Any]]]:
        self.executed_sql.append(sql)
        if self.fail_with:
            raise self.fail_with
        if self.sleep_sec:
            try:
                await asyncio.wait_for(asyncio.sleep(self.sleep_sec), timeout=timeout)
            except TimeoutError as exc:
                raise QueryTimeoutError("查询超时", detail="QUERY_TIMEOUT") from exc
        match = re.search(r"LIMIT\s+(\d+)", sql, re.IGNORECASE)
        limit = int(match.group(1)) if match else len(self.rows)
        return self.columns, self.rows[:limit]

    async def list_tables(self) -> list[dict[str, Any]]:
        return [{"name": "users", "estimated_rows": 10, "comment": None}]

    async def table_schema(self, table: str) -> dict[str, Any]:
        return {"table": table, "columns": [], "indexes": [], "foreign_keys": []}

    async def search_schema(self, keyword: str) -> dict[str, list[dict[str, str]]]:
        return {"tables": [], "columns": []}

    async def explain(self, sql: str, analyze: bool, timeout: float) -> dict[str, Any]:
        return {"format": "json", "analyze": analyze, "plan": {"Node Type": "Seq Scan"}}


class FakePool:
    def __init__(self, conn: FakeConnection) -> None:
        self.conn = conn

    async def run(self, op):
        return await op(self.conn)

    async def ping(self) -> dict[str, Any]:
        return {"ok": True, "latency_ms": 1.0, "dialect": "postgres"}

    async def close(self) -> None:
        pass


def _conn_config(**kwargs) -> ConnectionConfig:
    defaults = dict(
        name="test",
        type="postgres",
        host="h",
        port=5432,
        database="d",
        user="u",
        masked_columns=["phone"],
        exclude_columns=["salary"],
        exclude_tables=["audit_log"],
    )
    defaults.update(kwargs)
    return ConnectionConfig(**defaults)


def _gateway(fake: FakeConnection, audit_path, **server_kwargs) -> SecurityGateway:
    server = ServerConfig(**server_kwargs)
    audit = AuditLogger(AuditConfig(output="file", path=str(audit_path)))
    return SecurityGateway(_conn_config(), FakePool(fake), audit, server)


@pytest.mark.asyncio
async def test_limit_auto_append_and_truncate(tmp_path):  # LIMIT-001
    fake = FakeConnection()
    gw = _gateway(fake, tmp_path / "audit.log")
    result = await gw.execute_query("SELECT * FROM big_table")
    assert fake.executed_sql[0].endswith("LIMIT 101")
    assert result["row_count"] == 100
    assert result["truncated"] is True


@pytest.mark.asyncio
async def test_respect_user_limit(tmp_path):  # LIMIT-002
    fake = FakeConnection()
    gw = _gateway(fake, tmp_path / "audit.log")
    result = await gw.execute_query("SELECT * FROM big_table LIMIT 1")
    assert "LIMIT 1" in fake.executed_sql[0]
    assert result["row_count"] == 1
    assert result["truncated"] is False


@pytest.mark.asyncio
async def test_clamp_huge_limit(tmp_path):  # LIMIT-003
    fake = FakeConnection()
    gw = _gateway(fake, tmp_path / "audit.log")
    result = await gw.execute_query("SELECT * FROM big_table LIMIT 1000000")
    assert fake.executed_sql[0].endswith("LIMIT 101")
    assert result["truncated"] is True
    assert result["row_count"] == 100


@pytest.mark.asyncio
async def test_requested_limit_capped(tmp_path):
    fake = FakeConnection()
    gw = _gateway(fake, tmp_path / "audit.log", default_limit=50)
    result = await gw.execute_query("SELECT * FROM big_table", limit=99999)
    assert fake.executed_sql[0].endswith("LIMIT 51")
    assert result["row_count"] == 50


@pytest.mark.asyncio
async def test_slow_query_timeout(tmp_path):  # TIMEOUT-001
    fake = FakeConnection(sleep_sec=30)
    gw = _gateway(fake, tmp_path / "audit.log", query_timeout_sec=1)
    with pytest.raises(QueryTimeoutError):
        await gw.execute_query("SELECT count(*) FROM big_table")


@pytest.mark.asyncio
async def test_fast_query_within_timeout(tmp_path):  # TIMEOUT-002
    fake = FakeConnection(sleep_sec=0)
    gw = _gateway(fake, tmp_path / "audit.log", query_timeout_sec=10)
    result = await gw.execute_query("SELECT count(*) FROM big_table", limit=5)
    assert result["row_count"] == 5


@pytest.mark.asyncio
async def test_dangerous_query_rejected_before_execution(tmp_path):
    fake = FakeConnection()
    gw = _gateway(fake, tmp_path / "audit.log")
    with pytest.raises(SecurityRejectedError):
        await gw.execute_query("SELECT pg_sleep(20)")
    assert fake.executed_sql == []  # 未触达数据库


@pytest.mark.asyncio
async def test_redaction_applied_in_result(tmp_path):
    fake = FakeConnection(columns=["id", "phone", "salary"], rows=[[1, "138", 100]])
    gw = _gateway(fake, tmp_path / "audit.log")
    result = await gw.execute_query("SELECT * FROM users", limit=10)
    assert result["columns"] == ["id", "phone"]
    assert result["rows"] == [[1, "***"]]


@pytest.mark.asyncio
async def test_alias_and_qualified_redaction(tmp_path):
    # MASK-002: 别名引用 masked 列仍脱敏；MASK-003: 限定排除列不出现
    fake = FakeConnection(columns=["s", "p", "name"], rows=[[100, "138", "alice"]])
    gw = _gateway(fake, tmp_path / "audit.log")
    result = await gw.execute_query(
        "SELECT u.salary AS s, u.phone AS p, u.name FROM users u", limit=10
    )
    assert result["columns"] == ["p", "name"]
    assert result["rows"] == [["***", "alice"]]


@pytest.mark.asyncio
async def test_audit_written_for_success_and_rejection(tmp_path):
    audit_path = tmp_path / "audit.log"
    fake = FakeConnection()
    gw = _gateway(fake, audit_path)
    await gw.execute_query("SELECT * FROM users", limit=5)
    with pytest.raises(SecurityRejectedError):
        await gw.execute_query("DELETE FROM users")
    lines = audit_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    import json

    ok_entry = json.loads(lines[0])
    assert ok_entry["allowed"] is True
    assert ok_entry["tool"] == "execute_query"
    assert ok_entry["connection"] == "test"
    assert ok_entry["sql"] == "SELECT * FROM users"
    assert ok_entry["rows"] == 5
    assert "duration_ms" in ok_entry
    assert ok_entry["user"]

    rej_entry = json.loads(lines[1])
    assert rej_entry["allowed"] is False
    assert rej_entry["detail"]["code"] == "SECURITY_REJECTED"
