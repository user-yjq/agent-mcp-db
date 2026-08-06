"""执行计划可视化：把 PG/MySQL EXPLAIN JSON 统一为树结构 + markdown 摘要。

驱动返回结构：{"format": "json"|"text", "analyze": bool, "plan": ...}。
- PG：EXPLAIN (FORMAT JSON) → 单行 JSON 树
- MySQL：EXPLAIN FORMAT=JSON → JSON 树；不支持时降级为文本行
任何无法解析的情况都回退返回原始输出，绝不抛异常中断工具调用。
"""

from __future__ import annotations

from typing import Any

from db_assistant_mcp.errors import InvalidParamsError

SUPPORTED_FORMATS = ("raw", "tree", "markdown")


def _to_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _to_int(value: Any) -> int | None:
    try:
        if value is None:
            return None
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _fmt(value: Any) -> str:
    if value is None:
        return "-"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _walk(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for node in nodes:
        result.append(node)
        result.extend(_walk(node.get("children") or []))
    return result


def _pg_node(node: dict[str, Any]) -> dict[str, Any]:
    """PostgreSQL EXPLAIN JSON 节点。"""
    children = [_pg_node(c) for c in (node.get("Plans") or [])]
    return {
        "label": node.get("Node Type") or "UNKNOWN",
        "node_type": node.get("Node Type") or "UNKNOWN",
        "table": node.get("Relation Name") or node.get("Index Name") or node.get("CTE Name"),
        "cost": _to_float(node.get("Total Cost")),
        "rows": _to_int(node.get("Plan Rows")),
        "condition": (
            node.get("Filter")
            or node.get("Index Cond")
            or node.get("Join Filter")
            or node.get("Hash Cond")
            or node.get("Recheck Cond")
        ),
        "actual_rows": _to_int(node.get("Actual Rows")),
        "children": children,
    }


def _mysql_cost(node: dict[str, Any]) -> float | None:
    info = node.get("cost_info")
    if not isinstance(info, dict):
        return None
    return _to_float(info.get("query_cost") or info.get("read_cost") or info.get("eval_cost") or info.get("prefix_cost"))


def _mysql_node(obj: dict[str, Any]) -> list[dict[str, Any]]:
    """MySQL EXPLAIN FORMAT=JSON 节点（query_block / table / nested_loop / union）。"""
    nodes: list[dict[str, Any]] = []

    if "query_block" in obj:
        qb = obj["query_block"]
        node: dict[str, Any] = {
            "label": f"QUERY_BLOCK #{qb.get('select_id', '?')}",
            "node_type": "QUERY_BLOCK",
            "table": None,
            "cost": _mysql_cost(qb),
            "rows": _to_int(qb.get("rows_produced_per_join")),
            "condition": None,
            "children": [],
        }
        if isinstance(qb.get("table"), dict):
            node["children"].extend(_mysql_node({"table": qb["table"]}))
        for item in qb.get("nested_loop") or []:
            node["children"].extend(_mysql_node(item))
        if isinstance(qb.get("union_result"), dict):
            node["children"].extend(_mysql_node(qb["union_result"]))
        nodes.append(node)
        return nodes

    if "table" in obj and isinstance(obj["table"], dict):
        tbl = obj["table"]
        node = {
            "label": tbl.get("table_name") or tbl.get("table") or "TABLE",
            "node_type": tbl.get("access_type") or "TABLE",
            "table": tbl.get("table_name"),
            "cost": _mysql_cost(tbl),
            "rows": _to_int(tbl.get("rows_examined_per_scan") or tbl.get("rows_produced_per_join")),
            "condition": (
                tbl.get("attached_condition")
                or tbl.get("index_condition")
                or tbl.get("using_index_condition")
                or tbl.get("using_join_buffer")
            ),
            "children": [],
        }
        if isinstance(tbl.get("query_block"), dict):  # 子查询 / 派生表
            node["children"].extend(_mysql_node({"query_block": tbl["query_block"]}))
        nodes.append(node)
        return nodes

    if "union_result" in obj:
        ur = obj["union_result"]
        node = {
            "label": "UNION RESULT",
            "node_type": "UNION",
            "table": None,
            "cost": None,
            "rows": None,
            "condition": None,
            "children": [],
        }
        for item in ur.get("query_specifications") or []:
            node["children"].extend(_mysql_node(item))
        nodes.append(node)
        return nodes

    return nodes


def to_tree(plan_result: dict[str, Any]) -> list[dict[str, Any]]:
    """JSON 执行计划 → 统一树节点列表；text 或无法识别返回 []。"""
    if plan_result.get("format") != "json":
        return []
    plan = plan_result.get("plan")
    if not isinstance(plan, dict):
        return []
    if "Plan" in plan:  # PostgreSQL
        return [_pg_node(plan["Plan"])]
    # MySQL: query_block / union_result 顶层
    return _mysql_node(plan)


def summarize(plan_result: dict[str, Any]) -> dict[str, Any]:
    """提取摘要：格式、扫描类型、涉及表、总成本、关键节点。"""
    fmt = plan_result.get("format")
    analyze = bool(plan_result.get("analyze"))
    if fmt == "text":
        rows = plan_result.get("plan")
        return {
            "format": "text",
            "analyze": analyze,
            "tables": [],
            "node_types": [],
            "total_cost": None,
            "row_count": len(rows) if isinstance(rows, list) else 0,
            "note": "数据库未返回 JSON 执行计划（MySQL 5.7/MariaDB 降级为文本），无法生成树/摘要，请使用 format=raw",
        }
    nodes = to_tree(plan_result)
    if not nodes:
        return {
            "format": fmt,
            "analyze": analyze,
            "tables": [],
            "node_types": [],
            "total_cost": None,
            "note": "无法解析执行计划结构，请使用 format=raw 查看原始输出",
        }
    all_nodes = _walk(nodes)
    tables = sorted({n["table"] for n in all_nodes if n.get("table")})
    node_types = sorted({n["node_type"] for n in all_nodes if n.get("node_type")})
    costs = [n["cost"] for n in all_nodes if n.get("cost") is not None]
    return {
        "format": fmt,
        "analyze": analyze,
        "tables": tables,
        "node_types": node_types,
        "total_cost": max(costs) if costs else None,
        "note": None,
    }


def to_markdown(plan_result: dict[str, Any]) -> str:
    """生成便于 AI 阅读的 markdown 摘要。"""
    summary = summarize(plan_result)
    lines = ["## 执行计划摘要"]
    if summary.get("note"):
        lines.append(f"> {summary['note']}")
    if summary["format"] == "text":
        return "\n".join(lines)
    lines.append(f"- 扫描方式: {', '.join(summary['node_types']) or '-'}")
    lines.append(f"- 涉及表: {', '.join(summary['tables']) or '-'}")
    lines.append(f"- 总成本: {_fmt(summary['total_cost'])}")
    lines.append(f"- 是否分析: {'是' if summary['analyze'] else '否'}")
    lines.append("")
    lines.append("| 节点 | 表 | 成本 | 预估行数 | 条件 |")
    lines.append("|---|---|---|---|---|")
    for node in _walk(to_tree(plan_result)):
        condition = (node.get("condition") or "-").replace("|", "\\|")
        lines.append(f"| {node['label']} | {node.get('table') or '-'} | {_fmt(node.get('cost'))} | {_fmt(node.get('rows'))} | {condition} |")
    return "\n".join(lines)


def format_plan(plan_result: dict[str, Any], fmt: str) -> dict[str, Any]:
    """按 fmt 转换执行计划；任何解析失败都回退原始输出 + 提示，不抛异常。"""
    fmt = (fmt or "raw").strip().lower()
    if fmt not in SUPPORTED_FORMATS:
        raise InvalidParamsError(
            f"不支持的输出格式: {fmt!r}，可选 {list(SUPPORTED_FORMATS)}",
            detail=f"UNSUPPORTED_FORMAT:{fmt}",
            hint=f"format 仅支持 {list(SUPPORTED_FORMATS)}",
        )
    if fmt == "raw":
        return {"format": "raw", "plan": plan_result}
    try:
        if fmt == "tree":
            if plan_result.get("format") == "text":
                return {
                    "format": "tree", "tree": [], "plan": plan_result,
                    "warning": "数据库未返回 JSON 执行计划（MySQL 5.7/MariaDB 降级），无法生成树",
                }
            tree = to_tree(plan_result)
            if not tree:
                return {"format": "tree", "tree": [], "plan": plan_result, "warning": "无法解析执行计划结构"}
            return {"format": "tree", "tree": tree, "summary": summarize(plan_result), "plan": plan_result}
        # markdown
        return {
            "format": "markdown",
            "markdown": to_markdown(plan_result),
            "summary": summarize(plan_result),
            "plan": plan_result,
        }
    except Exception:  # noqa: BLE001
        return {"format": fmt, "plan": plan_result, "warning": "执行计划解析失败，已回退返回原始输出"}
