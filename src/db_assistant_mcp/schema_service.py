"""Schema 快照缓存：TTL 失效 + 主动刷新 + 命中指标。"""

from __future__ import annotations

import asyncio
import difflib
import time
from dataclasses import dataclass, field
from typing import Any

from db_assistant_mcp.drivers.pool import DriverPool
from db_assistant_mcp.errors import TableNotFoundError
from db_assistant_mcp.observability import metrics
from db_assistant_mcp.security.redactor import Redactor
from db_assistant_mcp.semantic import Glossary


@dataclass
class CacheEntry:
    summary: dict[str, Any]
    fetched_at: float
    tables: list[dict[str, Any]] = field(default_factory=list)


class SchemaService:
    """按连接维护 schema 快照（全库摘要 + 表列表）。"""

    def __init__(
        self,
        *,
        name: str,
        pool: DriverPool,
        redactor: Redactor,
        glossary: Glossary,
        ttl_sec: float,
    ) -> None:
        self._name = name
        self._pool = pool
        self._redactor = redactor
        self._glossary = glossary
        self._ttl = ttl_sec
        self._cache: CacheEntry | None = None
        self._lock = asyncio.Lock()

    def _hit(self) -> None:
        metrics.schema_cache_hits.labels(connection=self._name).inc()

    def _miss(self) -> None:
        metrics.schema_cache_misses.labels(connection=self._name).inc()

    def invalidate(self) -> None:
        self._cache = None

    def _build_summary(self, tables: list[dict[str, Any]], schema_by_table: dict[str, dict[str, Any]]) -> dict[str, Any]:
        visible_tables = [t for t in tables if not self._redactor.is_excluded_table(t["name"])]
        summary: dict[str, Any] = {
            "connection": self._name,
            "database": None,
            "tables": [],
        }
        for t in visible_tables:
            schema = schema_by_table.get(t["name"], {})
            columns = schema.get("columns", [])
            columns = self._redactor.filter_schema(t["name"], columns)
            columns = self._glossary.enrich_columns(t["name"], columns)
            summary["tables"].append(
                {
                    "name": t["name"],
                    "estimated_rows": t.get("estimated_rows"),
                    "comment": t.get("comment"),
                    "kind": t.get("kind"),
                    "columns": columns,
                    "indexes": schema.get("indexes", []),
                    "foreign_keys": schema.get("foreign_keys", []),
                }
            )
        return summary

    async def _fetch_summary(self) -> CacheEntry:
        tables = await self._pool.run(lambda conn: conn.list_tables())
        schema_by_table: dict[str, dict[str, Any]] = {}
        visible = [t for t in tables if not self._redactor.is_excluded_table(t["name"])]
        for t in visible:
            try:
                schema_by_table[t["name"]] = await self._pool.run(
                    lambda conn, table=t["name"]: conn.table_schema(table)
                )
            except Exception:  # noqa: BLE001
                # 单表 introspection 失败不拖垮整个摘要
                schema_by_table[t["name"]] = {"columns": [], "indexes": [], "foreign_keys": []}
        summary = self._build_summary(tables, schema_by_table)
        return CacheEntry(summary=summary, fetched_at=time.monotonic(), tables=tables)

    async def get_summary(self, *, force_refresh: bool = False) -> dict[str, Any]:
        async with self._lock:
            if self._cache and not force_refresh and time.monotonic() - self._cache.fetched_at < self._ttl:
                self._hit()
                return self._cache.summary
            self._miss()
            self._cache = await self._fetch_summary()
            return self._cache.summary

    async def list_tables(self, *, force_refresh: bool = False) -> list[dict[str, Any]]:
        summary = await self.get_summary(force_refresh=force_refresh)
        return [
            {
                "name": t["name"],
                "estimated_rows": t["estimated_rows"],
                "comment": t["comment"],
                "kind": t.get("kind"),
            }
            for t in summary["tables"]
        ]

    async def table_schema(self, table: str, *, force_refresh: bool = False) -> dict[str, Any]:
        summary = await self.get_summary(force_refresh=force_refresh)
        for t in summary["tables"]:
            if t["name"].lower() == table.lower():
                return t
        raise TableNotFoundError(
            f"表 '{table}' 不存在（或已被排除）",
            detail=f"TABLE_NOT_FOUND:{table}",
            connection=self._name,
            hint="使用 search_schema 搜索相似表名",
            context={"suggestions": self.suggest_tables(table)},
        )

    def suggest_tables(self, keyword: str, *, limit: int = 5, threshold: float = 0.6) -> list[dict[str, Any]]:
        """基于可见表名做模糊匹配建议（difflib，阈值 ≥0.6，最多 limit 个，遵守 exclude 规则）。"""
        summary = self._cache.summary if self._cache else {}
        visible = [t["name"] for t in summary.get("tables", [])]
        if not visible:
            return []
        kw = keyword.lower()
        scored: list[tuple[float, str]] = []
        for name in visible:
            ratio = difflib.SequenceMatcher(None, kw, name.lower()).ratio()
            if ratio >= threshold:
                scored.append((ratio, name))
        scored.sort(key=lambda item: (-item[0], item[1]))
        return [{"name": name, "score": round(score, 3)} for score, name in scored[:limit]]

    async def search(self, keyword: str) -> dict[str, Any]:
        """按关键字模糊搜索表与列；同时命中 glossary 语义词（含 pattern 展开）。"""
        summary = await self.get_summary()
        kw = keyword.lower()
        tables = [
            {"name": t["name"]}
            for t in summary["tables"]
            if kw in t["name"].lower()
        ]
        columns: dict[tuple[str, str], dict[str, Any]] = {}
        # 1) 表/列名子串命中（原有行为），并附带 glossary meaning
        for t in summary["tables"]:
            for c in t["columns"]:
                if kw in c["name"].lower() or kw in t["name"].lower():
                    entry = {"table": t["name"], "column": c["name"]}
                    term = self._glossary.lookup(t["name"], c["name"])
                    if term and term.meaning:
                        entry["meaning"] = term.meaning
                    columns[(t["name"], c["name"])] = entry
        # 2) 语义词命中：meaning/别名含关键字 → 解析到具体表/列（excluded 表/列天然被 summary 过滤）
        for term in self._glossary.search_terms(kw):
            if term.table and not term.column and not term.pattern:
                continue  # 表级术语由第 3 步统一处理（避免把表语义打到所有列上）
            for t in summary["tables"]:
                for c in t["columns"]:
                    if self._glossary.term_matches(term, t["name"], c["name"]):
                        entry = columns.setdefault(
                            (t["name"], c["name"]),
                            {"table": t["name"], "column": c["name"]},
                        )
                        if term.meaning:
                            entry["meaning"] = term.meaning
        # 3) 表级语义词命中：别名/语义命中时把表本身加入结果（如「用户/顾客/会员」→ users）
        seen = {t["name"].lower() for t in tables}
        for term in self._glossary.table_terms(kw):
            if term.table and term.table.lower() not in seen:
                tables.append({"name": term.table})
                seen.add(term.table.lower())
        return {
            "keyword": keyword,
            "tables": tables[:50],
            "columns": list(columns.values())[:100],
        }
