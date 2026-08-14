"""Per-tool declared timeouts (``Tool.timeout_s``) enforced by the runner.

The contract under test, borrowed from dsh's tool-call timeout policy:

- a tool declares its own wall-clock budget; enforcement lives in one place
  (the runner), so no tool re-implements deadline handling;
- expiry becomes a structured ``TOOL_TIMEOUT`` result the model can read and
  react to — errors-as-data, never an exception that ends the run;
- attribution is scoped: a ``TimeoutError`` the tool raised itself, and an
  outer cancellation of the whole run, must NOT be misread as this deadline.
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

from core.agent_runtime.runner import AgentRunner, AgentRunSpec
from core.agent_runtime.tools.base import Tool, ToolResult, tool_parameters
from core.agent_runtime.tools.registry import ToolRegistry
from core.providers.base import LLMResponse, ToolCallRequest


@tool_parameters({"type": "object", "properties": {}})
class _SlowTool(Tool):
    """Sleeps far past its declared budget; honors cancellation."""

    def __init__(self, budget_s: float | None, sleep_s: float = 30.0):
        self._budget_s = budget_s
        self._sleep_s = sleep_s
        self.cancelled = False

    @property
    def name(self) -> str:
        return "slow"

    @property
    def description(self) -> str:
        return "Sleep for a while."

    @property
    def timeout_s(self) -> float | None:
        return self._budget_s

    async def execute(self, **kwargs: Any) -> Any:
        try:
            await asyncio.sleep(self._sleep_s)
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        return "slept"


@tool_parameters({"type": "object", "properties": {}})
class _SelfTimeoutTool(Tool):
    """Raises ``TimeoutError`` from inside — a tool-owned timeout, not ours."""

    def __init__(self, budget_s: float | None):
        self._budget_s = budget_s

    @property
    def name(self) -> str:
        return "self_timeout"

    @property
    def description(self) -> str:
        return "Fail with the tool's own TimeoutError."

    @property
    def timeout_s(self) -> float | None:
        return self._budget_s

    async def execute(self, **kwargs: Any) -> Any:
        raise TimeoutError("upstream service timed out")


class _ScriptedProvider:
    """One tool call, then a plain completion (mirrors the kernel tests)."""

    def __init__(self, tool_name: str):
        self._responses = [
            LLMResponse(
                content="",
                tool_calls=[ToolCallRequest(id="call-1", name=tool_name, arguments={})],
                finish_reason="tool_calls",
            ),
            LLMResponse(content="done", finish_reason="stop"),
        ]
        self.calls = 0

    def get_default_model(self) -> str:
        return "fake-model"

    async def chat_with_retry(self, **kwargs: Any) -> LLMResponse:
        index = min(self.calls, len(self._responses) - 1)
        self.calls += 1
        return self._responses[index]


def _run(tool: Tool) -> tuple[Any, Any]:
    registry = ToolRegistry()
    registry.register(tool)
    provider = _ScriptedProvider(tool.name)
    spec = AgentRunSpec(
        initial_messages=[{"role": "user", "content": "go"}],
        tools=registry,
        model="fake-model",
        max_iterations=5,
        max_tool_result_chars=10_000,
    )
    result = asyncio.run(AgentRunner(provider).run(spec))
    tool_message = next(m for m in result.messages if m.get("role") == "tool")
    return result, tool_message


def test_expired_budget_becomes_structured_timeout_result() -> None:
    tool = _SlowTool(budget_s=0.05)
    result, tool_message = _run(tool)

    # The run itself survives — the model saw the timeout and answered.
    assert result.final_content == "done"
    assert result.error is None

    content = tool_message["content"]
    assert "timed out after 0.05s" in content
    event = next(e for e in result.tool_events if e["name"] == "slow")
    assert event["status"] == "timeout"

    # The tool coroutine was cancelled, not abandoned.
    assert tool.cancelled is True


def test_timeout_result_carries_machine_readable_metadata() -> None:
    """The structured code rides ToolResult metadata so frontends and retry
    logic can route on it without parsing display text."""

    async def capture() -> ToolResult:
        registry = ToolRegistry()
        tool = _SlowTool(budget_s=0.05)
        registry.register(tool)
        runner = AgentRunner(_ScriptedProvider("slow"))
        spec = AgentRunSpec(
            initial_messages=[{"role": "user", "content": "go"}],
            tools=registry,
            model="fake-model",
            max_iterations=5,
            max_tool_result_chars=10_000,
        )
        call = ToolCallRequest(id="call-1", name="slow", arguments={})
        result, _event, _error = await runner._run_tool(spec, call, {})
        return result

    result = asyncio.run(capture())
    assert isinstance(result, ToolResult)
    assert result.is_error is True
    assert result.metadata["error_code"] == "TOOL_TIMEOUT"
    assert result.metadata["timeout_s"] == 0.05


def test_tools_own_timeout_error_is_not_blamed_on_the_deadline() -> None:
    """A TimeoutError raised inside the tool, while a budget is armed but not
    expired, is an ordinary tool failure — not a TOOL_TIMEOUT."""

    tool = _SelfTimeoutTool(budget_s=60.0)
    result, tool_message = _run(tool)

    assert result.final_content == "done"
    event = next(e for e in result.tool_events if e["name"] == "self_timeout")
    assert event["status"] == "error"
    assert "TimeoutError" in tool_message["content"]
    assert "TOOL_TIMEOUT" not in tool_message["content"]


def test_no_budget_means_no_deadline() -> None:
    tool = _SlowTool(budget_s=None, sleep_s=0.01)
    result, tool_message = _run(tool)

    assert result.final_content == "done"
    assert tool_message["content"] == "slept"


def test_outer_cancellation_passes_through_unchanged() -> None:
    """Cancelling the whole run must surface as CancelledError — never be
    swallowed into a timeout result the model would then try to act on."""

    async def scenario() -> None:
        registry = ToolRegistry()
        tool = _SlowTool(budget_s=60.0)
        registry.register(tool)
        runner = AgentRunner(_ScriptedProvider("slow"))
        spec = AgentRunSpec(
            initial_messages=[{"role": "user", "content": "go"}],
            tools=registry,
            model="fake-model",
            max_iterations=5,
            max_tool_result_chars=10_000,
        )
        task = asyncio.ensure_future(AgentRunner.run(runner, spec))
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(scenario())
