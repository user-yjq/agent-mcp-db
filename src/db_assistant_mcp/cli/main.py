"""CLI：add / list / test / remove / logs / refresh / version。"""

from __future__ import annotations

import asyncio
import getpass
import json
import sys
import time
from pathlib import Path
from typing import Annotated, Any

import typer

from db_assistant_mcp import __version__
from db_assistant_mcp.cli.diagnostics import check_config, run_doctor
from db_assistant_mcp.config import (
    PLANNED_MODES,
    VALID_MODES,
    ConnectionConfig,
    _resolve_config_path,
    load_config,
    parse_semantic,
    remove_connection,
    save_connection,
)
from db_assistant_mcp.errors import AppError
from db_assistant_mcp.runtime import RuntimeRegistry
from db_assistant_mcp.security.audit import AuditLogger
from db_assistant_mcp.semantic import Glossary
from db_assistant_mcp.semantic_gen import (
    LLMProvider,
    RejectionDict,
    SemanticCandidateGenerator,
    backup_file,
    default_candidate_path,
    read_terms,
    write_candidates,
    write_terms_doc,
)


def _version_option(value: bool) -> None:
    if value:
        typer.echo(f"db-assistant {__version__}")
        raise typer.Exit()


app = typer.Typer(
    name="db-assistant",
    help="MCP 数据库助手连接管理 CLI",
    no_args_is_help=True,
)


@app.callback()
def _cli_callback(
    version: Annotated[
        bool,
        typer.Option("--version", "-V", help="输出版本号", callback=_version_option, is_eager=True),
    ] = False,
) -> None:
    """db-assistant 管理 CLI 入口。"""
semantic_app = typer.Typer(help="语义词典管理（生成 / 审核 / 导入）")
app.add_typer(semantic_app, name="semantic")
config_app = typer.Typer(help="配置校验与诊断")
app.add_typer(config_app, name="config")


def _reconfigure_stdio() -> None:
    """管道/重定向时按 UTF-8 输出；交互终端保持控制台代码页编码。"""
    for stream in (sys.stdout, sys.stderr):
        if stream.isatty():
            continue
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
        except (AttributeError, OSError):
            pass


def _config_path(config: str | None) -> Path:
    return _resolve_config_path(config)


def _print_table(headers: list[str], rows: list[list[object]]) -> None:
    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(str(cell)))
    print("  ".join(h.ljust(widths[i]) for i, h in enumerate(headers)))
    print("  ".join("-" * w for w in widths))
    for row in rows:
        print("  ".join(str(c).ljust(widths[i]) for i, c in enumerate(row)))


def _print_checks(checks) -> None:
    labels = {"ok": "[ok]  ", "warn": "[warn]", "error": "[err] "}
    has_error = False
    for check in checks:
        suffix = f"  → {check.fix}" if check.fix else ""
        print(f"{labels[check.status]} {check.item}: {check.message}{suffix}")
        if check.status == "error":
            has_error = True
    if has_error:
        raise typer.Exit(1)


