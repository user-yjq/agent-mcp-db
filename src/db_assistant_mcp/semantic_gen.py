"""语义候选生成：离线启发式（防噪音）+ 可选 LLM，输出 glossary 兼容的候选文件。

生成策略（plan_v0.2 T-1.1）：
- LLM provider 为主要语义推断路径（配置 llm_api_key_env 时启用），confidence < 0.7 默认不输出
- 离线模式仅输出有依据的候选：强类型/后缀规则命中、常见缩写词典全 token 命中；
  纯下划线/驼峰拆词（含未知 token）不产生候选
- 拒绝词典：review --reject 沉淀的模式/列在生成时跳过
"""

from __future__ import annotations

import json
import re
import shutil
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from db_assistant_mcp.logging_utils import get_logger, log_context
from db_assistant_mcp.semantic import Glossary

DEFAULT_CONFIDENCE_THRESHOLD = 0.7

logger = get_logger("db_assistant_mcp.semantic_gen")

# 强规则：(正则, 含义, confidence) —— 命中即产出候选
STRONG_RULES: list[tuple[re.Pattern[str], str, float]] = [
    (re.compile(r"^is_.+$", re.I), "布尔标志（是否为...）", 0.9),
    (re.compile(r"^has_.+$", re.I), "布尔标志（是否包含...）", 0.9),
    (re.compile(r".+_at$", re.I), "时间戳（记录时间）", 0.85),
    (re.compile(r".+_dt$", re.I), "日期时间字段", 0.85),
    (re.compile(r".+_date$", re.I), "日期字段", 0.85),
    (re.compile(r".+_time$", re.I), "时间字段", 0.85),
    (re.compile(r".+_cnt$|.+_count$", re.I), "数量（计数）", 0.85),
    (re.compile(r".+_amt$|.+_amount$", re.I), "金额", 0.85),
    (re.compile(r".+_id$", re.I), "标识符（ID）", 0.8),
    (re.compile(r".+_no$|.+_num$|.+_number$", re.I), "编号", 0.8),
    # 弱规则：有依据但置信度低于默认阈值，需 --include-low 才输出
    (re.compile(r".+_flag$", re.I), "标志（布尔）", 0.6),
    (re.compile(r".+_key$", re.I), "键值", 0.6),
]

# 常见缩写词典：token → 业务语义（全 token 命中才组合成候选）
ABBREVIATIONS: dict[str, str] = {
    "user": "用户", "usr": "用户",
    "order": "订单", "ord": "订单",
    "dt": "时间", "tm": "时间", "time": "时间",
    "amt": "金额", "amount": "金额",
    "qty": "数量", "cnt": "数量", "count": "数量",
    "addr": "地址", "address": "地址",
    "tel": "电话", "phone": "电话", "mobile": "手机号",
    "email": "邮箱", "mail": "邮箱",
    "name": "名称", "nm": "名称",
    "desc": "描述", "description": "描述",
    "status": "状态", "state": "状态",
    "type": "类型", "kind": "类型",
    "no": "编号", "num": "编号", "number": "编号",
    "code": "编码", "cd": "编码",
    "created": "创建", "updated": "更新", "modified": "修改",
    "deleted": "删除", "flag": "标志",
    "price": "价格", "cost": "成本",
    "total": "总额", "sum": "合计",
    "unit": "单位", "currency": "币种",
}

_WORD_SPLIT = re.compile(r"[^A-Za-z0-9]+")


@dataclass
class Candidate:
    table: str | None
    column: str
    meaning: str
    confidence: float
    source: str  # "rule" | "dict" | "llm"

    def to_term(self) -> dict[str, Any]:
        term: dict[str, Any] = {
            "column": self.column,
            "meaning": self.meaning,
            "status": "pending_review",
            "confidence": round(self.confidence, 3),
        }
        if self.table:
            term["table"] = self.table
        return term


@dataclass
class Rejection:
    column: str | None = None
    pattern: str | None = None
    reason: str | None = None

    def matches(self, column: str) -> bool:
        if self.column and self.column.lower() == column.lower():
            return True
        if self.pattern:
            try:
                if re.search(self.pattern, column, re.IGNORECASE):
                    return True
            except re.error:
                return False
        return False


@dataclass
class RejectionDict:
    items: list[Rejection] = field(default_factory=list)

    @classmethod
    def load(cls, path: str | None) -> RejectionDict:
        rd = cls()
        if not path:
            return rd
        p = Path(path).expanduser()
        if not p.exists():
            return rd
        try:
            raw = tomllib.loads(p.read_text(encoding="utf-8-sig"))
        except (tomllib.TOMLDecodeError, OSError) as exc:
            log_context(logger, 40, "拒绝词典解析失败", path=str(p), error=str(exc))
            return rd
        for item in raw.get("rejections", []):
            if isinstance(item, dict):
                rd.items.append(
                    Rejection(column=item.get("column"), pattern=item.get("pattern"), reason=item.get("reason"))
                )
        return rd

    def is_rejected(self, column: str) -> bool:
        return any(r.matches(column) for r in self.items)


