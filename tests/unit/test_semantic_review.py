"""语义审核与导入（T-1.2/T-1.3）：review --list/--approve/--reject、import、备份、pending 不注入。"""

from __future__ import annotations

from typer.testing import CliRunner

from db_assistant_mcp.cli.main import app
from db_assistant_mcp.semantic import Glossary
from db_assistant_mcp.semantic_gen import read_terms

runner = CliRunner()

CANDIDATE = """[[terms]]
table = "orders"
column = "user_order_dt"
meaning = "用户下单时间"
status = "pending_review"
confidence = 0.95

[[terms]]
column = "phone"
meaning = "手机号"
status = "pending_review"
confidence = 0.7
"""


def _setup(tmp_path, glossary_body: str = "") -> tuple[object, object, object]:
    glossary = tmp_path / "glossary.toml"
    candidate = tmp_path / "glossary.candidate.toml"
    rejected = tmp_path / "glossary.rejected.toml"
    glossary.write_text(glossary_body, encoding="utf-8")
    candidate.write_text(CANDIDATE, encoding="utf-8")
    return glossary, candidate, rejected


def _env(cfg_path, **extra) -> dict:
    env = {"DB_ASSISTANT_CONFIG": str(cfg_path)}
    env.update(extra)
    return env


def _write_config(tmp_path) -> object:
    cfg = tmp_path / "config.toml"
    cfg.write_text(
        f'[semantic]\nglossary_file = "{tmp_path}/glossary.toml"\n',
        encoding="utf-8",
    )
    return cfg


def test_review_list_shows_pending(tmp_path):
    cfg = _write_config(tmp_path)
    _setup(tmp_path)
    result = runner.invoke(app, ["semantic", "review", "--list"], env=_env(cfg))
    assert result.exit_code == 0, result.output
    assert "1" in result.output and "user_order_dt" in result.output
    assert "2" in result.output and "phone" in result.output


def test_review_list_empty(tmp_path):
    cfg = _write_config(tmp_path)
    _setup(tmp_path)
    (tmp_path / "glossary.candidate.toml").write_text("", encoding="utf-8")
    result = runner.invoke(app, ["semantic", "review", "--list"], env=_env(cfg))
    assert result.exit_code == 0
    assert "没有待审核" in result.output


def test_review_approve_moves_to_glossary(tmp_path):
    cfg = _write_config(tmp_path)
    glossary, candidate, _ = _setup(tmp_path)
    result = runner.invoke(app, ["semantic", "review", "--approve", "--id", "1"], env=_env(cfg))
    assert result.exit_code == 0, result.output
    assert "已批准" in result.output
    terms = read_terms(glossary)
    assert len(terms) == 1
    assert terms[0]["column"] == "user_order_dt"
    assert terms[0]["status"] == "approved"
    remaining = read_terms(candidate)
    assert [t["column"] for t in remaining] == ["phone"]
    assert (tmp_path / "glossary.toml.bak").exists()  # 写前备份
    assert (tmp_path / "glossary.candidate.toml.bak").exists()


def test_review_approve_with_meaning_override(tmp_path):
    cfg = _write_config(tmp_path)
    glossary, _, _ = _setup(tmp_path)
    result = runner.invoke(
        app, ["semantic", "review", "--approve", "--id", "1", "--meaning", "用户确认下单时间"],
        env=_env(cfg),
    )
    assert result.exit_code == 0
    assert read_terms(glossary)[0]["meaning"] == "用户确认下单时间"


def test_review_approve_id_out_of_range(tmp_path):
    cfg = _write_config(tmp_path)
    _setup(tmp_path)
    result = runner.invoke(app, ["semantic", "review", "--approve", "--id", "99"], env=_env(cfg))
    assert result.exit_code == 1
    assert "id 不存在" in result.output


def test_review_approve_already_processed(tmp_path):
    cfg = _write_config(tmp_path)
    _setup(tmp_path)
    candidate = tmp_path / "glossary.candidate.toml"
    candidate.write_text(
        '[[terms]]\ncolumn = "x"\nmeaning = "y"\nstatus = "approved"\n', encoding="utf-8"
    )
    result = runner.invoke(app, ["semantic", "review", "--approve", "--id", "1"], env=_env(cfg))
    assert result.exit_code == 1
    assert "已处理" in result.output


