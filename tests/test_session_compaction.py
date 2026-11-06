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
    # Newest turn survives. The old turns are replaced by the summary — but
    # the original task (MSG0) survives, embedded verbatim in the checkpoint;
    # MSG1 is the oldest turn that is genuinely gone.
    assert "FINAL newest question" in joined
    assert "MSG0 " in joined
    assert "MSG1 " not in joined
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
    COMPACT_KEEP_USER_CHARS as _COMPACT_KEEP_USER_CHARS,
    SUMMARY_PREFIX as _SUMMARY_PREFIX,
    TASK_BLOCK_LABEL as _TASK_BLOCK_LABEL,
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
        {"role": "assistant", "content": "a1"},
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
    # The dropped turn's task (u1) survives verbatim inside the checkpoint.
    assert (
        out[1]["content"]
        == f"{_SUMMARY_PREFIX}\nTHE SUMMARY\n\n{_TASK_BLOCK_LABEL}\nu1"
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
    assert not any(m.get("content") in {"u1", "a1"} for m in out)


def test_checkpoint_embeds_original_task_verbatim():
    # The task is ground truth: it survives compaction byte-for-byte, in a
    # labeled block appended after the summary.
    task = "please refactor the widget pipeline end to end"
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": task},
        {"role": "assistant", "content": "a1"},
        {"role": "user", "content": "u2"},
        {"role": "assistant", "content": "a2"},
    ]
    out = AgentRunner._build_compacted_history(messages, "THE SUMMARY")
    assert (
        out[1]["content"]
        == f"{_SUMMARY_PREFIX}\nTHE SUMMARY\n\n{_TASK_BLOCK_LABEL}\n{task}"
    )
    assert out[1]["compaction"] == {"reset": True, "retain": 3}


def test_checkpoint_task_block_is_capped_at_the_keep_limit():
    task = "T" * 70_000
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": task},
        {"role": "assistant", "content": "a1"},
        {"role": "user", "content": "u2"},
        {"role": "assistant", "content": "a2"},
    ]
    out = AgentRunner._build_compacted_history(messages, "THE SUMMARY")
    content = out[1]["content"]
    assert content.endswith("\n[original task truncated]")
    assert task[:_COMPACT_KEEP_USER_CHARS] in content
    assert f"{_TASK_BLOCK_LABEL}\n{task}" not in content


def test_recompaction_keeps_the_original_task_block():
    task = "please refactor the widget pipeline end to end"
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": task},
        {"role": "assistant", "content": "a1"},
        {"role": "user", "content": "u2"},
        {"role": "assistant", "content": "a2"},
    ]
    once = AgentRunner._build_compacted_history(messages, "THE SUMMARY")
    twice = AgentRunner._build_compacted_history(once, "THE SUMMARY 2")
    assert twice[1]["content"].startswith(f"{_SUMMARY_PREFIX}\nTHE SUMMARY 2")
    assert f"{_TASK_BLOCK_LABEL}\n{task}" in twice[1]["content"]


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
    assert "MSG0 " in joined  # the original task survives in the checkpoint
    assert "MSG1 " not in joined  # old turns replaced by the summary
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
