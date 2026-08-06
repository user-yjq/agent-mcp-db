"""驱动层 EXPLAIN 解析回归：数据库返回的 JSON 是字符串，驱动须解析为树。"""

from __future__ import annotations

import pytest

from db_assistant_mcp.config import ConnectionConfig
from db_assistant_mcp.drivers.mysql import MysqlConnection
from db_assistant_mcp.drivers.postgres import PostgresConnection

PG_JSON = '[{"Plan": {"Node Type": "Seq Scan", "Relation Name": "t", "Plan Rows": 1}}]'
MYSQL_JSON = '{"query_block": {"select_id": 1, "table": {"table_name": "t1", "access_type": "ALL"}}}'


def _pg_config() -> ConnectionConfig:
    return ConnectionConfig(name="pg", type="postgres", host="h", port=5432, database="d", user="u")


def _mysql_config() -> ConnectionConfig:
    return ConnectionConfig(name="my", type="mysql", host="h", port=3306, database="d", user="u")


@pytest.mark.asyncio
async def test_postgres_explain_parses_json_string(monkeypatch):
    conn = PostgresConnection(_pg_config())

    async def fake_fetch(sql: str, timeout: float):
        return (["QUERY PLAN"], [[PG_JSON]])

    monkeypatch.setattr(conn, "fetch", fake_fetch)
    plan = await conn.explain("SELECT 1", analyze=False, timeout=5)
    assert plan["format"] == "json"
    assert plan["plan"]["Plan"]["Node Type"] == "Seq Scan"
    assert plan["plan"]["Plan"]["Relation Name"] == "t"


@pytest.mark.asyncio
async def test_postgres_explain_unparseable_keeps_raw(monkeypatch):
    conn = PostgresConnection(_pg_config())

    async def fake_fetch(sql: str, timeout: float):
        return (["QUERY PLAN"], [["not-json"]])

    monkeypatch.setattr(conn, "fetch", fake_fetch)
    plan = await conn.explain("SELECT 1", analyze=False, timeout=5)
    assert plan["plan"] == "not-json"


@pytest.mark.asyncio
async def test_mysql_explain_parses_json_string(monkeypatch):
    conn = MysqlConnection(_mysql_config())
    conn._conn = object()  # type: ignore[attr-defined]

    async def fake_fetch(sql: str, timeout: float):
        return (["EXPLAIN"], [[MYSQL_JSON]])

    monkeypatch.setattr(conn, "fetch", fake_fetch)
    plan = await conn.explain("SELECT 1", analyze=False, timeout=5)
    assert plan["format"] == "json"
    assert plan["plan"]["query_block"]["table"]["table_name"] == "t1"


@pytest.mark.asyncio
async def test_mysql_explain_falls_back_to_text(monkeypatch):
    conn = MysqlConnection(_mysql_config())
    conn._conn = object()  # type: ignore[attr-defined]

    async def fake_fetch(sql: str, timeout: float):
        if "FORMAT=JSON" in sql:
            raise RuntimeError("FORMAT=JSON unsupported")
        return (["id"], [[1]])

    monkeypatch.setattr(conn, "fetch", fake_fetch)
    plan = await conn.explain("SELECT 1", analyze=False, timeout=5)
    assert plan["format"] == "text"
    assert plan["plan"] == [[1]]
