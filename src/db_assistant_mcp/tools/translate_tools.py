"""方言转换工具：translate_sql（仅经网关原子调用，无 sqlglot 直接路径）。"""

from __future__ import annotations

from db_assistant_mcp.runtime import RuntimeRegistry
from db_assistant_mcp.tools.common import tool_handler


def register(registry: RuntimeRegistry) -> dict[str, object]:
    @tool_handler(registry, "translate_sql")
    async def translate_sql(sql: str, from_dialect: str = "postgres", to_dialect: str = "mysql") -> dict:
        """在 PostgreSQL 与 MySQL 之间转换 SQL 方言，转换结果强制通过只读校验。
        sql: 待转换的 SQL；from_dialect: 源方言（postgres/mysql，别名 pg/mariadb 亦可）；
        to_dialect: 目标方言。返回 {source, target, sql, warnings}。"""
        gateway = registry.default_gateway()
        return await gateway.translate_sql(sql, from_dialect=from_dialect, to_dialect=to_dialect)

    return {"translate_sql": translate_sql}