@app.command()
def add(
    db_type: Annotated[str, typer.Argument(help="postgres | mysql")],
    name: Annotated[str, typer.Option("--name", "-n", help="连接名")],
    host: Annotated[str, typer.Option("--host", help="数据库主机")],
    port: Annotated[int, typer.Option("--port", "-p", help="端口")] = 0,
    dbname: Annotated[str, typer.Option("--dbname", "-d", help="数据库名")] = "",
    user: Annotated[str, typer.Option("--user", "-u", help="用户名")] = "",
    password_env: Annotated[str | None, typer.Option("--password-env", help="密码环境变量名（推荐）")] = None,
    mode: Annotated[str, typer.Option("--mode", help="read_only（v1 唯一实现值；safe_write/full 规划中）")] = "read_only",
    ssl: Annotated[bool, typer.Option("--ssl", help="启用 TLS")] = False,
    masked_columns: Annotated[str | None, typer.Option("--masked", help="逗号分隔的脱敏列")] = None,
    exclude_columns: Annotated[str | None, typer.Option("--exclude-columns", help="逗号分隔的排除列")] = None,
    exclude_tables: Annotated[str | None, typer.Option("--exclude-tables", help="逗号分隔的排除表")] = None,
    config: Annotated[str | None, typer.Option("--config", help="配置文件路径")] = None,
) -> None:
    """添加数据库连接（密码交互输入，不回显）。"""
    db_type = db_type.lower()
    if db_type not in ("postgres", "mysql"):
        typer.echo(f"错误: type 必须为 postgres 或 mysql，当前: {db_type}", err=True)
        raise typer.Exit(1)
    if not name or not host or not dbname or not user:
        typer.echo("错误: --name/--host/--dbname/--user 均为必填", err=True)
        raise typer.Exit(1)
    if mode not in VALID_MODES:
        if mode in PLANNED_MODES:
            typer.echo(f"错误: mode={mode!r} 是规划中的写模式（v1 仅实现 read_only），请改为 read_only", err=True)
        else:
            typer.echo(f"错误: mode={mode!r} 非法，当前仅支持 {sorted(VALID_MODES)}", err=True)
        raise typer.Exit(1)
    port = port or (5432 if db_type == "postgres" else 3306)

    encrypted = None
    if password_env:
        if not password_env.isidentifier():
            typer.echo(f"错误: 环境变量名非法: {password_env}", err=True)
            raise typer.Exit(1)
    else:
        pw = getpass.getpass("密码（不回显）: ")
        if pw:
            from db_assistant_mcp.secrets_util import encrypt_secret, ensure_master_key_file

            ensure_master_key_file()
            encrypted = encrypt_secret(pw)

    def split_csv(value: str | None) -> list[str]:
        return [v.strip() for v in value.split(",") if v.strip()] if value else []

    conn = ConnectionConfig(
        name=name,
        type=db_type,
        host=host,
        port=port,
        database=dbname,
        user=user,
        password_env=password_env,
        password_encrypted=encrypted,
        mode=mode,
        masked_columns=split_csv(masked_columns),
        exclude_columns=split_csv(exclude_columns),
        exclude_tables=split_csv(exclude_tables),
        ssl=ssl,
    )
    path = _config_path(config)
    save_connection(path, conn)
    typer.echo(f"已添加连接 '{name}' -> {path}")
    if encrypted:
        typer.echo("密码已用 AES-256-GCM 加密保存（密钥见 DB_ASSISTANT_MASTER_KEY 或本地密钥文件）")
    else:
        typer.echo(f"请确保环境变量 {password_env} 已导出")


@app.command("list")
def list_connections(
    config: Annotated[str | None, typer.Option("--config", help="配置文件路径")] = None,
) -> None:
    """列出所有已配置连接。"""
    try:
        cfg = load_config(str(_config_path(config)))
    except AppError as exc:
        typer.echo(f"错误: {exc.message}", err=True)
        raise typer.Exit(1) from None
    rows = [
        [c.name, c.type, f"{c.host}:{c.port}", c.database, c.user, c.mode]
        for c in cfg.connections.values()
    ]
    _print_table(["名称", "类型", "地址", "数据库", "用户", "模式"], rows)


@app.command("test")
def test_connection(
    name: str = typer.Argument(..., help="连接名"),
    config: Annotated[str | None, typer.Option("--config", help="配置文件路径")] = None,
) -> None:
    """实际连接验证。"""
    asyncio.run(_async_test(name, str(_config_path(config))))


async def _async_test(name: str, config_path: str) -> None:
    try:
        cfg = load_config(config_path)
        audit = AuditLogger(cfg.audit)
        registry = RuntimeRegistry(cfg, audit, Glossary.load(cfg.semantic.glossary_file))
        result = await registry.ping(name)
        entry = result.get(name, {})
        if entry.get("ok"):
            typer.echo(f"连接 '{name}' 正常，延迟 {entry.get('latency_ms')}ms")
        else:
            typer.echo(f"连接 '{name}' 失败: {entry.get('error')}", err=True)
            raise typer.Exit(1)
        await registry.close_all()
    except AppError as exc:
        typer.echo(f"错误: {exc.message}", err=True)
        raise typer.Exit(1) from None


