"""config.toml 加载、${ENV_VAR} 替换与 schema 校验。"""

from __future__ import annotations

import os
import re
import stat
import tomllib
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from db_assistant_mcp.errors import ConfigError, ConnectionError_

DEFAULT_CONFIG_PATH = Path("~/.config/db-assistant/config.toml").expanduser()
ENV_REF = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")

VALID_TYPES = {"postgres", "mysql"}
VALID_MODES = {"read_only", "safe_write", "full"}
VALID_AUDIT_OUTPUTS = {"file", "stdout", "stderr", "webhook"}


@dataclass
class ServerConfig:
    mode: str = "read_only"
    default_limit: int = 100
    query_timeout_sec: int = 10
    max_concurrent: int = 5
    schema_cache_ttl_sec: int = 300
    config_reload_interval_sec: int = 30


@dataclass
class ConnectionConfig:
    name: str
    type: str
    host: str
    port: int
    database: str
    user: str
    password_env: str | None = None
    password_encrypted: str | None = None
    mode: str = "read_only"
    masked_columns: list[str] = field(default_factory=list)
    exclude_columns: list[str] = field(default_factory=list)
    exclude_tables: list[str] = field(default_factory=list)
    connect_timeout_sec: int = 5
    ssl: bool = False
    charset: str = "utf8mb4"

    @property
    def password(self) -> str | None:
        if self.password_env:
            value = os.environ.get(self.password_env)
            if value is None:
                raise ConfigError(
                    f"连接 '{self.name}' 引用的环境变量 {self.password_env} 不存在",
                    detail=f"MISSING_ENV_VAR:{self.password_env}",
                    connection=self.name,
                )
            return value
        if self.password_encrypted:
            from db_assistant_mcp.secrets_util import decrypt_secret

            return decrypt_secret(self.password_encrypted)
        return None


@dataclass
class SemanticConfig:
    glossary_file: str | None = None
    templates_dir: str | None = None
    candidate_file: str | None = None
    llm_api_key_env: str | None = None
    llm_base_url: str | None = None
    llm_model: str | None = None


@dataclass
class AuditConfig:
    output: str = "file"
    path: str | None = None
    webhook_url: str | None = None
    webhook_secret_env: str | None = None


@dataclass
class MetricsConfig:
    enabled: bool = True
    port: int = 9102


@dataclass
class HttpConfig:
    """[http] 段：streamable HTTP 共享部署。token_env 为必填（fail-closed）。"""

    token_env: str | None = None
    host: str = "127.0.0.1"
    port: int = 8000


@dataclass
class AppConfig:
    server: ServerConfig
    connections: dict[str, ConnectionConfig]
    semantic: SemanticConfig
    audit: AuditConfig
    metrics: MetricsConfig
    http: HttpConfig
    config_path: str

    @property
    def connection_names(self) -> list[str]:
        return list(self.connections.keys())

    def get_connection(self, name: str) -> ConnectionConfig:
        try:
            return self.connections[name]
        except KeyError as exc:
            available = ", ".join(self.connections) or "(无)"
            raise ConnectionError_(
                f"连接 '{name}' 不存在，可用连接: {available}",
                detail=f"UNKNOWN_CONNECTION:{name}",
                connection=name,
                hint="使用 list_databases 查看可用连接",
            ) from exc


def _resolve_config_path(path: str | None) -> Path:
    if path:
        return Path(path).expanduser()
    env_path = os.environ.get("DB_ASSISTANT_CONFIG")
    if env_path:
        return Path(env_path).expanduser()
    return DEFAULT_CONFIG_PATH


def _substitute_env(value: str, *, source: str) -> str:
    def repl(match: re.Match[str]) -> str:
        name = match.group(1)
        if name not in os.environ:
            raise ConfigError(
                f"配置项引用的环境变量 {name} 不存在（位置: {source}）",
                detail=f"MISSING_ENV_VAR:{name}",
            )
        return os.environ[name]

    return ENV_REF.sub(repl, value)


