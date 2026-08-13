"""B-2 配置热重载：RuntimeRegistry.reload 协调 + server 轮询器。"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

import pytest

from db_assistant_mcp.config import load_config
from db_assistant_mcp.errors import ConnectionError_
from db_assistant_mcp.security.audit import AuditLogger
from db_assistant_mcp.server import (
    _config_reload_loop,
    _config_stat,
    create_server,
    reload_config_if_changed,
)


def _config_text(
    *,
    connections: tuple[str, ...] = ("demo",),
    server: str = "",
    audit_output: str = "stdout",
    audit_path: str | None = None,
    glossary: str | None = None,
    http_token_env: str | None = None,
) -> str:
    conns = "\n".join(
        (
            f'[connections.{name}]\n'
            'type = "postgres"\nhost = "127.0.0.1"\nport = 5432\n'
            'database = "db"\nuser = "u"\n'
        )
        for name in connections
    )
    glossary_line = f'glossary_file = "{glossary}"\n' if glossary else ""
    http_line = f'[http]\ntoken_env = "{http_token_env}"\n' if http_token_env else ""
    audit_path_line = f'path = "{audit_path}"\n' if audit_path else ""
    return (
        f"{server}\n"
        f"[audit]\noutput = \"{audit_output}\"\n{audit_path_line}"
        f"[semantic]\n{glossary_line}"
        "[metrics]\nenabled = false\n"
        f"{http_line}\n"
        f"{conns}\n"
    )


def _write_config(tmp_path: Path, **kwargs) -> Path:
    cfg = tmp_path / "config.toml"
    cfg.write_text(_config_text(**kwargs), encoding="utf-8")
    return cfg


def _registry(tmp_path: Path, **kwargs):
    app_config = load_config(str(_write_config(tmp_path, **kwargs)))
    mcp = create_server(app_config)
    return mcp.registry


# ---------- config 解析 ----------


def test_config_reload_interval_default():
    from db_assistant_mcp.config import ServerConfig

    assert ServerConfig().config_reload_interval_sec == 30


def test_config_reload_interval_parsing(tmp_path):
    cfg = _write_config(tmp_path, server="[server]\nconfig_reload_interval_sec = 5\n")
    assert load_config(str(cfg)).server.config_reload_interval_sec == 5

    cfg = _write_config(tmp_path, server="[server]\nconfig_reload_interval_sec = 0\n")
    assert load_config(str(cfg)).server.config_reload_interval_sec == 0


def test_config_reload_interval_invalid(tmp_path):
    from db_assistant_mcp.errors import ConfigError

    cfg = _write_config(tmp_path, server="[server]\nconfig_reload_interval_sec = -1\n")
    with pytest.raises(ConfigError):
        load_config(str(cfg))


# ---------- RuntimeRegistry.reload ----------


@pytest.mark.asyncio
async def test_reload_adds_connection(tmp_path):
    registry = _registry(tmp_path)
    cfg = _write_config(tmp_path, connections=("demo", "extra"))
    summary = await registry.reload(load_config(str(cfg)))
    assert summary["added"] == ["extra"]
    assert summary["removed"] == []
    assert summary["updated"] == []
    assert "extra" in registry.names
    assert registry.get("extra").config.name == "extra"


@pytest.mark.asyncio
async def test_reload_removes_connection_and_closes_pool(tmp_path):
    registry = _registry(tmp_path, connections=("demo", "extra"))
    old_runtime = registry.get("extra")
    cfg = _write_config(tmp_path, connections=("demo",))
    summary = await registry.reload(load_config(str(cfg)))
    assert summary["removed"] == ["extra"]
    assert old_runtime.pool._closed
    with pytest.raises(ConnectionError_):
        registry.get("extra")
    assert "demo" in registry.names


@pytest.mark.asyncio
async def test_reload_rebuilds_changed_connection(tmp_path):
    registry = _registry(tmp_path)
    old_runtime = registry.get("demo")
    cfg = _write_config(tmp_path)
    cfg.write_text(cfg.read_text(encoding="utf-8").replace("port = 5432", "port = 6432"), encoding="utf-8")
    summary = await registry.reload(load_config(str(cfg)))
    assert summary["updated"] == ["demo"]
    assert old_runtime.pool._closed
    new_runtime = registry.get("demo")
    assert new_runtime is not old_runtime
    assert new_runtime.config.port == 6432


@pytest.mark.asyncio
async def test_reload_server_change_rebuilds_all(tmp_path):
    registry = _registry(tmp_path)
    old_runtime = registry.get("demo")
    cfg = _write_config(tmp_path, server="[server]\nquery_timeout_sec = 5\n")
    summary = await registry.reload(load_config(str(cfg)))
    assert summary["rebuilt_all"] is True
    assert old_runtime.pool._closed
    assert registry.get("demo").gateway._server.query_timeout_sec == 5


@pytest.mark.asyncio
async def test_reload_audit_change_rebuilds_all_with_new_logger(tmp_path):
    registry = _registry(tmp_path)
    old_audit = registry._audit
    old_runtime = registry.get("demo")
    cfg = _write_config(tmp_path, audit_output="file", audit_path=str(tmp_path / "audit.log"))
    summary = await registry.reload(load_config(str(cfg)))
    assert summary["rebuilt_all"] is True
    assert old_runtime.pool._closed
    assert registry._audit is not old_audit
    assert isinstance(registry._audit, AuditLogger)
    assert registry.get("demo").gateway._audit is registry._audit


@pytest.mark.asyncio
async def test_reload_glossary_change_rebuilds_all(tmp_path):
    glossary = tmp_path / "glossary.toml"
    glossary.write_text('[[terms]]\ncolumn = "id"\nmeaning = "identifier"\n', encoding="utf-8")
    registry = _registry(tmp_path, glossary=str(glossary))
    old_glossary = registry._glossary
    old_runtime = registry.get("demo")
    time.sleep(0.01)
    glossary.write_text(
        '[[terms]]\ncolumn = "id"\nmeaning = "identifier v2"\nstatus = "approved"\n',
        encoding="utf-8",
    )
    summary = await registry.reload(load_config(str(tmp_path / "config.toml")))
    assert summary["rebuilt_all"] is True
    assert old_runtime.pool._closed
    assert registry._glossary is not old_glossary
    assert len(registry._glossary.terms) == 1


@pytest.mark.asyncio
async def test_reload_http_change_flagged_restart_required(tmp_path):
    registry = _registry(tmp_path)
    cfg = _write_config(tmp_path, http_token_env="DB_ASSISTANT_HTTP_TOKEN")
    summary = await registry.reload(load_config(str(cfg)))
    assert "http" in summary["restart_required"]
    assert "metrics" not in summary["restart_required"]


# ---------- server 轮询器 ----------


@pytest.mark.asyncio
async def test_reload_if_changed_no_change(tmp_path):
    registry = _registry(tmp_path)
    cfg = str(tmp_path / "config.toml")
    changed, summary = await reload_config_if_changed(registry, cfg, _config_stat(cfg))
    assert changed is False
    assert summary is None


@pytest.mark.asyncio
async def test_reload_if_changed_detects_file_change(tmp_path):
    registry = _registry(tmp_path)
    cfg_path = str(tmp_path / "config.toml")
    before = _config_stat(cfg_path)
    _write_config(tmp_path, connections=("demo", "extra"))
    changed, summary = await reload_config_if_changed(registry, cfg_path, before)
    assert changed is True
    assert summary["added"] == ["extra"]


@pytest.mark.asyncio
async def test_config_reload_loop_picks_up_changes(tmp_path):
    registry = _registry(tmp_path)
    cfg = str(tmp_path / "config.toml")
    task = asyncio.create_task(_config_reload_loop(registry, cfg, interval=0.05))
    try:
        await asyncio.sleep(0.1)  # 让轮询器先捕获初始 stat
        _write_config(tmp_path, connections=("demo", "extra"))
        await asyncio.sleep(0.3)
        assert "extra" in registry.names

        _write_config(tmp_path, connections=("demo",))
        await asyncio.sleep(0.3)
        assert registry.names == ["demo"]
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


@pytest.mark.asyncio
async def test_config_reload_loop_keeps_old_config_on_invalid(tmp_path):
    registry = _registry(tmp_path, connections=("demo", "extra"))
    cfg = str(tmp_path / "config.toml")
    task = asyncio.create_task(_config_reload_loop(registry, cfg, interval=0.05))
    try:
        (tmp_path / "config.toml").write_text("not valid toml [[[", encoding="utf-8")
        await asyncio.sleep(0.3)
        assert "demo" in registry.names
        assert "extra" in registry.names

        _write_config(tmp_path, connections=("demo", "extra2"))
        await asyncio.sleep(0.3)
        assert "extra2" in registry.names
        assert "extra" not in registry.names
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
