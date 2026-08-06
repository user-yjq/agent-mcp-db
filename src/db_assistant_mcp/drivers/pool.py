"""连接池：每配置连接独立池、健康检查、自动重连、空闲回收。"""

from __future__ import annotations

import asyncio
import time
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, TypeVar

from db_assistant_mcp.config import ConnectionConfig
from db_assistant_mcp.drivers.base import DatabaseConnection
from db_assistant_mcp.drivers.mysql import MysqlConnection
from db_assistant_mcp.drivers.postgres import PostgresConnection
from db_assistant_mcp.errors import ConnectionError_, ConnectionTimeoutError, QueryTimeoutError
from db_assistant_mcp.logging_utils import get_logger, log_context
from db_assistant_mcp.observability import metrics

T = TypeVar("T")

_CONNECTION_ERRORS = (
    ConnectionError,
    ConnectionResetError,
    ConnectionAbortedError,
    ConnectionRefusedError,
    BrokenPipeError,
    TimeoutError,
    OSError,
)


@dataclass
class PooledConnection:
    conn: DatabaseConnection
    created_at: float
    last_used: float


class DriverPool:
    """按连接名隔离的独立连接池。"""

    def __init__(
        self,
        config: ConnectionConfig,
        *,
        max_size: int,
        idle_ttl_sec: float = 300,
        connect_retries: int = 1,
    ) -> None:
        self._config = config
        self._max_size = max_size
        self._idle_ttl_sec = idle_ttl_sec
        self._connect_retries = connect_retries
        self._sem = asyncio.Semaphore(max_size)
        self._idle: deque[PooledConnection] = deque()
        self._active = 0
        self._closed = False
        self._logger = get_logger("db_assistant_mcp.pool")

    @property
    def name(self) -> str:
        return self._config.name

    def _make_conn(self) -> DatabaseConnection:
        if self._config.type == "postgres":
            return PostgresConnection(self._config)
        if self._config.type == "mysql":
            return MysqlConnection(self._config)
        raise ConnectionError_(f"不支持的连接类型: {self._config.type}", connection=self._config.name)

    async def _create(self) -> PooledConnection:
        conn = self._make_conn()
        last_exc: Exception | None = None
        for attempt in range(self._connect_retries + 1):
            try:
                await conn.connect()
                metrics.active_connections.labels(connection=self.name).inc()
                return PooledConnection(conn=conn, created_at=time.monotonic(), last_used=time.monotonic())
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                if attempt < self._connect_retries:
                    log_context(self._logger, 30, "连接失败，准备重试", connection=self.name, attempt=attempt + 1)
                    await asyncio.sleep(min(0.5 * (attempt + 1), 2))
        await conn.close()
        raise last_exc if last_exc else ConnectionError_(f"连接创建失败: {self._config.name}")

    async def _acquire(self) -> PooledConnection:
        if self._closed:
            raise ConnectionError_("连接池已关闭", connection=self.name)
        try:
            await asyncio.wait_for(self._sem.acquire(), timeout=self._config.connect_timeout_sec)
        except TimeoutError as exc:
            raise ConnectionTimeoutError(
                f"连接池耗尽（max_concurrent={self._max_size}），请稍后重试",
                detail="POOL_EXHAUSTED",
                connection=self.name,
            ) from exc
        try:
            while self._idle:
                pooled = self._idle.popleft()
                stale = time.monotonic() - pooled.last_used > self._idle_ttl_sec
                if stale or not await pooled.conn.is_valid():
                    await self._discard(pooled)
                    continue
                self._active += 1
                pooled.last_used = time.monotonic()
                return pooled
            pooled = await self._create()
            self._active += 1
            return pooled
        except Exception:
            self._sem.release()
            raise

    async def _release(self, pooled: PooledConnection, *, healthy: bool = True) -> None:
        self._active -= 1
        if healthy and not self._closed:
            pooled.last_used = time.monotonic()
            self._idle.append(pooled)
        else:
            await self._discard(pooled)
        self._sem.release()

    async def _discard(self, pooled: PooledConnection) -> None:
        try:
            await pooled.conn.close()
        except Exception:  # noqa: BLE001
            pass
        metrics.active_connections.labels(connection=self.name).dec()

    async def run(self, op: Callable[[DatabaseConnection], Awaitable[T]]) -> T:
        """执行数据库操作；连接级错误时自动重连一次。"""
        pooled = await self._acquire()
        try:
            result = await op(pooled.conn)
        except QueryTimeoutError:
            # 超时后连接状态不可信，丢弃并由下次调用重建
            await self._release(pooled, healthy=False)
            raise
        except _CONNECTION_ERRORS:
            await self._release(pooled, healthy=False)
            log_context(self._logger, 30, "连接中断，自动重连", connection=self.name)
            retry = await self._acquire()
            try:
                result = await op(retry.conn)
            except (QueryTimeoutError, *_CONNECTION_ERRORS):
                # 重试时连接级失败：状态不可信，丢弃而非归还空闲池
                await self._release(retry, healthy=False)
                raise
            except Exception:
                # 重试时 DB 端错误（如语法错误）通常不影响连接，归还复用
                await self._release(retry)
                raise
            else:
                await self._release(retry)
                return result
        except Exception:
            # DB 端错误（如语法错误）通常不影响连接，归还复用；
            # 若连接实际已失效，下次 acquire 时 is_valid() 会将其替换
            await self._release(pooled, healthy=True)
            raise
        else:
            await self._release(pooled)
            return result

    async def ping(self) -> dict[str, Any]:
        latency = await self.run(lambda conn: conn.ping())
        return {
            "connection": self.name,
            "ok": True,
            "latency_ms": latency,
            "dialect": self._config.type,
        }

    async def close(self) -> None:
        self._closed = True
        while self._idle:
            await self._discard(self._idle.popleft())
