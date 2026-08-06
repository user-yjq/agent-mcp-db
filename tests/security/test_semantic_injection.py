"""安全回归（T-4.4）：语义候选文件注入防护。

恶意 meaning/table/column（换行、引号、TOML 结构、控制字符）写入候选文件时
必须被 TOML 转义，保证文件结构不被破坏、内容原样保留（meaning 可能来自 LLM，
存在 prompt 注入风险）。
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from db_assistant_mcp.semantic_gen import Candidate, default_candidate_path, write_candidates


def _write(cands: list[Candidate], tmp_path: Path) -> tuple[Path, str]:
    path = tmp_path / "glossary.candidate.toml"
    write_candidates(path, cands)
    return path, path.read_text(encoding="utf-8")


def _parse(content: str) -> list[dict]:
    data = tomllib.loads(content)
    return data.get("terms", [])


def test_meaning_toml_structure_injection_blocked(tmp_path):
    evil = 'x"\n[[terms]]\ncolumn = "injected"\nmeaning = "已注入"\nstatus = "approved"\n# '
    path, content = _write(
        [Candidate(table="users", column="id", meaning=evil, confidence=0.9, source="llm")], tmp_path
    )
    terms = _parse(content)  # 不抛异常即结构未被破坏
    assert len(terms) == 1
    assert terms[0]["column"] == "id"
    assert terms[0]["meaning"] == evil  # 原样保留
    assert terms[0]["status"] == "pending_review"  # 注入的 approved 无效


def test_meaning_quotes_and_backslashes_safe(tmp_path):
    evil = '他说 "hello" \\ 换行\n和 \t tab'
    _path, content = _write(
        [Candidate(table="t", column="c", meaning=evil, confidence=0.5, source="rule")], tmp_path
    )
    terms = _parse(content)
    assert terms[0]["meaning"] == evil


def test_control_characters_safe(tmp_path):
    evil = "a\x00b\x1fc\x7fd"
    _path, content = _write(
        [Candidate(table="t", column="c", meaning=evil, confidence=0.5, source="rule")], tmp_path
    )
    terms = _parse(content)
    assert terms[0]["meaning"] == evil


def test_table_column_injection_safe(tmp_path):
    table = 'users"\n[[terms]]\ncolumn="x"\n# '
    column = 'id"\nmeaning="注入"\n# '
    _path, content = _write(
        [Candidate(table=table, column=column, meaning="ok", confidence=0.9, source="rule")], tmp_path
    )
    terms = _parse(content)
    assert len(terms) == 1
    assert terms[0]["meaning"] == "ok"


def test_default_candidate_path_never_overwrites_glossary():
    """候选文件默认与 glossary 同目录但不同名，避免覆盖已审核词典。"""
    candidate = default_candidate_path("/etc/db-assistant/glossary.toml")
    assert candidate == Path("/etc/db-assistant/glossary.candidate.toml")
    assert candidate.name != "glossary.toml"


def test_relative_candidate_path_parseable(tmp_path):
    """../ 等相对路径是 CLI 显式语义，写入结果必须仍是合法 TOML。"""
    target = tmp_path / "sub"
    target.mkdir()
    path = target / ".." / "candidate.toml"  # 指向 tmp_path/candidate.toml
    write_candidates(
        path, [Candidate(table="t", column="c", meaning="ok", confidence=0.9, source="rule")]
    )
    assert (tmp_path / "candidate.toml").exists()
    assert tomllib.loads(path.read_text(encoding="utf-8"))["terms"][0]["meaning"] == "ok"


@pytest.mark.parametrize(
    "meaning",
    [
        '"]\nmeaning="a"\nstatus="approved"\n[[terms]]\ncolumn="b"\nmeaning="c"\nstatus="approved"\n#',
        "x" * 5000,
        "\u0001\u0002\u001f",
    ],
)
def test_fuzzish_injection_inputs_do_not_break_file(tmp_path, meaning):
    _path, content = _write(
        [Candidate(table="t", column="c", meaning=meaning, confidence=0.5, source="llm")], tmp_path
    )
    terms = _parse(content)
    assert len(terms) == 1
    assert terms[0]["meaning"] == meaning
