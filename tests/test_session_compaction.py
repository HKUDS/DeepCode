"""P2g: AgentSession arms the runner's context-compaction ladder.

The runner has always had ``_snip_history`` (trim history to a token budget)
and a pressure-gated prune/summarize pass, but they are dormant unless
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
    # Newest turn survives; the old turns are replaced by the summary. The
    # checkpoint carries the user's NEWEST earlier wording, so the oldest turn
    # (MSG0) is genuinely gone rather than resurrected.
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

from core.agent_runtime.compaction import (  # noqa: E402
    EARLIER_USER_CHARS as _EARLIER_USER_CHARS,
    EARLIER_USER_LABEL as _EARLIER_USER_LABEL,
    SUMMARY_PREFIX as _SUMMARY_PREFIX,
)
from core.agent_runtime.runner import (  # noqa: E402
    AgentRunner,
    AgentRunSpec,
)


def test_build_compacted_history_keeps_recent_tail_and_checkpoint():
    # The tool pair belongs to the NEWEST turn: that is what "retain a
    # tool-bearing tail" has to mean. A pair from an older turn is exactly
    # what the checkpoint is supposed to replace.
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "u1"},
        # Bulky on purpose: the block may only use the space the summary frees,
        # so a toy history would legitimately carry nothing.
        {"role": "assistant", "content": "a1 " + "x" * 3_000},
        {"role": "user", "content": "u2"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"id": "c1", "function": {"name": "read"}}],
        },
        {"role": "tool", "content": "t1", "tool_call_id": "c1"},
        {"role": "assistant", "content": "a2"},
    ]
    out = AgentRunner._build_compacted_history(messages, "THE SUMMARY")
    assert out[0]["role"] == "system"
    # The dropped turn's user wording (u1) survives verbatim inside the
    # checkpoint; u2 is already in the kept tail, so it is not repeated.
    assert out[1]["content"] == (
        f"{_SUMMARY_PREFIX}\nTHE SUMMARY\n\n{_EARLIER_USER_LABEL}\nu1"
    )
    assert out[1].get("compaction", {}).get("reset") is True
    # The newest user turn and everything it produced survive verbatim.
    assert [m.get("role") for m in out[2:]] == [
        "user",
        "assistant",
        "tool",
        "assistant",
    ]
    assert any(m.get("content") == "a2" for m in out)
    assert any(m.get("role") == "tool" for m in out)
    # The older turn is what the checkpoint stands in for.
    assert not any(m.get("content") == "u1" for m in out)
    assert not any("x" * 3_000 in str(m.get("content") or "") for m in out)


def test_earlier_user_messages_are_newest_first_and_skip_noise():
    """Newest first, and never the environment slot or a checkpoint."""
    from core.agent_runtime.compaction import _earlier_user_messages

    non_system = [
        {"role": "user", "content": "<environment_context/>", "env_context": True},
        {"role": "user", "content": "u1 the first ask"},
        {"role": "assistant", "content": "a1"},
        {"role": "user", "content": "u2"},
        {"role": "assistant", "content": "a2"},
        {
            "role": "user",
            "content": "summary of everything",
            "compaction": {"reset": True, "retain": 0},
        },
        {"role": "assistant", "content": "a3"},
        {"role": "user", "content": "u3 the current ask"},
    ]
    assert _earlier_user_messages(non_system) == [
        "u3 the current ask",
        "u2",
        "u1 the first ask",
    ]


def test_earlier_user_messages_stop_at_the_budget():
    """A budget keeps the newest wording and refuses to grow with history."""
    from core.agent_runtime.compaction import _earlier_user_messages

    non_system = [{"role": "user", "content": f"m{i} " + "x" * 187} for i in range(6)]
    picked = _earlier_user_messages(non_system, budget=400)
    assert len(picked) == 2, picked  # 190 chars each + a 2-char separator
    assert picked[0].startswith("m5")  # newest first
    assert picked[1].startswith("m4")


def test_checkpoint_carries_the_users_earlier_wording():
    """The user's own words survive compaction, under a neutral heading."""
    ask = "please refactor the widget pipeline end to end"
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": ask},
        # Bulky on purpose: the block may only use the space the summary frees.
        {"role": "assistant", "content": "a1 " + "x" * 3_000},
        {"role": "user", "content": "u2"},
        {"role": "assistant", "content": "a2"},
    ]
    out = AgentRunner._build_compacted_history(messages, "THE SUMMARY")
    assert out[1]["content"] == (
        f"{_SUMMARY_PREFIX}\nTHE SUMMARY\n\n{_EARLIER_USER_LABEL}\n{ask}"
    )
    # Not called "the original task": that label pulls the model back to old work.
    assert "ORIGINAL TASK" not in out[1]["content"]
    assert out[1]["compaction"] == {"reset": True, "retain": 2, "user_words": [ask]}


