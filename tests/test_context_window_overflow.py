"""Input-side context overflow recovers once, then surfaces the original error."""

from __future__ import annotations

import asyncio

from core.agent_runtime.runner import AgentRunner, AgentRunSpec
from core.agent_runtime.tools.registry import ToolRegistry
from core.providers.base import LLMResponse, is_context_window_error


class _OverflowThenOk:
    def __init__(self) -> None:
        self.calls = 0

    def get_default_model(self) -> str:
        return "fake"

    async def chat_with_retry(self, **kwargs):
        self.calls += 1
        if self.calls == 1:
            return LLMResponse(
                content="This model's maximum context length was exceeded",
                finish_reason="error",
                error_code="context_length_exceeded",
            )
        return LLMResponse(content="recovered", finish_reason="stop")


def test_overflow_classifier_reads_provider_code() -> None:
    response = LLMResponse(
        content="nope",
        finish_reason="error",
        error_code="context_length_exceeded",
    )
    assert is_context_window_error(response)
    assert not is_context_window_error(
        LLMResponse(content="rate limited", finish_reason="error")
    )


def test_overflow_recovers_once() -> None:
    provider = _OverflowThenOk()
    spec = AgentRunSpec(
        initial_messages=[
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "old " * 20},
            {"role": "assistant", "content": "a"},
            {"role": "user", "content": "now"},
        ],
        tools=ToolRegistry(),
        model="fake",
        max_iterations=3,
        max_tool_result_chars=1000,
        context_window_tokens=8_000,
    )
    result = asyncio.run(AgentRunner(provider).run(spec))
    assert result.final_content == "recovered"
    assert provider.calls == 2
