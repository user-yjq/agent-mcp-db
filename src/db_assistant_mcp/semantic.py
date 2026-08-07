"""语义层：glossary.toml 加载、匹配（精确表列 > 精确列 > 正则）与注入。"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from db_assistant_mcp.logging_utils import get_logger, log_context


@dataclass
class GlossaryTerm:
    table: str | None = None
    column: str | None = None
    pattern: str | None = None
    meaning: str | None = None
    status: str = "approved"
    confidence: float | None = None


@dataclass
class Glossary:
    terms: list[GlossaryTerm] = field(default_factory=list)
    _exact_qualified: dict[tuple[str, str], GlossaryTerm] = field(default_factory=dict)
    _exact_column: dict[str, GlossaryTerm] = field(default_factory=dict)
    _patterns: list[tuple[re.Pattern[str], GlossaryTerm]] = field(default_factory=list)

    @classmethod
    def load(cls, path: str | None) -> Glossary:
        glossary = cls()
        if not path:
            return glossary
        p = Path(path).expanduser()
        if not p.exists():
            log_context(get_logger("db_assistant_mcp.semantic"), 30, "glossary 文件不存在", path=str(p))
            return glossary
        try:
            raw = tomllib.loads(p.read_text(encoding="utf-8-sig"))
        except (tomllib.TOMLDecodeError, OSError) as exc:
            log_context(get_logger("db_assistant_mcp.semantic"), 40, "glossary 解析失败", path=str(p), error=str(exc))
            return glossary
        for item in raw.get("terms", []):
            if not isinstance(item, dict):
                continue
            term = GlossaryTerm(
                table=item.get("table"),
                column=item.get("column"),
                pattern=item.get("pattern"),
                meaning=item.get("meaning"),
                status=item.get("status", "approved"),
                confidence=item.get("confidence"),
            )
            glossary.terms.append(term)
            if term.pattern:
                try:
                    glossary._patterns.append((re.compile(term.pattern), term))
                except re.error:
                    continue
            elif term.table and term.column:
                glossary._exact_qualified[(term.table.lower(), term.column.lower())] = term
            elif term.column:
                glossary._exact_column[term.column.lower()] = term
        return glossary

    def lookup(self, table: str | None, column: str) -> GlossaryTerm | None:
        """按优先级返回匹配的术语：精确表列 > 精确列 > 正则。"""
        if table:
            term = self._exact_qualified.get((table.lower(), column.lower()))
            if term:
                return term
        term = self._exact_column.get(column.lower())
        if term:
            return term
        for pattern, term in self._patterns:
            if pattern.search(column):
                return term
        return None

    def search_terms(self, keyword: str) -> list[GlossaryTerm]:
        """按语义词（meaning）匹配已审核术语，供 search_schema 命中中文语义。"""
        kw = keyword.lower()
        matched: list[GlossaryTerm] = []
        for term in self.terms:
            if term.status not in ("approved", "reviewed"):
                continue
            if term.meaning and kw in term.meaning.lower():
                matched.append(term)
        return matched

    def term_matches(self, term: GlossaryTerm, table: str, column: str) -> bool:
        """术语是否覆盖指定 表/列（pattern 术语按正则展开）。"""
        if term.table and term.table.lower() != table.lower():
            return False
        if term.pattern:
            try:
                return re.compile(term.pattern).search(column) is not None
            except re.error:
                return False
        if term.column:
            return term.column.lower() == column.lower()
        return False

    def enrich_columns(self, table: str | None, columns: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """为 schema 列追加 meaning（仅已审核术语）。"""
        for col in columns:
            term = self.lookup(table, str(col.get("name", "")))
            if term and term.meaning and term.status in ("approved", "reviewed"):
                col = {**col, "meaning": term.meaning}
        return columns

    def to_resource(self) -> dict[str, Any]:
        return {"terms": [t.__dict__ for t in self.terms]}