def test_checkpoint_never_carries_more_than_the_earlier_user_budget():
    """A huge first message cannot swamp the checkpoint."""
    ask = "T" * 70_000
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": ask},
        {"role": "assistant", "content": "a1"},
        {"role": "user", "content": "u2"},
        {"role": "assistant", "content": "a2"},
    ]
    out = AgentRunner._build_compacted_history(messages, "THE SUMMARY")
    content = out[1]["content"]
    assert len(content) < _EARLIER_USER_CHARS + 1_000
    assert ask not in content


def test_recompaction_keeps_carrying_the_user_wording():
    """A checkpoint's own block is re-read, not lost on the next round."""
    from core.agent_runtime.compaction import _earlier_user_messages

    ask = "please refactor the widget pipeline end to end"
    checkpoint = {
        "role": "user",
        "content": (f"{_SUMMARY_PREFIX}\nSUMMARY 1\n\n{_EARLIER_USER_LABEL}\n{ask}"),
        "compaction": {"reset": True, "retain": 0, "user_words": [ask]},
    }
    assert _earlier_user_messages([checkpoint]) == [ask]


def test_recompaction_carries_the_words_verbatim_from_data():
    """Indented code survives a second round: quotes are data, not parsed text.

    And a summary that echoes the heading is never read back as the user's.
    """
    from core.agent_runtime.compaction import _earlier_user_messages

    code = "def f(x):\n    y = x + 1\n\n    return y"
    checkpoint = {
        "role": "user",
        "content": (
            f"{_SUMMARY_PREFIX}\n{_EARLIER_USER_LABEL}\nNext: the summarizer said this"
        ),
        "compaction": {"reset": True, "retain": 0, "user_words": [code]},
    }
    assert _earlier_user_messages([checkpoint]) == [code]
    echoed = {**checkpoint, "compaction": {"reset": True, "retain": 0}}
    assert _earlier_user_messages([echoed]) == []


def test_a_cut_quote_says_so_and_a_tight_budget_carries_nothing():
    """A fragment can reverse a request; a shortened quote is marked as cut."""
    from core.agent_runtime.compaction import _earlier_user_messages

    ask = "Delete the old migration files only after I confirm the backup worked."
    assert _earlier_user_messages([{"role": "user", "content": ask}], budget=40) == []
    long_ask = "Refactor the parser. " * 100
    picked = _earlier_user_messages([{"role": "user", "content": long_ask}], budget=600)
    assert len(picked) == 1
    assert picked[0].endswith("[… rest cut]")
    assert len(picked[0]) <= 600


def test_carried_words_are_shortened_rather_than_lost():
    """With little room, the carried request is cut and marked, not dropped."""
    from core.agent_runtime.compaction import _earlier_user_messages

    request = "Port the importer to the streaming API. " * 80
    checkpoint = {
        "role": "user",
        "content": f"{_SUMMARY_PREFIX}\nS",
        "compaction": {"reset": True, "retain": 0, "user_words": [request]},
    }
    picked = _earlier_user_messages([checkpoint], budget=2_000)
    assert len(picked) == 1 and picked[0].endswith("[… rest cut]")


