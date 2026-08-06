"""安全回归（T-4.4）：相似表名建议不得泄露 excluded 表信息。"""

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
            {"name": "raw_events", "estimated_rows": 200, "comment": None},
        ]

    async def table_schema(self, table: str) -> dict[str, Any]:
        raise AssertionError("不应查询到表结构")

    async def search_schema(self, keyword: str) -> dict[str, list[dict[str, str]]]:
        return {"tables": [], "columns": []}

    async def explain(self, sql: str, analyze: bool, timeout: float) -> dict[str, Any]:
        return {}


class FakePool:
    async def run(self, op):
        return await op(FakeConn())

    async def close(self) -> None:
        pass


def _service(exclude_tables: list[str] | None = None) -> SchemaService:
    cfg = ConnectionConfig(
        name="t", type="postgres", host="h", port=5432, database="d", user="u",
        exclude_tables=exclude_tables or ["audit_log", "raw_events"],
    )
    return SchemaService(name="t", pool=FakePool(), redactor=Redactor(cfg), glossary=Glossary(), ttl_sec=300)


@pytest.mark.asyncio
async def test_table_not_found_suggestions_never_include_excluded():
    service = _service()
    with pytest.raises(TableNotFoundError) as exc_info:
        await service.table_schema("user")
    suggestions = exc_info.value.context["suggestions"]
    names = {s["name"] for s in suggestions}
    assert "users" in names
    assert not (names & {"audit_log", "raw_events"})  # 排除表绝不出现


@pytest.mark.asyncio
async def test_high_similarity_to_excluded_table_still_hidden():
    """查询词与 excluded 表高度相似（audit vs audit_log）时也不给出建议。"""
    service = _service()
    await service.list_tables()  # 填充缓存
    assert service.suggest_tables("audit") == []
    assert service.suggest_tables("raw_event") == []


@pytest.mark.asyncio
async def test_suggestions_never_expose_other_excluded_hint():
    """错误响应中不携带 excluded 表名（连 hint 文本都不含）。"""
    service = _service()
    with pytest.raises(TableNotFoundError) as exc_info:
        await service.table_schema("audit")  # 目标本身就是被排除表
    payload = exc_info.value.to_dict()
    assert "audit_log" not in str(payload)  # 对外 payload 不含被排除表名
    assert exc_info.value.context["suggestions"] == []
