from __future__ import annotations

import json
import re

from typer.testing import CliRunner

from db_assistant_mcp.cli.main import app

runner = CliRunner()


def _strip_ansi(text: str) -> str:
    """移除 rich/typer 帮助输出中的 ANSI 转义码。

    CI（GITHUB_ACTIONS=true）下 typer 强制富文本着色，高亮器会把
    含内部连字符的选项名（如 --include-low）切成多段样式并在段间
    插入转义码，导致字面字符串不再连续，需先剥离 ANSI 再断言。
    """
    return re.sub(r"\x1b\[[0-9;]*m", "", text)


def _run(*args, config_path=None):
    env = {}
    if config_path:
        env["DB_ASSISTANT_CONFIG"] = str(config_path)
    return runner.invoke(app, list(args), env=env)


def test_version():
    result = _run("version")
    assert result.exit_code == 0
    assert "db-assistant" in result.output


def test_version_flag_top_level():
    """T-5.1：db-assistant --version 与子命令 version 等价（PyPI 入口验收）。"""
    from db_assistant_mcp import __version__

    result = _run("--version")
    assert result.exit_code == 0
    assert "db-assistant" in result.output
    assert __version__ in result.output  # 动态断言，避免版本 bump 后 CI 失败


def test_console_script_entry_points():
    """回归：db-assistant-mcp 必须指向 MCP Server 入口（__main__:main）而非 typer CLI。

    v0.1 曾把两个入口都指向 cli.main:app，导致 README 的 MCP 配置
    （command: db-assistant-mcp）只会打印 CLI help 而无法启动 server。
    """
    import tomllib
    from pathlib import Path

    pyproject = tomllib.loads(
        Path(__file__).resolve().parents[1].joinpath("pyproject.toml").read_text(encoding="utf-8")
    )
    scripts = pyproject["project"]["scripts"]
    assert scripts["db-assistant"] == "db_assistant_mcp.cli.main:app"
    assert scripts["db-assistant-mcp"] == "db_assistant_mcp.__main__:main"


def test_add_list_remove(tmp_path):
    cfg = tmp_path / "config.toml"
    result = _run(
        "add", "mysql", "--name", "local-dev", "--host", "127.0.0.1",
        "--port", "3306", "--dbname", "app", "--user", "root",
        "--password-env", "MYSQL_PW", "--masked", "phone,email",
        "--exclude-tables", "raw_events", config_path=cfg,
    )
    assert result.exit_code == 0, result.output
    assert cfg.exists()
    import stat

    assert stat.S_IMODE(cfg.stat().st_mode) & 0o077 == 0  # 新建配置仅本人可读写

    result = _run("list", config_path=cfg)
    assert result.exit_code == 0
    assert "local-dev" in result.output

    result = _run("remove", "local-dev", "--yes", config_path=cfg)
    assert result.exit_code == 0
    result = _run("list", config_path=cfg)
    assert "local-dev" not in result.output


def test_add_missing_args(tmp_path):
    cfg = tmp_path / "config.toml"
    result = _run("add", "postgres", "--name", "x", config_path=cfg)
    assert result.exit_code == 2  # Typer 必填参数校验失败


def test_list_missing_config(tmp_path):
    result = _run("list", config_path=tmp_path / "nope.toml")
    assert result.exit_code == 1
    assert "错误" in result.output


def test_remove_confirmation_abort(tmp_path):
    cfg = tmp_path / "config.toml"
    _run(
        "add", "postgres", "--name", "dev", "--host", "h", "--dbname", "d",
        "--user", "u", "--password-env", "PW", config_path=cfg,
    )
    result = runner.invoke(app, ["remove", "dev"], env={"DB_ASSISTANT_CONFIG": str(cfg)}, input="no\n")
    assert "已取消" in result.output
    assert "dev" in cfg.read_text(encoding="utf-8")


def test_logs_export(tmp_path):
    audit = tmp_path / "audit.log"
    audit.write_text(
        json.dumps({"ts": "2026-08-05T10:00:00+08:00", "tool": "execute_query",
                    "connection": "c", "sql": "SELECT 1", "rows": 1,
                    "duration_ms": 1, "allowed": True, "client": None, "user": "alice"})
        + "\n",
        encoding="utf-8",
    )
    cfg = tmp_path / "config.toml"
    cfg.write_text(
        f'[connections.c]\ntype="postgres"\nhost="h"\ndatabase="d"\nuser="u"\n'
        f'[audit]\noutput="file"\npath="{audit.as_posix()}"\n',
        encoding="utf-8",
    )
    import os

    os.chmod(cfg, 0o600)  # 与 CLI 新建行为一致，避免权限告警噪音
    export = tmp_path / "export.json"
    result = _run("logs", "--export", str(export), config_path=cfg)
    assert result.exit_code == 0
    assert json.loads(export.read_text(encoding="utf-8"))[0]["tool"] == "execute_query"


