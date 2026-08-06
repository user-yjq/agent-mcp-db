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