def _check_file_permissions(path: Path) -> None:
    """配置文件权限过宽时告警但不阻断（CFG-005）。"""
    if os.name == "nt":
        return  # Windows 无 POSIX 权限语义
    try:
        mode = stat.S_IMODE(path.stat().st_mode)
    except OSError:
        return
    if mode & 0o077:
        warnings.warn(
            f"配置文件 {path} 权限过宽（0o{mode:o}），建议限制为仅本人可读写",
            RuntimeWarning,
            stacklevel=3,
        )


def _parse_int(raw: Any, name: str, *, default: int, min_value: int, max_value: int) -> int:
    if raw is None:
        return default
    if isinstance(raw, bool) or not isinstance(raw, int):
        raise ConfigError(f"配置项 {name} 必须为整数，当前: {raw!r}")
    if not min_value <= raw <= max_value:
        raise ConfigError(f"配置项 {name} 超出范围 [{min_value}, {max_value}]，当前: {raw}")
    return raw


def _parse_server(raw: dict[str, Any]) -> ServerConfig:
    mode = raw.get("mode", "read_only")
    if mode not in VALID_MODES:
        raise ConfigError(f"[server].mode 非法: {mode!r}，可选 {sorted(VALID_MODES)}")
    return ServerConfig(
        mode=mode,
        default_limit=_parse_int(raw.get("default_limit"), "server.default_limit", default=100, min_value=1, max_value=1000),
        query_timeout_sec=_parse_int(
            raw.get("query_timeout_sec"), "server.query_timeout_sec", default=10, min_value=1, max_value=60
        ),
        max_concurrent=_parse_int(raw.get("max_concurrent"), "server.max_concurrent", default=5, min_value=1, max_value=100),
        schema_cache_ttl_sec=_parse_int(
            raw.get("schema_cache_ttl_sec"), "server.schema_cache_ttl_sec", default=300, min_value=1, max_value=86400
        ),
        config_reload_interval_sec=_parse_int(
            raw.get("config_reload_interval_sec"), "server.config_reload_interval_sec", default=30, min_value=0, max_value=86400
        ),
    )


def parse_semantic(raw: dict[str, Any]) -> SemanticConfig:
    """解析 [semantic] 段（不要求 [connections]，供 glossary 管理类 CLI 使用）。"""
    semantic_raw = raw.get("semantic", {}) or {}
    return SemanticConfig(
        glossary_file=semantic_raw.get("glossary_file"),
        templates_dir=semantic_raw.get("templates_dir"),
        candidate_file=semantic_raw.get("candidate_file"),
        llm_api_key_env=semantic_raw.get("llm_api_key_env"),
        llm_base_url=semantic_raw.get("llm_base_url"),
        llm_model=semantic_raw.get("llm_model"),
    )


def _parse_connection(name: str, raw: dict[str, Any]) -> ConnectionConfig:
    required = ("type", "host", "database", "user")
    missing = [k for k in required if k not in raw or raw[k] in (None, "")]
    if missing:
        raise ConfigError(
            f"连接 '{name}' 缺少必需字段: {', '.join(missing)}",
            detail=f"MISSING_FIELD:{name}:{','.join(missing)}",
        )
    conn_type = str(raw["type"]).lower()
    if conn_type not in VALID_TYPES:
        raise ConfigError(
            f"连接 '{name}' 的 type 非法: {conn_type!r}，可选 {sorted(VALID_TYPES)}",
            detail=f"INVALID_TYPE:{name}:{conn_type}",
        )
    mode = raw.get("mode", "read_only")
    if mode not in VALID_MODES:
        raise ConfigError(
            f"连接 '{name}' 的 mode 非法: {mode!r}，可选 {sorted(VALID_MODES)}",
            detail=f"INVALID_MODE:{name}:{mode}",
        )
    password_env = raw.get("password_env")
    password_encrypted = raw.get("password_encrypted")
    if not password_env and not password_encrypted:
        # 允许无密码（本地可信环境），记录到 detail 而非报错
        pass

    def str_list(key: str) -> list[str]:
        value = raw.get(key, [])
        if not isinstance(value, list) or not all(isinstance(i, str) and i.strip() for i in value):
            raise ConfigError(f"连接 '{name}' 的 {key} 必须是非空字符串数组")
        return list(value)

    return ConnectionConfig(
        name=name,
        type=conn_type,
        host=_substitute_env(str(raw["host"]), source=f"connections.{name}.host"),
        port=_parse_int(raw.get("port"), f"connections.{name}.port", default=5432 if conn_type == "postgres" else 3306, min_value=1, max_value=65535),
        database=str(raw["database"]),
        user=str(raw["user"]),
        password_env=str(password_env) if password_env else None,
        password_encrypted=str(password_encrypted) if password_encrypted else None,
        mode=mode,
        masked_columns=str_list("masked_columns"),
        exclude_columns=str_list("exclude_columns"),
        exclude_tables=str_list("exclude_tables"),
        connect_timeout_sec=_parse_int(
            raw.get("connect_timeout_sec"), f"connections.{name}.connect_timeout_sec", default=5, min_value=1, max_value=60
        ),
        ssl=bool(raw.get("ssl", False)),
        charset=str(raw.get("charset", "utf8mb4")),
    )


