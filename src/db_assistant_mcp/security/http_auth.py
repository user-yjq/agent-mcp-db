"""HTTP Bearer token 鉴权：恒定时间比较，fail-closed。

token 只经环境变量注入，不落盘、不进日志（审计/异常信息均不包含 token 本身）。
"""

from __future__ import annotations

import hmac
import os
from typing import Any

from starlette.responses import JSONResponse

from db_assistant_mcp.config import HttpConfig
from db_assistant_mcp.errors import ConfigError


def resolve_http_token(http: HttpConfig) -> str:
    """解析 HTTP token；未配置或为空时拒绝启动（fail-closed）。"""
    if not http.token_env:
        raise ConfigError(
            "HTTP 模式必须配置 [http] token_env（如 DB_ASSISTANT_HTTP_TOKEN）",
            detail="HTTP_TOKEN_ENV_MISSING",
            hint="在配置文件中添加 [http] token_env = \"DB_ASSISTANT_HTTP_TOKEN\" 并导出对应环境变量",
        )
    token = os.environ.get(http.token_env, "").strip()
    if not token:
        raise ConfigError(
            f"HTTP token 环境变量 {http.token_env} 未设置或为空",
            detail="HTTP_TOKEN_EMPTY",
            hint=f"先执行 export {http.token_env}=<随机长令牌> 再启动",
        )
    return token


class BearerTokenMiddleware:
    """ASGI 中间件：校验 Authorization: Bearer <token>，失败返回 401。"""

    def __init__(self, app: Any, token: str) -> None:
        self.app = app
        self._expected = f"Bearer {token}".encode()

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = {k.lower(): v for k, v in (scope.get("headers") or [])}
        provided = headers.get(b"authorization", b"")
        # 恒定时间比较：长度差异不影响时序，且不泄露 token 内容
        if not hmac.compare_digest(provided, self._expected):
            response = JSONResponse(
                {"error": "UNAUTHORIZED", "message": "缺少或无效的访问令牌（Authorization: Bearer <token>）"},
                status_code=401,
            )
            await response(scope, receive, send)
            return
        await self.app(scope, receive, send)
