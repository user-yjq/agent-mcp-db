"""Schema 工具：list_databases / list_tables / get_table_schema / search_schema。"""

from __future__ import annotations

from db_assistant_mcp.runtime import RuntimeRegistry
from db_assistant_mcp.tools.common import tool_handler


def register(registry: RuntimeRegistry) -> dict[str, object]:
    @tool_handler(registry, "list_databases")
    async def list_databases() -> dict:
        """列出所有已配置的数据库连接，让 AI 知道可用数据源。"""
        return {"databases": registry.configured_summary}

    @tool_handler(registry, "list_tables")
    async def list_tables(connection: str) -> dict:
        """列出指定连接下的表名与行数估算。connection: 配置中的连接名。"""
        runtime = registry.get(connection)
        tables = await runtime.schema.list_tables()
        return {"connection": connection, "tables": tables, "table_count": len(tables)}

    @tool_handler(registry, "get_table_schema")
    async def get_table_schema(connection: str, table: str) -> dict:
        """获取表结构：列/类型/主外键/索引/注释/业务语义。connection: 连接名；table: 表名。"""
        runtime = registry.get(connection)
        schema = await runtime.schema.table_schema(table)
        return {"connection": connection, "schema": schema}

    @tool_handler(registry, "search_schema")
    async def search_schema(connection: str, keyword: str) -> dict:
        """按关键字模糊搜索表与列。connection: 连接名；keyword: 搜索关键字。"""
        runtime = registry.get(connection)
        result = await runtime.schema.search(keyword)
        return {"connection": connection, **result}

    return {
        "list_databases": list_databases,
        "list_tables": list_tables,
        "get_table_schema": get_table_schema,
        "search_schema": search_schema,
    }

