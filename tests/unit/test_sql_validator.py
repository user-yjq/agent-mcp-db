"""安全边界：SEC-001..024 + 危险函数 + 方言差异。"""

from __future__ import annotations

import pytest

from db_assistant_mcp.errors import SecurityRejectedError
from db_assistant_mcp.security.sql_validator import SqlValidator


@pytest.fixture(params=["postgres", "mysql"])
def validator(request):
    return SqlValidator(request.param)


@pytest.mark.parametrize(
    "sql",
    [
        "INSERT INTO users VALUES (1)",            # SEC-001
        "UPDATE users SET name='x'",               # SEC-002
        "DELETE FROM users",                       # SEC-003
        "DROP TABLE users",                        # SEC-004
        "TRUNCATE TABLE users",                    # SEC-005
        "CREATE TABLE x (a int)",                  # SEC-006
        "ALTER TABLE users ADD x int",             # SEC-007
        "GRANT ALL ON users TO bob",               # SEC-008
        "SELECT 1; DROP TABLE users",              # SEC-009 多语句
        "WITH x AS (INSERT INTO users VALUES (1) RETURNING *) SELECT * FROM x",  # SEC-010 嵌套写
        "SELECT pg_read_file('/etc/passwd')",      # SEC-011
        "SELECT LOAD_FILE('/etc/passwd')",         # SEC-012
        "SELECT * INTO OUTFILE '/tmp/x'",          # SEC-013
        "COPY users TO '/tmp/x'",                  # SEC-014
        "CALL some_procedure()",                   # SEC-015
        "BEGIN; SELECT 1; COMMIT",                 # SEC-016 事务
        "SELECT 1 -- comment\nSELECT 2",           # SEC-019 注释多语句
        "SELECT 'unterminated",                    # SEC-020 畸形
        "",                                        # SEC-021 空 SQL
        "SELECT * INTO newtab FROM users",         # PG SELECT INTO 会建表
        "SET statement_timeout = 100",             # 禁止 SET
        "USE app",                                 # 禁止 USE
        "SELECT pg_catalog.pg_read_file('/x')",    # schema 限定危险函数
        "SELECT sleep(5)",                         # MySQL 延迟
        "SELECT benchmark(1000000, md5('a'))",     # MySQL 压力
        "SELECT load_file('/etc/passwd')",         # 小写绕过
        "SELECT get_lock('x', 10)",
        "EXPLAIN DELETE FROM t",                   # EXPLAIN 写入
        "SELECT 'a' || 'b'; SELECT 2",             # 多语句
    ],
)
def test_rejected_statements(validator, sql):
    assert not validator.validate(sql).ok


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT 1",                               # happy path
        "select * from users",                    # 小写：解析器规范化，仍只读放行
        "SELECT a, b FROM t WHERE x = 1",
        "WITH x AS (SELECT 1 AS a) SELECT * FROM x",
        "/* comment */SELECT 1",                  # SEC-018 注释前缀
        "SELECT -- inline\n 1",
        "SELECT md5('x')",
        "SELECT now()",
        "SELECT * FROM t JOIN u ON t.id = u.id",
        "SELECT DISTINCT city FROM users",
        "SELECT count(*) FROM orders GROUP BY status",
        "EXPLAIN SELECT 1",                       # PG: Command 回退
        "EXPLAIN ANALYZE SELECT 1",
        "SHOW search_path",                       # PG SHOW
        "SELECT copy FROM t",                     # 列名 copy 不应误伤
        "SELECT 1 UNION SELECT 2",               # 集合查询只读
        "SELECT 1 UNION ALL SELECT 2",
        "SELECT 1 INTERSECT SELECT 2",
        "SELECT 1 EXCEPT SELECT 2",
        "(SELECT 1) UNION (SELECT 2)",
    ],
)
def test_allowed_statements(validator, sql):
    assert validator.validate(sql).ok


