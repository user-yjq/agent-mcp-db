from __future__ import annotations

from db_assistant_mcp.semantic import Glossary, GlossaryTerm


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

def test_search_terms_matches_meaning_and_filters_status():
    g = Glossary()
    g.terms = [
        GlossaryTerm(column="user_order_dt", meaning="下单时间（用户确认订单的时间）"),
        GlossaryTerm(table="orders", column="order_status", meaning="订单状态：pending/paid/shipped/completed/cancelled"),
        GlossaryTerm(pattern=".*_status$", meaning="状态字段，取值见对应枚举表"),
        GlossaryTerm(column="secret_note", meaning="订单备注", status="pending_review"),
    ]
    assert [t.meaning for t in g.search_terms("订单")] == [
        "下单时间（用户确认订单的时间）",
        "订单状态：pending/paid/shipped/completed/cancelled",
    ]
    assert [t.meaning for t in g.search_terms("状态")] == [
        "订单状态：pending/paid/shipped/completed/cancelled",
        "状态字段，取值见对应枚举表",
    ]
    # pending_review 术语不参与语义搜索
    assert g.search_terms("备注") == []
    assert g.search_terms("不存在的词") == []


def test_term_matches_resolves_exact_and_pattern():
    g = Glossary()
    term_col = GlossaryTerm(column="user_order_dt", meaning="下单时间")
    term_qualified = GlossaryTerm(table="orders", column="order_status", meaning="订单状态")
    term_pattern = GlossaryTerm(pattern=".*_?status$", meaning="状态字段")
    assert g.term_matches(term_col, "orders", "user_order_dt")
    assert not g.term_matches(term_col, "orders", "other")
    assert g.term_matches(term_qualified, "orders", "order_status")
    assert not g.term_matches(term_qualified, "users", "order_status")  # 表限定
    assert g.term_matches(term_pattern, "users", "status")
    assert g.term_matches(term_pattern, "products", "stock_status")
    assert not g.term_matches(term_pattern, "orders", "user_order_dt")
    assert not g.term_matches(GlossaryTerm(pattern="[", meaning="坏正则"), "t", "c")
