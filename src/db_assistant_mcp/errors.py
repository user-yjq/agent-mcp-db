"""统一错误码与异常类型。

所有工具调用返回结构化 JSON；detail 截断后透出给 AI 便于自纠，
敏感原始值仍仅写入审计/日志。
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any


class ErrorCode(StrEnum):
    CONFIG_ERROR = "CONFIG_ERROR"
    CONNECTION_FAILED = "CONNECTION_FAILED"
    CONNECTION_TIMEOUT = "CONNECTION_TIMEOUT"
    QUERY_TIMEOUT = "QUERY_TIMEOUT"
    SECURITY_REJECTED = "SECURITY_REJECTED"
    INVALID_PARAMS = "INVALID_PARAMS"
    TABLE_NOT_FOUND = "TABLE_NOT_FOUND"
    DATABASE_NOT_FOUND = "DATABASE_NOT_FOUND"
    RESOURCE_NOT_FOUND = "RESOURCE_NOT_FOUND"
    INTERNAL_ERROR = "INTERNAL_ERROR"
    AUDIT_ERROR = "AUDIT_ERROR"


# 透出给 AI 的 detail 最大长度（截断防泄露）
AI_DETAIL_MAX = 300


class AppError(Exception):
    """业务异常的基类，统一携带错误码、明细与给 AI 的提示。"""

    code: ErrorCode = ErrorCode.INTERNAL_ERROR

    def __init__(
        self,
        message: str,
        *,
        code: ErrorCode | None = None,
        detail: str | None = None,
        connection: str | None = None,
        hint: str | None = None,
        context: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.code = code or self.code
        self.detail = detail
        self.connection = connection
        self.hint = hint
        self.context = context or {}

    def to_dict(self) -> dict[str, Any]:
        """面向 AI 客户端的结构化错误（detail 截断后透出，便于模型自纠）。"""
        payload: dict[str, Any] = {
            "error": self.code.value,
            "message": self.message,
        }
        if self.detail is not None:
            payload["detail"] = self.detail[:AI_DETAIL_MAX]
        if self.connection is not None:
            payload["connection"] = self.connection
        if self.hint is not None:
            payload["hint"] = self.hint
        if self.context:
            payload["context"] = self.context
        return payload

    def audit_detail(self) -> dict[str, Any]:
        """仅写入审计日志的详细原因。"""
        return {
            "detail": self.detail or self.message,
            "code": self.code.value,
            **(self.context or {}),
        }


class ConfigError(AppError):
    code = ErrorCode.CONFIG_ERROR


class ConnectionError_(AppError):
    code = ErrorCode.CONNECTION_FAILED


class ConnectionTimeoutError(AppError):
    code = ErrorCode.CONNECTION_TIMEOUT


class QueryTimeoutError(AppError):
    code = ErrorCode.QUERY_TIMEOUT


class SecurityRejectedError(AppError):
    code = ErrorCode.SECURITY_REJECTED


class InvalidParamsError(AppError):
    code = ErrorCode.INVALID_PARAMS


class TableNotFoundError(AppError):
    code = ErrorCode.TABLE_NOT_FOUND


class DatabaseNotFoundError(AppError):
    code = ErrorCode.DATABASE_NOT_FOUND


class ResourceNotFoundError(AppError):
    code = ErrorCode.RESOURCE_NOT_FOUND


class AuditError(AppError):
    code = ErrorCode.AUDIT_ERROR

