"""连接池边界：CONN-004 自动重连、CONN-005 池耗尽、空闲回收、超时丢弃。"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from db_assistant_mcp.config import ConnectionConfig
from db_assistant_mcp.drivers.base import DatabaseConnection
from db_assistant_mcp.drivers.pool import DriverPool
from db_assistant_mcp.errors import ConnectionTimeoutError, QueryTimeoutError


class FakeConn(DatabaseConnection):
    dialect = "postgres"

    def __init__(self, fail_first=False, valid=True, fetch_sleep=0) -> None:
        self.fail_first = fail_first
        self._valid = valid
        self.fetch_sleep = fetch_sleep
        self.calls = 0
        self.closed = False

    async def close(self) -> None:
        self.closed = True

    async def connect(self) -> None:
        self.closed = False

    async def is_valid(self) -> bool:
        return self._valid and not self.closed

    async def ping(self) -> float:
        return 1.0

    async def fetch(self, sql: str, timeout: float) -> tuple[list[str], list[list[Any]]]:
        self.calls += 1
        if self.fail_first and self.calls == 1:
            raise ConnectionResetError("connection reset")
        if self.fetch_sleep:
            try:
                await asyncio.wait_for(asyncio.sleep(self.fetch_sleep), timeout=timeout)
            except TimeoutError as exc:
                raise QueryTimeoutError("查询超时", detail="QUERY_TIMEOUT") from exc
        return ["one"], [[1]]

    async def list_tables(self) -> list[dict[str, Any]]:
        return []

    async def table_schema(self, table: str) -> dict[str, Any]:
        return {"columns": [], "indexes": [], "foreign_keys": []}

    async def search_schema(self, keyword: str) -> dict[str, list[dict[str, str]]]:
        return {"tables": [], "columns": []}

    async def explain(self, sql: str, analyze: bool, timeout: float) -> dict[str, Any]:
        return {}


def _cfg(**kwargs) -> ConnectionConfig:
    defaults = dict(name="t", type="postgres", host="h", port=5432, database="d", user="u")
    defaults.update(kwargs)
    return ConnectionConfig(**defaults)


@pytest.mark.asyncio
async def test_run_and_release_to_idle():
    pool = DriverPool(_cfg(), max_size=2)
    conn = FakeConn()
    pool._make_conn = lambda: conn  # type: ignore[method-assign]
    result = await pool.run(lambda c: c.fetch("SELECT 1", 5))
    assert result == (["one"], [[1]])
    assert len(pool._idle) == 1  # 归还空闲池
    await pool.close()
    assert conn.closed


@pytest.mark.asyncio
async def test_connection_error_retries_once():  # CONN-004
    pool = DriverPool(_cfg(), max_size=2)
    first = FakeConn(fail_first=True)
    second = FakeConn()
    created = {"n": 0}

    def make():
        created["n"] += 1
        return first if created["n"] == 1 else second

    pool._make_conn = make  # type: ignore[method-assign]
    result = await pool.run(lambda c: c.fetch("SELECT 1", 5))
    assert result == (["one"], [[1]])
    assert created["n"] == 2  # 第一次失败后自动重建
    assert first.closed
    await pool.close()


@pytest.mark.asyncio
async def test_pool_exhaustion_times_out():  # CONN-005
    cfg = _cfg(connect_timeout_sec=1)
    pool = DriverPool(cfg, max_size=1)
    slow = FakeConn(fetch_sleep=5)
    pool._make_conn = lambda: slow  # type: ignore[method-assign]
    task = asyncio.create_task(pool.run(lambda c: c.fetch("SELECT 1", 10)))
    await asyncio.sleep(0.1)
    with pytest.raises(ConnectionTimeoutError):
        await pool.run(lambda c: c.fetch("SELECT 1", 5))
    await task
    await pool.close()


@pytest.mark.asyncio
async def test_stale_connection_recycled():
    pool = DriverPool(_cfg(), max_size=2, idle_ttl_sec=0.01)
    stale = FakeConn(valid=False)
    fresh = FakeConn()
    created = {"n": 0}

    def make():
        created["n"] += 1
        return stale if created["n"] == 1 else fresh

    pool._make_conn = make  # type: ignore[method-assign]
    await pool.run(lambda c: c.fetch("SELECT 1", 5))
    await asyncio.sleep(0.05)
    result = await pool.run(lambda c: c.fetch("SELECT 1", 5))
    assert result == (["one"], [[1]])
    assert created["n"] == 2
    assert stale.closed
    await pool.close()


@pytest.mark.asyncio
async def test_timeout_discards_connection():
    pool = DriverPool(_cfg(), max_size=2)
    conn = FakeConn(fetch_sleep=5)
    pool._make_conn = lambda: conn  # type: ignore[method-assign]
    with pytest.raises(QueryTimeoutError):
        await pool.run(lambda c: c.fetch("SELECT 1", 0.1))
    assert conn.closed  # 超时后连接被丢弃
    await pool.close()


class TrackedConn(FakeConn):
    """记录并发活跃数，用于验证信号量上限。"""

    def __init__(self, state: dict[str, int], **kwargs) -> None:
        super().__init__(**kwargs)
        self._state = state

    async def fetch(self, sql: str, timeout: float) -> tuple[list[str], list[list[Any]]]:
        self._state["active"] += 1
        self._state["max_active"] = max(self._state["max_active"], self._state["active"])
        try:
            await asyncio.sleep(self.fetch_sleep)
            return ["one"], [[1]]
        finally:
            self._state["active"] -= 1


@pytest.mark.asyncio
async def test_concurrent_run_invariants():
    """并发压力：信号量上限不破、连接复用、归还后无泄漏/重复。"""
    state = {"active": 0, "max_active": 0}
    created = {"n": 0, "conns": []}
    pool = DriverPool(_cfg(), max_size=3)

    def make():
        created["n"] += 1
        conn = TrackedConn(state, fetch_sleep=0.01)
        created["conns"].append(conn)
        return conn

    pool._make_conn = make  # type: ignore[method-assign]
    results = await asyncio.gather(
        *(pool.run(lambda c: c.fetch("SELECT 1", 5)) for _ in range(20))
    )
    assert all(r == (["one"], [[1]]) for r in results)
    assert created["n"] == 3  # 只创建 max_size 个连接，其余复用
    assert state["max_active"] <= 3  # 信号量上限未被突破
    assert pool._active == 0  # 全部归还
    assert len(pool._idle) == created["n"]  # 无泄漏/重复归还
    await pool.close()


@pytest.mark.asyncio
async def test_retry_timeout_discards_retry_conn():
    """回归：重试路径上查询超时时，连接必须被丢弃而非归还空闲池。"""
    pool = DriverPool(_cfg(), max_size=2)
    first = FakeConn(fail_first=True)
    second = FakeConn(fetch_sleep=5)
    created = {"n": 0}

    def make():
        created["n"] += 1
        return first if created["n"] == 1 else second

    pool._make_conn = make  # type: ignore[method-assign]
    with pytest.raises(QueryTimeoutError):
        await pool.run(lambda c: c.fetch("SELECT 1", 0.1))
    assert first.closed
    assert second.closed  # 超时后丢弃
    assert len(pool._idle) == 0
    await pool.close()


@pytest.mark.asyncio
async def test_retry_connection_error_discards_retry_conn():
    """回归：重试连接再次出现连接级错误时，也应丢弃而非归还。"""

    class FailAlways(FakeConn):
        async def fetch(self, sql: str, timeout: float) -> tuple[list[str], list[list[Any]]]:
            raise ConnectionResetError("connection reset again")

    pool = DriverPool(_cfg(), max_size=2)
    created = {"n": 0}
    conns: list[FakeConn] = []

    def make():
        created["n"] += 1
        conn = FailAlways() if created["n"] > 1 else FakeConn(fail_first=True)
        conns.append(conn)
        return conn

    pool._make_conn = make  # type: ignore[method-assign]
    with pytest.raises(ConnectionResetError):
        await pool.run(lambda c: c.fetch("SELECT 1", 5))
    assert created["n"] == 2
    assert all(c.closed for c in conns)  # 两次失败连接均被丢弃
    assert len(pool._idle) == 0
    await pool.close()
