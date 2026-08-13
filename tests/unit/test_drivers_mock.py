"""驱动层 mock 单测：不依赖真实数据库，覆盖 connect/健康检查/查询/introspection 路径。

真实库行为由 tests/integration/test_real_db.py 覆盖；此处用假连接对象补齐
错误映射与结果整形逻辑的单元覆盖。
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import uuid4

import asyncpg
import pytest
from pymysql import err as pymysql_err

from db_assistant_mcp.config import ConnectionConfig
from db_assistant_mcp.drivers.mysql import MysqlConnection
from db_assistant_mcp.drivers.postgres import PostgresConnection
from db_assistant_mcp.errors import ConnectionError_, QueryTimeoutError


def _pg_config() -> ConnectionConfig:
    return ConnectionConfig(name="pg", type="postgres", host="h", port=5432, database="d", user="u")


def _mysql_config() -> ConnectionConfig:
    return ConnectionConfig(name="my", type="mysql", host="h", port=3306, database="d", user="u")


# ---------- PostgreSQL ----------


class FakePgConn:
    """asyncpg.Connection 假对象：fetch/fetchval 按 SQL 标记路由结果。"""

    def __init__(self) -> None:
        self.fetch_plans: list[tuple[str, list[dict[str, Any]]]] = []
        self.fetchval_result: Any = None
        self.closed = False
        self.calls: list[str] = []

    async def fetch(self, sql: str, *args: Any) -> list[dict[str, Any]]:
        self.calls.append(sql)
        for marker, rows in self.fetch_plans:
            if marker in sql:
                return rows
        return []

    async def fetchval(self, sql: str, *args: Any) -> Any:
        self.calls.append(sql)
        return self.fetchval_result

    async def close(self) -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_pg_connect_success(monkeypatch):
    fake = FakePgConn()

    async def fake_connect(**kwargs):
        return fake

    monkeypatch.setattr("db_assistant_mcp.drivers.postgres.asyncpg.connect", fake_connect)
    conn = PostgresConnection(_pg_config())
    await conn.connect()
    assert conn._closed is False
    assert conn._conn is fake


@pytest.mark.asyncio
async def test_pg_connect_timeout(monkeypatch):
    async def fake_connect(**kwargs):
        raise TimeoutError()

    monkeypatch.setattr("db_assistant_mcp.drivers.postgres.asyncpg.connect", fake_connect)
    conn = PostgresConnection(_pg_config())
    with pytest.raises(ConnectionError_) as exc:
        await conn.connect()
    assert "超时" in exc.value.message
    assert exc.value.detail == "CONNECT_TIMEOUT"


@pytest.mark.asyncio
async def test_pg_connect_auth_and_catalog_errors(monkeypatch):
    async def raise_auth(**kwargs):
        raise asyncpg.InvalidPasswordError("bad password")

    async def raise_catalog(**kwargs):
        raise asyncpg.InvalidCatalogNameError("no such db")

    monkeypatch.setattr("db_assistant_mcp.drivers.postgres.asyncpg.connect", raise_auth)
    conn = PostgresConnection(_pg_config())
    with pytest.raises(ConnectionError_) as exc:
        await conn.connect()
    assert "认证失败" in exc.value.message

    monkeypatch.setattr("db_assistant_mcp.drivers.postgres.asyncpg.connect", raise_catalog)
    with pytest.raises(ConnectionError_) as exc:
        await conn.connect()
    assert "数据库不存在" in exc.value.message


@pytest.mark.asyncio
async def test_pg_connect_oserror(monkeypatch):
    async def raise_os(**kwargs):
        raise OSError("connection refused")

    monkeypatch.setattr("db_assistant_mcp.drivers.postgres.asyncpg.connect", raise_os)
    conn = PostgresConnection(_pg_config())
    with pytest.raises(ConnectionError_) as exc:
        await conn.connect()
    assert exc.value.detail == "CONNECT_FAILED:pg"


@pytest.mark.asyncio
async def test_pg_close_and_is_valid(monkeypatch):
    fake = FakePgConn()
    async def fake_connect(**k):
        return fake

    monkeypatch.setattr("db_assistant_mcp.drivers.postgres.asyncpg.connect", fake_connect)
    conn = PostgresConnection(_pg_config())
    await conn.connect()

    assert await conn.is_valid() is True
    await conn.close()
    assert fake.closed and conn._closed
    assert await conn.is_valid() is False

    async def boom(*a, **k):
        raise RuntimeError("disconnected")

    monkeypatch.setattr(fake, "fetchval", boom)
    await conn.connect()
    assert await conn.is_valid() is False


@pytest.mark.asyncio
async def test_pg_ping(monkeypatch):
    fake = FakePgConn()
    async def fake_connect(**k):
        return fake

    monkeypatch.setattr("db_assistant_mcp.drivers.postgres.asyncpg.connect", fake_connect)
    conn = PostgresConnection(_pg_config())
    with pytest.raises(ConnectionError_):
        await conn.ping()
    await conn.connect()
    latency = await conn.ping()
    assert isinstance(latency, float)


@pytest.mark.asyncio
async def test_pg_fetch_normalizes_values(monkeypatch):
    fake = FakePgConn()
    async def fake_connect(**k):
        return fake

    monkeypatch.setattr("db_assistant_mcp.drivers.postgres.asyncpg.connect", fake_connect)
    conn = PostgresConnection(_pg_config())
    conn._conn = fake

    class Row:
        def __init__(self, **kw):
            self._data = kw

        def keys(self):
            return ["id", "amount", "ts", "raw", "meta"]

        def __getitem__(self, key):
            return self._data[key]

        def __iter__(self):
            return iter(self._data.values())

    uid = uuid4()
    row = Row(
        id=1,
        amount=Decimal("12.50"),
        ts=datetime(2026, 1, 1, tzinfo=UTC),
        raw=b"\x01\x02",
        meta={"k": Decimal("1"), "nested": [uid]},
    )
    fake.fetch_plans.append(("SELECT", [row]))
    columns, rows = await conn.fetch("SELECT 1", timeout=5)
    assert columns == ["id", "amount", "ts", "raw", "meta"]
    assert rows[0] == [1, 12.5, "2026-01-01T00:00:00+00:00", "0102", {"k": 1.0, "nested": [str(uid)]}]


@pytest.mark.asyncio
async def test_pg_fetch_no_conn_and_timeout(monkeypatch):
    conn = PostgresConnection(_pg_config())
    with pytest.raises(ConnectionError_):
        await conn.fetch("SELECT 1", timeout=5)

    fake = FakePgConn()
    async def fake_connect(**k):
        return fake

    monkeypatch.setattr("db_assistant_mcp.drivers.postgres.asyncpg.connect", fake_connect)
    await conn.connect()

    async def slow(*a, **k):
        await asyncio.sleep(10)

    fake.fetch = slow  # type: ignore[method-assign]
    with pytest.raises(QueryTimeoutError) as exc:
        await conn.fetch("SELECT pg_sleep(9)", timeout=0.05)
    assert exc.value.detail == "QUERY_TIMEOUT"


@pytest.mark.asyncio
async def test_pg_list_tables(monkeypatch):
    fake = FakePgConn()
    fake.fetch_plans.append(
        ("pg_class", [
            {"name": "orders", "estimated_rows": 100, "comment": "订单", "kind": "table"},
            {"name": "v_active", "estimated_rows": 0, "comment": None, "kind": "view"},
        ]),
    )
    conn = PostgresConnection(_pg_config())
    conn._conn = fake
    tables = await conn.list_tables()
    assert tables[0] == {"name": "orders", "estimated_rows": 100, "comment": "订单", "kind": "table"}
    assert tables[1]["kind"] == "view"


@pytest.mark.asyncio
async def test_pg_table_schema(monkeypatch):
    fake = FakePgConn()
    fake.fetch_plans = [
        ("information_schema.columns", [
            {"column_name": "id", "data_type": "integer", "udt_name": "int4",
             "is_nullable": "NO", "column_default": None, "comment": None},
            {"column_name": "email", "data_type": "text", "udt_name": "text",
             "is_nullable": "YES", "column_default": None, "comment": "邮箱"},
        ]),
        ("pg_indexes", [{"indexname": "idx_email", "indexdef": "CREATE INDEX ..."}]),
        ("pg_index i", [{"attname": "id"}]),
        ("table_constraints", [{"column_name": "user_id", "ref_table": "users", "ref_column": "id"}]),
    ]
    fake.fetchval_result = "订单表"
    conn = PostgresConnection(_pg_config())
    conn._conn = fake
    schema = await conn.table_schema("orders")
    assert schema["table"] == "orders" and schema["schema"] == "public"
    assert schema["comment"] == "订单表"
    assert schema["columns"][0] == {
        "name": "id", "data_type": "int4", "nullable": False,
        "default": None, "comment": None, "primary_key": True,
    }
    assert schema["columns"][1]["primary_key"] is False
    assert schema["indexes"] == [{"name": "idx_email", "definition": "CREATE INDEX ..."}]
    assert schema["foreign_keys"] == [{"column": "user_id", "ref_table": "users", "ref_column": "id"}]


@pytest.mark.asyncio
async def test_pg_search_schema(monkeypatch):
    fake = FakePgConn()
    fake.fetch_plans = [
        ("pg_tables", [{"name": "orders"}]),
        ("information_schema.columns", [{"table_name": "orders", "column_name": "id"}]),
    ]
    conn = PostgresConnection(_pg_config())
    conn._conn = fake
    result = await conn.search_schema("order")
    assert result == {"tables": [{"name": "orders"}], "columns": [{"table": "orders", "column": "id"}]}


# ---------- MySQL ----------


class FakeMyCursor:
    def __init__(self, conn: FakeMyConn) -> None:
        self._conn = conn
        self.description: list[tuple[str]] = [("col",)]

    async def __aenter__(self) -> FakeMyCursor:
        return self

    async def __aexit__(self, *args: Any) -> bool:
        return False

    async def execute(self, sql: str, args: Any = None) -> None:
        self._conn.last_sql = sql
        self._conn.calls.append(sql)

    async def fetchall(self) -> list[Any]:
        for marker, rows in self._conn.fetchall_plans:
            if marker in self._conn.last_sql:
                return rows
        return []

    async def fetchone(self) -> Any:
        for marker, row in self._conn.fetchone_plans:
            if marker in self._conn.last_sql:
                return row
        return None


class FakeMyConn:
    def __init__(self) -> None:
        self.fetchall_plans: list[tuple[str, list[Any]]] = []
        self.fetchone_plans: list[tuple[str, Any]] = []
        self.last_sql = ""
        self.calls: list[str] = []

    def cursor(self) -> FakeMyCursor:
        return FakeMyCursor(self)

    def close(self) -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_my_connect_success(monkeypatch):
    fake = FakeMyConn()

    async def fake_connect(**kwargs):
        return fake

    monkeypatch.setattr("db_assistant_mcp.drivers.mysql.aiomysql.connect", fake_connect)
    conn = MysqlConnection(_mysql_config())
    await conn.connect()
    assert conn._closed is False


@pytest.mark.asyncio
async def test_my_connect_error_mapping(monkeypatch):
    async def raise_timeout(**kwargs):
        raise TimeoutError()

    async def raise_1045(**kwargs):
        raise pymysql_err.OperationalError(1045, "Access denied")

    async def raise_1049(**kwargs):
        raise pymysql_err.OperationalError(1049, "Unknown database")

    async def raise_other(**kwargs):
        raise pymysql_err.OperationalError(2002, "Can't connect")

    conn = MysqlConnection(_mysql_config())

    monkeypatch.setattr("db_assistant_mcp.drivers.mysql.aiomysql.connect", raise_timeout)
    with pytest.raises(ConnectionError_) as exc:
        await conn.connect()
    assert exc.value.detail == "CONNECT_TIMEOUT"

    monkeypatch.setattr("db_assistant_mcp.drivers.mysql.aiomysql.connect", raise_1045)
    with pytest.raises(ConnectionError_) as exc:
        await conn.connect()
    assert "认证失败" in exc.value.message

    monkeypatch.setattr("db_assistant_mcp.drivers.mysql.aiomysql.connect", raise_1049)
    with pytest.raises(ConnectionError_) as exc:
        await conn.connect()
    assert "数据库不存在" in exc.value.message

    monkeypatch.setattr("db_assistant_mcp.drivers.mysql.aiomysql.connect", raise_other)
    with pytest.raises(ConnectionError_) as exc:
        await conn.connect()
    assert "连接失败" in exc.value.message


@pytest.mark.asyncio
async def test_my_close_is_valid_ping(monkeypatch):
    fake = FakeMyConn()
    async def fake_connect(**k):
        return fake

    monkeypatch.setattr("db_assistant_mcp.drivers.mysql.aiomysql.connect", fake_connect)
    conn = MysqlConnection(_mysql_config())
    with pytest.raises(ConnectionError_):
        await conn.ping()
    await conn.connect()
    assert await conn.is_valid() is True
    latency = await conn.ping()
    assert isinstance(latency, float)
    await conn.close()
    assert conn._closed


@pytest.mark.asyncio
async def test_my_fetch(monkeypatch):
    fake = FakeMyConn()
    fake.fetchall_plans.append(("SELECT", [(1, Decimal("2.5"))]))
    conn = MysqlConnection(_mysql_config())
    conn._conn = fake
    columns, rows = await conn.fetch("SELECT 1", timeout=5)
    assert columns == ["col"]
    assert rows == [[1, 2.5]]

    with pytest.raises(ConnectionError_):
        await MysqlConnection(_mysql_config()).fetch("SELECT 1", timeout=5)

    async def slow(*a, **k):
        await asyncio.sleep(10)

    conn._conn = FakeMyConn()
    monkeypatch.setattr(FakeMyCursor, "execute", slow)
    with pytest.raises(QueryTimeoutError) as exc:
        await conn.fetch("SELECT 1", timeout=0.05)
    assert exc.value.detail == "QUERY_TIMEOUT"


@pytest.mark.asyncio
async def test_my_list_tables(monkeypatch):
    fake = FakeMyConn()
    fake.fetchall_plans.append(
        ("FROM information_schema.TABLES", [
            ("orders", 100, "订单表", "BASE TABLE"),
            ("v_active", 0, None, "VIEW"),
        ]),
    )
    conn = MysqlConnection(_mysql_config())
    conn._conn = fake
    tables = await conn.list_tables()
    assert tables[0] == {"name": "orders", "estimated_rows": 100, "comment": "订单表", "kind": "table"}
    assert tables[1] == {"name": "v_active", "estimated_rows": 0, "comment": None, "kind": "view"}


@pytest.mark.asyncio
async def test_my_table_schema(monkeypatch):
    fake = FakeMyConn()
    fake.fetchall_plans = [
        ("COLUMNS", [("id", "int", "int(11)", "NO", None, "", "PRI"), ("email", "varchar", "varchar(255)", "YES", None, "邮箱", "")]),
        ("SHOW INDEX", [
            ("orders", 0, "PRIMARY", 1, "id", "A", 0, None, None, "", "", "BTREE", "", "", "YES", None),
            ("orders", 0, "idx_email", 1, "email", "A", 0, None, None, "", "", "BTREE", "", "", "YES", None),
        ],),
        ("KEY_COLUMN_USAGE", [("user_id", "users", "id")]),
    ]
    fake.fetchone_plans = [("TABLE_COMMENT", ("订单表",))]
    conn = MysqlConnection(_mysql_config())
    conn._conn = fake
    schema = await conn.table_schema("orders")
    assert schema["table"] == "orders" and schema["comment"] == "订单表"
    assert schema["columns"][0]["primary_key"] is True
    assert schema["columns"][1] == {
        "name": "email", "data_type": "varchar", "nullable": True,
        "default": None, "comment": "邮箱", "primary_key": False,
    }
    assert schema["indexes"] == [{"name": "idx_email", "definition": "KEY idx_email (email)"}]
    assert schema["foreign_keys"] == [{"column": "user_id", "ref_table": "users", "ref_column": "id"}]


@pytest.mark.asyncio
async def test_my_search_schema(monkeypatch):
    fake = FakeMyConn()
    fake.fetchall_plans = [
        ("FROM information_schema.TABLES", [("orders",)]),
        ("FROM information_schema.COLUMNS", [("orders", "id")]),
    ]
    conn = MysqlConnection(_mysql_config())
    conn._conn = fake
    result = await conn.search_schema("order")
    assert result == {"tables": [{"name": "orders"}], "columns": [{"table": "orders", "column": "id"}]}
