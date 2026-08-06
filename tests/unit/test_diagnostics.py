"""CLI 诊断：T-4.2 config validate / doctor 结构化检查。"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from db_assistant_mcp.cli.diagnostics import (
    check_config,
    check_dependencies,
    check_file,
    check_glossary,
    check_metrics_port,
    run_doctor,
)
from db_assistant_mcp.config import load_config


def _write_config(tmp_path: Path, body: str, *, mode: int | None = 0o600) -> Path:
    path = tmp_path / "config.toml"
    path.write_text(body, encoding="utf-8")
    if mode is not None:
        os.chmod(path, mode)
    return path


GOOD = """
[connections.pg]
type = "postgres"
host = "h"
database = "d"
user = "u"
"""


# ---------- check_file ----------

def test_missing_config_file_error(tmp_path):
    checks = check_file(tmp_path / "nope.toml")
    assert checks[0].status == "error"
    assert "不存在" in checks[0].message


def test_loose_permission_warns(tmp_path):
    path = _write_config(tmp_path, GOOD, mode=0o644)
    statuses = {c.item: c.status for c in check_file(path)}
    assert statuses["配置文件权限"] == "warn"


def test_secure_permission_ok(tmp_path):
    path = _write_config(tmp_path, GOOD, mode=0o600)
    statuses = {c.item: c.status for c in check_file(path)}
    assert statuses["配置文件权限"] == "ok"


# ---------- check_config ----------

def test_valid_config_all_ok(tmp_path):
    path = _write_config(
        tmp_path,
        f"""
{GOOD}
[audit]
output = "file"
path = "{tmp_path / 'audit.log'}"
""",
    )
    checks = check_config(path)
    assert all(c.status != "error" for c in checks)
    assert any(c.item == "配置解析" and c.status == "ok" for c in checks)
    assert any(c.item == "连接 'pg'" and c.status == "ok" for c in checks)


def test_http_token_env_warns_when_missing(tmp_path):
    path = _write_config(tmp_path, GOOD)
    checks = check_config(path)
    http_check = next(c for c in checks if c.item == "[http] token_env")
    assert http_check.status == "warn"


def test_toml_syntax_error_reports(tmp_path):
    path = _write_config(tmp_path, "[connections\nbad", mode=0o600)
    checks = check_config(path)
    assert any(c.status == "error" and "TOML" in c.message for c in checks)


def test_no_connections_reports(tmp_path):
    path = _write_config(tmp_path, "[audit]\noutput = 'stdout'\n", mode=0o600)
    checks = check_config(path)
    assert any(c.status == "error" for c in checks)


def test_invalid_port_type_reports(tmp_path):
    path = _write_config(
        tmp_path,
        """
[connections.pg]
type = "postgres"
host = "h"
port = "not-a-number"
database = "d"
user = "u"
""",
        mode=0o600,
    )
    checks = check_config(path)
    assert any(c.status == "error" and "整数" in c.message for c in checks)


# ---------- check_dependencies ----------

def test_all_dependencies_installed():
    checks = check_dependencies()
    assert checks and all(c.status == "ok" for c in checks)


# ---------- check_glossary ----------

def test_glossary_not_configured_warns(tmp_path):
    path = _write_config(tmp_path, GOOD)
    cfg = load_config(str(path))
    checks = check_glossary(cfg)
    assert checks[0].status == "warn"


def test_glossary_missing_file_error(tmp_path):
    path = _write_config(
        tmp_path,
        GOOD + '\n[semantic]\nglossary_file = "/nonexistent/glossary.toml"\n',
    )
    cfg = load_config(str(path))
    checks = check_glossary(cfg)
    assert checks[0].status == "error"
    assert "不存在" in checks[0].message


def test_glossary_broken_toml_error(tmp_path):
    glossary = tmp_path / "glossary.toml"
    glossary.write_text("[[terms\nbad", encoding="utf-8")
    path = _write_config(tmp_path, GOOD + f'\n[semantic]\nglossary_file = "{glossary}"\n')
    cfg = load_config(str(path))
    checks = check_glossary(cfg)
    assert checks[0].status == "error"
    assert "解析失败" in checks[0].message


def test_glossary_valid_ok(tmp_path):
    glossary = tmp_path / "glossary.toml"
    glossary.write_text(
        '[[terms]]\ntable = "users"\ncolumn = "id"\nmeaning = "用户主键"\n', encoding="utf-8"
    )
    path = _write_config(tmp_path, GOOD + f'\n[semantic]\nglossary_file = "{glossary}"\n')
    cfg = load_config(str(path))
    checks = check_glossary(cfg)
    assert checks[0].status == "ok"
    assert "1 条术语" in checks[0].message


# ---------- check_metrics_port ----------

def test_metrics_disabled_warns(tmp_path):
    path = _write_config(tmp_path, GOOD + "\n[metrics]\nenabled = false\n")
    cfg = load_config(str(path))
    checks = check_metrics_port(cfg)
    assert checks[0].status == "warn"


def test_metrics_port_bindable_ok(tmp_path):
    path = _write_config(tmp_path, GOOD + "\n[metrics]\nenabled = true\nport = 29102\n")
    cfg = load_config(str(path))
    checks = check_metrics_port(cfg, try_bind=lambda port: None)
    assert checks[0].status == "ok"


def test_metrics_port_occupied_error(tmp_path):
    path = _write_config(tmp_path, GOOD + "\n[metrics]\nenabled = true\nport = 29103\n")
    cfg = load_config(str(path))
    checks = check_metrics_port(cfg, try_bind=lambda port: "Address already in use")
    assert checks[0].status == "error"
    assert "29103" in checks[0].message


# ---------- run_doctor 汇总 ----------

@pytest.mark.asyncio
async def test_run_doctor_broken_config_still_checks_dependencies(tmp_path):
    path = _write_config(tmp_path, "[connections\nbad", mode=0o600)
    checks = await run_doctor(str(path))
    assert any(c.status == "error" for c in checks)
    assert any(c.item.startswith("依赖 ") for c in checks)


@pytest.mark.asyncio
async def test_run_doctor_valid_config_full_report(tmp_path, monkeypatch):
    path = _write_config(
        tmp_path,
        f"""
{GOOD}
[semantic]
[metrics]
enabled = false
""",
    )

    class FakePool:
        async def ping(self):
            return {"ok": True, "latency_ms": 1.5, "dialect": "postgres"}

        async def close(self):
            pass

    class FakeRuntime:
        pool = FakePool()

    class FakeRegistry:
        def __init__(self, *_args, **_kwargs):
            pass

        def get(self, _name):
            return FakeRuntime()

        async def ping(self):
            return {"pg": {"ok": True, "latency_ms": 1.5, "dialect": "postgres"}}

        async def close_all(self):
            pass

    monkeypatch.setattr("db_assistant_mcp.cli.diagnostics.RuntimeRegistry", FakeRegistry)
    checks = await run_doctor(str(path), try_bind=lambda port: None)
    assert all(c.status != "error" for c in checks)
    assert any(c.item == "连接 'pg'" and c.status == "ok" for c in checks)
    assert any(c.item.startswith("依赖 ") for c in checks)
