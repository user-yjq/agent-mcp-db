"""方言转换：T-2.1 translate_sql 单元 + 网关原子性 + 审计联动。"""

from __future__ import annotations

import json
from typing import Any

import pytest

from db_assistant_mcp.config import AuditConfig, ConnectionConfig, ServerConfig
from db_assistant_mcp.errors import InvalidParamsError, SecurityRejectedError
from db_assistant_mcp.security.audit import AuditLogger
from db_assistant_mcp.security.gateway import SecurityGateway
from db_assistant_mcp.security.sql_validator import MAX_SQL_LENGTH
from db_assistant_mcp.translate import normalize_dialect, translate_sql

# ---------- 纯函数：正常转换 ----------

def test_postgres_to_mysql_basic():
    r = translate_sql("SELECT id, name FROM users WHERE age > 18 LIMIT 10", "postgres", "mysql")
    assert r["source"] == "postgres"
    assert r["target"] == "mysql"
    assert "LIMIT 10" in r["sql"]
    assert r["warnings"] == []


def test_mysql_to_postgres_limit_offset():
    r = translate_sql("SELECT * FROM users LIMIT 10, 20", "mysql", "postgres")
    assert "LIMIT 20 OFFSET 10" in r["sql"]


def test_identifier_quote_conversion():
    r = translate_sql("SELECT `id` FROM `users`", "mysql", "postgres")
    assert '"id"' in r["sql"]
    assert '"users"' in r["sql"]


def test_ilike_conversion():
    r = translate_sql("SELECT * FROM users WHERE name ILIKE '%x%'", "postgres", "mysql")
    assert "LIKE" in r["sql"].upper()


def test_function_dialect_difference():
    r = translate_sql("SELECT IFNULL(a, 0) FROM t", "mysql", "postgres")
    assert "COALESCE" in r["sql"].upper()


def test_cte_preserved():
    r = translate_sql("WITH x AS (SELECT 1 AS a) SELECT * FROM x", "postgres", "mysql")
    assert "WITH" in r["sql"].upper()


def test_explain_preserved():
    r = translate_sql("EXPLAIN SELECT * FROM t", "postgres", "mysql")
    assert r["sql"].upper().startswith("EXPLAIN")


def test_dialect_aliases():
    r = translate_sql("SELECT 1", "pg", "mariadb")
    assert (r["source"], r["target"]) == ("postgres", "mysql")


def test_same_dialect_returns_as_is():
    r = translate_sql("SELECT 1", "postgres", "pg")
    assert r["sql"] == "SELECT 1"
    assert r["warnings"]


def test_normalize_dialect_invalid():
    assert normalize_dialect("Pg") == "postgres"
    with pytest.raises(InvalidParamsError):
        normalize_dialect("oracle")


# ---------- 纯函数：拒绝路径 ----------

def test_unsupported_dialect_rejected():
    with pytest.raises(InvalidParamsError, match="方言"):
        translate_sql("SELECT 1", "oracle", "mysql")


def test_empty_sql_rejected():
    with pytest.raises(InvalidParamsError, match="空"):
        translate_sql("   ", "postgres", "mysql")


def test_too_long_sql_rejected():
    with pytest.raises(InvalidParamsError, match="长度"):
        translate_sql("SELECT " + "1" * MAX_SQL_LENGTH, "postgres", "mysql")


def test_nul_byte_rejected():
    with pytest.raises(InvalidParamsError, match="NUL"):
        translate_sql("SELECT \x001", "postgres", "mysql")


def test_multi_statement_rejected():
    with pytest.raises(InvalidParamsError, match="多语句"):
        translate_sql("SELECT 1; SELECT 2", "postgres", "mysql")


def test_unparseable_sql_rejected():
    with pytest.raises(InvalidParamsError, match="无法解析"):
        translate_sql("SELEC 1 FROM", "postgres", "mysql")


@pytest.mark.parametrize(
    ("sql", "src", "dst"),
    [
        ("INSERT INTO t VALUES (1)", "postgres", "mysql"),
        ("UPDATE t SET x = 1", "mysql", "postgres"),
        ("DELETE FROM t", "postgres", "mysql"),
        ("DROP TABLE t", "mysql", "postgres"),
        ("CREATE TABLE x (a INT)", "postgres", "mysql"),
        ("SELECT pg_sleep(1)", "postgres", "mysql"),
        ("SELECT sleep(5)", "mysql", "postgres"),
        ("SELECT load_file('/etc/passwd')", "mysql", "postgres"),
        ("SELECT pg_read_file('/etc/passwd')", "postgres", "mysql"),
        ("SELECT get_lock('x', 10)", "mysql", "postgres"),
    ],
)
def test_write_or_dangerous_transpiled_rejected(sql, src, dst):
    """非只读/危险语句：转换前或产物回验阶段必须拒绝（fail-closed）。"""
    with pytest.raises(SecurityRejectedError):
        translate_sql(sql, src, dst)


def test_union_with_write_rejected_fail_closed():
    """集合查询混入写操作：无论走解析失败还是产物回验，都必须拒绝。"""
    with pytest.raises((SecurityRejectedError, InvalidParamsError)):
        translate_sql("SELECT 1 UNION ALL (INSERT INTO t VALUES (1) RETURNING 1)", "postgres", "mysql")


def test_union_is_read_only_and_translatable():
    """回归：UNION 等集合查询是只读的，不得被产物回验误拒。"""
    r = translate_sql("SELECT 1 UNION SELECT 2", "postgres", "mysql")
    assert "UNION" in r["sql"].upper()


