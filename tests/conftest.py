from __future__ import annotations

import os

import pytest


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """隔离应用配置类环境变量，避免测试间串扰（保留 DB_ASSISTANT_TEST_* 供集成测试使用）。"""
    for key in list(os.environ):
        if key.startswith("DB_ASSISTANT_") and not key.startswith("DB_ASSISTANT_TEST_"):
            monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("DB_ASSISTANT_MASTER_KEY", "test-master-key-for-unit-tests")
    yield


@pytest.fixture(autouse=True)
def _silence_sse_watcher_teardown_noise(event_loop):
    """静默 sse_starlette 内部 loop 级 _shutdown_watcher 在事件循环关闭时的已知良性告警。

    sse_starlette 会在每个事件循环上启动一个 watcher 等待 shutdown 信号；测试环境
    没有 AppServer 触发优雅退出，循环关闭时该任务被销毁并打印
    "Task was destroyed but it is pending!"（第三方问题，与 db-assistant 代码无关）。
    """
    original = event_loop.get_exception_handler()

    def filtered(loop, context):
        message = context.get("message", "")
        if "Task was destroyed but it is pending" in message:
            task = context.get("task", "")
            if "sse_starlette" in str(task):
                return
        original(loop, context)

    event_loop.set_exception_handler(filtered)
    yield