def test_separators_count_against_the_budget():
    from core.agent_runtime.compaction import _earlier_user_messages

    non_system = [{"role": "user", "content": f"m{i:03d}"} for i in range(600)]
    picked = _earlier_user_messages(non_system, budget=1_000)
    assert len("\n\n".join(picked)) <= 1_000


def test_the_block_leaves_the_room_the_caller_reserves():
    """The PreCompact hook's re-injected context is not squeezed out by quotes."""
    from core.agent_runtime.compaction import (
        TailRetainingCompactionStrategy,
        message_chars,
    )

    messages: list[dict[str, Any]] = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "keep the public API " * 200},
    ]
    for index in range(3):
        messages += _tool_round(index, size=2_000)
    messages += [
        {"role": "user", "content": "latest"},
        {"role": "assistant", "content": "ok"},
    ]
    strategy = TailRetainingCompactionStrategy()
    before = sum(message_chars(m) for m in messages)
    for reserve in (0, 3_000, 6_000):
        out = strategy.build_history(
            messages, "S", context_window_tokens=65_536, reserve_chars=reserve
        )
        after = sum(message_chars(m) for m in out)
        assert "user_words" in out[1]["compaction"]
        assert before - after - 1 >= reserve


def test_earlier_user_messages_skip_what_the_runtime_wrote():
    """Runner notes and Goal prompts ride user-role messages; nobody said them."""
    from core.agent_runtime.compaction import _earlier_user_messages

    non_system = [
        {"role": "user", "content": "REAL: keep the public API unchanged"},
        {"role": "assistant", "content": "a1"},
        {
            "role": "user",
            "content": "Continue working toward the Goal.",
            "context_note": "goal_continuation",
        },
        {
            "role": "user",
            "content": "You called bash with the same arguments 3 times.",
            "context_note": "repeat_guard",
        },
        {
            "role": "user",
            "content": "[PreCompact checkpoint]\nhook context",
            "context_note": "precompact_checkpoint",
        },
        # A Steer is the user's own words, typed mid-Turn: it is not marked.
        {"role": "user", "content": "STEER: also add --strict"},
    ]
    assert _earlier_user_messages(non_system) == [
        "STEER: also add --strict",
        "REAL: keep the public API unchanged",
    ]


def _tool_round(index: int, size: int = 6_000) -> list[dict[str, Any]]:
    call = {
        "id": f"c{index}",
        "type": "function",
        "function": {"name": "bash", "arguments": "{}"},
    }
    return [
        {"role": "assistant", "content": "", "tool_calls": [call]},
        {
            "role": "tool",
            "tool_call_id": f"c{index}",
            "name": "bash",
            "content": "x" * size,
        },
    ]


def test_checkpoint_does_not_requote_the_retained_tail():
    """The tail keeps its user message verbatim; the budget goes to older words.

    The newest user message always survives in the tail. Letting it into the
    budget first crowded the older request out, and when it was longer than
    the budget a truncated copy of it was quoted on top of the full one.
    """
    from core.agent_runtime.compaction import TailRetainingCompactionStrategy

    ask = "REAL-REQUEST: build the parser, keep the public API unchanged."
    latest = "LATEST " + "y" * (_EARLIER_USER_CHARS + 1_000)
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": ask},
    ]
    for index in range(8):
        messages += _tool_round(index)
    messages += [
        {"role": "user", "content": latest + "\n"},
        {"role": "assistant", "content": "working on it"},
    ]
    out = TailRetainingCompactionStrategy().build_history(
        messages, "THE SUMMARY", context_window_tokens=65_536
    )
    content = out[1]["content"]
    assert ask in content
    assert "LATEST" not in content
    assert out[-2]["content"] == latest + "\n"


