from __future__ import annotations

from db_assistant_mcp.semantic import Glossary


def test_glossary_priority():
    g = Glossary.load(None)
    g._exact_qualified[("orders", "user_order_dt")] = __import__("db_assistant_mcp.semantic", fromlist=["GlossaryTerm"]).GlossaryTerm(
        table="orders", column="user_order_dt", meaning="精确表列"
    )
    g._exact_column["created_at"] = __import__("db_assistant_mcp.semantic", fromlist=["GlossaryTerm"]).GlossaryTerm(
        column="created_at", meaning="精确列"
    )
    g._patterns.append((__import__("re").compile(".*_status$"), __import__("db_assistant_mcp.semantic", fromlist=["GlossaryTerm"]).GlossaryTerm(
        pattern=".*_status$", meaning="正则"
    )))

    assert g.lookup("orders", "user_order_dt").meaning == "精确表列"
    assert g.lookup("any", "created_at").meaning == "精确列"
    assert g.lookup("any", "order_status").meaning == "正则"
    assert g.lookup("any", "other") is None


def test_glossary_load_from_file(tmp_path):
    p = tmp_path / "glossary.toml"
    p.write_text(
        """
[[terms]]
column = "user_order_dt"
meaning = "下单时间"

[[terms]]
pattern = ".*_status$"
meaning = "状态字段"
status = "pending_review"
""",
        encoding="utf-8",
    )
    g = Glossary.load(str(p))
    assert len(g.terms) == 2
    assert g.lookup(None, "user_order_dt").meaning == "下单时间"

