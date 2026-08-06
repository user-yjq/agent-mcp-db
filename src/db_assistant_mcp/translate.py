"""SQL 方言转换：基于 sqlglot transpile，产物强制只读回验（fail-closed）。

转换与校验必须作为同一原子流程使用（由 SecurityGateway.translate_sql 调用），
工具层禁止直接调用 sqlglot，避免"先转换后忘校验"的绕过路径。
"""

from __future__ import annotations

import logging
from typing import Any

import sqlglot

from db_assistant_mcp.errors import InvalidParamsError, SecurityRejectedError
from db_assistant_mcp.security.sql_validator import MAX_SQL_LENGTH, SqlValidator

# sqlglot 对 EXPLAIN/SHOW 会打印"falling back to Command"的 warning（无害），
# 真正的解析失败通过 ErrorLevel.RAISE 抛异常，此处静音避免污染 MCP stderr。
logging.getLogger("sqlglot").setLevel(logging.ERROR)

SUPPORTED_DIALECTS = ("postgres", "mysql")

# 用户友好别名 → 规范名（与配置 VALID_TYPES 一致）
_DIALECT_ALIASES = {
    "pg": "postgres",
    "pgsql": "postgres",
    "postgres": "postgres",
    "postgresql": "postgres",
    "mysql": "mysql",
    "mariadb": "mysql",
}


def normalize_dialect(dialect: str) -> str:
    """规范化方言名；非法值抛 InvalidParamsError。"""
    key = (dialect or "").strip().lower()
    normalized = _DIALECT_ALIASES.get(key)
    if normalized is None:
        raise InvalidParamsError(
            f"不支持的方言: {dialect!r}，可选 {sorted(SUPPORTED_DIALECTS)}",
            detail=f"UNSUPPORTED_DIALECT:{key}",
            hint=f"from_dialect / to_dialect 仅支持 {sorted(SUPPORTED_DIALECTS)}",
        )
    return normalized


def translate_sql(sql: str, from_dialect: str, to_dialect: str) -> dict[str, Any]:
    """方言转换（原子：parse → transpile → 目标方言只读回验）。

    返回 {"source", "target", "sql", "warnings"}。
    失败路径：方言非法 / 空 SQL / 超长 / 无法解析 → InvalidParamsError；
    转换产物含写操作或危险函数 → SecurityRejectedError。
    """
    source = normalize_dialect(from_dialect)
    target = normalize_dialect(to_dialect)

    if not sql or not sql.strip():
        raise InvalidParamsError("SQL 不能为空", detail="EMPTY_SQL", hint="请传入需要转换的 SQL 语句")
    if len(sql) > MAX_SQL_LENGTH:
        raise InvalidParamsError("SQL 超过长度上限", detail="SQL_TOO_LONG", hint=f"SQL 长度不能超过 {MAX_SQL_LENGTH} 字符")
    if "\x00" in sql:
        raise InvalidParamsError("SQL 包含 NUL 字节", detail="NUL_BYTE")

    try:
        statements = sqlglot.parse(sql, read=source, error_level=sqlglot.ErrorLevel.RAISE)
    except Exception as exc:  # noqa: BLE001
        raise InvalidParamsError(
            "SQL 无法解析", detail=f"PARSE_ERROR:{type(exc).__name__}",
            hint="请检查 SQL 语法是否符合源方言", context={"dialect": source},
        ) from exc
    if len(statements) != 1:
        raise InvalidParamsError("禁止多语句转换", detail="MULTI_STATEMENT", hint="一次只能转换一条语句")

    warnings: list[str] = []
    if source == target:
        warnings.append("源方言与目标方言相同，SQL 原样返回")
        return {"source": source, "target": target, "sql": sql, "warnings": warnings}

    try:
        transpiled = sqlglot.transpile(sql, read=source, write=target, error_level=sqlglot.ErrorLevel.RAISE)
    except Exception as exc:  # noqa: BLE001
        raise InvalidParamsError(
            "方言转换失败", detail=f"TRANSPILE_ERROR:{type(exc).__name__}",
            hint="请检查 SQL 在当前方言下是否包含无法转换的语法",
            context={"dialect": source},
        ) from exc

    if not transpiled:
        raise InvalidParamsError("方言转换失败", detail="TRANSPILE_EMPTY", hint="无法从该方言生成目标语句")

    result = "".join(transpiled)

    # 产物只读回验：任何不确定性均拒绝（fail-closed）
    target_validator = SqlValidator(target)
    validation = target_validator.validate(result)
    if not validation.ok:
        raise SecurityRejectedError(
            "转换产物未通过只读校验：仅允许只读查询（SELECT / WITH / EXPLAIN / SHOW）",
            detail=f"{validation.rule}:{validation.reason}",
            hint="转换后的语句包含写入或危险操作，已拒绝",
            context={"rule": validation.rule},
        )

    return {"source": source, "target": target, "sql": result, "warnings": warnings}
