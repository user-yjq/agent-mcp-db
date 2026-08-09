"""审计日志：file / stdout / webhook，失败不影响主流程。"""

from __future__ import annotations

import asyncio
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import aiohttp

from db_assistant_mcp.config import AuditConfig
from db_assistant_mcp.logging_utils import get_logger, log_context, redact_dict

MAX_SQL_IN_AUDIT = 4096


class AuditLogger:
    def __init__(self, config: AuditConfig) -> None:
        self._config = config
        self._path = Path(config.path).expanduser() if config.path else None
        self._logger = get_logger("db_assistant_mcp.audit")

    def _entry(
        self,
        *,
        tool: str,
        connection: str | None,
        sql: str | None,
        rows: int | None,
        duration_ms: float | None,
        allowed: bool,
        client: str | None,
        user: str | None,
        detail: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        entry: dict[str, Any] = {
            "ts": datetime.now(UTC).isoformat(timespec="milliseconds"),
            "client": client,
            "user": user,
            "tool": tool,
            "connection": connection,
            "sql": (sql[:MAX_SQL_IN_AUDIT] + "...(truncated)") if sql and len(sql) > MAX_SQL_IN_AUDIT else sql,
            "rows": rows,
            "duration_ms": round(duration_ms, 3) if duration_ms is not None else None,
            "allowed": allowed,
        }
        if detail:
            entry["detail"] = redact_dict(detail)
        return entry

    def _write_file(self, entry: dict[str, Any]) -> None:
        if not self._path:
            return
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def _write_stdout(self, entry: dict[str, Any]) -> None:
        print(json.dumps(entry, ensure_ascii=False))

    def _write_stderr(self, entry: dict[str, Any]) -> None:
        """stderr 输出：MCP stdio 模式下 stdout 保留给 JSON-RPC，审计走 stderr。"""
        print(json.dumps(entry, ensure_ascii=False), file=sys.stderr)

    async def _write_webhook(self, entry: dict[str, Any]) -> None:
        url = self._config.webhook_url
        if not url:
            return
        headers = {"Content-Type": "application/json"}
        if self._config.webhook_secret_env:
            import os

            secret = os.environ.get(self._config.webhook_secret_env)
            if secret:
                headers["X-Db-Assistant-Signature"] = secret
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=entry, headers=headers, timeout=aiohttp.ClientTimeout(total=3)) as resp:
                resp.raise_for_status()

    def record(self, *, tool: str, connection: str | None, sql: str | None, rows: int | None,
               duration_ms: float | None, allowed: bool, client: str | None = None,
               user: str | None = None, detail: dict[str, Any] | None = None) -> None:
        """同步写入审计（file/stdout），webhook 异步尽力发送；失败仅记日志。"""
        entry = self._entry(
            tool=tool, connection=connection, sql=sql, rows=rows,
            duration_ms=duration_ms, allowed=allowed, client=client, user=user, detail=detail,
        )
        try:
            if self._config.output == "stdout":
                self._write_stdout(entry)
            elif self._config.output == "stderr":
                self._write_stderr(entry)
            elif self._config.output == "webhook":
                try:
                    loop = asyncio.get_running_loop()
                except RuntimeError:
                    loop = None
                if loop and loop.is_running():
                    asyncio.create_task(self._write_webhook(entry))
                else:
                    asyncio.run(self._write_webhook(entry))
            else:
                self._write_file(entry)
        except Exception as exc:  # noqa: BLE001
            log_context(self._logger, 40, "审计日志写入失败", error=str(exc), output=self._config.output)

    def read(
        self,
        *,
        tail: int = 50,
        user: str | None = None,
        connection: str | None = None,
        tool: str | None = None,
        min_duration_ms: float | None = None,
    ) -> list[dict[str, Any]]:
        """读取审计日志（CLI logs 使用）。min_duration_ms 过滤慢查询（duration_ms 严格大于阈值）。"""
        if self._config.output != "file" or not self._path or not self._path.exists():
            return []
        entries: list[dict[str, Any]] = []
        with self._path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        filtered = entries
        if user:
            filtered = [e for e in filtered if e.get("user") == user]
        if connection:
            filtered = [e for e in filtered if e.get("connection") == connection]
        if tool:
            filtered = [e for e in filtered if e.get("tool") == tool]
        if min_duration_ms is not None:
            filtered = [e for e in filtered if (e.get("duration_ms") or 0) > min_duration_ms]
        return filtered[-tail:] if tail else filtered
