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
