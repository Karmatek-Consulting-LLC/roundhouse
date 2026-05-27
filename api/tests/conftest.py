"""Shared pytest fixtures.

Lab's first test file lands alongside the Kubernetes backend — pytest is
already declared in pyproject.toml's dev extras, so tests run via
`pytest -q` from the api/ directory or via `pytest api/tests/`.
"""
from __future__ import annotations

import os

import pytest


@pytest.fixture(autouse=True)
def _isolate_settings(monkeypatch):
    """Clear MCP_* env so each test sees the documented defaults unless it
    explicitly overrides. Also drops the @lru_cache'd settings + orchestrator
    singleton between tests so config edits take effect."""
    for key in list(os.environ):
        if key.startswith("MCP_") or key in {"NODE_NAME", "POD_NAMESPACE"}:
            monkeypatch.delenv(key, raising=False)

    from app.config import get_settings
    from app.services.orchestrator import reset_orchestrator_for_tests

    get_settings.cache_clear()
    reset_orchestrator_for_tests()
    yield
    get_settings.cache_clear()
    reset_orchestrator_for_tests()
