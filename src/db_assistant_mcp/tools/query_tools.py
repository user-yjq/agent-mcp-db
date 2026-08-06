"""只读查询工具：execute_query。"""

from __future__ import annotations

from db_assistant_mcp.identity import current_identity
from db_assistant_mcp.runtime import RuntimeRegistry
from db_assistant_mcp.tools.common import tool_handler


def register(registry: RuntimeRegistry) -> dict[str, object]:
    @tool_handler(registry, "execute_query")
    async def execute_query(connection: str, sql: str, limit: int | None = None) -> dict:
        """对已配置的数据库执行只读 SQL 查询。仅允许 SELECT/WITH/EXPLAIN/SHOW，
        自动限制返回行数（默认 100，最大 1000）与查询超时（默认 10s）。
        connection: 连接名；sql: 只读 SQL；limit: 可选返回行数上限。"""
        runtime = registry.get(connection)
        client, user = current_identity()
        return await runtime.gateway.execute_query(sql, limit=limit, client=client, user=user)

    return {"execute_query": execute_query}
