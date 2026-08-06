"""MySQL / MariaDB 驱动：基于 aiomysql。"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import aiomysql
from pymysql import err as pymysql_err

from db_assistant_mcp.config import ConnectionConfig
from db_assistant_mcp.drivers.base import DatabaseConnection, normalize_value
from db_assistant_mcp.errors import ConnectionError_, QueryTimeoutError


class MysqlConnection(DatabaseConnection):
    dialect = "mysql"

    def __init__(self, config: ConnectionConfig) -> None:
        self._config = config
        self._conn: aiomysql.Connection | None = None
        self._closed = True

    async def connect(self) -> None:
        cfg = self._config
        kwargs: dict[str, Any] = {
            "host": cfg.host,
            "port": cfg.port,
            "db": cfg.database,
            "user": cfg.user,
            "charset": cfg.charset,
            "autocommit": True,
            "connect_timeout": cfg.connect_timeout_sec,
        }
        if cfg.password is not None:
            kwargs["password"] = cfg.password
        if cfg.ssl:
            kwargs["ssl"] = {}
        try:
            self._conn = await asyncio.wait_for(
                aiomysql.connect(**kwargs),
                timeout=cfg.connect_timeout_sec,
            )
            self._closed = False
        except TimeoutError as exc:
            raise ConnectionError_(
                f"连接 MySQL {cfg.host}:{cfg.port}/{cfg.database} 超时",
                detail="CONNECT_TIMEOUT",
                connection=cfg.name,
            ) from exc
        except pymysql_err.OperationalError as exc:
            code = exc.args[0] if exc.args else 0
            label = "认证失败" if code in (1045, 1698) else ("数据库不存在" if code == 1049 else "连接失败")
            raise ConnectionError_(
                f"连接 MySQL 失败（{label}）: {cfg.host}:{cfg.port}/{cfg.database}",
                detail=f"{label.upper().replace(' ', '_')}:{cfg.name}",
                connection=cfg.name,
            ) from exc
        except (OSError, Exception) as exc:  # noqa: BLE001
            raise ConnectionError_(
                f"连接 MySQL 失败: {cfg.host}:{cfg.port}/{cfg.database}",
                detail=f"CONNECT_FAILED:{cfg.name}",
                connection=cfg.name,
            ) from exc

    async def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
        self._conn = None
        self._closed = True

    async def is_valid(self) -> bool:
        if self._closed or self._conn is None:
            return False
        try:
            async with self._conn.cursor() as cur:
                await cur.execute("SELECT 1")
            return True
        except Exception:  # noqa: BLE001
            return False

    async def ping(self) -> float:
        if self._conn is None:
            raise ConnectionError_("连接未建立", connection=self._config.name)
        start = asyncio.get_event_loop().time()
        async with self._conn.cursor() as cur:
            await cur.execute("SELECT 1")
        return round((asyncio.get_event_loop().time() - start) * 1000, 2)

    async def fetch(self, sql: str, timeout: float) -> tuple[list[str], list[list[Any]]]:
        if self._conn is None:
            raise ConnectionError_("连接未建立", connection=self._config.name)
        try:
            async with self._conn.cursor() as cur:
                await asyncio.wait_for(cur.execute(sql), timeout=timeout)
                columns = [d[0] for d in (cur.description or [])]
                rows = await cur.fetchall()
        except TimeoutError as exc:
            raise QueryTimeoutError(
                f"查询超时（>{timeout:.1f}s）",
                detail="QUERY_TIMEOUT",
                connection=self._config.name,
                context={"timeout_sec": timeout},
                hint="建议增加 WHERE 条件或降低采样",
            ) from exc
        return columns, [[normalize_value(v) for v in row] for row in rows]

    async def list_tables(self) -> list[dict[str, Any]]:
        assert self._conn is not None
        async with self._conn.cursor() as cur:
            await cur.execute(
                """
                SELECT TABLE_NAME, TABLE_ROWS, TABLE_COMMENT
                FROM information_schema.TABLES
                WHERE TABLE_SCHEMA = DATABASE() AND TABLE_TYPE = 'BASE TABLE'
                ORDER BY TABLE_NAME
                """
            )
            rows = await cur.fetchall()
        return [
            {"name": r[0], "estimated_rows": int(r[1] or 0), "comment": r[2] or None}
            for r in rows
        ]

    async def table_schema(self, table: str) -> dict[str, Any]:
        assert self._conn is not None
        async with self._conn.cursor() as cur:
            await cur.execute(
                """
                SELECT COLUMN_NAME, DATA_TYPE, COLUMN_TYPE, IS_NULLABLE,
                       COLUMN_DEFAULT, COLUMN_COMMENT, COLUMN_KEY
                FROM information_schema.COLUMNS
                WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s
                ORDER BY ORDINAL_POSITION
                """,
                (table,),
            )
            col_rows = await cur.fetchall()
            await cur.execute(f"SHOW INDEX FROM `{table.replace(chr(96), '')}`")
            index_rows = await cur.fetchall()
            await cur.execute(
                """
                SELECT COLUMN_NAME, REFERENCED_TABLE_NAME, REFERENCED_COLUMN_NAME
                FROM information_schema.KEY_COLUMN_USAGE
                WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s
                  AND REFERENCED_TABLE_NAME IS NOT NULL
                """,
                (table,),
            )
            fk_rows = await cur.fetchall()
        columns = [
            {
                "name": r[0],
                "data_type": r[1],
                "nullable": r[3] == "YES",
                "default": r[4],
                "comment": r[5],
                "primary_key": r[6] == "PRI",
            }
            for r in col_rows
        ]
        indexes: list[dict[str, str]] = []
        for r in index_rows:
            if r[2] == "PRIMARY":
                continue
            indexes.append({"name": r[2], "definition": f"KEY {r[2]} ({r[4]})"})
        foreign_keys = [
            {"column": r[0], "ref_table": r[1], "ref_column": r[2]}
            for r in fk_rows
        ]
        return {
            "table": table,
            "schema": None,
            "comment": None,
            "columns": columns,
            "indexes": indexes,
            "foreign_keys": foreign_keys,
        }

    async def search_schema(self, keyword: str) -> dict[str, list[dict[str, str]]]:
        assert self._conn is not None
        like = f"%{keyword}%"
        async with self._conn.cursor() as cur:
            await cur.execute(
                """
                SELECT TABLE_NAME FROM information_schema.TABLES
                WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME LIKE %s
                ORDER BY TABLE_NAME LIMIT 50
                """,
                (like,),
            )
            tables = await cur.fetchall()
            await cur.execute(
                """
                SELECT TABLE_NAME, COLUMN_NAME FROM information_schema.COLUMNS
                WHERE TABLE_SCHEMA = DATABASE()
                  AND (TABLE_NAME LIKE %s OR COLUMN_NAME LIKE %s)
                ORDER BY TABLE_NAME, COLUMN_NAME LIMIT 100
                """,
                (like, like),
            )
            columns = await cur.fetchall()
        return {
            "tables": [{"name": r[0]} for r in tables],
            "columns": [{"table": r[0], "column": r[1]} for r in columns],
        }

    async def explain(self, sql: str, analyze: bool, timeout: float) -> dict[str, Any]:
        assert self._conn is not None
        if analyze:
            raise ConnectionError_(
                "MySQL 不支持 EXPLAIN ANALYZE（v0.1），请使用 analyze=false",
                detail="EXPLAIN_ANALYZE_UNSUPPORTED",
                connection=self._config.name,
            )
        try:
            _, rows = await self.fetch(f"EXPLAIN FORMAT=JSON {sql}", timeout)
            plan = rows[0][0] if rows else None
            if isinstance(plan, str):
                try:
                    plan = json.loads(plan)
                except (TypeError, ValueError):
                    pass  # 保持原始字符串，由 format 层兜底
            return {"format": "json", "analyze": False, "plan": plan}
        except Exception:  # noqa: BLE001  # MariaDB/MySQL5.7 不支持 FORMAT=JSON
            _, rows = await self.fetch(f"EXPLAIN {sql}", timeout)
            return {"format": "text", "analyze": False, "plan": rows}
