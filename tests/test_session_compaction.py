"""P2g: AgentSession arms the runner's context-compaction ladder.

The runner has always had ``_snip_history`` (trim history to a token budget)
and ``_microcompact`` (drop stale tool results), but they are dormant unless
``AgentRunSpec.context_window_tokens`` is set. AgentSession now resolves that
from the model catalog, so a session whose history outgrows the window gets
trimmed instead of overflowing the model. These tests prove the wiring:
what the provider actually receives is smaller than the full transcript, the
newest turn survives, the oldest is dropped, and the run still completes.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import asyncio  # noqa: E402

from core.agent_runtime.tools.registry import ToolRegistry  # noqa: E402
from core.events import AgentSession, UserInput  # noqa: E402
from core.providers.base import LLMResponse  # noqa: E402


class _CapturingProvider:
    """Records the messages of the last chat call, then finalizes."""

    def __init__(self) -> None:
        self.received_messages: list[dict[str, Any]] | None = None

    def get_default_model(self) -> str:
        return "gpt-5.4"

    async def chat_with_retry(self, **kwargs: Any) -> LLMResponse:
        self.received_messages = kwargs.get("messages")
        return LLMResponse(content="done", finish_reason="stop")


def _run(session: AgentSession, text: str) -> list:
    async def _collect() -> list:
        return [ev async for ev in session.run_stream(UserInput(text=text))]

    return asyncio.run(_collect())


def test_catalog_resolves_window_when_not_given():
    # gpt-5.4 → 400K from the seed catalog, no explicit override needed.
    session = AgentSession(
        _CapturingProvider(), ToolRegistry(), model="gpt-5.4", system_prompt="sys"
    )
    assert session._context_window_tokens == 400_000


def test_long_history_is_trimmed_before_the_model():
    provider = _CapturingProvider()
    # Small-but-positive window so budget = 8000 - 4096 (default max_out)
    # - 1024 ≈ 2880 tokens; a ~30K-token history must be snipped to fit.
    session = AgentSession(
        provider,
        ToolRegistry(),
        model="gpt-5.4",
        system_prompt="system directives",
        context_window_tokens=8_000,
    )
    big = "lorem ipsum dolor sit amet " * 250  # ~6.7K chars ≈ 1.7K tokens each
    history = []
    for i in range(20):
        role = "user" if i % 2 == 0 else "assistant"
        history.append({"role": role, "content": f"MSG{i} {big}"})
    session._history = history

    events = _run(session, "FINAL newest question")

    received = provider.received_messages
    assert received is not None
    # Trimmed: strictly fewer than the 20 preloaded + 1 new user message.
    assert len(received) < 21
    joined = " ".join(str(m.get("content")) for m in received)
    # Newest turn survives; oldest is dropped.
    assert "FINAL newest question" in joined
    assert "MSG0 " not in joined
    # The system prompt is always preserved at the head.
    assert any(m.get("role") == "system" for m in received)
    # And the run completes cleanly despite the compaction.
    assert events[-1].msg.type == "task_complete"
    assert events[-1].msg.stop_reason == "completed"


def test_short_history_is_untouched():
    provider = _CapturingProvider()
    session = AgentSession(
        provider, ToolRegistry(), model="gpt-5.4", context_window_tokens=8_000
    )
    _run(session, "just one short question")
    received = provider.received_messages
    assert received is not None
    # One user turn, nothing to trim.
    assert any("just one short question" in str(m.get("content")) for m in received)


# -- C4a: summarization-based compaction ------------------------------------

from core.agent_runtime.runner import (  # noqa: E402
    AgentRunner,
    AgentRunSpec,
    _SUMMARY_PREFIX,
)


def test_build_compacted_history_keeps_system_recent_users_and_summary():
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "u1"},
        {"role": "assistant", "content": "a1"},
        {"role": "tool", "content": "t1"},
        {"role": "user", "content": "u2"},
        {"role": "assistant", "content": "a2"},
    ]
    out = AgentRunner._build_compacted_history(messages, "THE SUMMARY")
    assert [m["role"] for m in out] == ["system", "user", "user", "user"]
    assert out[1]["content"] == "u1" and out[2]["content"] == "u2"
    assert out[-1]["content"] == f"{_SUMMARY_PREFIX}\nTHE SUMMARY"
    assert not any(m["content"] in ("a1", "a2", "t1") for m in out)  # dropped


class _CompactProvider:
    """First chat call returns a summary; subsequent calls return 'done'."""

    def __init__(self) -> None:
        self.calls = 0
        self.main_request: list[dict[str, Any]] | None = None

    def get_default_model(self) -> str:
        return "gpt-5.4"

    async def chat_with_retry(self, **kwargs: Any) -> LLMResponse:
        self.calls += 1
        if self.calls == 1:
            return LLMResponse(
                content="HANDOFF: did X, next do Y", finish_reason="stop"
            )
        self.main_request = kwargs.get("messages")
        return LLMResponse(content="done", finish_reason="stop")


def _big_history(n: int = 20) -> list[dict[str, Any]]:
    big = "lorem ipsum dolor sit amet " * 250
    return [
        {"role": "user" if i % 2 == 0 else "assistant", "content": f"MSG{i} {big}"}
        for i in range(n)
    ]


def _compact_spec(provider, messages, **kw):
    base = dict(
        initial_messages=messages,
        tools=ToolRegistry(),
        model="gpt-5.4",
        max_iterations=3,
        max_tool_result_chars=100_000,
        context_window_tokens=8_000,
    )
    base.update(kw)
    return AgentRunSpec(**base)


def test_over_budget_history_is_summarized_not_just_dropped():
    provider = _CompactProvider()
    messages = (
        [{"role": "system", "content": "sys"}]
        + _big_history()
        + [{"role": "user", "content": "FINAL question"}]
    )
    result = asyncio.run(AgentRunner(provider).run(_compact_spec(provider, messages)))
    assert provider.calls == 2  # summarization call, then the main call
    joined = " ".join(str(m.get("content")) for m in (provider.main_request or []))
    assert _SUMMARY_PREFIX in joined and "HANDOFF: did X" in joined
    assert "FINAL question" in joined
    assert "MSG0 " not in joined  # old turns replaced by the summary
    assert result.final_content == "done"


def test_under_budget_history_is_not_compacted():
    provider = _CompactProvider()
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "yo"},
        {"role": "user", "content": "more"},
        {"role": "assistant", "content": "ok"},
    ]
    spec = _compact_spec(provider, [], context_window_tokens=400_000)
    out = asyncio.run(AgentRunner(provider)._maybe_compact(spec, messages))
    assert out is messages and provider.calls == 0


def test_pre_compact_hook_can_skip_compaction():
    from core.harness.hooks.engine import ContextOutcome

    provider = _CompactProvider()
    messages = [{"role": "system", "content": "sys"}] + _big_history()

    async def block(trigger):
        return ContextOutcome(block=True, block_reason="skip")

    spec = _compact_spec(provider, [], pre_compact_hook=block)
    out = asyncio.run(AgentRunner(provider)._maybe_compact(spec, messages))
    assert out is messages and provider.calls == 0  # skipped, no summary call
