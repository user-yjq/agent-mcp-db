from __future__ import annotations

import pytest

from db_assistant_mcp.errors import ConfigError
from db_assistant_mcp.secrets_util import decrypt_secret, encrypt_secret


def test_roundtrip():
    payload = encrypt_secret("p@ssw0rd")
    assert decrypt_secret(payload) == "p@ssw0rd"
    assert payload != "p@ssw0rd"


def test_wrong_key_fails(monkeypatch):
    payload = encrypt_secret("secret")
    monkeypatch.setenv("DB_ASSISTANT_MASTER_KEY", "different-key")
    with pytest.raises(ConfigError, match="解密失败"):
        decrypt_secret(payload)


def test_missing_key(monkeypatch):
    monkeypatch.delenv("DB_ASSISTANT_MASTER_KEY", raising=False)
    monkeypatch.setattr("db_assistant_mcp.secrets_util._KEY_FILE", __import__("pathlib").Path("z:/nonexistent/key"))
    with pytest.raises(ConfigError, match="主密钥"):
        encrypt_secret("x")