def _parse_audit(raw: dict[str, Any]) -> AuditConfig:
    output = raw.get("output", "file")
    if output not in VALID_AUDIT_OUTPUTS:
        raise ConfigError(f"[audit].output 非法: {output!r}，可选 {sorted(VALID_AUDIT_OUTPUTS)}")
    path = raw.get("path")
    if output == "file" and not path:
        path = str(DEFAULT_CONFIG_PATH.parent / "audit.log")
    return AuditConfig(
        output=output,
        path=_substitute_env(str(path), source="audit.path") if path else None,
        webhook_url=raw.get("url"),
        webhook_secret_env=raw.get("secret_env"),
    )


def _parse_metrics(raw: dict[str, Any]) -> MetricsConfig:
    return MetricsConfig(
        enabled=bool(raw.get("enabled", True)),
        port=_parse_int(raw.get("port"), "metrics.port", default=9102, min_value=1, max_value=65535),
    )


def _parse_http(raw: dict[str, Any]) -> HttpConfig:
    http_raw = raw.get("http", {}) or {}
    token_env = http_raw.get("token_env")
    if token_env is not None and not str(token_env).strip():
        raise ConfigError("[http].token_env 不能为空", detail="HTTP_EMPTY_TOKEN_ENV")
    return HttpConfig(
        token_env=str(token_env).strip() if token_env else None,
        host=str(http_raw.get("host", "127.0.0.1")),
        port=_parse_int(http_raw.get("port"), "http.port", default=8000, min_value=1, max_value=65535),
    )


def load_config(path: str | None = None) -> AppConfig:
    """加载并校验配置；缺失/非法时抛出 ConfigError（含明确信息）。"""
    config_path = _resolve_config_path(path)
    if not config_path.exists():
        raise ConfigError(
            f"配置文件不存在: {config_path}",
            detail="CONFIG_NOT_FOUND",
            hint="使用 `db-assistant add` 创建，或设置 DB_ASSISTANT_CONFIG 环境变量",
        )
    _check_file_permissions(config_path)
    try:
        raw = tomllib.loads(config_path.read_text(encoding="utf-8-sig"))
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(
            f"配置文件 TOML 语法错误: {config_path}: {exc}",
            detail=f"TOML_SYNTAX_ERROR:{exc}",
        ) from exc
    except OSError as exc:
        raise ConfigError(f"无法读取配置文件 {config_path}: {exc}", detail="CONFIG_READ_ERROR") from exc

    server = _parse_server(raw.get("server", {}))
    connections_raw = raw.get("connections", {})
    if not isinstance(connections_raw, dict) or not connections_raw:
        raise ConfigError("配置缺少 [connections] 段（至少需要一个连接）", detail="NO_CONNECTIONS")
    connections = {
        name: _parse_connection(name, section)
        for name, section in connections_raw.items()
        if isinstance(section, dict)
    }
    semantic = parse_semantic(raw)
    audit = _parse_audit(raw.get("audit", {}) or {})
    metrics = _parse_metrics(raw.get("metrics", {}) or {})
    http = _parse_http(raw)
    return AppConfig(
        server=server,
        connections=connections,
        semantic=semantic,
        audit=audit,
        metrics=metrics,
        http=http,
        config_path=str(config_path),
    )