@app.command()
def remove(
    name: str = typer.Argument(..., help="连接名"),
    yes: Annotated[bool, typer.Option("--yes", "-y", help="跳过二次确认")] = False,
    config: Annotated[str | None, typer.Option("--config", help="配置文件路径")] = None,
) -> None:
    """移除连接（二次确认）。"""
    path = _config_path(config)
    try:
        cfg = load_config(str(path))
    except AppError as exc:
        typer.echo(f"错误: {exc.message}", err=True)
        raise typer.Exit(1) from None
    if name not in cfg.connections:
        typer.echo(f"错误: 连接 '{name}' 不存在", err=True)
        raise typer.Exit(1)
    if not yes:
        confirm = typer.prompt(f"确认移除连接 '{name}'？(yes/no)")
        if confirm.lower() not in ("yes", "y"):
            typer.echo("已取消")
            return
    if not remove_connection(path, name):
        typer.echo(f"错误: 无法移除连接 '{name}'（配置读取失败）", err=True)
        raise typer.Exit(1)
    typer.echo(f"已移除连接 '{name}'")


@app.command()
def logs(
    tail: Annotated[int, typer.Option("--tail", help="读取最近 N 条")] = 50,
    follow: Annotated[bool, typer.Option("--follow", "-f", help="实时跟随")] = False,
    user: Annotated[str | None, typer.Option("--user", help="按用户过滤")] = None,
    connection: Annotated[str | None, typer.Option("--connection", help="按连接过滤")] = None,
    tool: Annotated[str | None, typer.Option("--tool", help="按工具过滤")] = None,
    slow: Annotated[bool, typer.Option("--slow", help="仅显示慢查询（duration_ms 超过阈值）")] = False,
    threshold: Annotated[int, typer.Option("--threshold", help="慢查询阈值（毫秒，需配合 --slow）")] = 1000,
    export: Annotated[str | None, typer.Option("--export", help="导出到 JSON 文件")] = None,
    config: Annotated[str | None, typer.Option("--config", help="配置文件路径")] = None,
) -> None:
    """查看审计日志。"""
    try:
        cfg = load_config(str(_config_path(config)))
        audit = AuditLogger(cfg.audit)
        if slow and cfg.audit.output != "file":
            typer.echo(
                f"审计输出为 {cfg.audit.output} 模式，无法按 --slow 读取日志文件（请改用 output = \"file\"）",
                err=True,
            )
            raise typer.Exit(1)
        min_duration = float(threshold) if slow else None
        entries = audit.read(tail=tail, user=user, connection=connection, tool=tool, min_duration_ms=min_duration)
    except AppError as exc:
        typer.echo(f"错误: {exc.message}", err=True)
        raise typer.Exit(1) from None
    if export:
        Path(export).write_text(json.dumps(entries, ensure_ascii=False, indent=2), encoding="utf-8")
        typer.echo(f"已导出 {len(entries)} 条到 {export}")
        return
    if follow:
        _follow_logs(cfg.audit.path)
        return
    for e in entries:
        print(json.dumps(e, ensure_ascii=False))
    typer.echo(f"(共 {len(entries)} 条)")


def _follow_logs(path: str | None) -> None:
    if not path:
        typer.echo("仅 file 输出支持 --follow", err=True)
        raise typer.Exit(1)
    p = Path(path).expanduser()
    pos = p.stat().st_size if p.exists() else 0
    try:
        while True:
            if p.exists():
                with p.open("r", encoding="utf-8") as fh:
                    fh.seek(pos)
                    for line in fh:
                        print(line.rstrip())
                    pos = fh.tell()
            time.sleep(0.5)
    except KeyboardInterrupt:
        typer.echo("已停止")


@app.command()
def refresh(
    name: str = typer.Argument(..., help="连接名"),
    config: Annotated[str | None, typer.Option("--config", help="配置文件路径")] = None,
) -> None:
    """主动失效并重建 schema 缓存（同时验证连接可达）。"""
    asyncio.run(_async_refresh(name, str(_config_path(config))))


async def _async_refresh(name: str, config_path: str) -> None:
    try:
        cfg = load_config(config_path)
        audit = AuditLogger(cfg.audit)
        registry = RuntimeRegistry(cfg, audit, Glossary.load(cfg.semantic.glossary_file))
        runtime = registry.get(name)
        runtime.schema.invalidate()
        summary = await runtime.schema.get_summary(force_refresh=True)
        typer.echo(f"已刷新连接 '{name}' 的 schema 缓存，共 {len(summary['tables'])} 张表")
        await registry.close_all()
    except AppError as exc:
        typer.echo(f"错误: {exc.message}", err=True)
        raise typer.Exit(1) from None


