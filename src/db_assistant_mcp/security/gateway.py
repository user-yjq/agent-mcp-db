"""安全网关：所有数据库操作的唯一出口。"""

from __future__ import annotations

import time
from typing import Any

from db_assistant_mcp.config import ConnectionConfig, ServerConfig
from db_assistant_mcp.drivers.base import DatabaseConnection
from db_assistant_mcp.drivers.pool import DriverPool
from db_assistant_mcp.errors import AppError, ErrorCode, SecurityRejectedError, TableNotFoundError
from db_assistant_mcp.identity import current_identity
from db_assistant_mcp.observability import metrics
from db_assistant_mcp.security.audit import AuditLogger
from db_assistant_mcp.security.redactor import Redactor
from db_assistant_mcp.security.sql_validator import SqlValidator


class SecurityGateway:
    """校验 → 限制 → 执行 → 脱敏 → 审计。"""

    def __init__(
        self,
        conn: ConnectionConfig,
        pool: DriverPool,
        audit: AuditLogger,
        server: ServerConfig,
        redactor: Redactor | None = None,
        validator: SqlValidator | None = None,
    ) -> None:
        self._conn = conn
        self._pool = pool
        self._audit = audit
        self._server = server
        self._redactor = redactor or Redactor(conn)
        self._validator = validator or SqlValidator(conn.type, conn.exclude_tables)

    @property
    def name(self) -> str:
        return self._conn.name

    def _effective_limit(self, requested: int | None) -> int:
        base = requested if requested is not None else self._server.default_limit
        return max(1, min(base, self._server.default_limit, 1000))

    def _record(
        self,
        *,
        tool: str,
        sql: str | None,
        rows: int | None,
        duration_ms: float,
        allowed: bool,
        client: str | None,
        user: str | None,
        detail: dict[str, Any] | None = None,
    ) -> None:
        self._audit.record(
            tool=tool, connection=self.name, sql=sql, rows=rows,
            duration_ms=duration_ms, allowed=allowed, client=client, user=user, detail=detail,
        )

    async def execute_query(
        self,
        sql: str,
        *,
        limit: int | None = None,
        client: str | None = None,
        user: str | None = None,
    ) -> dict[str, Any]:
        start = time.monotonic()
        timeout = self._server.query_timeout_sec
        if client is None or user is None:
            client, user = current_identity()
        try:
            result = self._validator.ensure_read_only(sql)
            if not result.ok:
                raise SecurityRejectedError("语句被安全策略拒绝", detail=result.rule)

            effective = self._effective_limit(limit)
            rewritten = self._validator.add_limit(sql, effective)
            exec_sql = rewritten or sql

            def _fetch(conn: DatabaseConnection) -> Any:
                return conn.fetch(exec_sql, timeout=timeout)

            columns, rows = await self._pool.run(_fetch)
            truncated = False
            if rewritten and len(rows) > effective:
                truncated = True
            rows = rows[:effective]

            projections = self._validator.analyze_projections(sql)
            columns, rows = self._redactor.filter_result(columns, rows, projections)
            duration = (time.monotonic() - start) * 1000
            metrics.query_duration.labels(tool="execute_query", connection=self.name).observe(duration / 1000)
            metrics.tool_calls.labels(tool="execute_query", connection=self.name, result="ok").inc()
            self._record(
                tool="execute_query", sql=sql, rows=len(rows), duration_ms=duration,
                allowed=True, client=client, user=user,
            )
            return {
                "columns": columns,
                "rows": rows,
                "row_count": len(rows),
                "truncated": truncated,
                "duration_ms": round(duration, 3),
            }
        except SecurityRejectedError as exc:
            duration = (time.monotonic() - start) * 1000
            rule = (exc.context or {}).get("rule") or (exc.detail or "SECURITY_REJECTED")
            metrics.security_rejections.labels(connection=self.name, rule=str(rule)).inc()
            metrics.tool_calls.labels(tool="execute_query", connection=self.name, result="rejected").inc()
            self._record(
                tool="execute_query", sql=sql, rows=0, duration_ms=duration,
                allowed=False, client=client, user=user, detail=exc.audit_detail(),
            )
            raise
        except TableNotFoundError:
            raise
        except AppError as exc:
            duration = (time.monotonic() - start) * 1000
            metrics.tool_calls.labels(tool="execute_query", connection=self.name, result="error").inc()
            self._record(
                tool="execute_query", sql=sql, rows=0, duration_ms=duration,
                allowed=False, client=client, user=user, detail=exc.audit_detail(),
            )
            raise
        except Exception as exc:  # noqa: BLE001
            duration = (time.monotonic() - start) * 1000
            metrics.tool_calls.labels(tool="execute_query", connection=self.name, result="error").inc()
            self._record(
                tool="execute_query", sql=sql, rows=0, duration_ms=duration,
                allowed=False, client=client, user=user,
                detail={"detail": "INTERNAL_ERROR", "error": str(exc)[:500]},
            )
            raise AppError("执行查询时发生内部错误", code=ErrorCode.INTERNAL_ERROR, connection=self.name) from exc

    async def explain_query(
        self, sql: str, *, analyze: bool = False, client: str | None = None, user: str | None = None
    ) -> dict[str, Any]:
        start = time.monotonic()
        if client is None or user is None:
            client, user = current_identity()
        try:
            self._validator.ensure_read_only(sql)
            plan = await self._pool.run(lambda conn: conn.explain(sql, analyze, self._server.query_timeout_sec))
            duration = (time.monotonic() - start) * 1000
            metrics.tool_calls.labels(tool="explain_query", connection=self.name, result="ok").inc()
            metrics.query_duration.labels(tool="explain_query", connection=self.name).observe(duration / 1000)
            self._record(
                tool="explain_query", sql=sql, rows=0, duration_ms=duration,
                allowed=True, client=client, user=user,
            )
            return {"plan": plan, "duration_ms": round(duration, 3)}
        except SecurityRejectedError as exc:
            duration = (time.monotonic() - start) * 1000
            rule = (exc.context or {}).get("rule") or "SECURITY_REJECTED"
            metrics.security_rejections.labels(connection=self.name, rule=str(rule)).inc()
            metrics.tool_calls.labels(tool="explain_query", connection=self.name, result="rejected").inc()
            self._record(
                tool="explain_query", sql=sql, rows=0, duration_ms=duration,
                allowed=False, client=client, user=user, detail=exc.audit_detail(),
            )
            raise
        except AppError:
            raise

    async def translate_sql(
        self,
        sql: str,
        *,
        from_dialect: str,
        to_dialect: str,
        client: str | None = None,
        user: str | None = None,
    ) -> dict[str, Any]:
        """方言转换（原子：transpile → 只读回验，无绕过路径）。"""
        start = time.monotonic()
        if client is None or user is None:
            client, user = current_identity()
        try:
            from db_assistant_mcp.translate import translate_sql as _translate

            result = _translate(sql, from_dialect, to_dialect)
            duration = (time.monotonic() - start) * 1000
            metrics.tool_calls.labels(tool="translate_sql", connection=self.name, result="ok").inc()
            self._record(
                tool="translate_sql", sql=sql, rows=0, duration_ms=duration,
                allowed=True, client=client, user=user,
                detail={
                    "from_dialect": result["source"],
                    "to_dialect": result["target"],
                    "target_sql": result["sql"][:500],
                },
            )
            return {
                "source": result["source"],
                "target": result["target"],
                "sql": result["sql"],
                "warnings": result["warnings"],
                "duration_ms": round(duration, 3),
            }
        except SecurityRejectedError as exc:
            duration = (time.monotonic() - start) * 1000
            rule = (exc.context or {}).get("rule") or "SECURITY_REJECTED"
            metrics.security_rejections.labels(connection=self.name, rule=str(rule)).inc()
            metrics.tool_calls.labels(tool="translate_sql", connection=self.name, result="rejected").inc()
            self._record(
                tool="translate_sql", sql=sql, rows=0, duration_ms=duration,
                allowed=False, client=client, user=user, detail=exc.audit_detail(),
            )
            raise
        except AppError as exc:
            duration = (time.monotonic() - start) * 1000
            metrics.tool_calls.labels(tool="translate_sql", connection=self.name, result="error").inc()
            self._record(
                tool="translate_sql", sql=sql, rows=0, duration_ms=duration,
                allowed=False, client=client, user=user, detail=exc.audit_detail(),
            )
            raise