def test_review_reject_records_reason(tmp_path):
    cfg = _write_config(tmp_path)
    _, candidate, rejected = _setup(tmp_path)
    result = runner.invoke(
        app, ["semantic", "review", "--reject", "--id", "1", "--reason", "实为最后活跃时间"],
        env=_env(cfg),
    )
    assert result.exit_code == 0, result.output
    assert "已拒绝" in result.output
    text = rejected.read_text(encoding="utf-8")
    assert "user_order_dt" in text and "最后活跃时间" in text
    assert [t["column"] for t in read_terms(candidate)] == ["phone"]


def test_review_reject_backs_up_existing_file(tmp_path):
    cfg = _write_config(tmp_path)
    _, candidate, rejected = _setup(tmp_path)
    rejected.write_text('[[rejections]]\ncolumn = "old"\nreason = "旧记录"\n', encoding="utf-8")
    result = runner.invoke(
        app, ["semantic", "review", "--reject", "--id", "1", "--reason", "实为最后活跃时间"],
        env=_env(cfg),
    )
    assert result.exit_code == 0
    assert (tmp_path / "glossary.rejected.toml.bak").exists()
    assert "old" in rejected.read_text(encoding="utf-8")  # 备份后仍保留旧记录


def test_review_reject_requires_reason(tmp_path):
    cfg = _write_config(tmp_path)
    _setup(tmp_path)
    result = runner.invoke(app, ["semantic", "review", "--reject", "--id", "1"], env=_env(cfg))
    assert result.exit_code == 1
    assert "--reason" in result.output


def test_review_requires_single_action(tmp_path):
    cfg = _write_config(tmp_path)
    _setup(tmp_path)
    result = runner.invoke(app, ["semantic", "review"], env=_env(cfg))
    assert result.exit_code == 2
    assert "一个动作" in result.output


def test_import_dedupes_and_force_overwrites(tmp_path):
    cfg = _write_config(tmp_path)
    glossary, _, _ = _setup(
        tmp_path,
        glossary_body='[[terms]]\ntable = "orders"\ncolumn = "user_order_dt"\nmeaning = "旧含义"\nstatus = "approved"\n',
    )
    src = tmp_path / "bulk.toml"
    src.write_text(
        '[[terms]]\ntable = "orders"\ncolumn = "user_order_dt"\nmeaning = "新含义"\nstatus = "approved"\n\n'
        '[[terms]]\ncolumn = "email"\nmeaning = "邮箱"\nstatus = "approved"\n',
        encoding="utf-8",
    )
    # 默认跳过已存在
    result = runner.invoke(app, ["semantic", "import", "--file", str(src)], env=_env(cfg))
    assert result.exit_code == 0, result.output
    assert "已导入 1 条" in result.output and "跳过已存在 1 条" in result.output
    terms = read_terms(glossary)
    assert {t["column"] for t in terms} == {"user_order_dt", "email"}
    assert next(t for t in terms if t["column"] == "user_order_dt")["meaning"] == "旧含义"

    # --force 覆盖
    result = runner.invoke(app, ["semantic", "import", "--file", str(src), "--force"], env=_env(cfg))
    assert result.exit_code == 0
    terms = read_terms(glossary)
    assert next(t for t in terms if t["column"] == "user_order_dt")["meaning"] == "新含义"
    assert (tmp_path / "glossary.toml.bak").exists()


def test_import_missing_file(tmp_path):
    cfg = _write_config(tmp_path)
    _setup(tmp_path)
    result = runner.invoke(app, ["semantic", "import", "--file", str(tmp_path / "nope.toml")], env=_env(cfg))
    assert result.exit_code == 1
    assert "文件不存在" in result.output


def test_write_terms_doc_preserves_extra_sections(tmp_path):
    from db_assistant_mcp.semantic_gen import write_terms_doc

    out = tmp_path / "g.toml"
    write_terms_doc(
        out,
        {
            "meta": {"version": 2, "owner": "team"},
            "terms": [{"column": "a", "meaning": "A", "status": "approved"}],
        },
    )
    text = out.read_text(encoding="utf-8")
    assert "[meta]" in text and "version = 2" in text
    assert "[[terms]]" in text


def test_pending_review_not_injected_into_schema(tmp_path):
    """T-1.3：Glossary 只向 schema 注入 approved/reviewed，pending_review 不泄漏。"""
    glossary = tmp_path / "glossary.toml"
    glossary.write_text(CANDIDATE, encoding="utf-8")
    g = Glossary.load(str(glossary))
    cols = g.enrich_columns("orders", [{"name": "user_order_dt"}, {"name": "phone"}])
    assert all("meaning" not in c for c in cols)  # 全部是 pending_review，不注入
