from __future__ import annotations

import asyncio
from dataclasses import replace

import pytest

from core.application.agent_adapter import DefaultAgentSessionFactory
from core.application.session_runtime import SessionRuntimeRegistry
from core.config import DeepCodeConfig, home_config_path
from core.domain.execution_profile import ExecutionProfile, ExecutionSelection
from core.private_storage import atomic_write_private_json
from core.providers.credentials import CredentialStore
from core.providers.profiles import ConnectionResolver
from core.sessions import SessionStore


def profile():
    return ExecutionProfile(
        connection_id="route",
        provider_name="custom",
        adapter="openai_compat",
        model_id="model",
        context_window=32000,
        max_output_tokens=4096,
        max_tokens=4096,
        temperature=0.1,
        reasoning_effort=None,
        config_revision="same-route",
        provider_revision="a" * 64,
    )


@pytest.mark.parametrize(
    "patch",
    [
        {"provider_revision": "b" * 64},
        {"tool_calling": False},
        {"input_modalities": ("text",)},
    ],
)
def test_idle_session_rebuilds_for_frozen_model_compatibility_changes(tmp_path, patch):
    class Session:
        closed = False

        def load_history(self, _history):
            pass

        async def aclose(self):
            self.closed = True

    class Factory(DefaultAgentSessionFactory):
        def create(self, **_kwargs):
            return Session()

    store = SessionStore(tmp_path / "sessions")
    canonical = store.create_session(title="revision")
    registry = SessionRuntimeRegistry(store, Factory())

    async def scenario():
        first = await registry.acquire(
            canonical.session_id,
            workspace=str(tmp_path),
            model="model",
            execution_profile=profile(),
            approval_callback=lambda *_args: False,
        )
        registry.release(canonical.session_id)
        second = await registry.acquire(
            canonical.session_id,
            workspace=str(tmp_path),
            model="model",
            execution_profile=replace(profile(), **patch),
            approval_callback=lambda *_args: False,
        )
        try:
            assert first is not second and first.closed
        finally:
            registry.release(canonical.session_id)
            await registry.close_all()

    asyncio.run(scenario())


@pytest.mark.asyncio
async def test_factory_owned_provider_pool_closes_at_session_end(tmp_path, monkeypatch):
    from core.agent_setup import build_agent_session
    from core.compat.runtime import DeepCodeRuntime

    monkeypatch.setenv("DEEPCODE_HOME", str(tmp_path / "home"))
    config = DeepCodeConfig.model_validate(
        {
            "agents": {"defaults": {"connection": "route", "model": "model"}},
            "providers": {
                "profiles": {
                    "route": {
                        "template": "custom",
                        "protocol": "openai_chat",
                        "auth": "none",
                        "apiBase": "http://127.0.0.1:9/v1",
                    }
                }
            },
        }
    )
    atomic_write_private_json(
        home_config_path(), config.model_dump(by_alias=True, exclude_none=True)
    )
    resolved = ConnectionResolver(config, CredentialStore()).execution_profile(
        ExecutionSelection("route", "model")
    )
    session = DefaultAgentSessionFactory().create(
        workspace=str(tmp_path),
        model="model",
        execution_profile=resolved,
        approval_callback=lambda *_args: False,
    )
    sdk = session._provider._client
    assert not sdk.is_closed()
    await session.aclose()
    assert sdk.is_closed()
    await session.aclose()
    # A borrowed/global runtime stays alive until its actual owner closes it.
    runtime = DeepCodeRuntime(config)
    borrowed, *_ = build_agent_session(
        workspace=str(tmp_path),
        model="model",
        execution_profile=resolved,
        runtime=runtime,
        project_trusted=True,
        allow_spawn=False,
    )
    shared_sdk = borrowed._provider._client
    await borrowed.aclose()
    assert not shared_sdk.is_closed()
    await runtime.aclose()
    assert shared_sdk.is_closed()
