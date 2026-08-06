"""执行计划工具：explain_query（支持 raw/tree/markdown 输出）。"""

from __future__ import annotations

from db_assistant_mcp.explain_format import format_plan
from db_assistant_mcp.identity import current_identity
from db_assistant_mcp.runtime import RuntimeRegistry
from db_assistant_mcp.tools.common import tool_handler


def register(registry: RuntimeRegistry) -> dict[str, object]:
    @tool_handler(registry, "explain_query")
    async def explain_query(connection: str, sql: str, analyze: bool = False, format: str = "raw") -> dict:
        """获取只读 SQL 的执行计划，辅助优化。connection: 连接名；sql: 只读 SQL；
        analyze: 是否执行 EXPLAIN ANALYZE（PG 支持，MySQL 不支持）；
        format: 输出格式，raw（原始 JSON，默认）| tree（统一执行计划树）| markdown（摘要表格）。"""
        runtime = registry.get(connection)
        client, user = current_identity()
        result = await runtime.gateway.explain_query(sql, analyze=analyze, client=client, user=user)
        formatted = format_plan(result["plan"], format)
        return {**result, **formatted}

    return {"explain_query": explain_query}
