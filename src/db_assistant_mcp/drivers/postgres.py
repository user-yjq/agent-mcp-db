"""PostgreSQL 驱动：基于 asyncpg。"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import asyncpg

from db_assistant_mcp.config import ConnectionConfig
from db_assistant_mcp.drivers.base import DatabaseConnection, normalize_value
from db_assistant_mcp.errors import ConnectionError_, QueryTimeoutError


class PostgresConnection(DatabaseConnection):
    dialect = "postgres"

    def __init__(self, config: ConnectionConfig) -> None:
        self._config = config
        self._conn: asyncpg.Connection | None = None
        self._closed = True

    async def connect(self) -> None:
        cfg = self._config
        kwargs: dict[str, Any] = {
            "host": cfg.host,
            "port": cfg.port,
            "database": cfg.database,
            "user": cfg.user,
            "timeout": cfg.connect_timeout_sec,
        }
        if cfg.password is not None:
            kwargs["password"] = cfg.password
        if cfg.ssl:
            kwargs["ssl"] = "require"
        try:
            self._conn = await asyncio.wait_for(
                asyncpg.connect(**kwargs),
                timeout=cfg.connect_timeout_sec,
            )
            self._closed = False
        except TimeoutError as exc:
            raise ConnectionError_(
                f"连接 PostgreSQL {cfg.host}:{cfg.port}/{cfg.database} 超时",
                detail="CONNECT_TIMEOUT",
                connection=cfg.name,
            ) from exc
        except (asyncpg.InvalidPasswordError, asyncpg.InvalidCatalogNameError) as exc:
            label = "认证失败" if isinstance(exc, asyncpg.InvalidPasswordError) else "数据库不存在"
            raise ConnectionError_(
                f"连接 PostgreSQL 失败（{label}）: {cfg.host}:{cfg.port}/{cfg.database}",
                detail=f"{label.upper().replace(' ', '_')}:{cfg.name}",
                connection=cfg.name,
            ) from exc
        except (OSError, asyncpg.PostgresError) as exc:
            raise ConnectionError_(
                f"连接 PostgreSQL 失败: {cfg.host}:{cfg.port}/{cfg.database}",
                detail=f"CONNECT_FAILED:{cfg.name}",
                connection=cfg.name,
            ) from exc

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
        self._conn = None
        self._closed = True

    async def is_valid(self) -> bool:
        if self._closed or self._conn is None:
            return False
        try:
            await self._conn.fetchval("SELECT 1")
            return True
        except Exception:  # noqa: BLE001
            return False

    async def ping(self) -> float:
        if self._conn is None or self._closed:
            raise ConnectionError_("连接已关闭", connection=self._config.name)
        start = asyncio.get_event_loop().time()
        await self._conn.fetchval("SELECT 1")
        return round((asyncio.get_event_loop().time() - start) * 1000, 2)

    async def fetch(self, sql: str, timeout: float) -> tuple[list[str], list[list[Any]]]:
        if self._conn is None:
            raise ConnectionError_("连接未建立", connection=self._config.name)
        try:
            rows = await asyncio.wait_for(self._conn.fetch(sql), timeout=timeout)
        except TimeoutError as exc:
            raise QueryTimeoutError(
                f"查询超时（>{timeout:.1f}s）",
                detail="QUERY_TIMEOUT",
                connection=self._config.name,
                context={"timeout_sec": timeout},
                hint="建议增加 WHERE 条件或降低采样",
            ) from exc
        columns = list(rows[0].keys()) if rows else []
        data = [[normalize_value(v) for v in row] for row in rows]
        return columns, data

    async def list_tables(self) -> list[dict[str, Any]]:
        sql = """
            SELECT t.tablename AS name,
                   GREATEST(COALESCE(s.n_live_tup, 0), 0) AS estimated_rows,
                   obj_description((quote_ident(t.schemaname) || '.' || quote_ident(t.tablename))::regclass::oid)
                     AS comment
            FROM pg_catalog.pg_tables t
            LEFT JOIN pg_catalog.pg_stat_user_tables s
              ON s.schemaname = t.schemaname AND s.relname = t.tablename
            WHERE t.schemaname NOT IN ('pg_catalog', 'information_schema')
            ORDER BY t.tablename
        """
        rows = await self._conn.fetch(sql)  # type: ignore[union-attr]
        return [
            {"name": r["name"], "estimated_rows": int(r["estimated_rows"]), "comment": r["comment"]}
            for r in rows
        ]

    async def table_schema(self, table: str) -> dict[str, Any]:
        assert self._conn is not None
        schema = "public"
        columns_raw = await self._conn.fetch(
            """
            SELECT column_name, data_type, udt_name, is_nullable, column_default,
                   col_description((quote_ident($1) || '.' || quote_ident($2))::regclass::oid,
                                   ordinal_position) AS comment
            FROM information_schema.columns
            WHERE table_schema = $1 AND table_name = $2
            ORDER BY ordinal_position
            """,
            schema,
            table,
        )
        pk_rows = await self._conn.fetch(
            """
            SELECT a.attname
            FROM pg_index i
            JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = ANY(i.indkey)
            WHERE i.indrelid = (quote_ident($1) || '.' || quote_ident($2))::regclass
              AND i.indisprimary
            """,
            schema,
            table,
        )
        pk_set = {r["attname"] for r in pk_rows}
        columns = [
            {
                "name": r["column_name"],
                "data_type": r["udt_name"] or r["data_type"],
                "nullable": r["is_nullable"] == "YES",
                "default": r["column_default"],
                "comment": r["comment"],
                "primary_key": r["column_name"] in pk_set,
            }
            for r in columns_raw
        ]
        index_rows = await self._conn.fetch(
            "SELECT indexname, indexdef FROM pg_indexes WHERE schemaname = $1 AND tablename = $2",
            schema,
            table,
        )
        indexes = [{"name": r["indexname"], "definition": r["indexdef"]} for r in index_rows]
        fk_rows = await self._conn.fetch(
            """
            SELECT DISTINCT kcu.column_name AS column_name,
                   ccu.table_name AS ref_table,
                   ccu.column_name AS ref_column
            FROM information_schema.table_constraints tc
            JOIN information_schema.key_column_usage kcu
              ON tc.constraint_name = kcu.constraint_name
              AND tc.table_schema = kcu.table_schema
            LEFT JOIN information_schema.constraint_column_usage ccu
              ON tc.constraint_name = ccu.constraint_name
              AND tc.table_schema = ccu.table_schema
            WHERE tc.constraint_type = 'FOREIGN KEY'
              AND tc.table_schema = $1 AND tc.table_name = $2
            """,
            schema,
            table,
        )
        foreign_keys = [
            {"column": r["column_name"], "ref_table": r["ref_table"], "ref_column": r["ref_column"]}
            for r in fk_rows
        ]
        comment_row = await self._conn.fetchval(
            "SELECT obj_description((quote_ident($1) || '.' || quote_ident($2))::regclass::oid)",
            schema,
            table,
        )
        return {
            "table": table,
            "schema": schema,
            "comment": comment_row,
            "columns": columns,
            "indexes": indexes,
            "foreign_keys": foreign_keys,
        }

    async def search_schema(self, keyword: str) -> dict[str, list[dict[str, str]]]:
        assert self._conn is not None
        like = f"%{keyword}%"
        tables = await self._conn.fetch(
            """
            SELECT tablename AS name FROM pg_catalog.pg_tables
            WHERE schemaname = 'public' AND tablename ILIKE $1
            ORDER BY tablename LIMIT 50
            """,
            like,
        )
        columns = await self._conn.fetch(
            """
            SELECT table_name, column_name FROM information_schema.columns
            WHERE table_schema = 'public'
              AND (table_name ILIKE $1 OR column_name ILIKE $1)
            ORDER BY table_name, column_name LIMIT 100
            """,
            like,
        )
        return {
            "tables": [{"name": r["name"]} for r in tables],
            "columns": [{"table": r["table_name"], "column": r["column_name"]} for r in columns],
        }

    async def explain(self, sql: str, analyze: bool, timeout: float) -> dict[str, Any]:
        fmt = "ANALYZE, FORMAT JSON" if analyze else "FORMAT JSON"
        plan_sql = f"EXPLAIN ({fmt}) {sql}"
        _, rows = await self.fetch(plan_sql, timeout)
        plan = rows[0][0] if rows else None
        if isinstance(plan, str):
            try:
                parsed = json.loads(plan)
                if isinstance(parsed, list) and parsed:
                    parsed = parsed[0]
                plan = parsed
            except (TypeError, ValueError):
                pass  # 保持原始字符串，由 format 层兜底
        return {"format": "json", "analyze": analyze, "plan": plan}