def save_connection(config_path: Path, conn: ConnectionConfig) -> None:
    """将单个连接写入配置文件（CLI add 使用，保留已有内容）。"""
    config_path.parent.mkdir(parents=True, exist_ok=True)
    raw: dict[str, Any] = {}
    if config_path.exists():
        try:
            raw = tomllib.loads(config_path.read_text(encoding="utf-8-sig"))
        except tomllib.TOMLDecodeError:
            raw = {}
    connections = raw.setdefault("connections", {})
    section: dict[str, Any] = {
        "type": conn.type,
        "host": conn.host,
        "port": conn.port,
        "database": conn.database,
        "user": conn.user,
        "mode": conn.mode,
    }
    if conn.password_env:
        section["password_env"] = conn.password_env
    if conn.password_encrypted:
        section["password_encrypted"] = conn.password_encrypted
    if conn.masked_columns:
        section["masked_columns"] = conn.masked_columns
    if conn.exclude_columns:
        section["exclude_columns"] = conn.exclude_columns
    if conn.exclude_tables:
        section["exclude_tables"] = conn.exclude_tables
    if conn.charset != "utf8mb4":
        section["charset"] = conn.charset
    connections[conn.name] = section
    _write_connections(config_path, raw)


def remove_connection(config_path: Path, name: str) -> bool:
    """从配置文件中移除连接；返回是否移除成功。"""
    if not config_path.exists():
        return False
    try:
        raw = tomllib.loads(config_path.read_text(encoding="utf-8-sig"))
    except tomllib.TOMLDecodeError:
        return False
    connections = raw.setdefault("connections", {})
    if name not in connections:
        return False
    del connections[name]
    if not connections:
        raw["connections"] = {}
    _write_connections(config_path, raw)
    return True


def _write_connections(config_path: Path, raw: dict[str, Any]) -> None:
    """按 [connections.X] 顺序重写整个配置文件（保留 server/semantic/audit/metrics）。"""
    lines: list[str] = []
    sections = {k: v for k, v in raw.items() if k != "connections"}
    for name, section_data in sections.items():
        _append_section(lines, name, section_data)
    connections = raw.get("connections", {})
    if connections:
        for name, section_data in connections.items():
            lines.append(f"[connections.{name}]")
            for key, value in section_data.items():
                if isinstance(value, bool):
                    lines.append(f"{key} = {'true' if value else 'false'}")
                elif isinstance(value, int):
                    lines.append(f"{key} = {value}")
                elif isinstance(value, list):
                    quoted = ", ".join(f'"{i}"' for i in value)
                    lines.append(f"{key} = [{quoted}]")
                else:
                    escaped = str(value).replace("\\", "\\\\").replace('"', '\\"')
                    lines.append(f'{key} = "{escaped}"')
            lines.append("")
    data = "\n".join(lines)
    # 新建配置文件以 0o600 创建（含加密凭据，避免全局可读）；已存在的文件保留原权限
    existed = config_path.exists()
    fd = os.open(config_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    if not existed:
        try:
            os.fchmod(fd, 0o600)
        except OSError:
            pass
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(data)


def _append_section(lines: list[str], name: str, section_data: Any) -> None:
    if not isinstance(section_data, dict):
        return
    lines.append(f"[{name}]")
    for key, value in section_data.items():
        if isinstance(value, bool):
            lines.append(f"{key} = {'true' if value else 'false'}")
        elif isinstance(value, int):
            lines.append(f"{key} = {value}")
        elif isinstance(value, list):
            quoted = ", ".join(f'"{i}"' for i in value)
            lines.append(f"{key} = [{quoted}]")
        elif isinstance(value, dict):
            continue  # 嵌套段（如 override）由后续递归处理
        else:
            escaped = str(value).replace("\\", "\\\\").replace('"', '\\"')
            lines.append(f'{key} = "{escaped}"')
    lines.append("")
    for sub_name, sub_data in section_data.items():
        if isinstance(sub_data, dict):
            _append_section(lines, f"{name}.{sub_name}", sub_data)
