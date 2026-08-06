"""调用身份解析：环境变量优先，回退系统用户。"""

from __future__ import annotations

import getpass
import os


def current_identity() -> tuple[str, str]:
    """返回 (client, user)。"""
    client = os.environ.get("DB_ASSISTANT_CLIENT", "mcp")
    user = os.environ.get("DB_ASSISTANT_USER") or getpass.getuser()
    return client, user

