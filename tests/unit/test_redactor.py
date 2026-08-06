"""脱敏与排除：MASK-001..006。"""

from __future__ import annotations

from db_assistant_mcp.config import ConnectionConfig
from db_assistant_mcp.security.redactor import Redactor


def _redactor(**kwargs):
    defaults = dict(
        name="t",
        type="postgres",
        host="h",
        port=5432,
        database="d",
        user="u",
        masked_columns=["phone", "email"],
        exclude_columns=["users.salary", "card_number"],
        exclude_tables=["audit_log"],
    )
    defaults.update(kwargs)
    return Redactor(ConnectionConfig(**defaults))


def test_masked_column_value():  # MASK-001
    r = _redactor()
    cols, rows = r.filter_result(["phone", "name"], [[13800138000, "alice"]])
    assert rows == [["***", "alice"]]


def test_masked_column_alias():  # MASK-002
    r = _redactor()
    projections = [
        {"is_star": False, "table": "users", "column": "phone"},
        {"is_star": False, "table": "users", "column": "name"},
    ]
    cols, rows = r.filter_result(["p", "name"], [[13800138000, "alice"]], projections)
    assert rows == [["***", "alice"]]


def test_qualified_exclusion_with_table():
    r = _redactor()
    projections = [
        {"is_star": False, "table": "users", "column": "salary"},
        {"is_star": False, "table": "users", "column": "name"},
    ]
    cols, rows = r.filter_result(["s", "name"], [[100, "alice"]], projections)
    assert cols == ["name"]


def test_excluded_column_hidden_from_result():  # MASK-003 / MASK-005
    r = _redactor()
    cols, rows = r.filter_result(["card_number", "name", "salary"], [["4111", "alice", 99]])
    assert cols == ["name"]
    assert rows == [["alice"]]


def test_select_star_masked():  # MASK-004
    r = _redactor()
    cols, rows = r.filter_result(["id", "phone", "name"], [[1, "138", "bob"]])
    assert rows == [[1, "***", "bob"]]


def test_excluded_table():  # MASK-006
    r = _redactor()
    assert r.is_excluded_table("AUDIT_LOG")
    assert not r.is_excluded_table("orders")


def test_auto_sensitive_patterns():
    r = _redactor(masked_columns=[], exclude_columns=[])
    for name in ["password_hash", "access_token", "client_secret", "api_key", "id_card", "mobile_no", "phone_number"]:
        cols, rows = r.filter_result([name], [["x"]])
        assert rows == [["***"]], name


def test_qualified_exclusion():
    r = _redactor()
    cols, _ = r.filter_result(["salary", "name"], [[10, "a"]])
    assert cols == ["name"]


def test_schema_filter_keeps_masked_marks():
    r = _redactor()
    cols = [
        {"name": "phone", "data_type": "text"},
        {"name": "salary", "data_type": "int"},
        {"name": "name", "data_type": "text"},
    ]
    out = r.filter_schema("users", cols)
    names = [c["name"] for c in out]
    assert names == ["phone", "name"]
    assert out[0]["masked"] is True
