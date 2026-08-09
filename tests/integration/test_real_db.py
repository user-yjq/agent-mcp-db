"""真实 PG/MySQL 集成测试：设置 DB_ASSISTANT_TEST_* 环境变量后运行，否则跳过。"""

from __future__ import annotations

import os

import pytest

from db_assistant_mcp.config import ConnectionConfig
from db_assistant_mcp.drivers.mysql import MysqlConnection
from db_assistant_mcp.drivers.postgres import PostgresConnection
from db_assistant_mcp.explain_format import summarize, to_markdown, to_tree


def _pg_config() -> ConnectionConfig | None:
    if not os.environ.get("DB_ASSISTANT_TEST_PG_HOST"):
        return None
    return ConnectionConfig(
        name="pg-test",
        type="postgres",
        host=os.environ["DB_ASSISTANT_TEST_PG_HOST"],
        port=int(os.environ.get("DB_ASSISTANT_TEST_PG_PORT", "5432")),
        database=os.environ.get("DB_ASSISTANT_TEST_PG_DB", "testdb"),
        user=os.environ.get("DB_ASSISTANT_TEST_PG_USER", "test"),
        password_env="DB_ASSISTANT_TEST_PG_PASSWORD",
    )


def _mysql_config() -> ConnectionConfig | None:
    if not os.environ.get("DB_ASSISTANT_TEST_MYSQL_PORT"):
        return None
    return ConnectionConfig(
        name="mysql-test",
        type="mysql",
        host=os.environ.get("DB_ASSISTANT_TEST_MYSQL_HOST", "127.0.0.1"),
        port=int(os.environ["DB_ASSISTANT_TEST_MYSQL_PORT"]),
        database=os.environ.get("DB_ASSISTANT_TEST_MYSQL_DB", "testdb"),
        user=os.environ.get("DB_ASSISTANT_TEST_MYSQL_USER", "root"),
        password_env="DB_ASSISTANT_TEST_MYSQL_PASSWORD",
    )


@pytest.mark.integration
@pytest.mark.asyncio
async def test_postgres_connect_query_schema():
    cfg = _pg_config()
    if cfg is None:
        pytest.skip("未设置 DB_ASSISTANT_TEST_PG_*")
    conn = PostgresConnection(cfg)
    await conn.connect()
    try:
        columns, rows = await conn.fetch("SELECT 1 AS one", timeout=5)
        assert columns == ["one"]
        assert rows == [[1]]
        tables = await conn.list_tables()
        assert isinstance(tables, list)
        assert await conn.ping() > 0
    finally:
        await conn.close()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_mysql_connect_query_schema():
    cfg = _mysql_config()
    if cfg is None:
        pytest.skip("未设置 DB_ASSISTANT_TEST_MYSQL_*")
    conn = MysqlConnection(cfg)
    await conn.connect()
    try:
        columns, rows = await conn.fetch("SELECT 1 AS one", timeout=5)
        assert columns == ["one"]
        assert rows == [[1]]
        assert await conn.ping() > 0
    finally:
        await conn.close()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_postgres_explain():
    cfg = _pg_config()
    if cfg is None:
        pytest.skip("未设置 DB_ASSISTANT_TEST_PG_*")
    conn = PostgresConnection(cfg)
    await conn.connect()
    try:
        plan = await conn.explain("SELECT 1", analyze=False, timeout=5)
        assert plan["format"] == "json"
        assert plan["plan"] is not None
    finally:
        await conn.close()



