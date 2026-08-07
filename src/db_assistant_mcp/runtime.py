"""连接运行时装配：每连接独立 pool / gateway / schema 缓存。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from db_assistant_mcp.config import AppConfig, ConnectionConfig, SemanticConfig
from db_assistant_mcp.drivers.pool import DriverPool
from db_assistant_mcp.errors import ConfigError, ConnectionError_
from db_assistant_mcp.schema_service import SchemaService
from db_assistant_mcp.security.audit import AuditLogger
from db_assistant_mcp.security.gateway import SecurityGateway
from db_assistant_mcp.security.redactor import Redactor
from db_assistant_mcp.security.sql_validator import SqlValidator
from db_assistant_mcp.semantic import Glossary


@dataclass
class ConnectionRuntime:
    config: ConnectionConfig
    pool: DriverPool
    redactor: Redactor
    validator: SqlValidator
    gateway: SecurityGateway
    schema: SchemaService


class RuntimeRegistry:
    def __init__(self, app_config: AppConfig, audit: AuditLogger, glossary: Glossary) -> None:
        self._config = app_config
        self._audit = audit
        self._glossary = glossary
        self._runtimes: dict[str, ConnectionRuntime] = {}
        self._glossary_src = _capture_glossary_src(app_config.semantic)

    def get(self, name: str) -> ConnectionRuntime:
        if name in self._runtimes:
            return self._runtimes[name]
        conn = self._config.get_connection(name)
        pool = DriverPool(
            conn,
            max_size=self._config.server.max_concurrent,
            idle_ttl_sec=min(self._config.server.schema_cache_ttl_sec, 300),
        )
        redactor = Redactor(conn)
        validator = SqlValidator(conn.type, conn.exclude_tables)
        gateway = SecurityGateway(
            conn=conn,
            pool=pool,
            audit=self._audit,
            server=self._config.server,
            redactor=redactor,
            validator=validator,
        )
        schema = SchemaService(
            name=conn.name,
            pool=pool,
            redactor=redactor,
            glossary=self._glossary,
            ttl_sec=self._config.server.schema_cache_ttl_sec,
        )
        runtime = ConnectionRuntime(
            config=conn,
            pool=pool,
            redactor=redactor,
            validator=validator,
            gateway=gateway,
            schema=schema,
        )
        self._runtimes[name] = runtime
        return runtime

    def default_gateway(self) -> SecurityGateway:
        """返回第一个已配置连接的网关（translate_sql 等无连接依赖的工具使用）。"""
        if not self.names:
            raise ConfigError("未配置任何数据库连接", detail="NO_CONNECTIONS", hint="请在配置文件中添加 connections")
        return self.get(self.names[0]).gateway

    @property
    def names(self) -> list[str]:
        return self._config.connection_names

    async def ping(self, name: str | None = None) -> dict[str, Any]:
        targets = [name] if name else self.names
        if name and name not in self.names:
            raise ConnectionError_(
                f"连接 '{name}' 不存在",
                detail=f"UNKNOWN_CONNECTION:{name}",
                connection=name,
                hint="使用 list_databases 查看可用连接",
            )
        results: dict[str, Any] = {}
        for target in targets:
            try:
                results[target] = await self.get(target).pool.ping()
            except Exception as exc:  # noqa: BLE001
                results[target] = {"connection": target, "ok": False, "error": str(exc)[:300]}
        return results

    async def close_all(self) -> None:
        for runtime in self._runtimes.values():
            await runtime.pool.close()
        self._runtimes.clear()

    async def reload(self, new_config: AppConfig) -> dict[str, Any]:
        """热重载：按新配置协调运行时并返回变更摘要。

        - 新增/删除连接：删除的立即关闭池；新增的在首次使用时懒构建
        - 连接参数、[server]、审计、glossary 变更：重建受影响运行时（池会重建，
          进行中的查询不受影响，归还时被丢弃）
        - [http] / [metrics] 变更：不热生效，需重启（记入 summary.restart_required）
        """
        old = self._config
        summary: dict[str, Any] = {"added": [], "removed": [], "updated": [], "rebuilt_all": False, "restart_required": []}

        if new_config.http != old.http or new_config.metrics != old.metrics:
            summary["restart_required"] = [
                section
                for section, new, oldv in (
                    ("http", new_config.http, old.http),
                    ("metrics", new_config.metrics, old.metrics),
                )
                if new != oldv
            ]

        if new_config.audit != old.audit:
            self._audit = AuditLogger(new_config.audit)
            summary["rebuilt_all"] = True

        if _glossary_src_changed(self._glossary_src, new_config.semantic):
            self._glossary = Glossary.load(new_config.semantic.glossary_file)
            self._glossary_src = _capture_glossary_src(new_config.semantic)
            summary["rebuilt_all"] = True

        if new_config.server != old.server:
            summary["rebuilt_all"] = True

        old_names = set(old.connections)
        new_names = set(new_config.connections)
        for name in sorted(old_names - new_names):
            await self._drop_runtime(name)
            summary["removed"].append(name)

        if summary["rebuilt_all"]:
            for name in sorted(old_names & new_names):
                await self._drop_runtime(name)
                summary["updated"].append(name)
        else:
            for name in sorted(old_names & new_names):
                if old.connections[name] != new_config.connections[name]:
                    await self._drop_runtime(name)
                    summary["updated"].append(name)

        summary["added"] = sorted(new_names - old_names)
        self._config = new_config
        return summary

    async def _drop_runtime(self, name: str) -> None:
        runtime = self._runtimes.pop(name, None)
        if runtime is not None:
            await runtime.pool.close()

    @property
    def configured_summary(self) -> list[dict[str, Any]]:
        return [
            {
                "name": c.name,
                "type": c.type,
                "database": c.database,
                "host": c.host,
                "port": c.port,
                "mode": c.mode,
            }
            for c in self._config.connections.values()
        ]


def _capture_glossary_src(semantic: SemanticConfig) -> tuple[str, tuple[int, int]] | None:
    """记录 glossary 文件解析路径与 (mtime_ns, size)，用于检测内容变更。"""
    if not semantic.glossary_file:
        return None
    path = Path(semantic.glossary_file).expanduser()
    try:
        st = path.stat()
    except OSError:
        return None
    return str(path), (st.st_mtime_ns, st.st_size)


def _glossary_src_changed(current: tuple[str, tuple[int, int]] | None, semantic: SemanticConfig) -> bool:
    return current != _capture_glossary_src(semantic)
