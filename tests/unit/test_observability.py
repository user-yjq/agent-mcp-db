from __future__ import annotations

import pytest
from aiohttp.test_utils import TestClient, TestServer

from db_assistant_mcp.drivers.pool import DriverPool
from db_assistant_mcp.observability.http_server import _make_app
from db_assistant_mcp.runtime import RuntimeRegistry


class FakePool(DriverPool):
    def __init__(self) -> None:
        self._config = None

    async def ping(self) -> dict:
        return {"connection": "demo", "ok": True, "latency_ms": 2.0, "dialect": "postgres"}

    async def close(self) -> None:
        pass


class FakeRegistry(RuntimeRegistry):
    def __init__(self) -> None:
        pass

    @property
    def names(self) -> list[str]:
        return ["demo"]

    async def ping(self, name=None) -> dict:
        return {"demo": {"connection": "demo", "ok": True, "latency_ms": 2.0}}


@pytest.mark.asyncio
async def test_metrics_and_healthz_endpoints():
    app = _make_app(FakeRegistry())
    async with TestClient(TestServer(app)) as client:
        resp = await client.get("/healthz")
        assert resp.status == 200
        body = await resp.json()
        assert body["status"] == "healthy"

        resp = await client.get("/metrics")
        assert resp.status == 200
        text = await resp.text()
        assert "db_assistant_tool_calls_total" in text
        assert "db_assistant_query_duration_seconds" in text
        assert "db_assistant_security_rejections_total" in text
        assert "db_assistant_schema_cache_hits_total" in text
        assert "db_assistant_active_connections" in text


@pytest.mark.asyncio
async def test_healthz_unhealthy():
    class UnhealthyRegistry(FakeRegistry):
        async def ping(self, name=None) -> dict:
            return {"demo": {"connection": "demo", "ok": False, "error": "refused"}}

    app = _make_app(UnhealthyRegistry())
    async with TestClient(TestServer(app)) as client:
        resp = await client.get("/healthz")
        assert resp.status == 503
        body = await resp.json()
        assert body["status"] == "unhealthy"
