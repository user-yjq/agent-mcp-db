"""HTTP 可观测性端点：/metrics（Prometheus）与 /healthz。"""

from __future__ import annotations

import asyncio

from aiohttp import web
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from db_assistant_mcp.runtime import RuntimeRegistry

_METRICS_CONTENT_TYPE = CONTENT_TYPE_LATEST.split("; charset")[0]


def _make_app(registry: RuntimeRegistry) -> web.Application:
    async def metrics_handler(_request: web.Request) -> web.Response:
        return web.Response(body=generate_latest(), content_type=_METRICS_CONTENT_TYPE)

    async def healthz_handler(_request: web.Request) -> web.Response:
        try:
            results = await asyncio.wait_for(registry.ping(), timeout=10)
            healthy = all(r.get("ok") for r in results.values()) if results else False
            status = "healthy" if healthy else "unhealthy"
            code = 200 if healthy else 503
            return web.json_response({"status": status, "connections": results}, status=code)
        except TimeoutError:
            return web.json_response(
                {"status": "unhealthy", "error": "health check timeout"}, status=503
            )

    app = web.Application()
    app.router.add_get("/metrics", metrics_handler)
    app.router.add_get("/healthz", healthz_handler)
    return app


async def start_http_server(registry: RuntimeRegistry, port: int) -> tuple[web.AppRunner, web.TCPSite]:
    runner = web.AppRunner(_make_app(registry))
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", port)
    await site.start()
    return runner, site
