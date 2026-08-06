"""语义候选生成（T-1.1）：规则/词典/LLM、confidence 门槛、拒绝词典、候选文件。"""

from __future__ import annotations

from pathlib import Path

import pytest

from db_assistant_mcp.semantic import Glossary
from db_assistant_mcp.semantic_gen import (
    LLMProvider,
    Rejection,
    RejectionDict,
    SemanticCandidateGenerator,
    _camel_candidate,
    default_candidate_path,
    load_candidates,
    write_candidates,
)

TABLES = [
    {
        "name": "users",
        "columns": [
            {"name": "id"},
            {"name": "user_order_dt"},
            {"name": "created_at"},
            {"name": "is_active"},
            {"name": "phone"},
            {"name": "weird_xyz_zz"},   # 纯拆词，不应产出
            {"name": "tmp_flag"},        # 弱规则 0.6，默认被阈值过滤
        ],
    },
    {
        "name": "orders",
        "columns": [
            {"name": "order_amt"},
            {"name": "order_no"},
            {"name": "user_order_desc"},
        ],
    },
]


def _generator(glossary: Glossary | None = None, rejections: RejectionDict | None = None, llm=None) -> SemanticCandidateGenerator:
    return SemanticCandidateGenerator(glossary or Glossary(), rejections, llm)


def test_strong_rules_produce_candidates():
    assert _camel_candidate("users", "created_at") is not None
    assert _camel_candidate("users", "created_at").meaning == "时间戳（记录时间）"
    assert _camel_candidate("users", "is_active").meaning == "布尔标志（是否为...）"
    assert _camel_candidate("orders", "order_amt").meaning == "金额"


def test_dictionary_all_tokens_produce_candidate():
    cand = _camel_candidate("orders", "user_order_desc")
    assert cand is not None
    assert cand.confidence == 0.7
    assert "订单" in cand.meaning and "描述" in cand.meaning


def test_pure_split_with_unknown_token_produces_nothing():
    assert _camel_candidate("users", "weird_xyz_zz") is None


def test_camel_case_tokenization():
    cand = _camel_candidate(None, "userOrderDt")
    assert cand is not None
    assert "订单" in cand.meaning


@pytest.mark.asyncio
async def test_confidence_gating_default_and_include_low():
    gen = _generator()
    default = await gen.generate(TABLES)
    columns = {c.column for c in default}
    assert "created_at" in columns and "order_amt" in columns
    assert "tmp_flag" not in columns  # 0.6 < 0.7，默认不输出

    low = await gen.generate(TABLES, include_low=True)
    assert "tmp_flag" in {c.column for c in low}


@pytest.mark.asyncio
async def test_covered_columns_skipped():
    glossary = Glossary.load(None)
    term = type("T", (), {"meaning": "创建时间", "status": "approved"})()
    glossary._exact_column["created_at"] = term  # type: ignore[attr-defined]
    gen = _generator(glossary=glossary)
    columns = {c.column for c in await gen.generate(TABLES)}
    assert "created_at" not in columns


@pytest.mark.asyncio
async def test_rejected_columns_skipped():
    rd = RejectionDict(items=[Rejection(pattern=r".*_tmp$", reason="临时字段")])
    gen = _generator(rejections=rd)
    columns = {c.column for c in await gen.generate(TABLES)}
    assert "tmp_flag" not in columns


@pytest.mark.asyncio
async def test_empty_schema():
    assert await _generator().generate([]) == []


@pytest.mark.asyncio
async def test_unicode_and_long_column_names():
    long_name = "a" * 200
    tables = [{"name": "表", "columns": [{"name": "订单_时间"}, {"name": long_name}]}]
    result = await _generator().generate(tables)
    assert isinstance(result, list)


def test_candidate_file_roundtrip_and_merge(tmp_path):
    out = tmp_path / "glossary.candidate.toml"
    cands = [
        __import__("db_assistant_mcp.semantic_gen", fromlist=["Candidate"]).Candidate(
            table="users", column="user_order_dt", meaning="用户订单时间", confidence=0.7, source="dict"
        ),
    ]
    assert write_candidates(out, cands) == 1
    text = out.read_text(encoding="utf-8")
    assert "pending_review" in text and "confidence = 0.7" in text

    # 合并：重复列去重，新增列追加
    cands2 = [
        __import__("db_assistant_mcp.semantic_gen", fromlist=["Candidate"]).Candidate(
            table="users", column="user_order_dt", meaning="用户订单时间（更新）", confidence=0.8, source="rule"
        ),
        __import__("db_assistant_mcp.semantic_gen", fromlist=["Candidate"]).Candidate(
            table=None, column="phone", meaning="手机号", confidence=0.7, source="dict"
        ),
    ]
    assert write_candidates(out, cands2) == 2
    loaded = load_candidates(out)
    assert {c.column for c in loaded} == {"user_order_dt", "phone"}


def test_default_candidate_path():
    assert default_candidate_path("config/glossary.example.toml") == Path("config/glossary.example.candidate.toml")
    assert default_candidate_path(None) == Path("glossary.candidate.toml")


def test_rejection_dict_match():
    rd = RejectionDict(items=[Rejection(column="dt", reason="误判"), Rejection(pattern=r".*_tmp$")])
    assert rd.is_rejected("DT")
    assert rd.is_rejected("x_tmp")
    assert not rd.is_rejected("user_order_dt")


class _FakeResp:
    def __init__(self, body) -> None:
        self._body = body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    def raise_for_status(self) -> None:
        pass

    async def json(self):
        return self._body


class _FakeSession:
    def __init__(self, body) -> None:
        self._body = body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    def post(self, *args, **kwargs):
        return _FakeResp(self._body)


@pytest.mark.asyncio
async def test_llm_provider_parses_response(monkeypatch):
    import aiohttp

    body = {
        "choices": [{"message": {"content": '{"columns": ['
                                          '{"column": "weird_xyz_zz", "meaning": "未知业务键", "confidence": 0.4},'
                                          '{"column": "user_order_dt", "meaning": "用户下单时间", "confidence": 0.95}]}'}}]
    }
    monkeypatch.setattr(aiohttp, "ClientSession", lambda **kw: _FakeSession(body))
    llm = LLMProvider(api_key="test-key")
    result = await llm.generate([("users", "weird_xyz_zz"), ("users", "user_order_dt")])
    assert len(result) == 2
    by_col = {c.column: c for c in result}
    assert by_col["user_order_dt"].meaning == "用户下单时间"
    assert by_col["weird_xyz_zz"].confidence == 0.4


@pytest.mark.asyncio
async def test_llm_failure_degrades_to_empty(monkeypatch):
    import aiohttp

    class FailingResp(_FakeResp):
        def raise_for_status(self) -> None:
            raise RuntimeError("boom")

    monkeypatch.setattr(aiohttp, "ClientSession", lambda **kw: _FakeSession(FailingResp({})))
    llm = LLMProvider(api_key="test-key")
    assert await llm.generate([("users", "weird_xyz_zz")]) == []


@pytest.mark.asyncio
async def test_generator_offline_when_no_llm():
    gen = _generator()
    assert not gen.llm_available
    result = await gen.generate(TABLES)
    # LLM 缺失时仅离线候选，且纯拆词列不产出
    assert {c.column for c in result} >= {"created_at", "user_order_dt", "order_amt"}
    assert "weird_xyz_zz" not in {c.column for c in result}