def test_runner_notes_are_marked_and_a_steer_is_not():
    """What the runner writes into the history says so; a Steer is the user's."""
    from types import SimpleNamespace

    from core.agent_runtime.helpers import CONTEXT_NOTE_KEY

    spec = SimpleNamespace(history_sink=None, context_note_sink=None)
    messages: list[dict[str, Any]] = []
    reminder = {"role": "user", "content": "you repeated yourself"}
    AgentRunner._enter_noted(spec, messages, reminder, "repeat_guard")
    AgentRunner._enter_noted(
        spec,
        messages,
        {"role": "user", "content": "my words"},
        "steer",
        message_id="m1",
    )
    assert messages[0][CONTEXT_NOTE_KEY] == "repeat_guard"
    assert CONTEXT_NOTE_KEY not in reminder  # the caller's dict is left alone
    assert CONTEXT_NOTE_KEY not in messages[1]


def test_a_goal_prompt_turn_is_marked_in_the_live_history():
    """A Turn whose prompt the runtime wrote carries the mark into history."""

    async def scenario() -> None:
        session = AgentSession(
            _TurnOrSummaryProvider(), ToolRegistry(), model="fake-model"
        )
        await session.submit(UserInput(text="REAL ask"))
        session.drain_events()
        await session.submit(
            UserInput(text="Continue the Goal.", context_note="goal_continuation")
        )
        session.drain_events()
        users = [m for m in session.history if m.get("role") == "user"]
        assert [m.get("context_note") for m in users] == [None, "goal_continuation"]

    asyncio.run(scenario())


class _TurnOrSummaryProvider:
    """Real turns answer 'ok'; the summarization request gets a handoff."""

    def __init__(self) -> None:
        self.turns = 0
        self.summaries = 0

    def get_default_model(self) -> str:
        return "fake-model"

    async def chat_with_retry(self, **kwargs: Any) -> LLMResponse:
        messages = kwargs.get("messages") or []
        joined = " ".join(str(m.get("content") or "") for m in messages)
        if "CONTEXT CHECKPOINT COMPACTION" in joined:
            self.summaries += 1
            return LLMResponse(content="HANDOFF: did X", finish_reason="stop")
        self.turns += 1
        return LLMResponse(content="ok", finish_reason="stop")


def test_compaction_in_a_workspace_session_skips_the_environment_slot(tmp_path):
    """The app path: a workspace prepends an env slot that must not be carried.

    This is the case the unit-level shapes miss: in a real session every history
    starts with ``{"role": "user", "env_context": True}``, so "the first user
    message" is the environment, not the request. It also has to stay bounded:
    a large first message must not ride into every later checkpoint.
    """

    async def scenario() -> None:
        workspace = tmp_path / "repo"
        workspace.mkdir()
        provider = _TurnOrSummaryProvider()
        session = AgentSession(
            provider, ToolRegistry(), model="fake-model", workspace=workspace
        )
        task = (
            "REFACTOR-THE-PIPELINE: stream it, keep bytes identical " + "-detail" * 900
        )
        await session.submit(UserInput(text=task))
        session.drain_events()
        for i in range(5):
            await session.submit(UserInput(text=f"follow-up {i}: " + "y" * 300))
            session.drain_events()

        # Sanity: the workspace put an environment slot at the head of history.
        assert session.history[0].get("env_context") is True

        await session.compact()

        checkpoint = next(m for m in session.history if m.get("compaction"))
        content = str(checkpoint.get("content") or "")
        assert provider.summaries == 1
        assert "environment_context" not in content
        assert "ORIGINAL TASK" not in content
        assert len(content) < _EARLIER_USER_CHARS + 2_000
        # The user's own recent wording is what the block carries.
        assert "follow-up" in content

    asyncio.run(scenario())


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
                content="HANDOFF: did X, next do Y",
                finish_reason="stop",
                usage={
                    "prompt_tokens": 100,
                    "completion_tokens": 10,
                    "total_tokens": 110,
                },
            )
        self.main_request = kwargs.get("messages")
        return LLMResponse(
            content="done",
            finish_reason="stop",
            usage={
                "prompt_tokens": 50,
                "completion_tokens": 5,
                "total_tokens": 55,
            },
        )


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
    assert "MSG1 " not in joined
    assert result.final_content == "done"
    assert result.usage == {
        "prompt_tokens": 150,
        "completion_tokens": 15,
        "total_tokens": 165,
    }


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
