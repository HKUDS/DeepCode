"""Test-wide isolation for DeepCode's process-global SessionStore."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _isolate_session_store(tmp_path, monkeypatch):
    home = tmp_path / "deepcode-home"
    monkeypatch.setenv("DEEPCODE_HOME", str(home))
    monkeypatch.setenv("DEEPCODE_SESSIONS_DIR", str(home / "sessions"))
    from core.providers.registry import PROVIDERS

    credential_environment_names = {
        "ANTHROPIC_API_KEY",
        "AZURE_OPENAI_API_KEY",
        "DEEPSEEK_API_KEY",
        "GEMINI_API_KEY",
        "GOOGLE_API_KEY",
        "GROQ_API_KEY",
        "OPENAI_API_KEY",
        "OPENROUTER_API_KEY",
        *(provider.env_key for provider in PROVIDERS if provider.env_key),
    }
    for name in credential_environment_names:
        monkeypatch.delenv(name, raising=False)
    # The project deepcode_config.json references ${DEEPSEEK_API_KEY}; loading
    # it (e.g. CodeIndexer.__init__ -> get_default_models) raises ValueError
    # when the env var is absent. Restore a placeholder so config-driven
    # construction stays side-effect free while the other credentials remain
    # stripped for hygiene.
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-placeholder-key")
    import core.compat.runtime as runtime_module
    import core.sessions.store as store_module

    monkeypatch.setattr(store_module, "_DEFAULT_STORE", None)
    monkeypatch.setattr(runtime_module, "_runtime", None)
    yield
