"""脱敏与排除：masked_columns / exclude_columns / exclude_tables。"""

from __future__ import annotations

import re
from typing import Any

from db_assistant_mcp.config import ConnectionConfig

MASK = "***"

# 敏感列名自动打码（6.4）
AUTO_MASK_PATTERN = re.compile(
    r"(password|passwd|pwd|token|secret|api[_-]?key|phone|mobile|id[_-]?card|ssn)",
    re.IGNORECASE,
)


class Redactor:
    """按连接配置执行结果/ schema 脱敏。"""

    def __init__(self, config: ConnectionConfig) -> None:
        self._masked: set[str] = set()      # 列名（小写）
        self._excluded: set[str] = set()    # 列名（小写）
        self._excluded_tables: set[str] = {t.lower() for t in config.exclude_tables}
        self._masked_qualified: dict[str, set[str]] = {}
        self._excluded_qualified: dict[str, set[str]] = {}
        self._config = config

        for entry in config.masked_columns:
            table, column = self._split_qualified(entry)
            if table:
                self._masked_qualified.setdefault(table, set()).add(column)
            else:
                self._masked.add(column)
        for entry in config.exclude_columns:
            table, column = self._split_qualified(entry)
            if table:
                self._excluded_qualified.setdefault(table, set()).add(column)
            else:
                self._excluded.add(column)

    @staticmethod
    def _split_qualified(entry: str) -> tuple[str | None, str]:
        parts = entry.lower().split(".")
        if len(parts) == 2 and parts[0] and parts[1]:
            return parts[0], parts[1]
        return None, parts[-1]

    def is_excluded_table(self, table: str) -> bool:
        return table.lower() in self._excluded_tables

    def _is_masked_name(self, column: str, table: str | None = None) -> bool:
        c = column.lower()
        if c in self._masked:
            return True
        if table and table.lower() in self._masked_qualified:
            if c in self._masked_qualified[table.lower()]:
                return True
        # 结果层无法确知表来源时，表限定规则按列名兜底（宁可多打码）
        if any(c in cols for cols in self._masked_qualified.values()):
            return True
        return bool(AUTO_MASK_PATTERN.search(c))

    def _is_excluded_name(self, column: str, table: str | None = None) -> bool:
        c = column.lower()
        if c in self._excluded:
            return True
        if table and table.lower() in self._excluded_qualified:
            if c in self._excluded_qualified[table.lower()]:
                return True
        return any(c in cols for cols in self._excluded_qualified.values())

    def filter_result(
        self,
        columns: list[str],
        rows: list[list[Any]],
        projections: list[dict[str, Any]] | None = None,
    ) -> tuple[list[str], list[list[Any]]]:
        """从结果集中排除列并打码；projections 提供底层表/列用于别名与限定规则。"""
        classified = self._classify(columns, projections)
        keep_indexes = [
            i for i, (_, table, column) in enumerate(classified)
            if not self._is_excluded_name(column if column is not None else columns[i], table)
        ]
        out_columns = [columns[i] for i in keep_indexes]
        out_rows: list[list[Any]] = []
        for row in rows:
            masked = []
            for i in keep_indexes:
                table, column = classified[i][1], classified[i][2]
                name_for_check = column or columns[i]
                masked.append(MASK if self._is_masked_name(name_for_check, table) else row[i])
            out_rows.append(masked)
        return out_columns, out_rows

    @staticmethod
    def _classify(
        columns: list[str],
        projections: list[dict[str, Any]] | None,
    ) -> list[tuple[int, str | None, str | None]]:
        """把结果列映射到投影的 (索引, 底层表, 底层列)。"""
        if not projections:
            return [(i, None, None) for i in range(len(columns))]
        result: list[tuple[int, str | None, str | None]] = []
        proj_idx = 0
        col_idx = 0
        while col_idx < len(columns):
            if proj_idx >= len(projections):
                result.append((col_idx, None, None))
                col_idx += 1
                continue
            proj = projections[proj_idx]
            if proj.get("is_star"):
                remaining_non_star = sum(
                    1 for p in projections[proj_idx + 1:] if not p.get("is_star")
                )
                consume = len(columns) - col_idx - remaining_non_star
                consume = max(1, consume)
                for _ in range(consume):
                    result.append((col_idx, None, None))
                    col_idx += 1
                proj_idx += 1
            else:
                result.append((col_idx, proj.get("table"), proj.get("column")))
                col_idx += 1
                proj_idx += 1
        return result

    def filter_schema(self, table: str | None, columns: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """从 schema 列定义中移除排除列；masked 列保留但说明已脱敏。"""
        out: list[dict[str, Any]] = []
        for col in columns:
            name = col.get("name", "")
            if self._is_excluded_name(name, table):
                continue
            if self._is_masked_name(name, table):
                col = {**col, "masked": True}
            out.append(col)
        return out
