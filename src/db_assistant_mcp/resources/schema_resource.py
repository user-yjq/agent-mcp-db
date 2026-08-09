"""资源注册：db://{name}/schema、db://{name}/tables、db://{name}/semantic。"""

from __future__ import annotations

import json

from db_assistant_mcp.errors import ResourceNotFoundError
from db_assistant_mcp.runtime import RuntimeRegistry


def _dump(data: object) -> str:
    return json.dumps(data, ensure_ascii=False, default=str)


def register(registry: RuntimeRegistry) -> dict[str, object]:
    async def schema_resource(name: str) -> str:
        """全库 schema 摘要（表、列、注释、索引、外键、业务语义）。"""
        if name not in registry.names:
            raise ResourceNotFoundError(f"资源不存在: db://{name}/schema", detail="RESOURCE_NOT_FOUND")
        runtime = registry.get(name)
        summary = await runtime.schema.get_summary()
        return _dump(summary)

    async def tables_resource(name: str) -> str:
        """表/视图列表与行数估算。"""
        if name not in registry.names:
            raise ResourceNotFoundError(f"资源不存在: db://{name}/tables", detail="RESOURCE_NOT_FOUND")
        runtime = registry.get(name)
        tables = await runtime.schema.list_tables()
        return _dump({"connection": name, "tables": tables})

    async def semantic_resource(name: str) -> str:
        """业务词典（列名 → 业务语义）。"""
        if name not in registry.names:
            raise ResourceNotFoundError(f"资源不存在: db://{name}/semantic", detail="RESOURCE_NOT_FOUND")
        registry.get(name)
        return _dump(registry._glossary.to_resource())

    return {
        "schema": schema_resource,
        "tables": tables_resource,
        "semantic": semantic_resource,
    }

