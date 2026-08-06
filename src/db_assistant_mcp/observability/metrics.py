"""Prometheus 指标定义。"""

from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram

tool_calls = Counter(
    "db_assistant_tool_calls_total",
    "工具调用总数",
    ["tool", "connection", "result"],
)

query_duration = Histogram(
    "db_assistant_query_duration_seconds",
    "查询耗时分布",
    ["tool", "connection"],
    buckets=(0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0),
)

security_rejections = Counter(
    "db_assistant_security_rejections_total",
    "安全拒绝次数",
    ["connection", "rule"],
)

schema_cache_hits = Counter(
    "db_assistant_schema_cache_hits_total",
    "Schema 缓存命中次数",
    ["connection"],
)

schema_cache_misses = Counter(
    "db_assistant_schema_cache_misses_total",
    "Schema 缓存未命中次数",
    ["connection"],
)

active_connections = Gauge(
    "db_assistant_active_connections",
    "当前活跃数据库连接数",
    ["connection"],
)

