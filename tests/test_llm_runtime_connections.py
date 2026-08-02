from __future__ import annotations

import asyncio
from types import SimpleNamespace

from core.compat.agent import Agent
from core.domain.execution_profile import ExecutionProfile
from core.llm_runtime import get_workflow_provider
from core.providers.base import GenerationSettings


class _Provider:
    generation = GenerationSettings(
        temperature=0.2,
        max_tokens=4096,
        reasoning_effort="high",
    )

    @staticmethod
    def get_default_model() -> str:
        return "provider/model"


class _Runtime:
    def __init__(self) -> None:
        self.config = SimpleNamespace(
            get_provider_name=lambda _model: "legacy",
            llm_provider="auto",
        )
        self.profile = ExecutionProfile(
            connection_id="team-router",
            provider_name="openrouter",
            adapter="openai_compat",
            model_id="provider/model",
            context_window=200_000,
            max_output_tokens=16_000,
            max_tokens=4096,
            temperature=0.2,
            reasoning_effort="high",
            config_revision="revision",
        )
        self.provider_kwargs = None

    def resolve_execution_profile(self, **_kwargs):
        return self.profile

    def provider_for(self, **kwargs):
        self.provider_kwargs = kwargs
        return _Provider()


def test_workflow_provider_uses_the_shared_named_connection_by_default() -> None:
    runtime = _Runtime()

    _provider, profile = get_workflow_provider(
        phase="planning",
        runtime=runtime,
    )

    assert runtime.provider_kwargs["execution_profile"] == runtime.profile
    assert runtime.provider_kwargs["connection_id"] == "team-router"
    assert profile.connection_id == "team-router"
    assert profile.context_window == 200_000


def test_compat_agent_uses_the_shared_named_connection_by_default() -> None:
    runtime = _Runtime()
    agent = Agent("connection-test")
    agent._runtime = runtime

    llm = asyncio.run(agent.attach_llm(phase="planning"))

    assert runtime.provider_kwargs["execution_profile"] == runtime.profile
    assert runtime.provider_kwargs["connection_id"] == "team-router"
    assert llm.provider_name == "openrouter"
