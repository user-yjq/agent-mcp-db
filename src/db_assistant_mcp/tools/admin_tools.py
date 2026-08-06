"""管理工具：refresh_schema / ping。"""

from __future__ import annotations

from db_assistant_mcp.runtime import RuntimeRegistry
from db_assistant_mcp.tools.common import tool_handler


def register(registry: RuntimeRegistry) -> dict[str, object]:
    @tool_handler(registry, "refresh_schema")
    async def refresh_schema(connection: str) -> dict:
        """主动失效并重建指定连接的 schema 缓存。connection: 连接名。"""
        runtime = registry.get(connection)
        runtime.schema.invalidate()
        summary = await runtime.schema.get_summary(force_refresh=True)
        return {"connection": connection, "refreshed": True, "tables": len(summary["tables"])}

    @tool_handler(registry, "ping")
    async def ping(connection: str | None = None) -> dict:
        """健康检查：测试指定连接（缺省则全部）是否可达。connection: 可选连接名。"""
        results = await registry.ping(connection)
        all_ok = all(r.get("ok") for r in results.values())
        return {"healthy": all_ok, "connections": results}

    return {"refresh_schema": refresh_schema, "ping": ping}