def _tokenize(name: str) -> list[str]:
    """下划线/驼峰/混合命名拆分为小写 token。"""
    parts = [p for p in _WORD_SPLIT.split(name) if p]
    tokens: list[str] = []
    for part in parts:
        if part.islower() or part.isdigit():
            tokens.append(part.lower())
            continue
        # 驼峰拆词
        start = 0
        for i, ch in enumerate(part):
            if i > 0 and ch.isupper():
                tokens.append(part[start:i].lower())
                start = i
        tokens.append(part[start:].lower())
    return tokens


def _camel_candidate(table: str | None, column: str) -> Candidate | None:
    """离线候选：强规则命中，或缩写词典全 token 命中；否则不产出（防噪音）。"""
    for pattern, meaning, confidence in STRONG_RULES:
        if pattern.match(column):
            return Candidate(table=table, column=column, meaning=meaning, confidence=confidence, source="rule")
    tokens = _tokenize(column)
    if not tokens:
        return None
    meanings = [ABBREVIATIONS.get(t) for t in tokens]
    if any(m is None for m in meanings):
        return None  # 含未知 token：纯拆词不产生候选
    joined = "".join(m for m in meanings if m)
    if not joined:
        return None
    return Candidate(
        table=table,
        column=column,
        meaning=joined,
        confidence=0.7,
        source="dict",
    )


class LLMProvider:
    """OpenAI 兼容 chat completions 批量生成语义；失败降级为不产出该批候选。"""

    def __init__(self, api_key: str, base_url: str | None = None, model: str | None = None) -> None:
        self._api_key = api_key
        self._base_url = (base_url or "https://api.openai.com/v1").rstrip("/")
        self._model = model or "gpt-4o-mini"

    async def generate(self, columns: list[tuple[str | None, str]]) -> list[Candidate]:
        import aiohttp

        if not columns:
            return []
        payload: dict[str, Any] = {
            "model": self._model,
            "temperature": 0,
            "response_format": {"type": "json_object"},
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "你是数据库语义分析专家。为给定的列名生成业务含义（中文，简洁），"
                        "并给出置信度 0-1。只输出 JSON，格式: "
                        '{"columns": [{"column": "列名", "meaning": "含义", "confidence": 0.9}]}'
                    ),
                },
                {
                    "role": "user",
                    "content": "列名（可选表名前缀）: " + ", ".join(
                        f"{t}.{c}" if t else c for t, c in columns
                    ),
                },
            ],
        }
        headers = {"Authorization": f"Bearer {self._api_key}", "Content-Type": "application/json"}
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{self._base_url}/chat/completions", json=payload, headers=headers,
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as resp:
                    resp.raise_for_status()
                    body = await resp.json()
        except Exception as exc:  # noqa: BLE001
            log_context(logger, 40, "LLM 语义生成失败", error=str(exc)[:300])
            return []
        try:
            content = body["choices"][0]["message"]["content"]
            data = json.loads(content)
            items = data.get("columns", [])
        except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
            log_context(logger, 40, "LLM 语义响应解析失败", error=str(exc)[:300])
            return []
        by_column = {c: t for t, c in columns}
        result: list[Candidate] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            col = item.get("column")
            meaning = item.get("meaning")
            if not col or not meaning:
                continue
            try:
                confidence = float(item.get("confidence", 0.5))
            except (TypeError, ValueError):
                confidence = 0.5
            result.append(
                Candidate(table=by_column.get(col), column=col, meaning=str(meaning),
                          confidence=min(1.0, max(0.0, confidence)), source="llm")
            )
        return result


class SemanticCandidateGenerator:
    """对 schema 快照生成候选词条：跳过已覆盖列、被拒列；LLM 未配置时仅离线。"""

    def __init__(
        self,
        glossary: Glossary,
        rejections: RejectionDict | None = None,
        llm: LLMProvider | None = None,
        threshold: float = DEFAULT_CONFIDENCE_THRESHOLD,
    ) -> None:
        self._glossary = glossary
        self._rejections = rejections or RejectionDict()
        self._llm = llm
        self._threshold = threshold

    @property
    def llm_available(self) -> bool:
        return self._llm is not None

    def _is_covered(self, table: str | None, column: str) -> bool:
        term = self._glossary.lookup(table, column)
        return bool(term and term.meaning and term.status in ("approved", "reviewed"))

    async def generate(self, tables: list[dict[str, Any]], *, include_low: bool = False) -> list[Candidate]:
        offline: list[Candidate] = []
        pending_llm: list[tuple[str | None, str]] = []
        for table in tables:
            tname = table.get("name")
            for col in table.get("columns", []):
                cname = str(col.get("name", ""))
                if not cname:
                    continue
                if self._is_covered(tname, cname):
                    continue
                if self._rejections.is_rejected(cname):
                    continue
                cand = _camel_candidate(tname, cname)
                if cand:
                    offline.append(cand)
                else:
                    pending_llm.append((tname, cname))

        candidates = offline
        if self._llm and pending_llm:
            candidates += await self._llm.generate(pending_llm)

        keep = include_low or self._threshold <= 0
        if not keep:
            candidates = [c for c in candidates if c.confidence >= self._threshold]
        # 表内稳定排序：先规则/词典，后 LLM；同列只保留一条（优先离线确定性结果）
        seen: set[tuple[str | None, str]] = set()
        unique: list[Candidate] = []
        for cand in sorted(candidates, key=lambda c: (c.source == "llm", c.table or "", c.column, -c.confidence)):
            key = (cand.table, cand.column)
            if key in seen:
                continue
            seen.add(key)
            unique.append(cand)
        return unique


