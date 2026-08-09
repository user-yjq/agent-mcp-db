from __future__ import annotations

import os

import pytest

from db_assistant_mcp.config import (
    ConnectionConfig,
    load_config,
    remove_connection,
    save_connection,
)
from db_assistant_mcp.errors import ConfigError


def _write(tmp_path, content: str) -> str:
    p = tmp_path / "config.toml"
    p.write_text(content, encoding="utf-8")
    os.chmod(p, 0o600)  # 与 CLI 新建行为一致（0o600），避免 CFG-005 告警噪音
    return str(p)


def test_minimal_config_defaults(tmp_path):
    path = _write(
        tmp_path,
        """
[connections.pg]
type = "postgres"
host = "localhost"
port = 5432
database = "orders"
user = "svc_ai"
password_env = "PG_PW"
""",
    )
    os.environ["PG_PW"] = "s3cret"
    cfg = load_config(path)
    assert cfg.server.mode == "read_only"
    assert cfg.server.default_limit == 100
    assert cfg.server.query_timeout_sec == 10
    conn = cfg.connections["pg"]
    assert conn.password == "s3cret"
    assert conn.mode == "read_only"


def test_env_substitution(tmp_path):
    os.environ["MY_HOST"] = "db.internal"
    os.environ["MY_PW"] = "pw123"
    path = _write(
        tmp_path,
        """
[connections.pg]
type = "postgres"
host = "${MY_HOST}"
database = "orders"
user = "svc"
password_env = "MY_PW"
""",
    )
    cfg = load_config(path)
    assert cfg.connections["pg"].host == "db.internal"


def test_env_var_missing(tmp_path):
    path = _write(
        tmp_path,
        """
[connections.pg]
type = "postgres"
host = "${NOPE}"
database = "orders"
user = "svc"
""",
    )
    with pytest.raises(ConfigError, match="NOPE"):
        load_config(path)


def test_toml_syntax_error(tmp_path):  # CFG-001
    path = _write(tmp_path, "[connections\nbad")
    with pytest.raises(ConfigError, match="TOML 语法错误"):
        load_config(path)


def test_missing_required_field(tmp_path):  # CFG-002
    path = _write(
        tmp_path,
        """
[connections.pg]
type = "postgres"
host = "localhost"
""",
    )
    with pytest.raises(ConfigError, match="缺少必需字段"):
        load_config(path)


def test_port_type_mismatch(tmp_path):  # CFG-004
    path = _write(
        tmp_path,
        """
[connections.pg]
type = "postgres"
host = "localhost"
port = "5432"
database = "orders"
user = "svc"
""",
    )
    with pytest.raises(ConfigError, match="必须为整数"):
        load_config(path)


def test_invalid_mode(tmp_path):
    path = _write(
        tmp_path,
        """
[connections.pg]
type = "postgres"
host = "localhost"
database = "orders"
user = "svc"
mode = "sudo"
""",
    )
    with pytest.raises(ConfigError, match="mode"):
        load_config(path)


def test_no_connections(tmp_path):
    path = _write(tmp_path, "[server]\nmode = \"read_only\"\n")
    with pytest.raises(ConfigError, match="connections"):
        load_config(path)


def test_config_not_found(tmp_path):
    with pytest.raises(ConfigError, match="不存在"):
        load_config(str(tmp_path / "nope.toml"))


def test_password_env_missing_at_access(tmp_path):
    path = _write(
        tmp_path,
        """
[connections.pg]
type = "postgres"
host = "localhost"
database = "orders"
user = "svc"
password_env = "NOT_SET_ANYWHERE"
""",
    )
    cfg = load_config(path)
    with pytest.raises(ConfigError, match="NOT_SET_ANYWHERE"):
        _ = cfg.connections["pg"].password


def test_save_and_remove_roundtrip(tmp_path):
    path = tmp_path / "config.toml"
    conn = ConnectionConfig(
        name="dev",
        type="mysql",
        host="127.0.0.1",
        port=3306,
        database="app",
        user="root",
        password_env="PW",
        masked_columns=["phone"],
        exclude_tables=["raw"],
    )
    save_connection(path, conn)
    cfg = load_config(str(path))
    assert cfg.connections["dev"].port == 3306
    assert cfg.connections["dev"].masked_columns == ["phone"]

    conn2 = ConnectionConfig(name="pg", type="postgres", host="h", port=5432, database="d", user="u")
    save_connection(path, conn2)
    cfg = load_config(str(path))
    assert set(cfg.connections) == {"dev", "pg"}

    assert remove_connection(path, "dev") is True
    cfg = load_config(str(path))
    assert set(cfg.connections) == {"pg"}
    assert remove_connection(path, "missing") is False


def test_default_limit_clamped(tmp_path):
    path = _write(
        tmp_path,
        """
[server]
default_limit = 5000
[connections.pg]
type = "postgres"
host = "localhost"
database = "orders"
user = "svc"
""",
    )
    with pytest.raises(ConfigError, match="超出范围"):
        load_config(path)



def test_save_connection_creates_restrictive_permissions(tmp_path):  # CFG-005 根因
    import stat

    path = tmp_path / "config.toml"
    conn = ConnectionConfig(name="dev", type="postgres", host="h", port=5432, database="d", user="u")
    save_connection(path, conn)
    mode = stat.S_IMODE(path.stat().st_mode)
    assert mode & 0o077 == 0  # 新建配置文件仅本人可读写


def test_load_config_warns_on_permissive_permissions(tmp_path):
    # CFG-005：手工创建/权限过宽的配置仍告警但不阻断
    path = tmp_path / "config.toml"
    path.write_text(
        '[connections.pg]\ntype="postgres"\nhost="h"\ndatabase="d"\nuser="u"\n',
        encoding="utf-8",
    )
    os.chmod(path, 0o644)
    with pytest.warns(RuntimeWarning, match="权限过宽"):
        load_config(str(path))

def test_audit_output_stderr_valid(tmp_path):
    path = _write(
        tmp_path,
        """
[connections.pg]
type = "postgres"
host = "localhost"
database = "orders"
user = "svc"
[audit]
output = "stderr"
""",
    )
    assert load_config(path).audit.output == "stderr"


def test_audit_output_invalid(tmp_path):
    path = _write(
        tmp_path,
        """
[connections.pg]
type = "postgres"
host = "localhost"
database = "orders"
user = "svc"
[audit]
output = "logstash"
""",
    )
    with pytest.raises(ConfigError):
        load_config(path)
