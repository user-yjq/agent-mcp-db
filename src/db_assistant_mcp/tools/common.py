"""工具公共设施：统一错误包装。"""

from __future__ import annotations

import functools
from collections.abc import Awaitable, Callable
from typing import Any

from db_assistant_mcp.errors import AppError, ErrorCode
from db_assistant_mcp.logging_utils import get_logger, log_context
from db_assistant_mcp.runtime import RuntimeRegistry

logger = get_logger("db_assistant_mcp.tools")


def tool_handler(registry: RuntimeRegistry, tool_name: str) -> Callable[[Callable[..., Awaitable[Any]]], Callable[..., Awaitable[Any]]]:
    """统一错误处理：AppError 转为结构化 JSON，未知异常降级为 INTERNAL_ERROR。"""

    def decorator(fn: Callable[..., Awaitable[Any]]) -> Callable[..., Awaitable[Any]]:
        @functools.wraps(fn)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            try:
                return await fn(*args, **kwargs)
            except AppError as exc:
                log_context(logger, 30, "工具调用失败", tool=tool_name, code=exc.code.value)
                return {"error": exc.to_dict()}
            except Exception as exc:  # noqa: BLE001
                log_context(logger, 50, "工具内部错误", tool=tool_name, error=str(exc)[:500])
                return {"error": {"error": ErrorCode.INTERNAL_ERROR.value, "message": "工具内部错误"}}

        wrapper.__tool_name__ = tool_name  # type: ignore[attr-defined]
        return wrapper

    return decorator
