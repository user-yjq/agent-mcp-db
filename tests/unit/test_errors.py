from __future__ import annotations

from db_assistant_mcp.errors import (
    AI_DETAIL_MAX,
    ErrorCode,
    SecurityRejectedError,
    TableNotFoundError,
)


def test_error_codes_exist():
    for code in [
        "CONFIG_ERROR", "CONNECTION_FAILED", "CONNECTION_TIMEOUT", "QUERY_TIMEOUT",
        "SECURITY_REJECTED", "INVALID_PARAMS", "TABLE_NOT_FOUND", "INTERNAL_ERROR",
    ]:
        assert ErrorCode(code) is not None  # Python 3.11 StrEnum 不支持 str in Enum


def test_to_dict_includes_truncated_detail():
    """C-1: detail 截断后透出给 AI，便于模型自纠。"""
    exc = SecurityRejectedError(
        "语句被拒绝",
        detail="FORBIDDEN_ROOT:drop",
        connection="prod",
        hint="请改为只读查询",
    )
    payload = exc.to_dict()
    assert payload["error"] == "SECURITY_REJECTED"
    assert payload["detail"] == "FORBIDDEN_ROOT:drop"
    assert payload["hint"]
    assert payload["connection"] == "prod"


def test_to_dict_truncates_overlong_detail():
    exc = SecurityRejectedError("语句被拒绝", detail="x" * (AI_DETAIL_MAX * 2))
    payload = exc.to_dict()
    assert len(payload["detail"]) == AI_DETAIL_MAX


def test_to_dict_omits_detail_when_absent():
    exc = SecurityRejectedError("语句被拒绝")
    assert "detail" not in exc.to_dict()


def test_table_not_found_hint():
    exc = TableNotFoundError("表不存在", detail="TABLE_NOT_FOUND:x", connection="dev")
    assert exc.to_dict()["error"] == "TABLE_NOT_FOUND"
    assert exc.audit_detail()["detail"] == "TABLE_NOT_FOUND:x"
