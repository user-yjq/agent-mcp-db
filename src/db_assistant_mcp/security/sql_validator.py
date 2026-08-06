"""SQL 语句校验：基于 sqlglot 真实解析器，fail-closed。"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

import sqlglot
from sqlglot import exp

from db_assistant_mcp.errors import SecurityRejectedError

MAX_SQL_LENGTH = 100_000  # >100KB 拒绝（SEC-022）

# 只读语句根类型白名单（SHOW/DESCRIBE 为元数据只读）
# COMMAND: sqlglot 对 PG 的 EXPLAIN/SHOW 会回退为 Command，需单独校验前缀
ALLOWED_ROOTS = {"SELECT", "EXPLAIN", "SHOW", "DESCRIBE", "DESC", "COMMAND"}

# 集合查询（只读）：UNION / INTERSECT / EXCEPT 顶层根节点
READ_ONLY_SET_OPS = (exp.Union, exp.Intersect, exp.Except)

# 禁止出现的表达式类型（含嵌套，如 CTE 中 INSERT）
FORBIDDEN_EXPRS = (
    exp.Insert,
    exp.Update,
    exp.Delete,
    exp.Create,
    exp.Drop,
    exp.Alter,
    exp.TruncateTable,
    exp.Grant,
    exp.Revoke,
    exp.Transaction,
    exp.Commit,
    exp.Rollback,
    exp.Into,
    exp.Copy,
    exp.Merge,
    exp.Use,
    exp.Set,
    exp.DDL,
)

_COMMAND_PREFIX = re.compile(r"^\s*(SHOW|EXPLAIN)\b", re.IGNORECASE)
_EXPLAIN_PREFIX = re.compile(
    r"^\s*EXPLAIN\b(?:\s+(?:ANALYZE|VERBOSE|COSTS|BUFFERS|WAL|TIMING|SUMMARY)\b|\s*\([^)]*\))*\s*",
    re.IGNORECASE,
)
_SHOW_PREFIX = re.compile(r"^\s*SHOW\b", re.IGNORECASE)

DANGEROUS_FUNCTIONS = {
    # PostgreSQL 危险/有副作用函数
    "pg_sleep", "pg_sleep_for", "pg_sleep_until",
    "pg_read_file", "pg_read_binary_file", "pg_ls_dir", "pg_ls_logdir",
    "pg_ls_waldir", "pg_ls_archive_statusdir", "pg_stat_file",
    "pg_write_file", "pg_write_binary_file", "pg_terminate_backend",
    "pg_cancel_backend", "pg_reload_conf", "pg_rotate_logfile",
    "pg_start_backup", "pg_stop_backup", "pg_create_restore_point",
    "pg_switch_wal", "pg_export_snapshot", "lo_export", "lo_import",
    "lo_from_bytea", "lo_put", "dblink_connect", "dblink_connect_u",
    "dblink_exec", "pg_advisory_lock", "pg_advisory_xact_lock",
    "pg_advisory_unlock", "pg_advisory_unlock_all", "pg_notify",
    # MySQL / MariaDB 危险函数
    "load_file", "sleep", "benchmark", "get_lock", "release_lock",
    "sys_eval", "sys_exec", "sys_get", "sys_set", "xp_cmdshell",
    "openrowset", "opendatasource",
}

_INTO_OUTFILE = re.compile(r"\binto\s+(outfile|dumpfile)\b", re.IGNORECASE)
_COPY_TO_FROM = re.compile(
    r"\bcopy\s+(\([^)]*\)|[a-zA-Z0-9_.\"`]+)\s+.*\b(to|from)\b",
    re.IGNORECASE | re.DOTALL,
)


@dataclass
class ValidationResult:
    ok: bool
    reason: str | None = None
    rule: str | None = None
    tables: list[str] = field(default_factory=list)


class SqlValidator:
    """分方言（postgres / mysql）的只读校验器。"""

    def __init__(self, dialect: str, exclude_tables: list[str] | None = None) -> None:
        if dialect not in ("postgres", "mysql"):
            raise ValueError(f"unsupported dialect: {dialect}")
        self._dialect = dialect
        self._exclude_tables = {t.lower() for t in (exclude_tables or [])}

    @property
    def dialect(self) -> str:
        return self._dialect

    def _parse(self, sql: str) -> list[exp.Expression]:
        return sqlglot.parse(sql, dialect=self._dialect, error_level=sqlglot.ErrorLevel.RAISE)

    def validate(self, sql: str) -> ValidationResult:
        """校验语句；任何不确定性均拒绝（fail-closed）。"""
        if not sql or not sql.strip():
            return self._reject("空 SQL", "EMPTY_SQL")
        if len(sql) > MAX_SQL_LENGTH:
            return self._reject("SQL 超过长度上限", "SQL_TOO_LONG")
        if "\x00" in sql:
            return self._reject("SQL 包含 NUL 字节", "NUL_BYTE")

        # 解析器不认识的结构（含 SELECT ... INTO OUTFILE 等）→ 解析失败即拒绝
        try:
            statements = self._parse(sql)
        except Exception as exc:  # noqa: BLE001
            return self._reject("语句无法解析", f"PARSE_ERROR:{type(exc).__name__}")

        if len(statements) != 1:
            return self._reject("禁止多语句执行", "MULTI_STATEMENT")

        root = statements[0]
        root_key = root.key.upper()
        if root_key not in ALLOWED_ROOTS and not isinstance(root, READ_ONLY_SET_OPS):
            return self._reject("仅允许只读语句", f"FORBIDDEN_ROOT:{root.key}")

        # sqlglot 对 PG 的 EXPLAIN/SHOW 回退为 Command：仅放行已知只读前缀
        if root_key == "COMMAND":
            if _SHOW_PREFIX.match(sql):
                return ValidationResult(ok=True)
            if _EXPLAIN_PREFIX.match(sql):
                inner = _EXPLAIN_PREFIX.sub("", sql)
                return self.validate(inner)
            return self._reject("仅允许只读语句", "FORBIDDEN_COMMAND")

        tables: set[str] = set()
        for node in root.walk():
            if isinstance(node, exp.Table):
                tables.add(node.name.lower())
            if isinstance(node, FORBIDDEN_EXPRS):
                return self._reject("语句包含写入或危险操作", f"FORBIDDEN_NODE:{type(node).__name__}")
            if isinstance(node, exp.Func):
                name = str(getattr(node, "name", "") or node.sql_name()).lower()
                bare_name = name.split(".")[-1]
                if name in DANGEROUS_FUNCTIONS or bare_name in DANGEROUS_FUNCTIONS:
                    return self._reject("语句包含危险函数", f"DANGEROUS_FUNCTION:{bare_name}")

        # 防御性文本检查（解析器可能尚未建模的结构）
        if _INTO_OUTFILE.search(sql):
            return self._reject("禁止 SELECT INTO OUTFILE", "INTO_OUTFILE")
        if _COPY_TO_FROM.search(sql):
            return self._reject("禁止 COPY", "COPY_STATEMENT")

        blocked = sorted(t for t in tables if t in self._exclude_tables)
        if blocked:
            return ValidationResult(ok=False, reason="表不存在", rule="EXCLUDED_TABLE", tables=blocked)

        return ValidationResult(ok=True, tables=sorted(tables))

    def _reject(self, reason: str, rule: str) -> ValidationResult:
        return ValidationResult(ok=False, reason=reason, rule=rule)

    def ensure_read_only(self, sql: str) -> ValidationResult:
        """供网关调用：拒绝时抛出 SecurityRejectedError（对外仅暴露通用信息）。"""
        result = self.validate(sql)
        if not result.ok:
            raise SecurityRejectedError(
                "语句被安全策略拒绝：仅允许只读查询（SELECT / WITH / EXPLAIN / SHOW）",
                detail=f"{result.rule}:{result.reason}",
                hint="请将语句改为只读查询，或检查表/列是否在排除清单中",
                context={"rule": result.rule},
            )
        return result

    def add_limit(self, sql: str, limit: int, *, fetch_probe: bool = True) -> str | None:
        """为顶层 SELECT 重写 LIMIT；未改动或非 SELECT 返回 None。"""
        try:
            statements = self._parse(sql)
            root = statements[0] if len(statements) == 1 else None
            if root is None or root.key.upper() != "SELECT":
                return None
            probe = limit + 1 if fetch_probe else limit
            changed = False
            if root.args.get("limit") is None:
                root.args["limit"] = exp.Limit(expression=exp.Literal.number(probe))
                changed = True
            else:
                existing = root.args["limit"].expression
                try:
                    existing_val = int(existing.name)
                except (AttributeError, ValueError):
                    existing_val = None
                if existing_val is None or existing_val > limit:
                    root.args["limit"] = exp.Limit(expression=exp.Literal.number(probe))
                    changed = True
            return root.sql(dialect=self._dialect) if changed else None
        except Exception:  # noqa: BLE001
            return None

    def extract_tables(self, sql: str) -> list[str]:
        try:
            result = self.validate(sql)
            return result.tables
        except Exception:  # noqa: BLE001
            return []

    def analyze_projections(self, sql: str) -> list[dict[str, Any]]:
        """分析 SELECT 投影，用于按底层表+列做脱敏/排除（含别名解析）。"""
        try:
            statements = self._parse(sql)
            root = statements[0] if len(statements) == 1 else None
            if root is None or root.key.upper() != "SELECT":
                return []
            alias_map: dict[str, str] = {}
            for table in root.find_all(exp.Table):
                if table.alias:
                    alias_map[table.alias.lower()] = table.name
            projections: list[dict[str, Any]] = []
            for proj in root.expressions:
                if isinstance(proj, exp.Star):
                    projections.append({"is_star": True, "table": None, "column": None})
                elif isinstance(proj, exp.Alias):
                    inner = proj.this
                    if isinstance(inner, exp.Column):
                        raw_table = inner.table or None
                        table = alias_map.get(raw_table.lower(), raw_table) if raw_table else None
                        projections.append(
                            {"is_star": False, "table": table, "column": inner.name}
                        )
                    else:
                        projections.append({"is_star": False, "table": None, "column": None})
                elif isinstance(proj, exp.Column):
                    raw_table = proj.table or None
                    table = alias_map.get(raw_table.lower(), raw_table) if raw_table else None
                    projections.append(
                        {"is_star": False, "table": table, "column": proj.name}
                    )
                else:
                    projections.append(
                        {"is_star": False, "table": None, "column": None}
                    )
            return projections
        except Exception:  # noqa: BLE001
            return []
