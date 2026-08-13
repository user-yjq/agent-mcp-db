from __future__ import annotations

import hashlib
import hmac
import json

from db_assistant_mcp.config import AuditConfig
from db_assistant_mcp.security.audit import AuditLogger


def _audit(tmp_path, **kwargs) -> AuditLogger:
    defaults = dict(output="file", path=str(tmp_path / "audit.log"))
    defaults.update(kwargs)
    return AuditLogger(AuditConfig(**defaults))


def test_file_output_fields(tmp_path):
    audit = _audit(tmp_path)
    audit.record(
        tool="execute_query", connection="main-prod", sql="SELECT * FROM orders",
        rows=42, duration_ms=320, allowed=True, client="cursor", user="alice",
    )
    line = json.loads((tmp_path / "audit.log").read_text(encoding="utf-8").strip())
    assert line["ts"].startswith("20")
    assert line["client"] == "cursor"
    assert line["user"] == "alice"
    assert line["tool"] == "execute_query"
    assert line["connection"] == "main-prod"
    assert line["sql"] == "SELECT * FROM orders"
    assert line["rows"] == 42
    assert line["duration_ms"] == 320.0
    assert line["allowed"] is True


def test_sql_truncated(tmp_path):
    audit = _audit(tmp_path)
    long_sql = "SELECT " + "x," * 5000
    audit.record(tool="execute_query", connection="c", sql=long_sql, rows=0,
                 duration_ms=1, allowed=True)
    line = json.loads((tmp_path / "audit.log").read_text(encoding="utf-8").strip())
    assert line["sql"].endswith("...(truncated)")
    assert len(line["sql"]) < len(long_sql)


def test_stdout_output(tmp_path, capsys):
    audit = _audit(tmp_path, output="stdout")
    audit.record(tool="ping", connection=None, sql=None, rows=0, duration_ms=1, allowed=True)
    captured = capsys.readouterr().out.strip()
    assert json.loads(captured)["tool"] == "ping"


def test_stderr_output(tmp_path, capsys):
    """C-3: output=stderr 写 stderr，不碰 stdout。"""
    audit = _audit(tmp_path, output="stderr")
    audit.record(tool="ping", connection=None, sql=None, rows=0, duration_ms=1, allowed=True)
    captured = capsys.readouterr()
    assert captured.out == ""
    assert json.loads(captured.err.strip())["tool"] == "ping"


def test_webhook_unreachable_does_not_raise(tmp_path):
    audit = _audit(tmp_path, output="webhook", webhook_url="http://127.0.0.1:1/nope")
    audit.record(tool="execute_query", connection="c", sql="SELECT 1", rows=0,
                 duration_ms=1, allowed=True)  # 不应抛异常


def test_webhook_hmac_signature(tmp_path, monkeypatch):
    """webhook 推送以 HMAC-SHA256 签名 body（sha256=<hex>），不再明文透传密钥。"""
    captured: dict[str, object] = {}

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        async def __aenter__(self) -> FakeResponse:
            return self

        async def __aexit__(self, *args) -> bool:
            return False

    class FakeRequest:
        def __init__(self, url, data, headers) -> None:
            self.url = url
            self.data = data
            self.headers = headers

        async def __aenter__(self) -> FakeResponse:
            captured["url"] = self.url
            captured["data"] = self.data
            captured["headers"] = self.headers
            return FakeResponse()

        async def __aexit__(self, *args) -> bool:
            return False

    class FakeSession:
        async def __aenter__(self) -> FakeSession:
            return self

        async def __aexit__(self, *args) -> bool:
            return False

        def post(self, url, data, headers, timeout):
            # aiohttp 的 session.post() 返回 _RequestContextManager（支持 async with），而非裸协程
            return FakeRequest(url, data, headers)

    monkeypatch.setattr("aiohttp.ClientSession", lambda: FakeSession())
    monkeypatch.setenv("DB_ASSISTANT_WEBHOOK_SECRET", "s3cret")
    audit = _audit(
        tmp_path,
        output="webhook",
        webhook_url="http://hook.example/audit",
        webhook_secret_env="DB_ASSISTANT_WEBHOOK_SECRET",
    )
    audit.record(tool="execute_query", connection="c", sql="SELECT 1", rows=0,
                 duration_ms=1, allowed=True)

    assert captured["url"] == "http://hook.example/audit"
    assert isinstance(captured["data"], bytes)
    expected = "sha256=" + hmac.new(b"s3cret", captured["data"], hashlib.sha256).hexdigest()
    assert captured["headers"]["X-Db-Assistant-Signature"] == expected
    assert b"s3cret" not in captured["data"]  # 密钥不出现在请求体中


def test_read_filters(tmp_path):
    audit = _audit(tmp_path)
    for user in ["alice", "bob"]:
        audit.record(tool="execute_query", connection="main-prod", sql="SELECT 1",
                     rows=1, duration_ms=1, allowed=True, user=user)
    entries = audit.read(tail=100, user="bob")
    assert len(entries) == 1 and entries[0]["user"] == "bob"
    entries = audit.read(tail=100, connection="main-prod", tool="execute_query")
    assert len(entries) == 2


def test_sensitive_detail_redacted(tmp_path):
    audit = _audit(tmp_path)
    audit.record(tool="execute_query", connection="c", sql="SELECT 1", rows=0,
                 duration_ms=1, allowed=False,
                 detail={"password": "hunter2", "reason": "ok"})
    line = json.loads((tmp_path / "audit.log").read_text(encoding="utf-8").strip())
    assert line["detail"]["password"] == "***"
    assert line["detail"]["reason"] == "ok"



def test_read_min_duration_filter(tmp_path):
    """T-4.3：duration_ms 严格大于阈值才保留。"""
    audit = _audit(tmp_path)
    audit.record(tool="execute_query", connection="c", sql="SELECT 1", rows=1,
                 duration_ms=50, allowed=True, user="u")
    audit.record(tool="execute_query", connection="c", sql="SELECT 2", rows=1,
                 duration_ms=1500, allowed=True, user="u")
    audit.record(tool="explain_query", connection="c", sql="EXPLAIN SELECT 1", rows=0,
                 duration_ms=2500, allowed=True, user="u")
    slow = audit.read(tail=100, min_duration_ms=1000)
    assert [e["duration_ms"] for e in slow] == [1500.0, 2500.0]
    # 边界：等于阈值不保留（严格大于）
    assert [e["duration_ms"] for e in audit.read(tail=100, min_duration_ms=1500)] == [2500.0]
    assert audit.read(tail=100, min_duration_ms=2500) == []
    # 与现有过滤可组合
    slow_tool = audit.read(tail=100, min_duration_ms=1000, tool="explain_query")
    assert len(slow_tool) == 1 and slow_tool[0]["tool"] == "explain_query"
