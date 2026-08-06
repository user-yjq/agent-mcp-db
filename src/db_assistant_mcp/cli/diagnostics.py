"""CLI 诊断：config validate / doctor 的结构化检查（可独立单测）。

每个检查返回 CheckResult（item / status: ok|warn|error / message / fix）。
"""

from __future__ import annotations

import importlib.metadata
import socket
import stat
import tomllib
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from db_assistant_mcp.config import AppConfig, load_config
from db_assistant_mcp.errors import ConfigError
from db_assistant_mcp.runtime import RuntimeRegistry
from db_assistant_mcp.security.audit import AuditLogger
from db_assistant_mcp.semantic import Glossary

DEPENDENCIES = (
    "mcp", "asyncpg", "aiomysql", "sqlglot",
    "typer", "prometheus-client", "aiohttp", "cryptography",
)


@dataclass
class CheckResult:
    item: str
    status: str  # ok | warn | error
    message: str
    fix: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {"item": self.item, "status": self.status, "message": self.message, "fix": self.fix}


# ---------- config validate ----------

def check_file(path: Path) -> list[CheckResult]:
    checks: list[CheckResult] = []
    if not path.exists():
        checks.append(
            CheckResult(
                "配置文件", "error", f"{path} 不存在",
                "使用 `db-assistant add` 创建，或设置 DB_ASSISTANT_CONFIG 环境变量",
            )
        )
        return checks
    if not path.is_file():
        checks.append(CheckResult("配置文件", "error", f"{path} 不是普通文件", "检查路径类型"))
        return checks
    try:
        mode = stat.S_IMODE(path.stat().st_mode)
    except OSError as exc:
        checks.append(CheckResult("配置文件", "error", f"无法读取文件属性: {exc}", "检查目录权限"))
        return checks
    if mode & 0o077:
        checks.append(
            CheckResult(
                "配置文件权限", "warn", f"权限过宽（0o{mode:o}）",
                "执行 `chmod 600 <配置文件>` 限制为仅本人可读写",
            )
        )
    else:
        checks.append(CheckResult("配置文件权限", "ok", f"0o{mode:o}", None))
    return checks


def check_config(path: Path) -> list[CheckResult]:
    checks = check_file(path)
    if any(c.status == "error" for c in checks):
        return checks
    try:
        cfg = load_config(str(path))
    except ConfigError as exc:
        checks.append(CheckResult("配置解析", "error", f"{exc.message}（{exc.detail}）", exc.hint))
        return checks
    except OSError as exc:
        checks.append(CheckResult("配置解析", "error", f"无法读取配置文件: {exc}", "检查文件权限"))
        return checks

    checks.append(CheckResult("配置解析", "ok", "TOML 语法与 schema 校验通过", None))
    for name, conn in cfg.connections.items():
        checks.append(
            CheckResult(
                f"连接 '{name}'", "ok",
                f"{conn.type} @ {conn.host}:{conn.port}/{conn.database}", None,
            )
        )
    if cfg.http.token_env:
        checks.append(CheckResult("[http] token_env", "ok", cfg.http.token_env, None))
    else:
        checks.append(
            CheckResult(
                "[http] token_env", "warn", "未配置",
                "stdio 模式可用；远程共享（HTTP）模式需配置并导出 token 环境变量",
            )
        )
    return checks


# ---------- doctor 扩展检查 ----------

def check_dependencies() -> list[CheckResult]:
    checks: list[CheckResult] = []
    for package in DEPENDENCIES:
        try:
            version = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            checks.append(CheckResult(f"依赖 {package}", "error", "未安装", "执行 `pip install -e .` 或安装项目依赖"))
        else:
            checks.append(CheckResult(f"依赖 {package}", "ok", version, None))
    return checks


def check_glossary(cfg: AppConfig) -> list[CheckResult]:
    glossary_file = cfg.semantic.glossary_file
    if not glossary_file:
        return [CheckResult("glossary 词典", "warn", "未配置", "可选；设置 semantic.glossary_file 启用语义层")]
    path = Path(glossary_file).expanduser()
    if not path.exists():
        return [CheckResult("glossary 词典", "error", f"{path} 不存在", "检查路径或移除配置")]
    try:
        tomllib.loads(path.read_text(encoding="utf-8-sig"))
    except (tomllib.TOMLDecodeError, OSError) as exc:
        return [CheckResult("glossary 词典", "error", f"解析失败: {exc}", "修复 glossary 的 TOML 语法")]
    glossary = Glossary.load(glossary_file)
    return [CheckResult("glossary 词典", "ok", f"{len(glossary.terms)} 条术语", None)]


def _try_bind(port: int) -> str | None:
    """尝试绑定 127.0.0.1:port；成功返回 None，失败返回原因。"""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind(("127.0.0.1", port))
            return None
    except OSError as exc:
        return str(exc)


def check_metrics_port(cfg: AppConfig, try_bind: Callable[[int], str | None] | None = None) -> list[CheckResult]:
    if not cfg.metrics.enabled:
        return [CheckResult("metrics 端口", "warn", "未启用", "metrics.enabled = true 启用指标端点")]
    port = cfg.metrics.port
    binder = try_bind or _try_bind
    reason = binder(port)
    if reason is None:
        return [CheckResult("metrics 端口", "ok", f"{port} 可绑定", None)]
    return [
        CheckResult(
            "metrics 端口", "error", f"{port} 无法绑定（{reason}）",
            "释放占用端口，或修改 metrics.port",
        )
    ]


async def check_connections(cfg: AppConfig) -> list[CheckResult]:
    audit = AuditLogger(cfg.audit)
    registry = RuntimeRegistry(cfg, audit, Glossary.load(cfg.semantic.glossary_file))
    checks: list[CheckResult] = []
    try:
        results = await registry.ping()
        for name, result in results.items():
            if result.get("ok"):
                checks.append(
                    CheckResult(
                        f"连接 '{name}'", "ok",
                        f"延迟 {result.get('latency_ms', '?')}ms，方言 {result.get('dialect', '?')}", None,
                    )
                )
            else:
                checks.append(
                    CheckResult(
                        f"连接 '{name}'", "error",
                        result.get("error", "连接失败"), "检查网络/凭据/防火墙/连接配置",
                    )
                )
    finally:
        await registry.close_all()
    return checks


async def run_doctor(config_path: str | None, *, try_bind: Callable[[int], str | None] | None = None) -> list[CheckResult]:
    checks: list[CheckResult] = []
    path = Path(config_path) if config_path else None
    if path is None:
        from db_assistant_mcp.config import _resolve_config_path

        path = _resolve_config_path(None)
    checks.extend(check_config(path))
    if any(c.status == "error" for c in checks):
        # 配置损坏时仍检查依赖，帮助定位环境问题
        checks.extend(check_dependencies())
        return checks
    cfg = load_config(str(path))
    checks.extend(check_dependencies())
    checks.extend(check_glossary(cfg))
    checks.extend(check_metrics_port(cfg, try_bind=try_bind))
    checks.extend(await check_connections(cfg))
    return checks