def test_set_ops_with_write_still_rejected(validator):
    """回归：集合查询放行后，混入写操作/危险函数仍必须拒绝。"""
    assert not validator.validate("SELECT 1 UNION ALL (INSERT INTO t VALUES (1) RETURNING 1)").ok
    assert not validator.validate("SELECT 1 UNION SELECT pg_sleep(1)").ok


def test_sql_too_long(validator):
    assert not validator.validate("SELECT 1 " * 30_000).ok


def test_unicode_identifiers(validator):
    assert validator.validate("SELECT \"列名\" FROM t").ok


def test_hex_encoding_bypass(validator):
    # 十六进制编码的内容若被解析器识别为危险函数则拒绝；无法解析则 fail-closed
    result = validator.validate("SELECT LOAD_FILE(0x2f6574632f706173737764)")
    assert not result.ok


def test_excluded_table_rejected():
    v = SqlValidator("postgres", exclude_tables=["audit_log", "raw_events"])
    result = v.validate("SELECT * FROM audit_log")
    assert not result.ok
    assert result.rule == "EXCLUDED_TABLE"
    assert result.tables == ["audit_log"]
    # 大小写不敏感
    assert not v.validate("SELECT * FROM RAW_EVENTS").ok
    # 未排除的表放行
    assert v.validate("SELECT * FROM orders").ok


def test_ensure_read_only_raises_generic_message(validator):
    with pytest.raises(SecurityRejectedError) as exc_info:
        validator.ensure_read_only("DROP TABLE users")
    payload = exc_info.value.to_dict()
    assert payload["error"] == "SECURITY_REJECTED"
    assert "DROP" not in payload["message"]  # 不暴露具体拒绝原因
    assert exc_info.value.detail  # 明细仅在日志/审计


def test_add_limit():
    v = SqlValidator("postgres")
    assert v.add_limit("SELECT * FROM users", 100) == "SELECT * FROM users LIMIT 101"
    assert v.add_limit("SELECT * FROM users LIMIT 5", 100) is None  # 无需改写
    assert v.add_limit("SELECT * FROM users LIMIT 1000000", 100) == "SELECT * FROM users LIMIT 101"
    assert v.add_limit("EXPLAIN SELECT 1", 100) is None
    assert v.add_limit("SHOW TABLES", 100) is None


def test_nul_byte_rejected(validator):
    # NUL 字节不应进入 SQL（fail-closed），无论出现在中间还是末尾
    result = validator.validate("SEL\x00ECT 1")
    assert not result.ok
    assert result.rule == "NUL_BYTE"
    assert not validator.validate("SELECT 1\x00").ok


def test_whitespace_confusion_rejected(validator):
    # 零宽空格 / 退格等混淆字符：解析结果不确定即拒绝
    assert not validator.validate("SELECT\u200b1").ok
    assert not validator.validate("SELECT\x081").ok


def test_parser_edge_cases_allowed(validator):
    # 嵌套注释与字符串内的危险关键字是只读内容，应放行（真实解析器而非正则）
    assert validator.validate("SELECT /* /* nested */ */ 1").ok
    assert validator.validate("SELECT 'DROP' || 'TABLE'").ok


MYSQL_ONLY_DANGEROUS_FUNCTIONS = [
    "SELECT LOAD_FILE('/etc/passwd')",
    "SELECT BENCHMARK(1000000, MD5('a'))",
    "SELECT GET_LOCK('x', 10)",
    "SELECT SLEEP(5)",
]


def test_mysql_specific_dangerous_functions_rejected():
    # 显式锁定 MySQL 专属危险函数（通用 fixture 已双方言覆盖，此处固化意图）
    v = SqlValidator("mysql")
    for sql in MYSQL_ONLY_DANGEROUS_FUNCTIONS:
        result = v.validate(sql)
        assert not result.ok
        assert result.rule.startswith("DANGEROUS_FUNCTION")
