"""结构化 JSON 日志 + 敏感信息脱敏。"""

from __future__ import annotations

import json
import logging
import re
import sys
from datetime import UTC, datetime
from typing import Any

_SENSITIVE_KEY = re.compile(
    r"(password|passwd|pwd|secret|token|api[_-]?key|private[_-]?key|credential|dsn|connection[_-]?string)",
    re.IGNORECASE,
)


def redact_value(key: str, value: Any) -> Any:
    """对敏感键的值打码，防止凭据进入日志。"""
    if _SENSITIVE_KEY.search(key):
        if value is None:
            return None
        return "***"
    return value


def redact_dict(data: dict[str, Any]) -> dict[str, Any]:
    """递归脱敏字典中敏感键对应的值。"""
    result: dict[str, Any] = {}
    for k, v in data.items():
        if _SENSITIVE_KEY.search(k):
            result[k] = "***" if v is not None else None
        elif isinstance(v, dict):
            result[k] = redact_dict(v)
        elif isinstance(v, (list, tuple)):
            result[k] = [redact_dict(i) if isinstance(i, dict) else i for i in v]
        else:
            result[k] = v
    return result


class JsonFormatter(logging.Formatter):
    """输出单行 JSON：{ts, level, msg, context}。"""

    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, Any] = {
            "ts": datetime.now(UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "msg": record.getMessage(),
        }
        context = getattr(record, "context", None)
        if context:
            entry["context"] = redact_dict(context)
        if record.exc_info and record.exc_info[0] is not None:
            entry["exc"] = self.formatException(record.exc_info)
        return json.dumps(entry, ensure_ascii=False, default=str)


def get_logger(name: str = "db_assistant_mcp") -> logging.Logger:
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger
    stream = sys.stderr
    try:
        stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    except (AttributeError, OSError):
        pass
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter())
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    return logger


def log_context(logger: logging.Logger, level: int, msg: str, **context: Any) -> None:
    """带结构化 context 的日志入口。"""
    logger.log(level, msg, extra={"context": context})