def test_semantic_generate_unknown_connection(tmp_path):
    cfg = tmp_path / "config.toml"
    cfg.write_text('[connections.demo]\ntype="postgres"\nhost="h"\ndatabase="d"\nuser="u"\n', encoding="utf-8")
    result = _run("semantic", "generate", "nope", config_path=cfg)
    assert result.exit_code == 1
    assert "不存在" in result.output


def test_semantic_generate_missing_config(tmp_path):
    result = _run("semantic", "generate", "demo", config_path=tmp_path / "nope.toml")
    assert result.exit_code == 1
    assert "错误" in result.output


def test_semantic_generate_help():
    result = _run("semantic", "generate", "--help")
    assert result.exit_code == 0
    assert "--include-low" in _strip_ansi(result.output)


def _write_toml(tmp_path, body: str, name: str = "config.toml"):
    cfg = tmp_path / name
    cfg.write_text(body, encoding="utf-8")
    import os

    os.chmod(cfg, 0o600)
    return cfg


def _audit_entry(duration_ms: float, *, tool: str = "execute_query", user: str = "alice",
                 connection: str = "c") -> str:
    return json.dumps(
        {"ts": "2026-08-05T10:00:00+08:00", "tool": tool, "connection": connection,
         "sql": "SELECT 1", "rows": 1, "duration_ms": duration_ms, "allowed": True,
         "client": None, "user": user}
    ) + "\n"


# ---------- T-4.2 config validate / doctor ----------

def test_config_validate_ok(tmp_path):
    cfg = _write_toml(tmp_path, '[connections.c]\ntype="postgres"\nhost="h"\ndatabase="d"\nuser="u"\n')
    result = _run("config", "validate", config_path=cfg)
    assert result.exit_code == 0
    assert "[ok]" in result.stdout
    assert "配置解析" in result.stdout


def test_config_validate_broken_exit_1(tmp_path):
    cfg = _write_toml(tmp_path, "[connections\nbad")
    result = _run("config", "validate", config_path=cfg)
    assert result.exit_code == 1
    assert "[err]" in result.stdout
    assert "TOML" in result.stdout


def test_config_validate_missing_file_exit_1(tmp_path):
    result = _run("config", "validate", config_path=tmp_path / "nope.toml")
    assert result.exit_code == 1
    assert "不存在" in result.stdout


def test_doctor_broken_config_exit_1(tmp_path):
    cfg = _write_toml(tmp_path, "[connections\nbad")
    result = _run("doctor", config_path=cfg)
    assert result.exit_code == 1
    assert "[err]" in result.stdout


def test_doctor_full_report(tmp_path, monkeypatch):
    cfg = _write_toml(
        tmp_path,
        '[connections.c]\ntype="postgres"\nhost="h"\ndatabase="d"\nuser="u"\n'
        '[audit]\noutput="stdout"\n[metrics]\nenabled=false\n[semantic]\n',
    )
    # 用假注册表避免真实连接/端口检查
    monkeypatch.setattr("db_assistant_mcp.cli.diagnostics.RuntimeRegistry", FakeRegistry)
    result = _run("doctor", config_path=cfg)
    assert result.exit_code == 0
    assert "依赖 mcp" in result.stdout
    assert "glossary 词典" in result.stdout


class FakeRegistry:
    def __init__(self, *args, **kwargs):
        pass

    async def ping(self):
        return {"c": {"ok": True, "latency_ms": 1.0, "dialect": "postgres"}}

    async def close_all(self):
        pass


# ---------- T-4.3 logs --slow ----------

def _slow_cfg(tmp_path):
    audit = tmp_path / "audit.log"
    audit.write_text(
        _audit_entry(50) + _audit_entry(1500, user="bob") + _audit_entry(2500, tool="explain_query"),
        encoding="utf-8",
    )
    cfg = _write_toml(tmp_path, f'[connections.c]\ntype="postgres"\nhost="h"\ndatabase="d"\nuser="u"\n[audit]\noutput="file"\npath="{audit.as_posix()}"\n')
    return cfg


def test_logs_slow_filters_by_threshold(tmp_path):
    cfg = _slow_cfg(tmp_path)
    result = _run("logs", "--slow", "--threshold", "1000", config_path=cfg)
    assert result.exit_code == 0
    assert "1500" in result.stdout
    assert "2500" in result.stdout
    assert '"duration_ms": 50' not in result.stdout


def test_logs_slow_composable_with_filters(tmp_path):
    cfg = _slow_cfg(tmp_path)
    result = _run("logs", "--slow", "--tool", "explain_query", config_path=cfg)
    assert result.exit_code == 0
    assert "2500" in result.stdout
    assert "1500" not in result.stdout


def test_logs_slow_without_file_output_hints(tmp_path):
    cfg = _write_toml(
        tmp_path,
        '[connections.c]\ntype="postgres"\nhost="h"\ndatabase="d"\nuser="u"\n[audit]\noutput="stdout"\n',
    )
    result = _run("logs", "--slow", config_path=cfg)
    assert result.exit_code == 1
    assert "无法按 --slow 读取" in result.stderr
