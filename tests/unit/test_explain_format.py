"""执行计划可视化：T-2.2 format_plan（raw/tree/markdown）+ 降级回退。"""

from __future__ import annotations

import pytest

from db_assistant_mcp.errors import InvalidParamsError
from db_assistant_mcp.explain_format import format_plan, summarize, to_markdown, to_tree

PG_PLAN = {
    "Plan": {
        "Node Type": "Hash Join",
        "Total Cost": 100.5,
        "Plan Rows": 50,
        "Hash Cond": "(a.id = b.a_id)",
        "Plans": [
            {
                "Node Type": "Seq Scan",
                "Relation Name": "users",
                "Total Cost": 10.0,
                "Plan Rows": 100,
                "Filter": "(age > 18)",
            },
            {
                "Node Type": "Index Scan",
                "Relation Name": "orders",
                "Index Name": "idx_orders_a_id",
                "Total Cost": 20.5,
                "Plan Rows": 60,
                "Index Cond": "(a_id > 0)",
            },
        ],
    },
    "Planning Time": 0.2,
    "Execution Time": 5.8,
}

MYSQL_PLAN = {
    "query_block": {
        "select_id": 1,
        "cost_info": {"query_cost": "2.35"},
        "nested_loop": [
            {
                "table": {
                    "table_name": "users",
                    "access_type": "ALL",
                    "rows_examined_per_scan": 1000,
                    "cost_info": {"read_cost": "1.00", "eval_cost": "0.20"},
                    "attached_condition": "(users.age > 18)",
                }
            },
            {
                "table": {
                    "table_name": "orders",
                    "access_type": "ref",
                    "key": "idx_a_id",
                    "rows_examined_per_scan": 60,
                    "cost_info": {"read_cost": "1.10"},
                    "index_condition": "(orders.a_id > 0)",
                }
            },
        ],
    }
}

MYSQL_SINGLE = {
    "query_block": {
        "select_id": 1,
        "cost_info": {"query_cost": "0.35"},
        "table": {"table_name": "t1", "access_type": "ALL", "rows_examined_per_scan": 10},
    }
}

MYSQL_SUBQUERY = {
    "query_block": {
        "select_id": 1,
        "table": {
            "table_name": "<derived2>",
            "access_type": "ALL",
            "rows_examined_per_scan": 5,
            "query_block": {"select_id": 2, "table": {"table_name": "t2", "access_type": "ALL", "rows_examined_per_scan": 100}},
        },
    }
}

TEXT_PLAN = {"format": "text", "analyze": False, "plan": [["1", "SIMPLE", "users", "ALL", None, "100"]]}


def _pg(**overrides) -> dict:
    plan = {"format": "json", "analyze": False, "plan": PG_PLAN}
    plan.update(overrides)
    return plan


def _mysql(**overrides) -> dict:
    plan = {"format": "json", "analyze": False, "plan": MYSQL_PLAN}
    plan.update(overrides)
    return plan


# ---------- tree：统一结构 ----------

def test_pg_tree_unified_structure():
    tree = to_tree(_pg(analyze=True))
    assert len(tree) == 1
    root = tree[0]
    assert root["label"] == "Hash Join"
    assert root["cost"] == 100.5
    assert root["rows"] == 50
    assert root["condition"] == "(a.id = b.a_id)"
    assert len(root["children"]) == 2
    leaf = root["children"][0]
    assert leaf["table"] == "users"
    assert leaf["node_type"] == "Seq Scan"
    assert leaf["condition"] == "(age > 18)"
    assert leaf["actual_rows"] is None


def test_mysql_nested_loop_tree():
    tree = to_tree(_mysql())
    assert len(tree) == 1
    root = tree[0]
    assert root["label"] == "QUERY_BLOCK #1"
    assert root["node_type"] == "QUERY_BLOCK"
    assert root["cost"] == 2.35
    children = root["children"]
    assert [c["table"] for c in children] == ["users", "orders"]
    assert children[0]["node_type"] == "ALL"
    assert children[0]["rows"] == 1000
    assert children[1]["condition"] == "(orders.a_id > 0)"


def test_mysql_single_table_tree():
    tree = to_tree({"format": "json", "analyze": False, "plan": MYSQL_SINGLE})
    root = tree[0]
    assert root["children"][0]["table"] == "t1"
    assert root["children"][0]["node_type"] == "ALL"


def test_mysql_subquery_nested():
    tree = to_tree({"format": "json", "analyze": False, "plan": MYSQL_SUBQUERY})
    derived = tree[0]["children"][0]
    assert derived["table"] == "<derived2>"
    assert derived["children"][0]["label"] == "QUERY_BLOCK #2"
    assert derived["children"][0]["children"][0]["table"] == "t2"


def test_text_format_returns_empty_tree():
    assert to_tree(TEXT_PLAN) == []


def test_unrecognized_plan_returns_empty_tree():
    assert to_tree({"format": "json", "analyze": False, "plan": {"weird": True}}) == []
    assert to_tree({"format": "json", "analyze": False, "plan": None}) == []
    assert to_tree({"format": "json", "analyze": False, "plan": "not-a-dict"}) == []


# ---------- summarize ----------

def test_pg_summary():
    summary = summarize(_pg())
    assert summary["format"] == "json"
    assert summary["tables"] == ["orders", "users"]
    assert "Hash Join" in summary["node_types"]
    assert summary["total_cost"] == 100.5
    assert summary["note"] is None


def test_mysql_summary():
    summary = summarize(_mysql())
    assert summary["tables"] == ["orders", "users"]
    assert summary["total_cost"] == 2.35


def test_text_summary_has_note():
    summary = summarize(TEXT_PLAN)
    assert summary["format"] == "text"
    assert summary["row_count"] == 1
    assert summary["note"]


# ---------- markdown ----------

def test_pg_markdown_contains_table_and_cost():
    md = to_markdown(_pg(analyze=True))
    assert "## 执行计划摘要" in md
    assert "orders" in md and "users" in md
    assert "100.5" in md
    assert "是否分析: 是" in md


def test_mysql_markdown_contains_scan_type():
    md = to_markdown(_mysql())
    assert "ALL" in md and "ref" in md


def test_text_markdown_fallback():
    md = to_markdown(TEXT_PLAN)
    assert "无法生成树/摘要" in md


# ---------- format_plan 入口 ----------

def test_raw_passthrough_backward_compatible():
    out = format_plan(_pg(), "raw")
    assert out["format"] == "raw"
    assert out["plan"] == _pg()


def test_tree_output_shape():
    out = format_plan(_pg(), "tree")
    assert out["format"] == "tree"
    assert out["tree"][0]["label"] == "Hash Join"
    assert out["summary"]["tables"] == ["orders", "users"]


def test_markdown_output_shape():
    out = format_plan(_pg(), "markdown")
    assert out["format"] == "markdown"
    assert out["markdown"].startswith("## 执行计划摘要")
    assert out["summary"]["total_cost"] == 100.5


def test_text_format_fallback_with_warning():
    out = format_plan(TEXT_PLAN, "tree")
    assert out["tree"] == []
    assert "warning" in out
    assert out["plan"] == TEXT_PLAN


def test_unparseable_markdown_has_note():
    bad = {"format": "json", "analyze": False, "plan": {"unexpected": 1}}
    out = format_plan(bad, "markdown")
    assert "无法解析执行计划结构" in out["markdown"]
    assert out["plan"] == bad


def test_invalid_format_rejected():
    with pytest.raises(InvalidParamsError, match="格式"):
        format_plan(_pg(), "xml")
