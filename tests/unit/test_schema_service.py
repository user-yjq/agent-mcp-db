from __future__ import annotations

from typing import Any

import pytest

from db_assistant_mcp.config import ConnectionConfig
from db_assistant_mcp.errors import TableNotFoundError
from db_assistant_mcp.schema_service import SchemaService
from db_assistant_mcp.security.redactor import Redactor
from db_assistant_mcp.semantic import Glossary


class FakeConn:
    async def list_tables(self) -> list[dict[str, Any]]:
        return [
            {"name": "users", "estimated_rows": 10, "comment": None},
            {"name": "audit_log", "estimated_rows": 99, "comment": None},
        ]

    async def table_schema(self, table: str) -> dict[str, Any]:
        return {
            "table": table,
            "columns": [
                {"name": "id", "data_type": "int", "nullable": False, "default": None, "comment": None},
                {"name": "phone", "data_type": "text", "nullable": True, "default": None, "comment": None},
                {"name": "salary", "data_type": "int", "nullable": True, "default": None, "comment": None},
            ],
            "indexes": [],
            "foreign_keys": [],
        }

    async def search_schema(self, keyword: str) -> dict[str, list[dict[str, str]]]:
        return {"tables": [], "columns": []}

    async def explain(self, sql: str, analyze: bool, timeout: float) -> dict[str, Any]:
        return {}


class FakePool:
    def __init__(self) -> None:
        self.calls = 0

    async def run(self, op):
        self.calls += 1
        return await op(FakeConn())

    async def close(self) -> None:
        pass


def _service() -> tuple[SchemaService, FakePool]:
    cfg = ConnectionConfig(
        name="t", type="postgres", host="h", port=5432, database="d", user="u",
        masked_columns=["phone"], exclude_columns=["users.salary"], exclude_tables=["audit_log"],
    )
    pool = FakePool()
    return SchemaService(name="t", pool=pool, redactor=Redactor(cfg), glossary=Glossary(), ttl_sec=300), pool


@pytest.mark.asyncio
async def test_excluded_table_hidden():
    service, _ = _service()
    tables = await service.list_tables()
    assert [t["name"] for t in tables] == ["users"]


@pytest.mark.asyncio
async def test_excluded_column_hidden_and_masked_marked():
    service, _ = _service()
    schema = await service.table_schema("users")
    names = [c["name"] for c in schema["columns"]]
    assert names == ["id", "phone"]
    assert schema["columns"][1]["masked"] is True


@pytest.mark.asyncio
async def test_excluded_table_schema_raises():
    service, _ = _service()
    with pytest.raises(TableNotFoundError):
        await service.table_schema("audit_log")


@pytest.mark.asyncio
async def test_cache_hit_reuses_pool():
    service, pool = _service()
    await service.list_tables()
    calls_after_first = pool.calls
    await service.list_tables()
    await service.list_tables()
    assert pool.calls == calls_after_first  # 缓存命中不再打库


@pytest.mark.asyncio
async def test_refresh_forces_rebuild():
    service, pool = _service()
    await service.list_tables()
    calls_after_first = pool.calls
    service.invalidate()
    await service.list_tables(force_refresh=True)
    assert pool.calls > calls_after_first


@pytest.mark.asyncio
async def test_search_respects_redaction():
    service, _ = _service()
    result = await service.search("salary")
    assert result["columns"] == []  # excluded 列不出现在搜索结果



@pytest.mark.asyncio
async def test_table_not_found_suggests_similar_tables():
    service, _ = _service()
    with pytest.raises(TableNotFoundError) as exc_info:
        await service.table_schema("user")  # 实际表名 users
    suggestions = exc_info.value.context["suggestions"]
    assert any(s["name"] == "users" for s in suggestions)
    assert all(0.6 <= s["score"] <= 1.0 for s in suggestions)


@pytest.mark.asyncio
async def test_suggestions_case_insensitive():
    service, _ = _service()
    with pytest.raises(TableNotFoundError) as exc_info:
        await service.table_schema("USER")
    assert any(s["name"] == "users" for s in exc_info.value.context["suggestions"])


@pytest.mark.asyncio
async def test_suggestions_exclude_hidden_tables():
    service, _ = _service()
    await service.list_tables()  # 填充缓存
    assert service.suggest_tables("audit") == []  # audit_log 被排除，不提示


@pytest.mark.asyncio
async def test_no_suggestions_when_nothing_similar():
    service, _ = _service()
    with pytest.raises(TableNotFoundError) as exc_info:
        await service.table_schema("zzz_nothing")
    assert exc_info.value.context["suggestions"] == []
    assert "search_schema" in exc_info.value.hint  # 无建议时保持原有引导

