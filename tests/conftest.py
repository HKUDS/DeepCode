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
    import core.compat.runtime as runtime_module
    import core.sessions.store as store_module

    monkeypatch.setattr(store_module, "_DEFAULT_STORE", None)
    monkeypatch.setattr(runtime_module, "_runtime", None)
    yield


@pytest.fixture
def shared_cli_service(monkeypatch):
    """Real HTTP/WS host in a test thread, so patched model providers stay local."""
    import asyncio
    import threading
    from core.application.agent_adapter import ConfiguredAgentSessionFactory
    from core.harness.permissions import PermissionMode
    from tests.app_server.support import control_server
    from tests.app_server.test_native_client import publish

    loop = asyncio.new_event_loop()
    thread = threading.Thread(target=loop.run_forever, daemon=True)
    thread.start()
    contexts = []
    lock = threading.Lock()

    async def open_host(files):
        context = control_server(
            files.database.parent,
            database_name=files.database.name,
            session_factory=ConfiguredAgentSessionFactory(
                default_permission_mode=PermissionMode.DEFAULT, streaming=False
            ),
        )
        control, _ = await context.__aenter__()
        published, lease = publish(control)
        contexts.append((context, published, lease))
        return await control.status()

    def start(files, **_kwargs):
        with lock:
            if not files.running():
                return asyncio.run_coroutine_threadsafe(open_host(files), loop).result(
                    20
                )

    monkeypatch.setattr("cli.service_cli.start_service", start)
    yield

    async def cleanup():
        for context, files, lease in reversed(contexts):
            try:
                await context.__aexit__(None, None, None)
            finally:
                files.clear()
                lease.close()

    try:
        asyncio.run_coroutine_threadsafe(cleanup(), loop).result(30)
    finally:
        loop.call_soon_threadsafe(loop.stop)
        thread.join(5)
        loop.close()
