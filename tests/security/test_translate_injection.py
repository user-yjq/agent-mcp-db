"""安全回归（T-4.4）：translate_sql 产物注入必须 fail-closed。

验证各种试图让转换产物绕过只读校验/携带写语句的构造全部被拒绝，
或转换成功且产物仍为只读。
"""

from __future__ import annotations

import pytest

from db_assistant_mcp.errors import InvalidParamsError, SecurityRejectedError
from db_assistant_mcp.translate import translate_sql


@pytest.mark.parametrize(
    ("sql", "src", "dst"),
    [
        # 注释/空白混淆写语句
        ("SELECT 1; DROP TABLE users", "postgres", "mysql"),
        ("SELECT 1;-- DROP TABLE users", "postgres", "mysql"),
        ("SELECT 1\\n\\n\\nDROP TABLE users", "postgres", "mysql"),
        # 大小写混淆
        ("SELECT 1; dRoP TABLE users", "postgres", "mysql"),
        ("SELECt pg_sleep(1)", "postgres", "mysql"),
        ("SELECT PG_SLEEP(1)", "postgres", "mysql"),
        # 函数名注释分割
        ("SELECT pg_/**/sleep(1)", "postgres", "mysql"),
        # CTE/子查询隐藏写操作
        ("WITH x AS (INSERT INTO t VALUES (1) RETURNING 1) SELECT * FROM x", "postgres", "mysql"),
        ("SELECT (SELECT load_file('/etc/passwd'))", "mysql", "postgres"),
        # INTO OUTFILE 等写文件路径
        ("SELECT * INTO OUTFILE '/tmp/x'", "mysql", "postgres"),
        ("SELECT * FROM t INTO DUMPFILE '/tmp/x'", "mysql", "postgres"),
        # 事务控制
        ("BEGIN; SELECT 1; COMMIT", "postgres", "mysql"),
        # NUL 字节
        ("SELECT \x001", "postgres", "mysql"),
    ],
)
def test_injection_inputs_never_produce_writable_output(sql, src, dst):
    with pytest.raises((SecurityRejectedError, InvalidParamsError)):
        translate_sql(sql, src, dst)


def test_string_literal_with_keywords_is_harmless():
    """字符串字面量里的危险关键字不是语句结构，应正常转换且保持只读。"""
    result = translate_sql("SELECT 'DROP' || ' TABLE'", "postgres", "mysql")
    assert "DROP" in result["sql"]  # 仅作为字符串保留
    assert result["sql"].upper().startswith("SELECT")


def test_comment_only_injection_preserved_as_readonly():
    """注释中的写语句文本不是实际语句（sqlglot 剥离），转换产物仍通过只读校验。"""
    from db_assistant_mcp.security.sql_validator import SqlValidator

    validator = SqlValidator("mysql")
    for sql in ("SELECT 1 /* DROP TABLE users */", "SELECT 1 /*; DROP TABLE users */"):
        result = translate_sql(sql, "postgres", "mysql")
        assert validator.validate(result["sql"]).ok


def test_unicode_homoglyph_not_mistaken_for_visible_function():
    """零宽/同形字符不会被解析成真实函数调用（解析失败或拒绝，均 fail-closed）。"""
    try:
        result = translate_sql("SELECT p\u200bg_sleep(1)", "postgres", "mysql")
        # 若解析成功，产物必须仍只读
        assert not any(k in result["sql"].lower() for k in ("pg_sleep", "insert", "drop", "update"))
    except (SecurityRejectedError, InvalidParamsError):
        pass  # 拒绝即安全


def test_transpile_output_always_validated_against_target_dialect():
    """转换产物回验使用目标方言 validator，非只读/危险函数跨方言仍被拒。"""
    with pytest.raises(SecurityRejectedError):
        translate_sql("SELECT get_lock('x', 10)", "mysql", "postgres")
    with pytest.raises(SecurityRejectedError):
        translate_sql("SELECT pg_sleep(1)", "postgres", "mysql")