@config_app.command("validate")
def config_validate(
    config: Annotated[str | None, typer.Option("--config", help="配置文件路径")] = None,
) -> None:
    """校验配置合法性：文件/权限/TOML/schema/连接/HTTP。"""
    checks = check_config(_config_path(config))
    _print_checks(checks)
    typer.echo("配置校验通过（无 error）")


@app.command("doctor")
def doctor(
    config: Annotated[str | None, typer.Option("--config", help="配置文件路径")] = None,
) -> None:
    """全面体检：配置、依赖版本、glossary、连接连通性、metrics 端口。"""
    checks = asyncio.run(run_doctor(str(_config_path(config))))
    _print_checks(checks)
    typer.echo("doctor 检查完成（无 error）")


@app.command("version")
def version() -> None:
    """输出版本号。"""
    typer.echo(f"db-assistant {__version__}")


@semantic_app.command("generate")
def semantic_generate(
    connection: str = typer.Argument(..., help="连接名"),
    include_low: Annotated[bool, typer.Option("--include-low", help="包含低置信度（<0.7）候选")] = False,
    output: Annotated[str | None, typer.Option("--output", "-o", help="候选文件输出路径（默认 glossary 同级）")] = None,
    config: Annotated[str | None, typer.Option("--config", help="配置文件路径")] = None,
) -> None:
    """扫描连接 schema，为无术语覆盖的列生成候选词条。"""
    asyncio.run(_async_semantic_generate(connection, include_low, output, str(_config_path(config))))


async def _async_semantic_generate(connection: str, include_low: bool, output: str | None, config_path: str) -> None:
    try:
        cfg = load_config(config_path)
        if connection not in cfg.connections:
            typer.echo(f"错误: 连接 '{connection}' 不存在", err=True)
            raise typer.Exit(1)
        audit = AuditLogger(cfg.audit)
        glossary = Glossary.load(cfg.semantic.glossary_file)
        registry = RuntimeRegistry(cfg, audit, glossary)
        runtime = registry.get(connection)
        summary = await runtime.schema.get_summary()
        tables = summary.get("tables", [])
        if not tables:
            typer.echo(f"连接 '{connection}' 无可分析的可见表（或全部被排除）")
            return

        llm = None
        if cfg.semantic.llm_api_key_env:
            import os

            api_key = os.environ.get(cfg.semantic.llm_api_key_env)
            if api_key:
                llm = LLMProvider(
                    api_key=api_key,
                    base_url=cfg.semantic.llm_base_url,
                    model=cfg.semantic.llm_model,
                )
            else:
                typer.echo(f"警告: 环境变量 {cfg.semantic.llm_api_key_env} 未设置，跳过 LLM，仅用离线规则", err=True)
        else:
            typer.echo("提示: 未配置 LLM（semantic.llm_api_key_env），仅输出词典/规则命中的保守候选")

        rejected_path = None
        if cfg.semantic.glossary_file:
            rejected_path = str(Path(cfg.semantic.glossary_file).expanduser().parent / "glossary.rejected.toml")
        rejections = RejectionDict.load(rejected_path)
        generator = SemanticCandidateGenerator(glossary, rejections, llm)
        candidates = await generator.generate(tables, include_low=include_low)
        if not candidates:
            typer.echo("没有生成新的候选词条（列均已覆盖、被拒绝或不符合规则）")
            return

        out_path = Path(output).expanduser() if output else default_candidate_path(cfg.semantic.glossary_file)
        written = write_candidates(out_path, candidates)
        llm_note = "LLM 生成" if llm else "离线规则/词典"
        typer.echo(f"已生成 {written} 条候选（来源: {llm_note}）-> {out_path}")
        typer.echo("使用 `db-assistant semantic review --list` 审核（T-1.2）")
        await registry.close_all()
    except AppError as exc:
        typer.echo(f"错误: {exc.message}", err=True)
        raise typer.Exit(1) from None


