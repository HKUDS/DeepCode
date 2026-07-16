"""Test-wide isolation for DeepCode's process-global SessionStore."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _isolate_session_store(tmp_path, monkeypatch):
    monkeypatch.setenv("DEEPCODE_SESSIONS_DIR", str(tmp_path / "sessions"))
    import core.sessions.store as store_module
    import core.compat.runtime as runtime_module

    monkeypatch.setattr(store_module, "_DEFAULT_STORE", None)
    monkeypatch.setattr(runtime_module, "_runtime", None)
    yield