class FakeSearchConn:
    async def list_tables(self) -> list[dict[str, Any]]:
        return [
            {"name": "users", "estimated_rows": 10, "comment": None},
            {"name": "orders", "estimated_rows": 10, "comment": None},
            {"name": "audit_log", "estimated_rows": 99, "comment": None},
        ]

    async def table_schema(self, table: str) -> dict[str, Any]:
        cols = {
            "users": [
                {"name": "id", "data_type": "int", "nullable": False, "default": None, "comment": None},
                {"name": "phone", "data_type": "text", "nullable": True, "default": None, "comment": None},
                {"name": "status", "data_type": "text", "nullable": True, "default": None, "comment": None},
                {"name": "salary", "data_type": "int", "nullable": True, "default": None, "comment": None},
            ],
            "orders": [
                {"name": "id", "data_type": "int", "nullable": False, "default": None, "comment": None},
                {"name": "order_status", "data_type": "text", "nullable": True, "default": None, "comment": None},
                {"name": "user_order_dt", "data_type": "timestamp", "nullable": True, "default": None, "comment": None},
                {"name": "total_amount", "data_type": "numeric", "nullable": True, "default": None, "comment": None},
            ],
            "audit_log": [],
        }[table]
        return {"table": table, "columns": cols, "indexes": [], "foreign_keys": []}

    async def search_schema(self, keyword: str) -> dict[str, list[dict[str, str]]]:
        return {"tables": [], "columns": []}

    async def explain(self, sql: str, analyze: bool, timeout: float) -> dict[str, Any]:
        return {}


class FakeSearchPool:
    async def run(self, op):
        return await op(FakeSearchConn())

    async def close(self) -> None:
        pass


def _search_service(tmp_path) -> tuple[SchemaService, FakeSearchPool]:
    cfg = ConnectionConfig(
        name="t", type="postgres", host="h", port=5432, database="d", user="u",
        masked_columns=["phone"], exclude_columns=["users.salary"], exclude_tables=["audit_log"],
    )
    glossary_path = tmp_path / "glossary.toml"
    glossary_path.write_text(
        """
[[terms]]
column = "user_order_dt"
meaning = "下单时间（用户确认订单的时间）"

[[terms]]
table = "orders"
column = "order_status"
meaning = "订单状态：pending/paid/shipped/completed/cancelled"

[[terms]]
pattern = ".*_?status$"
meaning = "状态字段，取值见对应枚举表"

[[terms]]
column = "salary"
meaning = "薪资（敏感列，不应出现在搜索结果）"
""",
        encoding="utf-8",
    )
    pool = FakeSearchPool()
    return SchemaService(name="t", pool=pool, redactor=Redactor(cfg), glossary=Glossary.load(str(glossary_path)), ttl_sec=300), pool


@pytest.mark.asyncio
async def test_search_hits_chinese_glossary_meaning(tmp_path):
    """C-2: 中文语义词命中对应表/列并附 meaning。"""
    service, _ = _search_service(tmp_path)
    result = await service.search("订单")
    cols = {(c["table"], c["column"]): c for c in result["columns"]}
    assert ("orders", "order_status") in cols
    assert ("orders", "user_order_dt") in cols
    assert cols[("orders", "order_status")]["meaning"].startswith("订单状态")


@pytest.mark.asyncio
async def test_search_pattern_term_expands_status_columns(tmp_path):
    """C-2: pattern 术语按正则展开到所有 *_status 列。"""
    service, _ = _search_service(tmp_path)
    result = await service.search("状态")
    cols = {(c["table"], c["column"]): c for c in result["columns"]}
    assert ("orders", "order_status") in cols
    assert ("users", "status") in cols
    assert cols[("users", "status")]["meaning"].startswith("状态字段")


@pytest.mark.asyncio
async def test_search_name_match_enriched_with_meaning(tmp_path):
    """C-2: 按列名命中时也附带 glossary meaning。"""
    service, _ = _search_service(tmp_path)
    result = await service.search("order_status")
    cols = {(c["table"], c["column"]): c for c in result["columns"]}
    assert cols[("orders", "order_status")]["meaning"].startswith("订单状态")


@pytest.mark.asyncio
async def test_search_glossary_respects_redaction(tmp_path):
    """C-2: 被排除的敏感列即使有语义词也不出现在搜索结果。"""
    service, _ = _search_service(tmp_path)
    assert await service.search("薪资") == {"keyword": "薪资", "tables": [], "columns": []}
    assert await service.search("salary") == {"keyword": "salary", "tables": [], "columns": []}