def _resolve_semantic_paths(
    config: str | None,
    *,
    glossary_opt: str | None,
    candidate_opt: str | None,
    rejected_opt: str | None,
    need_glossary: bool,
) -> tuple[Path, Path, Path]:
    """解析 glossary / candidate / rejected 路径：显式参数优先，否则取配置/约定。"""
    glossary_path: Path | None = Path(glossary_opt).expanduser() if glossary_opt else None
    semantic_cfg = None
    if glossary_path is None or candidate_opt is None or rejected_opt is None:
        semantic_cfg = _load_semantic_section(config)
    if glossary_path is None:
        if not semantic_cfg or not semantic_cfg.glossary_file:
            if need_glossary:
                typer.echo("错误: 未配置 semantic.glossary_file（或用 --glossary 显式指定）", err=True)
                raise typer.Exit(1)
            glossary_path = Path("glossary.toml")
        else:
            glossary_path = Path(semantic_cfg.glossary_file).expanduser()
    candidate_path = (
        Path(candidate_opt).expanduser()
        if candidate_opt
        else (
            Path(semantic_cfg.candidate_file).expanduser()
            if semantic_cfg and semantic_cfg.candidate_file
            else default_candidate_path(str(glossary_path))
        )
    )
    rejected_path = (
        Path(rejected_opt).expanduser()
        if rejected_opt
        else glossary_path.parent / "glossary.rejected.toml"
    )
    return glossary_path, candidate_path, rejected_path


def _load_semantic_section(config: str | None):
    """宽松读取配置中的 [semantic] 段：glossary 管理命令不需要 [connections]。"""
    import tomllib

    path = _config_path(config)
    if not path.exists():
        return None
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8-sig"))
    except (tomllib.TOMLDecodeError, OSError):
        return None
    return parse_semantic(raw)


@semantic_app.command("review")
def semantic_review(
    list_terms: Annotated[bool, typer.Option("--list", "-l", help="列出待审核候选")] = False,
    approve: Annotated[bool, typer.Option("--approve", "-a", help="批准词条（写入正式 glossary）")] = False,
    reject: Annotated[bool, typer.Option("--reject", "-r", help="拒绝词条（记录到拒绝词典）")] = False,
    term_id: Annotated[int | None, typer.Option("--id", help="词条 id（--list 输出中的序号）")] = None,
    meaning: Annotated[str | None, typer.Option("--meaning", help="批准时覆盖的含义")] = None,
    reason: Annotated[str | None, typer.Option("--reason", help="拒绝原因（拒绝时必填）")] = None,
    limit: Annotated[int, typer.Option("--limit", help="--list 最大显示条数")] = 50,
    glossary: Annotated[str | None, typer.Option("--glossary", help="正式 glossary 路径（默认取配置）")] = None,
    candidate: Annotated[str | None, typer.Option("--candidate", help="候选文件路径（默认取配置/约定）")] = None,
    rejected: Annotated[str | None, typer.Option("--rejected", help="拒绝词典路径（默认 glossary 同级）")] = None,
    config: Annotated[str | None, typer.Option("--config", help="配置文件路径")] = None,
) -> None:
    """审核候选词条：--list 查看，--approve/--reject 处理指定 id。"""
    actions = sum(bool(x) for x in (list_terms, approve, reject))
    if actions != 1:
        typer.echo("请指定且仅指定一个动作: --list / --approve / --reject", err=True)
        raise typer.Exit(2)
    glossary_path, candidate_path, rejected_path = _resolve_semantic_paths(
        config, glossary_opt=glossary, candidate_opt=candidate, rejected_opt=rejected,
        need_glossary=approve,
    )

    if list_terms:
        terms = [t for t in read_terms(candidate_path) if t.get("status", "pending_review") == "pending_review"]
        if not terms:
            typer.echo(f"没有待审核的候选（{candidate_path}）")
            return
        rows = []
        for i, t in enumerate(terms[:limit], start=1):
            table = t.get("table", "")
            rows.append([str(i), f"{table}.{t['column']}" if table else str(t["column"]),
                         str(t.get("meaning", "")), str(t.get("confidence", ""))])
        _print_table(["id", "列", "含义", "confidence"], rows)
        if len(terms) > limit:
            typer.echo(f"(共 {len(terms)} 条，仅显示前 {limit} 条)")
        return

    terms = read_terms(candidate_path)
    if not terms:
        typer.echo(f"没有待审核的候选（{candidate_path}）")
        return
    if term_id is None or term_id < 1 or term_id > len(terms):
        typer.echo(f"错误: --id 不存在（当前共 {len(terms)} 条，范围 1-{len(terms)}）", err=True)
        raise typer.Exit(1)
    term = terms[term_id - 1]
    if term.get("status", "pending_review") != "pending_review":
        typer.echo(f"错误: 词条 {term_id} 已处理（status={term.get('status')}）", err=True)
        raise typer.Exit(1)

    if approve:
        new_meaning = meaning if meaning is not None else term.get("meaning")
        if not new_meaning or not str(new_meaning).strip():
            typer.echo("错误: 词条含义为空，请用 --meaning 指定", err=True)
            raise typer.Exit(1)
        glossary_doc: dict[str, Any] = {"terms": read_terms(glossary_path)}
        approved = {
            "table": term.get("table"),
            "column": term.get("column"),
            "meaning": new_meaning,
            "status": "approved",
        }
        glossary_doc["terms"].append(approved)
        backup_file(glossary_path)
        backup_file(candidate_path)
        write_terms_doc(glossary_path, glossary_doc)
        del terms[term_id - 1]
        if terms:
            write_terms_doc(candidate_path, {"terms": terms})
        else:
            candidate_path.write_text("", encoding="utf-8")
        col = f"{term.get('table')}.{term['column']}" if term.get("table") else str(term["column"])
        typer.echo(f"已批准: {col} -> {glossary_path}")
        return

    if reject:
        if not reason or not reason.strip():
            typer.echo("错误: 拒绝时必须提供 --reason", err=True)
            raise typer.Exit(1)
        rejected_doc: dict[str, Any] = {"rejections": []}
        if rejected_path.exists():
            try:
                import tomllib as _tl

                raw = _tl.loads(rejected_path.read_text(encoding="utf-8-sig"))
                rejected_doc = raw if isinstance(raw, dict) else {}
                rejected_doc.setdefault("rejections", [])
            except (_tl.TOMLDecodeError, OSError):
                pass
        rejected_doc["rejections"].append({"column": str(term["column"]), "reason": reason.strip()})
        backup_file(rejected_path)
        backup_file(candidate_path)
        write_terms_doc(rejected_path, rejected_doc)
        del terms[term_id - 1]
        if terms:
            write_terms_doc(candidate_path, {"terms": terms})
        else:
            candidate_path.write_text("", encoding="utf-8")
        typer.echo(f"已拒绝: {term['column']}（{reason.strip()}）-> {rejected_path}")


