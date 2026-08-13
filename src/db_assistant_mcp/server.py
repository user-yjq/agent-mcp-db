"""MCP Server 装配：FastMCP（stdio / streamable HTTP 共用）。

- stdio 模式：`run_stdio()`（无鉴权，本地进程内协议）
- HTTP 模式：`run_http()` / `build_http_app()`（必须配置 [http] token_env，fail-closed）
两个模式共用同一 create_server 装配，工具/资源/lifespan 无重复代码。
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import uvicorn
from mcp.server.fastmcp import FastMCP
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from starlette.applications import Starlette
from starlette.responses import JSONResponse, Response

from db_assistant_mcp.config import AppConfig, AuditConfig, HttpConfig, load_config
from db_assistant_mcp.logging_utils import get_logger, log_context
from db_assistant_mcp.observability.http_server import start_http_server
from db_assistant_mcp.resources.schema_resource import register as register_resources
from db_assistant_mcp.runtime import RuntimeRegistry
from db_assistant_mcp.security.audit import AuditLogger
from db_assistant_mcp.security.http_auth import BearerTokenMiddleware, resolve_http_token
from db_assistant_mcp.semantic import Glossary
from db_assistant_mcp.tools import (
    admin_tools,
    explain_tools,
    query_tools,
    schema_tools,
    translate_tools,
)

logger = get_logger("db_assistant_mcp.server")


class DbAssistantFastMCP(FastMCP):
    """FastMCP + 运行期 registry 引用（供 /healthz /metrics 路由使用，替代私有属性 hack）。"""

    def __init__(self, registry: RuntimeRegistry, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.registry = registry


def create_server(
    app_config: AppConfig, *, host: str | None = None, port: int | None = None
) -> DbAssistantFastMCP:
    """装配 MCP Server（stdio/HTTP 共用）。host/port 仅影响 streamable-http 模式。"""
    audit_config = app_config.audit
    if host is None and audit_config.output == "stdout":
        # C-3：stdio 传输下 stdout 是 JSON-RPC 协议流，审计降级到 stderr 防止污染
        log_context(
            logger, 30,
            "stdio 模式下 [audit] output=stdout 已降级为 stderr（保护 JSON-RPC 协议流）",
        )
        audit_config = AuditConfig(
            output="stderr",
            path=audit_config.path,
            webhook_url=audit_config.webhook_url,
            webhook_secret_env=audit_config.webhook_secret_env,
        )
    audit = AuditLogger(audit_config)
    glossary = Glossary.load(app_config.semantic.glossary_file)
    registry = RuntimeRegistry(app_config, audit, glossary)

    @asynccontextmanager
    async def lifespan(_server: FastMCP) -> AsyncIterator[dict[str, object]]:
        http_runner = None
        reload_task = None
        try:
            if app_config.metrics.enabled:
                try:
                    http_runner, _site = await start_http_server(registry, app_config.metrics.port)
                    log_context(logger, 20, "指标端点已启动", port=app_config.metrics.port)
                except OSError as exc:
                    log_context(logger, 40, "指标端点启动失败（端口冲突）", port=app_config.metrics.port, error=str(exc))
            reload_interval = app_config.server.config_reload_interval_sec
            if reload_interval > 0:
                reload_task = asyncio.create_task(
                    _config_reload_loop(registry, app_config.config_path, reload_interval)
                )
            yield {"registry": registry}
        finally:
            # 无论正常退出还是异常/取消，都保证任务被回收、连接被关闭
            if reload_task is not None:
                reload_task.cancel()
                try:
                    await reload_task
                except asyncio.CancelledError:
                    pass
            if http_runner:
                await http_runner.cleanup()
            await registry.close_all()

    kwargs: dict[str, Any] = {}
    if host is not None:
        kwargs["host"] = host
    if port is not None:
        kwargs["port"] = port

    mcp = DbAssistantFastMCP(
        registry,
        "db-assistant-mcp",
        instructions=(
            "PostgreSQL/MySQL 只读数据库助手。所有查询默认只读，自动限制行数与超时，"
            "敏感列已脱敏。先通过 list_databases 查看连接，再用 get_table_schema / search_schema "
            "获取结构，最后用 execute_query / explain_query 完成查询与优化。"
        ),
        lifespan=lifespan,
        **kwargs,
    )

    for fn in schema_tools.register(registry).values():
        mcp.tool()(fn)
    for fn in query_tools.register(registry).values():
        mcp.tool()(fn)
    for fn in explain_tools.register(registry).values():
        mcp.tool()(fn)
    for fn in translate_tools.register(registry).values():
        mcp.tool()(fn)
    for fn in admin_tools.register(registry).values():
        mcp.tool()(fn)

    resources = register_resources(registry)
    mcp.resource("db://{name}/schema")(resources["schema"])
    mcp.resource("db://{name}/tables")(resources["tables"])
    mcp.resource("db://{name}/semantic")(resources["semantic"])

    return mcp


def _attach_observability_routes(app: Starlette, registry: RuntimeRegistry) -> None:
    """HTTP 模式下把 /healthz 与 /metrics 挂到 MCP 端口（与 9102 指标端点并存）。"""

    async def metrics_handler(_request: Any) -> Response:
        return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST.split(";")[0])

    async def healthz_handler(_request: Any) -> JSONResponse:
        try:
            results = await asyncio.wait_for(registry.ping(), timeout=10)
            healthy = all(r.get("ok") for r in results.values()) if results else False
            return JSONResponse(
                {"status": "healthy" if healthy else "unhealthy", "connections": results},
                status_code=200 if healthy else 503,
            )
        except TimeoutError:
            return JSONResponse({"status": "unhealthy", "error": "health check timeout"}, status_code=503)

    app.add_route("/healthz", healthz_handler, methods=["GET"])
    app.add_route("/metrics", metrics_handler, methods=["GET"])


def _config_stat(path: str) -> tuple[int, int] | None:
    try:
        st = Path(path).stat()
    except OSError:
        return None
    return st.st_mtime_ns, st.st_size


async def reload_config_if_changed(
    registry: RuntimeRegistry, config_path: str, last_stat: tuple[int, int] | None
) -> tuple[bool, dict[str, Any] | None]:
    """配置文件 mtime/size 变化时重新加载并热重载；无效配置抛 ConfigError（由调用方兜底）。"""
    current = _config_stat(config_path)
    if current == last_stat:
        return False, None
    new_config = load_config(config_path)
    summary = await registry.reload(new_config)
    return True, summary


async def _config_reload_loop(registry: RuntimeRegistry, config_path: str, interval: float) -> None:
    """周期轮询配置文件变更并热重载；加载失败保留旧配置继续服务（下个周期自动重试）。"""
    last_stat = _config_stat(config_path)
    while True:
        try:
            await asyncio.sleep(interval)
        except asyncio.CancelledError:
            # 显式处理取消，避免被下方 except Exception 吞掉（Py3.11+ CancelledError 虽已是
            # BaseException，但保持意图明确，防止未来重构引入回归）
            raise
        try:
            changed, summary = await reload_config_if_changed(registry, config_path, last_stat)
        except Exception as exc:  # noqa: BLE001
            log_context(logger, 40, "配置热重载失败，保留当前配置", error=str(exc)[:500])
            continue
        if changed:
            last_stat = _config_stat(config_path)
            log_context(logger, 20, "配置热重载完成", summary=str(summary))


def build_http_app(
    app_config: AppConfig, *, host: str | None = None, port: int | None = None, token: str | None = None
) -> Starlette:
    """构建 streamable HTTP ASGI 应用：鉴权中间件 + /healthz /metrics + MCP 端点 /mcp。"""
    http: HttpConfig = app_config.http
    effective_host = host or http.host
    effective_port = port or http.port
    auth_token = token if token is not None else resolve_http_token(http)
    mcp = create_server(app_config, host=effective_host, port=effective_port)
    app = mcp.streamable_http_app()
    _attach_observability_routes(app, mcp.registry)
    return BearerTokenMiddleware(app, auth_token)


def run_stdio(config_path: str | None = None) -> None:
    app_config = load_config(config_path)
    mcp = create_server(app_config)
    mcp.run(transport="stdio")


def run_http(config_path: str | None = None, host: str | None = None, port: int | None = None) -> None:
    """启动 streamable HTTP 模式；未配置/缺少 token 时拒绝启动（fail-closed）。"""
    app_config = load_config(config_path)
    http: HttpConfig = app_config.http
    effective_host = host or http.host
    effective_port = port or http.port
    auth_token = resolve_http_token(http)
    app = build_http_app(app_config, host=effective_host, port=effective_port, token=auth_token)
    log_context(
        logger, 20, "HTTP 模式启动",
        host=effective_host, port=effective_port,
        token_env=http.token_env, endpoint=f"http://{effective_host}:{effective_port}/mcp",
    )
    uvicorn.run(app, host=effective_host, port=effective_port, log_level="info")