# ---------- 网关：原子转换 + 审计 ----------

class FakeConnection:
    dialect = "postgres"

    async def close(self) -> None:
        pass

    async def is_valid(self) -> bool:
        return True

    async def ping(self) -> float:
        return 1.0

    async def fetch(self, sql: str, timeout: float):
        raise AssertionError("translate_sql 不应访问数据库")

    async def list_tables(self):
        return []

    async def table_schema(self, table: str):
        return {"table": table, "columns": []}

    async def search_schema(self, keyword: str):
        return {"tables": [], "columns": []}

    async def explain(self, sql: str, analyze: bool, timeout: float):
        raise AssertionError("translate_sql 不应访问数据库")


class FakePool:
    def __init__(self, conn: FakeConnection) -> None:
        self.conn = conn

    async def run(self, op):
        return await op(self.conn)

    async def ping(self) -> dict[str, Any]:
        return {"ok": True}

    async def close(self) -> None:
        pass


def _conn_config(**kwargs) -> ConnectionConfig:
    defaults = dict(name="test", type="postgres", host="h", port=5432, database="d", user="u")
    defaults.update(kwargs)
    return ConnectionConfig(**defaults)


def _gateway(audit_path) -> SecurityGateway:
    audit = AuditLogger(AuditConfig(output="file", path=str(audit_path)))
    return SecurityGateway(_conn_config(), FakePool(FakeConnection()), audit, ServerConfig())


@pytest.mark.asyncio
async def test_gateway_translate_sql_atomic(tmp_path):
    gw = _gateway(tmp_path / "audit.log")
    result = await gw.translate_sql(
        "SELECT id FROM users LIMIT 5", from_dialect="postgres", to_dialect="mysql"
    )
    assert result["source"] == "postgres"
    assert result["target"] == "mysql"
    assert "duration_ms" in result

    entries = [json.loads(line) for line in (tmp_path / "audit.log").read_text(encoding="utf-8").splitlines()]
    assert entries[-1]["tool"] == "translate_sql"
    assert entries[-1]["allowed"] is True
    assert entries[-1]["detail"]["from_dialect"] == "postgres"
    assert entries[-1]["detail"]["to_dialect"] == "mysql"


@pytest.mark.asyncio
async def test_gateway_translate_rejected_and_audited(tmp_path):
    gw = _gateway(tmp_path / "audit.log")
    with pytest.raises(SecurityRejectedError):
        await gw.translate_sql("INSERT INTO t VALUES (1)", from_dialect="postgres", to_dialect="mysql")

    entries = [json.loads(line) for line in (tmp_path / "audit.log").read_text(encoding="utf-8").splitlines()]
    assert entries[-1]["tool"] == "translate_sql"
    assert entries[-1]["allowed"] is False
    assert "rule" in entries[-1]["detail"]


@pytest.mark.asyncio
async def test_gateway_translate_invalid_params_audited(tmp_path):
    gw = _gateway(tmp_path / "audit.log")
    with pytest.raises(InvalidParamsError):
        await gw.translate_sql("SELECT 1", from_dialect="oracle", to_dialect="mysql")

    entries = [json.loads(line) for line in (tmp_path / "audit.log").read_text(encoding="utf-8").splitlines()]
    assert entries[-1]["tool"] == "translate_sql"
    assert entries[-1]["allowed"] is False


# ---------- 工具层：注册 + 经 registry 调用 ----------

def _write_app_config(tmp_path) -> str:
    path = tmp_path / "config.toml"
    path.write_text(
        f"""
[server]
mode = "read_only"
[audit]
output = "file"
path = "{tmp_path / 'audit.log'}"
[semantic]
[metrics]
enabled = false
[connections.demo]
type = "postgres"
host = "127.0.0.1"
port = 5432
database = "orders"
user = "svc"
""",
        encoding="utf-8",
    )
    return str(path)


@pytest.mark.asyncio
async def test_translate_sql_tool_via_registry(tmp_path):
    """回归：工具层通过 default_gateway 调用（曾因网关非 async 而失败）。"""
    from db_assistant_mcp.config import load_config
    from db_assistant_mcp.runtime import RuntimeRegistry
    from db_assistant_mcp.semantic import Glossary
    from db_assistant_mcp.tools.translate_tools import register

    cfg = load_config(_write_app_config(tmp_path))
    registry = RuntimeRegistry(cfg, AuditLogger(cfg.audit), Glossary.load(None))
    fn = register(registry)["translate_sql"]
    result = await fn("SELECT `id` FROM `users` LIMIT 10, 20", from_dialect="mysql", to_dialect="postgres")
    assert result["source"] == "mysql"
    assert result["target"] == "postgres"
    assert "OFFSET" in result["sql"]
    assert result["duration_ms"] >= 0


@pytest.mark.asyncio
async def test_translate_sql_tool_rejected_returns_error_dict(tmp_path):
    from db_assistant_mcp.config import load_config
    from db_assistant_mcp.runtime import RuntimeRegistry
    from db_assistant_mcp.semantic import Glossary
    from db_assistant_mcp.tools.translate_tools import register

    cfg = load_config(_write_app_config(tmp_path))
    registry = RuntimeRegistry(cfg, AuditLogger(cfg.audit), Glossary.load(None))
    fn = register(registry)["translate_sql"]
    result = await fn("DROP TABLE t", from_dialect="postgres", to_dialect="mysql")
    assert result["error"]["error"] == "SECURITY_REJECTED"