@pytest.mark.integration
@pytest.mark.asyncio
async def test_postgres_explain_tree_and_markdown():
    """T-2.2 集成：真实 PG EXPLAIN JSON 可转为统一树与 markdown 摘要。"""
    cfg = _pg_config()
    if cfg is None:
        pytest.skip("未设置 DB_ASSISTANT_TEST_PG_*")
    conn = PostgresConnection(cfg)
    await conn.connect()
    try:
        plan = await conn.explain("SELECT * FROM information_schema.tables", analyze=False, timeout=5)
        assert plan["format"] == "json"
        tree = to_tree(plan)
        assert tree and tree[0]["label"]
        md = to_markdown(plan)
        assert "## 执行计划摘要" in md
        # PG 会把 information_schema.tables 视图展开为 pg_catalog 基表
        assert any(t in md for t in ("pg_class", "pg_namespace", "pg_type"))
    finally:
        await conn.close()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_mysql_explain_tree_or_text():
    """T-2.2 集成：真实 MySQL EXPLAIN 转树；不支持 JSON 时降级文本也能给出摘要。"""
    cfg = _mysql_config()
    if cfg is None:
        pytest.skip("未设置 DB_ASSISTANT_TEST_MYSQL_*")
    conn = MysqlConnection(cfg)
    await conn.connect()
    try:
        plan = await conn.explain("SELECT * FROM information_schema.tables", analyze=False, timeout=5)
        if plan["format"] == "json":
            tree = to_tree(plan)
            assert tree and tree[0]["label"]
        summary = summarize(plan)
        assert summary["format"] in ("json", "text")
        assert summary["note"] is not None or summary["tables"]
    finally:
        await conn.close()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_postgres_table_comments():
    """D-1 集成：PG 表/列注释经 list_tables / table_schema 透出（演示库 seed 后）。"""
    cfg = _pg_config()
    if cfg is None:
        pytest.skip("未设置 DB_ASSISTANT_TEST_PG_*")
    conn = PostgresConnection(cfg)
    await conn.connect()
    try:
        tables = await conn.list_tables()
        users = next((t for t in tables if t["name"] == "users"), None)
        if users is None:
            pytest.skip("演示库未初始化（请先运行 scripts/seed_demo.py）")
        assert users["comment"] and "用户主表" in users["comment"]
        orders = await conn.table_schema("orders")
        assert orders["comment"] and "订单表" in orders["comment"]
        order_status = next(c for c in orders["columns"] if c["name"] == "order_status")
        assert order_status["comment"] and "订单状态" in order_status["comment"]
    finally:
        await conn.close()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_mysql_table_comments():
    """D-1 集成：MySQL 表/列注释经 table_schema 透出（演示库 seed 后）。"""
    cfg = _mysql_config()
    if cfg is None:
        pytest.skip("未设置 DB_ASSISTANT_TEST_MYSQL_*")
    conn = MysqlConnection(cfg)
    await conn.connect()
    try:
        schema = await conn.table_schema("users")
        if not schema["columns"]:
            pytest.skip("演示库未初始化（请先运行 scripts/seed_demo.py）")
        assert schema["comment"] and "用户主表" in schema["comment"]
        status_col = next(c for c in schema["columns"] if c["name"] == "status")
        assert status_col["comment"] and "账号状态" in status_col["comment"]
    finally:
        await conn.close()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_postgres_views_and_complex_structures():
    """D-2 集成：PG list_tables 暴露视图；JSON / 递归 CTE / 视图可直查。"""
    cfg = _pg_config()
    if cfg is None:
        pytest.skip("未设置 DB_ASSISTANT_TEST_PG_*")
    conn = PostgresConnection(cfg)
    await conn.connect()
    try:
        tables = await conn.list_tables()
        view = next((t for t in tables if t["name"] == "v_order_stats"), None)
        if view is None:
            pytest.skip("演示库未初始化（请先运行 scripts/seed_demo.py）")
        assert view["kind"] == "view"
        assert "users" in [t["name"] for t in tables]

        _, rows = await conn.fetch(
            "SELECT name FROM products WHERE specs->>'color' = '黑' ORDER BY name", timeout=10
        )
        assert len(rows) >= 1

        _, rows = await conn.fetch(
            "WITH RECURSIVE tree AS ("
            "SELECT id, parent_id FROM categories WHERE parent_id IS NULL "
            "UNION ALL "
            "SELECT c.id, c.parent_id FROM categories c JOIN tree t ON c.parent_id = t.id"
            ") SELECT count(*) AS n FROM tree",
            timeout=10,
        )
        assert rows[0][0] == 10

        view_schema = await conn.table_schema("v_order_stats")
        assert any(c["name"] == "total_spent" for c in view_schema["columns"])
    finally:
        await conn.close()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_mysql_views_and_complex_structures():
    """D-2 集成：MySQL list_tables 暴露视图；JSON / 递归 CTE / 视图可直查。"""
    cfg = _mysql_config()
    if cfg is None:
        pytest.skip("未设置 DB_ASSISTANT_TEST_MYSQL_*")
    conn = MysqlConnection(cfg)
    await conn.connect()
    try:
        tables = await conn.list_tables()
        view = next((t for t in tables if t["name"] == "v_order_stats"), None)
        if view is None:
            pytest.skip("演示库未初始化（请先运行 scripts/seed_demo.py）")
        assert view["kind"] == "view"

        _, rows = await conn.fetch(
            "SELECT name FROM products WHERE JSON_EXTRACT(specs, '$.color') = '黑' ORDER BY name", timeout=10
        )
        assert len(rows) >= 1

        _, rows = await conn.fetch(
            "WITH RECURSIVE tree AS ("
            "SELECT id, parent_id FROM categories WHERE parent_id IS NULL "
            "UNION ALL "
            "SELECT c.id, c.parent_id FROM categories c JOIN tree t ON c.parent_id = t.id"
            ") SELECT count(*) AS n FROM tree",
            timeout=10,
        )
        assert rows[0][0] == 10

        view_schema = await conn.table_schema("v_order_stats")
        assert any(c["name"] == "total_spent" for c in view_schema["columns"])
    finally:
        await conn.close()
