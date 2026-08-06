"""凭据加密：AES-256-GCM，密钥来自环境变量或本地密钥文件。"""

from __future__ import annotations

import base64
import hashlib
import os
from pathlib import Path

from db_assistant_mcp.errors import ConfigError

_MASTER_KEY_ENV = "DB_ASSISTANT_MASTER_KEY"
_KEY_FILE = Path("~/.config/db-assistant/master.key").expanduser()


def _derive_key(master_key: str) -> bytes:
    return hashlib.sha256(master_key.encode("utf-8")).digest()


def load_master_key() -> bytes:
    """从环境变量或本地密钥文件读取 32 字节主密钥。"""
    env_key = os.environ.get(_MASTER_KEY_ENV)
    if env_key:
        return _derive_key(env_key)
    if _KEY_FILE.exists():
        try:
            return base64.b64decode(_KEY_FILE.read_text(encoding="utf-8").strip())
        except Exception as exc:  # noqa: BLE001
            raise ConfigError(f"无法读取主密钥文件 {_KEY_FILE}: {exc}") from exc
    raise ConfigError(
        "未找到主密钥：请设置环境变量 DB_ASSISTANT_MASTER_KEY "
        f"或创建密钥文件 {_KEY_FILE}（base64 编码的 32 字节）"
    )


def ensure_master_key_file() -> Path:
    """创建本地密钥文件（首次使用交互式加密时调用）。"""
    if _KEY_FILE.exists():
        return _KEY_FILE
    _KEY_FILE.parent.mkdir(parents=True, exist_ok=True)
    raw = os.urandom(32)
    _KEY_FILE.write_text(base64.b64encode(raw).decode("ascii"), encoding="utf-8")
    try:
        os.chmod(_KEY_FILE, 0o600)
    except OSError:
        pass  # Windows 无 chmod 语义
    return _KEY_FILE


def encrypt_secret(plaintext: str) -> str:
    """返回 base64 编码的 iv:tag:ciphertext。"""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM  # type: ignore

    key = load_master_key()
    iv = os.urandom(12)
    ct = AESGCM(key).encrypt(iv, plaintext.encode("utf-8"), None)
    return base64.b64encode(iv).decode() + ":" + base64.b64encode(ct).decode()


def decrypt_secret(payload: str) -> str:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM  # type: ignore

    key = load_master_key()
    try:
        iv_b64, ct_b64 = payload.split(":", 1)
        iv = base64.b64decode(iv_b64)
        ct = base64.b64decode(ct_b64)
        return AESGCM(key).decrypt(iv, ct, None).decode("utf-8")
    except Exception as exc:  # noqa: BLE001
        raise ConfigError("凭据解密失败：主密钥不匹配或数据损坏") from exc