@semantic_app.command("import")
def semantic_import(
    file: Annotated[str, typer.Option("--file", "-f", help="要导入的词条文件（候选或 glossary 格式）")],
    force: Annotated[bool, typer.Option("--force", help="覆盖已存在的同列词条")] = False,
    glossary: Annotated[str | None, typer.Option("--glossary", help="正式 glossary 路径（默认取配置）")] = None,
    config: Annotated[str | None, typer.Option("--config", help="配置文件路径")] = None,
) -> None:
    """批量导入词条到正式 glossary（视为审核通过，status=approved）。"""
    glossary_path, _, _ = _resolve_semantic_paths(
        config, glossary_opt=glossary, candidate_opt=None, rejected_opt=None, need_glossary=True
    )
    src = Path(file).expanduser()
    if not src.exists():
        typer.echo(f"错误: 文件不存在: {src}", err=True)
        raise typer.Exit(1)
    imported = read_terms(src)
    if not imported:
        typer.echo("错误: 文件中没有可导入的词条（[[terms]]）", err=True)
        raise typer.Exit(1)

    existing = read_terms(glossary_path)
    keys = {(t.get("table"), t.get("column")) for t in existing}
    added = 0
    skipped = 0
    for term in imported:
        if not term.get("column") or not term.get("meaning"):
            continue
        key = (term.get("table"), term.get("column"))
        if key in keys and not force:
            skipped += 1
            continue
        if key in keys:
            existing = [t for t in existing if (t.get("table"), t.get("column")) != key]
        existing.append({"table": term.get("table"), "column": term["column"], "meaning": term["meaning"],
                         "status": "approved"})
        keys.add(key)
        added += 1
    backup_file(glossary_path)
    write_terms_doc(glossary_path, {"terms": existing})
    typer.echo(f"已导入 {added} 条到 {glossary_path}（跳过已存在 {skipped} 条）")


def main() -> None:
    _reconfigure_stdio()
    app()


if __name__ == "__main__":
    main()
