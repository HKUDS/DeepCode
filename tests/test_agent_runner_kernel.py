"""Kernel-level tests for the P0 AgentRunSpec seams.

Covers the two mechanism knobs added for the unified implementation loop:
- ``should_stop_callback`` — external stop conditions checked at the top of
  every iteration (budgets, completion checks, loop detectors).
- ``max_injection_cycles`` — parametrized continuation budget so domain
  loops can steer far past the default 5 cycles.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.agent_runtime.runner import AgentRunner, AgentRunSpec  # noqa: E402
from core.agent_runtime.tools.base import Tool, tool_parameters  # noqa: E402
from core.agent_runtime.tools.registry import ToolRegistry  # noqa: E402
from core.providers.base import LLMResponse, ToolCallRequest  # noqa: E402


@tool_parameters(
    {
        "type": "object",
        "properties": {"text": {"type": "string"}},
        "required": ["text"],
    }
)
class EchoTool(Tool):
    @property
    def name(self) -> str:
        return "echo"

    @property
    def description(self) -> str:
        return "Echo the given text back."

    async def execute(self, **kwargs: Any) -> Any:
        return f"echo: {kwargs.get('text', '')}"


class ScriptedProvider:
    """Provider returning a fixed sequence of responses (then repeating last)."""

    def __init__(self, responses: list[LLMResponse]):
        self.responses = list(responses)
        self.calls = 0

    def get_default_model(self) -> str:
        return "fake-model"

    async def chat_with_retry(self, **kwargs: Any) -> LLMResponse:
        index = min(self.calls, len(self.responses) - 1)
        self.calls += 1
        return self.responses[index]


def _tool_registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(EchoTool())
    return registry


def _spec(provider: ScriptedProvider, **overrides: Any) -> AgentRunSpec:
    defaults: dict[str, Any] = {
        "initial_messages": [
            {"role": "system", "content": "You are a test agent."},
            {"role": "user", "content": "go"},
        ],
        "tools": _tool_registry(),
        "model": provider.get_default_model(),
        "max_iterations": 20,
        "max_tool_result_chars": 10_000,
    }
    defaults.update(overrides)
    return AgentRunSpec(**defaults)


@pytest.mark.asyncio
async def test_tool_call_roundtrip_feeds_result_back():
    provider = ScriptedProvider(
        [
            LLMResponse(
                content="",
                tool_calls=[
                    ToolCallRequest(id="c1", name="echo", arguments={"text": "hi"})
                ],
                finish_reason="tool_calls",
            ),
            LLMResponse(content="done", finish_reason="stop"),
        ]
    )
    result = await AgentRunner(provider).run(_spec(provider))

    assert result.final_content == "done"
    assert result.stop_reason == "completed"
    assert result.tools_used == ["echo"]
    tool_messages = [m for m in result.messages if m.get("role") == "tool"]
    assert len(tool_messages) == 1
    assert tool_messages[0]["content"] == "echo: hi"
    assert provider.calls == 2


@pytest.mark.asyncio
async def test_should_stop_callback_stops_before_model_call():
    provider = ScriptedProvider([LLMResponse(content="never", finish_reason="stop")])

    async def stop_now() -> str:
        return "budget exhausted"

    result = await AgentRunner(provider).run(
        _spec(provider, should_stop_callback=stop_now)
    )

    assert result.stop_reason == "callback_stop"
    assert result.final_content is None
    assert provider.calls == 0


@pytest.mark.asyncio
async def test_should_stop_callback_checked_each_iteration():
    provider = ScriptedProvider(
        [
            LLMResponse(
                content="",
                tool_calls=[
                    ToolCallRequest(id="c1", name="echo", arguments={"text": "x"})
                ],
                finish_reason="tool_calls",
            ),
        ]
    )
    seen: list[int] = []

    async def stop_after_two() -> str | None:
        seen.append(len(seen))
        if len(seen) >= 3:
            return "enough iterations"
        return None

    result = await AgentRunner(provider).run(
        _spec(provider, should_stop_callback=stop_after_two)
    )

    assert result.stop_reason == "callback_stop"
    # Two model calls happened (iterations 0 and 1); the third check stopped.
    assert provider.calls == 2


@pytest.mark.asyncio
async def test_injection_cycles_default_cap_is_five():
    provider = ScriptedProvider([LLMResponse(content="final", finish_reason="stop")])

    async def always_inject() -> list[dict[str, Any]]:
        return [{"role": "user", "content": "keep going"}]

    result = await AgentRunner(provider).run(
        _spec(provider, injection_callback=always_inject)
    )

    # 1 initial call + 5 injection-continued calls, then the cap halts.
    assert provider.calls == 6
    assert result.had_injections is True


@pytest.mark.asyncio
async def test_max_injection_cycles_override_extends_continuation():
    provider = ScriptedProvider([LLMResponse(content="final", finish_reason="stop")])
    injections_left = {"count": 8}

    async def inject_eight_times() -> list[dict[str, Any]]:
        if injections_left["count"] <= 0:
            return []
        injections_left["count"] -= 1
        return [{"role": "user", "content": "keep going"}]

    result = await AgentRunner(provider).run(
        _spec(provider, injection_callback=inject_eight_times, max_injection_cycles=50)
    )

    # 1 initial + 8 injected continuations, ended by the empty injection.
    assert provider.calls == 9
    assert result.stop_reason == "completed"
    assert result.final_content == "final"
