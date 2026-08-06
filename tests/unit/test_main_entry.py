"""入口测试：python -m db_assistant_mcp 参数解析与 HTTP fail-closed 启动。"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def _run(*args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    full_env = os.environ.copy()
    if env:
        full_env.update(env)
    return subprocess.run(
        [sys.executable, "-m", "db_assistant_mcp", *args],
        capture_output=True,
        text=True,
        env=full_env,
        timeout=30,
    )


def _write_config(tmp_path: Path, http_section: str) -> Path:
    cfg = tmp_path / "config.toml"
    cfg.write_text(
        f"""
{http_section}
[audit]
output = "stdout"
[semantic]
[metrics]
enabled = false
[connections.pg]
type = "postgres"
host = "127.0.0.1"
port = 5432
database = "d"
user = "u"
""",
        encoding="utf-8",
    )
    return cfg


def test_version():
    result = _run("--version")
    assert result.returncode == 0
    assert "db-assistant-mcp" in result.stdout


def test_help_lists_transport_option():
    result = _run("--help")
    assert result.returncode == 0
    assert "--transport" in result.stdout


def test_invalid_transport_exit_2():
    result = _run("--transport", "sse")
    assert result.returncode == 2
    assert "transport" in result.stderr


def test_invalid_port_exit_2():
    result = _run("--transport", "streamable-http", "--port", "abc")
    assert result.returncode == 2
    assert "端口" in result.stderr


def test_http_without_token_env_config_fails_fast(tmp_path):
    """未配置 [http] token_env 时 HTTP 模式拒绝启动（fail-closed）。"""
    cfg = _write_config(tmp_path, "")
    result = _run("--transport", "streamable-http", "--config", str(cfg))
    assert result.returncode == 1
    assert "token_env" in result.stderr


def test_http_with_unset_token_env_fails_fast(tmp_path):
    """配置了 token_env 但环境变量为空时拒绝启动。"""
    cfg = _write_config(tmp_path, '[http]\ntoken_env = "DB_ASSISTANT_HTTP_TOKEN"\n')
    result = _run(
        "--transport", "streamable-http", "--config", str(cfg),
        env={"DB_ASSISTANT_HTTP_TOKEN": ""},
    )
    assert result.returncode == 1
    assert "未设置或为空" in result.stderr
