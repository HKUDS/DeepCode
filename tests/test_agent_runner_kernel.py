"""Kernel-level tests for the shared AgentRunSpec seams.

The runner has no default semantic limit on task length. It ends when the
model finishes, the caller cancels, or an explicit diagnostic limit is set:

- ``should_stop_callback`` — external stop conditions checked at the top of
  every sampling step.
- accepted input always reaches another model sample, including at an explicit
  max-iteration boundary.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.agent_runtime.context import EnvironmentContext
from core.agent_runtime.hook import AgentHook, AgentHookContext
from core.agent_runtime.runner import AgentRunner, AgentRunSpec
from core.agent_runtime.tools.base import Tool, ToolResult, tool_parameters
from core.agent_runtime.tools.registry import ToolRegistry
from core.providers.base import LLMResponse, ToolCallRequest


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


@tool_parameters({"type": "object", "properties": {}})
class StructuredFailureTool(Tool):
    @property
    def name(self) -> str:
        return "structured_failure"

    @property
    def description(self) -> str:
        return "Return a model-visible structured failure."

    async def execute(self, **kwargs: Any) -> ToolResult:
        return ToolResult(
            "[exit 7]\nverification failed",
            is_error=True,
            metadata={"exit_code": 7},
        )


class ScriptedProvider:
    """Provider returning a fixed sequence of responses (then repeating last)."""

    def __init__(self, responses: list[LLMResponse]):
        self.responses = list(responses)
        self.calls = 0
        self.requests: list[dict[str, Any]] = []

    def get_default_model(self) -> str:
        return "fake-model"

    async def chat_with_retry(self, **kwargs: Any) -> LLMResponse:
        self.requests.append(kwargs)
        index = min(self.calls, len(self.responses) - 1)
        self.calls += 1
        return self.responses[index]


class StreamingHook(AgentHook):
    def __init__(self) -> None:
        super().__init__()
        self.deltas: list[str] = []

    def wants_streaming(self) -> bool:
        return True

    async def on_stream(self, context: AgentHookContext, delta: str) -> None:
        self.deltas.append(delta)


class UsageRecordingHook(AgentHook):
    def __init__(self) -> None:
        super().__init__()
        self.responses: list[tuple[int, dict[str, int]]] = []

    async def on_model_response(self, context: AgentHookContext) -> None:
        self.responses.append((context.response_ordinal, dict(context.usage)))


class DelayedStreamingProvider:
    def __init__(self, delay_s: float) -> None:
        self.delay_s = delay_s
        self.calls = 0

    def get_default_model(self) -> str:
        return "streaming-model"

    async def chat_stream_with_retry(self, **kwargs: Any) -> LLMResponse:
        self.calls += 1
        await asyncio.sleep(self.delay_s)
        callback = kwargs.get("on_content_delta")
        if callback is not None:
            await callback("done")
        return LLMResponse(content="done", finish_reason="stop")

    async def chat_with_retry(self, **kwargs: Any) -> LLMResponse:
        self.calls += 1
        await asyncio.sleep(self.delay_s)
        return LLMResponse(content="done", finish_reason="stop")


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


def test_transient_context_preserves_priority_and_latest_user_order() -> None:
    messages = [
        {"role": "system", "content": "base system"},
        {"role": "system", "content": "hook context"},
        {"role": "user", "content": "previous question"},
        {"role": "assistant", "content": "previous answer"},
        {"role": "user", "content": "current task"},
    ]
    context = (
        {"role": "developer", "content": "skill catalog"},
        {"role": "user", "content": "environment facts"},
        {"role": "user", "content": "selected skill"},
    )

    composed = AgentRunner._with_transient_context(messages, context)

    assert [message["role"] for message in composed] == [
        "system",
        "user",
        "assistant",
        "user",
        "user",
        "user",
    ]
    assert all(
        marker in composed[0]["content"]
        for marker in ("base system", "hook context", "skill catalog")
    )
    assert [message["content"] for message in composed[-3:]] == [
        "environment facts",
        "selected skill",
        "current task",
    ]
    assert all(message["role"] != "developer" for message in composed)


def test_environment_context_is_resolved_and_xml_safe(tmp_path: Path) -> None:
    workspace = tmp_path / "project & sources"
    context = EnvironmentContext.for_workspace(workspace)

    rendered = context.render()

    assert context.cwd == str(workspace.resolve())
    assert (
        f"<cwd>{workspace.resolve().as_posix().replace('&', '&amp;')}</cwd>" in rendered
    )
    assert "<shell>" in rendered
    assert "<current_date>" in rendered
    assert "<timezone>" in rendered
    # The slot carries an explicit marker: the durable environment message is
    # recognised by that key, never by sniffing its content, so a user message
    # that merely quotes the block is not mistaken for the slot and overwritten.
    assert context.message() == {
        "role": "user",
        "content": rendered,
        "env_context": True,
    }


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
async def test_structured_tool_failure_remains_model_visible_and_marks_event_error():
    provider = ScriptedProvider(
        [
            LLMResponse(
                content="",
                tool_calls=[
                    ToolCallRequest(
                        id="c1",
                        name="structured_failure",
                        arguments={},
                    )
                ],
                finish_reason="tool_calls",
            ),
            LLMResponse(content="handled failure", finish_reason="stop"),
        ]
    )
    tools = ToolRegistry()
    tools.register(StructuredFailureTool())

    result = await AgentRunner(provider).run(_spec(provider, tools=tools))

    tool_message = next(
        message for message in result.messages if message.get("role") == "tool"
    )
    assert tool_message["content"].startswith("[exit 7]")
    assert result.tool_events == [
        {
            "name": "structured_failure",
            "status": "error",
            "detail": "[exit 7] verification failed",
        }
    ]
    assert result.final_content == "handled failure"


@pytest.mark.asyncio
async def test_each_provider_response_reports_incremental_usage_before_settlement():
    provider = ScriptedProvider(
        [
            LLMResponse(
                content="",
                tool_calls=[
                    ToolCallRequest(id="c1", name="echo", arguments={"text": "hi"})
                ],
                finish_reason="tool_calls",
                usage={
                    "prompt_tokens": 10,
                    "completion_tokens": 3,
                    "total_tokens": 13,
                },
            ),
            LLMResponse(
                content="done",
                finish_reason="stop",
                usage={
                    "prompt_tokens": 20,
                    "completion_tokens": 4,
                    "total_tokens": 24,
                },
            ),
        ]
    )
    hook = UsageRecordingHook()

    result = await AgentRunner(provider).run(_spec(provider, hook=hook))

    assert hook.responses == [
        (
            1,
            {
                "prompt_tokens": 10,
                "completion_tokens": 3,
                "total_tokens": 13,
            },
        ),
        (
            2,
            {
                "prompt_tokens": 20,
                "completion_tokens": 4,
                "total_tokens": 24,
            },
        ),
    ]
    assert result.usage == {
        "prompt_tokens": 30,
        "completion_tokens": 7,
        "total_tokens": 37,
    }


@pytest.mark.asyncio
async def test_unbounded_run_can_exceed_legacy_sampling_defaults() -> None:
    responses = [
        LLMResponse(
            content="",
            tool_calls=[
                ToolCallRequest(
                    id=f"c{index}",
                    name="echo",
                    arguments={"text": str(index)},
                )
            ],
            finish_reason="tool_calls",
        )
        for index in range(60)
    ]
    responses.append(
        LLMResponse(content="done after sixty tools", finish_reason="stop")
    )
    provider = ScriptedProvider(responses)

    result = await AgentRunner(provider).run(_spec(provider, max_iterations=None))

    assert result.stop_reason == "completed"
    assert result.final_content == "done after sixty tools"
    assert provider.calls == 61
    assert len(result.tools_used) == 60


@pytest.mark.asyncio
async def test_explicit_sampling_limit_remains_available_for_diagnostics() -> None:
    provider = ScriptedProvider(
        [
            LLMResponse(
                content="",
                tool_calls=[
                    ToolCallRequest(
                        id=f"c{index}",
                        name="echo",
                        arguments={"text": str(index)},
                    )
                ],
                finish_reason="tool_calls",
            )
            for index in range(4)
        ]
    )

    result = await AgentRunner(provider).run(_spec(provider, max_iterations=3))

    assert result.stop_reason == "max_iterations"
    assert provider.calls == 3
    assert "maximum number of tool call iterations (3)" in (result.final_content or "")


@pytest.mark.asyncio
async def test_streaming_request_is_not_cut_off_by_regular_request_timeout(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("DEEPCODE_LLM_TIMEOUT_S", "0.01")
    monkeypatch.delenv("DEEPCODE_LLM_STREAM_MAX_RUNTIME_S", raising=False)
    provider = DelayedStreamingProvider(0.03)
    hook = StreamingHook()

    result = await AgentRunner(provider).run(_spec(provider, hook=hook))

    assert result.stop_reason == "completed"
    assert result.final_content == "done"
    assert hook.deltas == ["done"]


@pytest.mark.asyncio
async def test_streaming_request_respects_explicit_safety_cap(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("DEEPCODE_LLM_STREAM_MAX_RUNTIME_S", "0.01")
    provider = DelayedStreamingProvider(0.03)

    result = await AgentRunner(provider).run(_spec(provider, hook=StreamingHook()))

    assert result.stop_reason == "error"
    assert result.final_content == (
        "Error calling LLM: stream exceeded the configured maximum runtime of 0.01s"
    )


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
async def test_bounded_callback_batch_is_not_truncated_by_runner():
    provider = ScriptedProvider([LLMResponse(content="final", finish_reason="stop")])
    pending = [{"role": "user", "content": f"follow-up-{index}"} for index in range(64)]

    async def drain_all() -> list[dict[str, Any]]:
        values = list(pending)
        pending.clear()
        return values

    result = await AgentRunner(provider).run(
        _spec(provider, injection_callback=drain_all)
    )

    assert provider.calls == 1
    request_text = str(provider.requests[0]["messages"])
    assert "follow-up-0" in request_text
    assert "follow-up-63" in request_text
    assert result.had_injections is True


@pytest.mark.asyncio
async def test_denied_tool_becomes_error_result_not_crash():
    """A permission denial must feed back as an errors-as-data tool result
    and let the loop continue — never raise, never abort the run."""
    provider = ScriptedProvider(
        [
            LLMResponse(
                content="",
                tool_calls=[
                    ToolCallRequest(id="c1", name="echo", arguments={"text": "secret"})
                ],
                finish_reason="tool_calls",
            ),
            LLMResponse(content="ok, backing off", finish_reason="stop"),
        ]
    )

    def deny_all(tool_name, arguments):
        return ("deny", "blocked by test policy")

    result = await AgentRunner(provider).run(
        _spec(provider, permission_checker=deny_all)
    )

    assert result.stop_reason == "completed"
    assert result.final_content == "ok, backing off"
    tool_messages = [m for m in result.messages if m.get("role") == "tool"]
    assert len(tool_messages) == 1
    assert "permission denied" in tool_messages[0]["content"]
    assert "blocked by test policy" in tool_messages[0]["content"]


@pytest.mark.asyncio
async def test_ask_without_approver_is_denied():
    provider = ScriptedProvider(
        [
            LLMResponse(
                content="",
                tool_calls=[
                    ToolCallRequest(id="c1", name="echo", arguments={"text": "x"})
                ],
                finish_reason="tool_calls",
            ),
            LLMResponse(content="done", finish_reason="stop"),
        ]
    )

    def ask_always(tool_name, arguments):
        return ("ask", "needs confirmation")

    result = await AgentRunner(provider).run(
        _spec(provider, permission_checker=ask_always)
    )
    tool_messages = [m for m in result.messages if m.get("role") == "tool"]
    assert "permission denied" in tool_messages[0]["content"]
    assert "no approver" in tool_messages[0]["content"]


@pytest.mark.asyncio
async def test_ask_with_approver_allows():
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
    approvals: list[str] = []

    def ask_always(tool_name, arguments):
        return ("ask", "needs confirmation")

    async def approve(tool_name, arguments, reason):
        approvals.append(tool_name)
        return True

    result = await AgentRunner(provider).run(
        _spec(provider, permission_checker=ask_always, approval_callback=approve)
    )
    tool_messages = [m for m in result.messages if m.get("role") == "tool"]
    assert tool_messages[0]["content"] == "echo: hi"
    assert approvals == ["echo"]


@pytest.mark.asyncio
async def test_injections_have_no_fixed_cycle_cap():
    provider = ScriptedProvider([LLMResponse(content="final", finish_reason="stop")])
    injections_left = {"count": 8}

    async def inject_eight_times() -> list[dict[str, Any]]:
        if injections_left["count"] <= 0:
            return []
        injections_left["count"] -= 1
        return [{"role": "user", "content": "keep going"}]

    result = await AgentRunner(provider).run(
        _spec(provider, injection_callback=inject_eight_times)
    )

    # The first injection is present on the first request, avoiding an empty
    # probe call; seven later continuations consume the remaining messages.
    assert provider.calls == 8
    assert result.stop_reason == "completed"
    assert result.final_content == "final"


@pytest.mark.asyncio
async def test_input_at_max_iteration_boundary_gets_another_model_sample():
    provider = ScriptedProvider(
        [
            LLMResponse(
                content="",
                tool_calls=[
                    ToolCallRequest(id="c1", name="echo", arguments={"text": "x"})
                ],
                finish_reason="tool_calls",
            ),
            LLMResponse(content="handled correction", finish_reason="stop"),
        ]
    )
    drains = 0

    async def inject_after_tool() -> list[dict[str, Any]]:
        nonlocal drains
        drains += 1
        if drains == 2:
            return [{"role": "user", "content": "correct the result"}]
        return []

    result = await AgentRunner(provider).run(
        _spec(
            provider,
            injection_callback=inject_after_tool,
            max_iterations=1,
        )
    )

    assert provider.calls == 2
    assert result.final_content == "handled correction"
    assert result.stop_reason == "completed"


@pytest.mark.asyncio
async def test_injection_callback_failure_is_observable():
    provider = ScriptedProvider([LLMResponse(content="unused", finish_reason="stop")])

    async def broken_callback() -> list[dict[str, Any]]:
        raise RuntimeError("mailbox unavailable")

    with pytest.raises(RuntimeError, match="mailbox unavailable"):
        await AgentRunner(provider).run(
            _spec(provider, injection_callback=broken_callback)
        )
    assert provider.calls == 0
