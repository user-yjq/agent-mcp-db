"""驱动抽象与通用值规范化。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import Decimal
from typing import Any
from uuid import UUID


@dataclass
class SchemaColumn:
    name: str
    data_type: str
    nullable: bool
    default: str | None
    comment: str | None
    primary_key: bool = False


def normalize_value(value: Any) -> Any:
    """把驱动返回的值转换为 JSON 安全类型。"""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, bytes):
        return value.hex()
    if isinstance(value, dict):
        return {str(k): normalize_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [normalize_value(v) for v in value]
    return str(value)


class DatabaseConnection(ABC):
    """单个数据库连接（连接池元素）的统一接口。"""

    dialect: str

    @abstractmethod
    async def close(self) -> None:
        """关闭底层连接。"""

    @abstractmethod
    async def is_valid(self) -> bool:
        """连接是否仍可用。"""

    @abstractmethod
    async def ping(self) -> float:
        """返回往返延迟（毫秒），失败抛异常。"""

    @abstractmethod
    async def fetch(self, sql: str, timeout: float) -> tuple[list[str], list[list[Any]]]:
        """执行只读 SQL，返回 (列名, 行值列表)。"""

    @abstractmethod
    async def list_tables(self) -> list[dict[str, Any]]:
        """返回 [{name, estimated_rows, comment}]。"""

    @abstractmethod
    async def table_schema(self, table: str) -> dict[str, Any]:
        """返回 {columns, indexes, foreign_keys, comment}。"""

    @abstractmethod
    async def search_schema(self, keyword: str) -> dict[str, list[dict[str, str]]]:
        """返回 {tables: [{name}], columns: [{table, column}]}。"""

    @abstractmethod
    async def explain(self, sql: str, analyze: bool, timeout: float) -> dict[str, Any]:
        """返回执行计划（优先 JSON 格式）。"""

