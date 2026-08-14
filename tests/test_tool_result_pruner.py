"""Model-free tool-result pruning and prefix-aligned summarization (dsh ⑥).

The contract under test, borrowed from dsh's compaction-tool-result-pruner
and its prefix-cache-reuse note:

- selection is purely per-result size — no recency window, no tool-name
  list, so MCP and custom tools are covered exactly like built-ins;
- the pruner is convergent: a pruned result sits under the threshold, so a
  second pass selects nothing;
- it never runs opportunistically below context pressure, and when it runs
  it lands BEFORE the summarizer — clearing pressure for free skips the
  model round-trip entirely;
- the summarization call replays the routed request's exact view (same
  governance, same transient context, same tool schemas) and appends only
  its instruction, so it is a genuine prefix of the last routed request;
- the AUTO compaction path rejects a summary that does not shrink its
  source by volume.
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

from core.agent_runtime.pruner import PRUNE_MARKER, ToolResultPruner
from core.agent_runtime.runner import (
    _SUMMARIZATION_PROMPT,
    AgentRunner,
    AgentRunSpec,
)
from core.agent_runtime.tools.base import Tool, tool_parameters
from core.agent_runtime.tools.registry import ToolRegistry
from core.providers.base import LLMResponse

# ---------------------------------------------------------------------------
# Pruner unit behavior
# ---------------------------------------------------------------------------


def test_oversized_tool_result_is_middle_pruned() -> None:
    pruner = ToolResultPruner(threshold_chars=100, head_chars=30, tail_chars=10)
    content = "H" * 50 + "M" * 100 + "T" * 50
    messages = [
        {"role": "user", "content": "q"},
        {"role": "tool", "tool_call_id": "c1", "name": "grep", "content": content},
    ]
    pruned, count = pruner.prune_messages(messages)
    assert count == 1
    result = pruned[1]["content"]
    assert result.startswith("H" * 30)
    assert result.endswith("T" * 10)
    assert PRUNE_MARKER in result
    assert len(result) <= pruner.threshold_chars
    # The original list and message are untouched.
    assert messages[1]["content"] == content


def test_small_results_and_non_tool_roles_are_untouched() -> None:
    pruner = ToolResultPruner(threshold_chars=100, head_chars=30, tail_chars=10)
    messages = [
        {"role": "user", "content": "U" * 500},  # not a tool result
        {"role": "tool", "tool_call_id": "c1", "content": "small"},
        {"role": "tool", "tool_call_id": "c2", "content": ["structured", "blocks"]},
    ]
    pruned, count = pruner.prune_messages(messages)
    assert count == 0
    assert pruned is messages  # identity signals the no-op


def test_pruner_converges_in_one_pass() -> None:
    pruner = ToolResultPruner(threshold_chars=100, head_chars=30, tail_chars=10)
    messages = [{"role": "tool", "tool_call_id": "c", "content": "X" * 500}]
    once, count = pruner.prune_messages(messages)
    assert count == 1
    twice, count_again = pruner.prune_messages(once)
    assert count_again == 0
    assert twice is once


def test_replacement_larger_than_threshold_is_rejected_at_construction() -> None:
    with pytest.raises(ValueError, match="must not exceed the threshold"):
        ToolResultPruner(threshold_chars=50, head_chars=40, tail_chars=40)
    with pytest.raises(ValueError, match="threshold must be positive"):
        ToolResultPruner(threshold_chars=0)


# ---------------------------------------------------------------------------
# The compaction ladder: gate → prune → remeasure → summarize
# ---------------------------------------------------------------------------


class _Provider:
    """Records every chat call; first call may return a canned summary."""

    def __init__(self, summary: str = "HANDOFF summary") -> None:
        self.summary = summary
        self.calls: list[dict[str, Any]] = []

    def get_default_model(self) -> str:
        return "fake-model"

    async def chat_with_retry(self, **kwargs: Any) -> LLMResponse:
        self.calls.append(kwargs)
        return LLMResponse(content=self.summary, finish_reason="stop")


def _spec(**kw: Any) -> AgentRunSpec:
    base: dict[str, Any] = dict(
        initial_messages=[],
        tools=ToolRegistry(),
        model="fake-model",
        max_iterations=1,
        max_tool_result_chars=100_000,
        context_window_tokens=16_000,
    )
    base.update(kw)
    return AgentRunSpec(**base)


def _tool_round(call_id: str, chars: int) -> list[dict[str, Any]]:
    return [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"id": call_id, "function": {"name": "grep", "arguments": "{}"}}
            ],
        },
        {
            "role": "tool",
            "tool_call_id": call_id,
            "name": "grep",
            "content": "R" * chars,
        },
    ]


def test_below_pressure_session_is_not_pruned_opportunistically() -> None:
    provider = _Provider()
    messages = (
        [{"role": "user", "content": "q1"}]
        + _tool_round("c1", 30_000)
        + [{"role": "assistant", "content": "a1"}]
    )
    spec = _spec(context_window_tokens=400_000)
    out = asyncio.run(AgentRunner(provider)._maybe_compact(spec, messages))
    assert out is messages  # untouched, oversized result and all
    assert provider.calls == []


def test_pruning_alone_clearing_pressure_skips_the_model_call() -> None:
    provider = _Provider()
    messages = (
        [{"role": "user", "content": "q1"}]
        + _tool_round("c1", 30_000)
        + _tool_round("c2", 30_000)
        + [{"role": "assistant", "content": "done so far"}]
    )
    out = asyncio.run(AgentRunner(provider)._maybe_compact(_spec(), messages))
    assert provider.calls == []  # the free pass was sufficient
    assert out is not messages
    tool_contents = [m["content"] for m in out if m.get("role") == "tool"]
    assert all(PRUNE_MARKER in c for c in tool_contents)
    # Durable: the pruned list IS the persisted history, so the next request's
    # prefix stays byte-stable instead of being re-blanked per request.


def test_summarizer_runs_over_the_pruned_surface_when_pruning_is_insufficient() -> None:
    provider = _Provider()
    bulk = [
        {
            "role": "user" if i % 2 == 0 else "assistant",
            "content": f"m{i} " + "w" * 2000,
        }
        for i in range(20)
    ]
    # The tool round sits at the NEWEST end so the snip that fits the
    # summarization request within budget keeps it in view.
    messages = (
        [{"role": "user", "content": "q1"}]
        + bulk
        + _tool_round("c1", 30_000)
        + [{"role": "assistant", "content": "done"}]
    )
    out = asyncio.run(AgentRunner(provider)._maybe_compact(_spec(), messages))
    assert len(provider.calls) == 1  # pruning landed, then one summary call
    summarize_request = provider.calls[0]["messages"]
    joined = " ".join(str(m.get("content")) for m in summarize_request)
    assert PRUNE_MARKER in joined  # the summarizer saw the pruned surface
    assert any("HANDOFF summary" in str(m.get("content")) for m in out)


# ---------------------------------------------------------------------------
# Prefix-aligned summarization (KV/prompt-cache reuse)
# ---------------------------------------------------------------------------


@tool_parameters({"type": "object", "properties": {}, "additionalProperties": False})
class _NoopTool(Tool):
    @property
    def name(self) -> str:
        return "noop"

    @property
    def description(self) -> str:
        return "does nothing"

    async def execute(self, **_kwargs: Any) -> str:
        return "ok"


def test_summarization_request_is_a_genuine_prefix_of_the_routed_request() -> None:
    provider = _Provider()
    registry = ToolRegistry()
    registry.register(_NoopTool())
    spec = _spec(
        tools=registry,
        transient_context_messages=({"role": "user", "content": "TRANSIENT ctx"},),
    )
    runner = AgentRunner(provider)
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "q1"},
        {"role": "assistant", "content": "a1"},
        {"role": "user", "content": "q2"},
        {"role": "assistant", "content": "a2"},
    ]
    summary = asyncio.run(runner._summarize(spec, messages))
    assert summary == "HANDOFF summary"
    (call,) = provider.calls
    # Tool schemas ride along even though the summarizer never calls one —
    # dropping them would misalign every token after the tools block.
    assert call["tools"] == spec.tool_definitions() and call["tools"]
    request = call["messages"]
    # The instruction is the ONLY addition after the routed request's view.
    assert request[-1] == {"role": "user", "content": _SUMMARIZATION_PROMPT}
    assert request[:-1] == runner._request_view(spec, messages)
    assert any("TRANSIENT ctx" in str(m.get("content")) for m in request[:-1])


# ---------------------------------------------------------------------------
# AUTO-path shrink gate
# ---------------------------------------------------------------------------


def test_auto_compaction_rejects_a_summary_that_grows_the_history() -> None:
    provider = _Provider(summary="verbose " + "z" * 30_000)
    messages = [
        {"role": "user" if i % 2 == 0 else "assistant", "content": f"m{i} " + "w" * 800}
        for i in range(8)
    ]
    spec = _spec(context_window_tokens=6_000)
    out = asyncio.run(AgentRunner(provider)._maybe_compact(spec, messages))
    assert len(provider.calls) == 1  # the summary was produced…
    assert out is messages  # …but rejected: it would have grown the history