def default_candidate_path(glossary_file: str | None) -> Path:
    """默认候选文件：与 glossary 同目录的 glossary.candidate.toml。"""
    if glossary_file:
        p = Path(glossary_file).expanduser()
        return p.parent / f"{p.stem}.candidate.toml"
    return Path("glossary.candidate.toml")


def load_candidates(path: Path) -> list[Candidate]:
    """读取已有候选文件（合并用）。"""
    if not path.exists():
        return []
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8-sig"))
    except (tomllib.TOMLDecodeError, OSError):
        return []
    result: list[Candidate] = []
    for item in raw.get("terms", []):
        if not isinstance(item, dict) or not item.get("column") or not item.get("meaning"):
            continue
        try:
            confidence = float(item.get("confidence", 0.5))
        except (TypeError, ValueError):
            confidence = 0.5
        result.append(
            Candidate(
                table=item.get("table"),
                column=str(item["column"]),
                meaning=str(item["meaning"]),
                confidence=min(1.0, max(0.0, confidence)),
                source="file",
            )
        )
    return result


def _toml_escape(value: str) -> str:
    """TOML basic string 转义：防止换行/引号/控制字符注入破坏候选文件结构。

    meaning 可能来自 LLM 输出（存在 prompt 注入风险），必须按 TOML 规范转义。
    """
    out: list[str] = []
    for ch in value:
        if ch == "\\":
            out.append("\\\\")
        elif ch == '"':
            out.append('\\"')
        elif ch == "\n":
            out.append("\\n")
        elif ch == "\r":
            out.append("\\r")
        elif ch == "\t":
            out.append("\\t")
        elif ord(ch) < 0x20 or ord(ch) == 0x7F:
            out.append(f"\\u{ord(ch):04x}")
        else:
            out.append(ch)
    return "".join(out)


def write_candidates(path: Path, candidates: list[Candidate]) -> int:
    """写候选文件（与 glossary.toml 格式兼容，含 status=pending_review）；返回写入条数。"""
    existing = load_candidates(path)
    merged: dict[tuple[str | None, str], Candidate] = {
        (c.table, c.column): c for c in existing
    }
    for cand in candidates:
        merged[(cand.table, cand.column)] = cand
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["# 语义候选词条（AI/规则生成，需人工 review 后进入 glossary.toml）", ""]
    for cand in merged.values():
        term = cand.to_term()
        if term.get("table"):
            lines.append(f"[[terms]]\ntable = \"{_toml_escape(str(term['table']))}\"")
        else:
            lines.append("[[terms]]")
        lines.append(f"column = \"{_toml_escape(str(term['column']))}\"")
        lines.append(f"meaning = \"{_toml_escape(str(term['meaning']))}\"")
        lines.append("status = \"pending_review\"")
        lines.append(f"confidence = {round(term['confidence'], 3)}")
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")
    return len(merged)


def backup_file(path: Path) -> None:
    """写操作前备份：复制为 <path>.bak。"""
    if path.exists():
        try:
            shutil.copy2(path, Path(str(path) + ".bak"))
        except OSError:
            pass


def read_terms(path: Path) -> list[dict[str, Any]]:
    """读取 [[terms]] 列表（glossary 与候选文件通用）。"""
    if not path.exists():
        return []
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8-sig"))
    except (tomllib.TOMLDecodeError, OSError):
        return []
    return [t for t in raw.get("terms", []) if isinstance(t, dict)]


def _dump_scalar(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, list):
        return "[" + ", ".join(_dump_scalar(i) for i in value) + "]"
    return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"') + '"'


def write_terms_doc(path: Path, doc: dict[str, Any]) -> None:
    """通用 TOML 文档写回：[[terms]] / [[rejections]] 表数组、[section] 段、标量均支持。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    for key, value in doc.items():
        if isinstance(value, dict):
            lines.append(f"[{key}]")
            for k, v in value.items():
                if isinstance(v, dict):
                    lines.append(f"[{key}.{k}]")
                    for k2, v2 in v.items():
                        lines.append(f"{k2} = {_dump_scalar(v2)}")
                else:
                    lines.append(f"{k} = {_dump_scalar(v)}")
            lines.append("")
        elif isinstance(value, list) and value and all(isinstance(i, dict) for i in value):
            for item in value:
                lines.append(f"[[{key}]]")
                for k, v in item.items():
                    if v is None:
                        continue
                    lines.append(f"{k} = {_dump_scalar(v)}")
                lines.append("")
        elif isinstance(value, list):
            lines.append(f"{key} = {_dump_scalar(value)}")
        else:
            lines.append(f"{key} = {_dump_scalar(value)}")
    path.write_text("\n".join(lines), encoding="utf-8")
