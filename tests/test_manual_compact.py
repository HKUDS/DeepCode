"""Manual `/compact` — on-demand summarization of resident context.

The contract under test, borrowed from dsh's `/compact` command:

- works even below automatic pressure (no budget gate), but never on a
  conversation too short to be worth a model round-trip;
- every failure is a stable, human-readable reason — the conversation is
  reported unchanged, never half-compacted;
- it rewrites the RESIDENT model context only; canonical Session data is
  untouched (the `/clear` contract, applied to a gentler operation);
- a busy Session (active Turn) refuses instead of racing the runner.
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
from core.agent_runtime.tools.registry import ToolRegistry
from core.providers.base import LLMResponse


class _SummarizingProvider:
    """Returns a canned summary for the summarization call."""

    def __init__(self, summary: str | None = "the work so far, condensed"):
        self._summary = summary
        self.calls = 0

    def get_default_model(self) -> str:
        return "fake-model"

    async def chat_with_retry(self, **kwargs: Any) -> LLMResponse:
        self.calls += 1
        return LLMResponse(content=self._summary or "", finish_reason="stop")


def _spec(provider: Any) -> AgentRunSpec:
    return AgentRunSpec(
        initial_messages=[],
        tools=ToolRegistry(),
        model="fake-model",
        max_iterations=1,
        max_tool_result_chars=10_000,
    )


def _long_history(turns: int = 6) -> list[dict[str, str]]:
    history: list[dict[str, str]] = []
    for i in range(turns):
        history.append({"role": "user", "content": f"question {i} " + "x" * 200})
        history.append({"role": "assistant", "content": f"answer {i} " + "y" * 200})
    return history


def test_compact_history_replaces_old_turns_with_a_summary() -> None:
    provider = _SummarizingProvider()
    runner = AgentRunner(provider)
    before = _long_history()
    compacted, reason = asyncio.run(runner.compact_history(_spec(provider), before))

    assert reason == "compacted"
    assert compacted is not None and len(compacted) < len(before)
    assert provider.calls == 1
    # The summary rides the final message; recent user input survives verbatim.
    assert "condensed" in compacted[-1]["content"]
    assert any("question 5" in m.get("content", "") for m in compacted)


def test_too_short_history_is_refused_without_a_model_call() -> None:
    provider = _SummarizingProvider()
    runner = AgentRunner(provider)
    short = [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello"},
    ]
    compacted, reason = asyncio.run(runner.compact_history(_spec(provider), short))
    assert compacted is None
    assert reason == "No compactable history yet."
    assert provider.calls == 0


def test_failed_summary_leaves_the_conversation_unchanged() -> None:
    provider = _SummarizingProvider(summary=None)
    runner = AgentRunner(provider)
    compacted, reason = asyncio.run(
        runner.compact_history(_spec(provider), _long_history())
    )
    assert compacted is None
    assert "could not produce a useful summary" in reason


def test_agent_session_compact_rewrites_resident_history() -> None:
    from core.events.session import AgentSession

    provider = _SummarizingProvider()
    session = AgentSession(provider, ToolRegistry(), model="fake-model")
    session.load_history(_long_history())

    report = asyncio.run(session.compact())

    assert report["messages_after"] < report["messages_before"]
    assert report["replaced_messages"] > 0
    assert len(session.history) == report["messages_after"]
    assert "condensed" in session.history[-1]["content"]


def test_agent_session_compact_refuses_while_a_turn_is_active() -> None:
    from core.events.session import AgentSession

    async def scenario() -> None:
        provider = _SummarizingProvider()
        session = AgentSession(provider, ToolRegistry(), model="fake-model")
        session.load_history(_long_history())
        # Simulate an in-flight Turn the way the kernel tracks one.
        session._current_task = asyncio.ensure_future(asyncio.sleep(30))
        try:
            with pytest.raises(RuntimeError, match="while a Turn is active"):
                await session.compact()
        finally:
            session._current_task.cancel()

    asyncio.run(scenario())


def test_registry_refuses_without_a_resident_runtime(tmp_path: Path) -> None:
    from core.application.errors import ConflictError
    from core.application.session_runtime import SessionRuntimeRegistry
    from core.sessions.store import SessionStore

    store = SessionStore(tmp_path / "sessions")
    session = store.create_session(title="t")
    registry = SessionRuntimeRegistry(store, factory=object())  # type: ignore[arg-type]

    with pytest.raises(ConflictError, match="No resident context"):
        asyncio.run(registry.compact_live_history(session.session_id))


def test_summary_that_grows_the_conversation_is_rejected() -> None:
    """Shrinkage is judged by volume, not message count — observed live: a
    short conversation 'compacted' 4 → 3 messages while GAINING 1,347 chars."""
    provider = _SummarizingProvider(summary="an extremely verbose " + "z" * 2000)
    runner = AgentRunner(provider)
    short_turns = [
        {"role": "user", "content": "q1"},
        {"role": "assistant", "content": "a1"},
        {"role": "user", "content": "q2"},
        {"role": "assistant", "content": "a2"},
    ]
    compacted, reason = asyncio.run(
        runner.compact_history(_spec(provider), short_turns)
    )
    assert compacted is None
    assert "would not shrink" in reason
