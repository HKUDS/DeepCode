"""Microcompact — unit tests for the clear policy, plus runner integration.

The unit tests exercise ``apply_microcompact`` directly: what is kept (the
recency window and the whole latest tool batch), what is cleared, and the
copy-on-write / media / pair-integrity invariants. The integration tests drive
``AgentRunner._maybe_compact`` end to end: with the switch on, the summarizer is
skipped and tool-call pairs stay intact; with it off, the history is unchanged.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import asyncio  # noqa: E402

from core.agent_runtime.microcompact import (  # noqa: E402
    MICROCOMPACT_PLACEHOLDER,
    MicrocompactPolicy,
    apply_microcompact,
    count_tool_results,
    estimate_tokens,
)


def _tool_result(tool_call_id: str, text: str) -> dict[str, Any]:
    return {"role": "tool", "tool_call_id": tool_call_id, "content": text}


def _assistant_calls(ids: list[str]) -> dict[str, Any]:
    return {
        "role": "assistant",
        "content": "",
        "tool_calls": [
            {
                "id": cid,
                "type": "function",
                "function": {"name": "read", "arguments": "{}"},
            }
            for cid in ids
        ],
    }


def _history(n_tool: int, chars: int = 4000) -> list[dict[str, Any]]:
    """A user turn then n_tool sequential call+result PAIRS (each its own
    assistant turn), so earlier results are stale and only the last is the
    'latest batch' the model has not read yet."""
    messages: list[dict[str, Any]] = [{"role": "user", "content": "go"}]
    for i in range(n_tool):
        messages.append(_assistant_calls([f"call_{i}"]))
        messages.append(_tool_result(f"call_{i}", "x" * chars))
    return messages


# -- policy validation -------------------------------------------------------


def test_policy_rejects_negative_knobs() -> None:
    for kwargs in (
        {"keep_recent_tool_results": -1},
        {"min_savings_tokens": -1.0},
    ):
        try:
            MicrocompactPolicy(**kwargs)
        except ValueError:
            pass
        else:
            raise AssertionError(f"expected ValueError for {kwargs}")


def test_placeholder_constant_is_stable() -> None:
    assert MICROCOMPACT_PLACEHOLDER == "[Old tool result content cleared]"


# -- what is kept / cleared --------------------------------------------------


def test_clears_the_stale_middle_but_keeps_the_recent_tail() -> None:
    # keep_recent=2 with 6 results: the last 2 stay, the first 4 clear.
    policy = MicrocompactPolicy(keep_recent_tool_results=2, min_savings_tokens=1.0)
    messages = _history(6)
    result = apply_microcompact(messages, policy)
    assert result.applied and result.cleared == 4
    contents = [m.get("content") for m in result.messages if m.get("role") == "tool"]
    assert contents[-2:] == ["x" * 4000, "x" * 4000]  # recent tail kept verbatim
    assert contents[:4] == [MICROCOMPACT_PLACEHOLDER] * 4  # stale middle cleared


def test_giant_parallel_batch_is_fully_protected() -> None:
    # The point-3 regression: 6 parallel calls in the LATEST batch exceed the
    # default keep_recent=5. Without latest-batch protection, the first of the
    # six would be cleared before the model read it. All six must survive.
    policy = MicrocompactPolicy(min_savings_tokens=1.0)  # keep_recent=5 default
    messages: list[dict[str, Any]] = [
        {"role": "user", "content": "go"},
        _assistant_calls(["old"]),
        _tool_result("old", "x" * 4000),  # stale, clearable
        _assistant_calls([f"b{j}" for j in range(6)]),  # latest batch of 6
    ]
    messages.extend(_tool_result(f"b{j}", f"batch result {j}") for j in range(6))
    result = apply_microcompact(messages, policy)
    assert result.cleared == 1  # only the stale "old" result
    batch = [
        m.get("content")
        for m in result.messages
        if str(m.get("tool_call_id", "")).startswith("b")
    ]
    assert batch == [f"batch result {j}" for j in range(6)]  # whole batch intact
    old = next(m for m in result.messages if m.get("tool_call_id") == "old")
    assert old.get("content") == MICROCOMPACT_PLACEHOLDER


def test_pair_integrity_and_roles_survive() -> None:
    policy = MicrocompactPolicy(keep_recent_tool_results=1, min_savings_tokens=1.0)
    messages = _history(3)
    result = apply_microcompact(messages, policy)
    # Same number of messages, same roles, same tool_call_ids — only content changed.
    assert [m.get("role") for m in result.messages] == [m.get("role") for m in messages]
    assert [
        m.get("tool_call_id") for m in result.messages if m.get("role") == "tool"
    ] == [m.get("tool_call_id") for m in messages if m.get("role") == "tool"]
    # Every result still pairs with the assistant that issued it.
    declared = {tc["id"] for m in result.messages for tc in (m.get("tool_calls") or [])}
    results = {
        m.get("tool_call_id") for m in result.messages if m.get("role") == "tool"
    }
    assert results == declared


def test_media_blocks_survive_verbatim() -> None:
    policy = MicrocompactPolicy(keep_recent_tool_results=0, min_savings_tokens=1.0)
    media = {
        "role": "tool",
        "tool_call_id": "c0",
        "content": [
            {"type": "text", "text": "caption"},
            {"type": "image", "source": {"data": "AAAA"}},
        ],
    }
    messages = [_assistant_calls(["c0"]), media]
    result = apply_microcompact(messages, policy)
    assert result.messages[1] is media  # untouched


def test_original_history_is_never_mutated_in_place() -> None:
    policy = MicrocompactPolicy(keep_recent_tool_results=0, min_savings_tokens=1.0)
    messages = _history(2)
    before = [dict(m) for m in messages]
    apply_microcompact(messages, policy)
    assert messages == before  # caller's list is byte-for-byte unchanged


def test_saved_output_path_line_is_preserved() -> None:
    # The point-4 regression: a result spilled to disk keeps its
    # "Full output saved to:" pointer even after the preview is cleared. The
    # spill is the STALE result; a newer call+result follows so it is not the
    # protected latest batch.
    policy = MicrocompactPolicy(keep_recent_tool_results=0, min_savings_tokens=1.0)
    spilled = (
        "[tool output persisted]\n"
        "Full output saved to: .deepcode/tool-results/abc.txt\n"
        "PREVIEW" + "x" * 400
    )
    messages = [
        _assistant_calls(["c0"]),
        _tool_result("c0", spilled),
        _assistant_calls(["c1"]),
        _tool_result("c1", "recent body"),
    ]
    result = apply_microcompact(messages, policy)
    content = result.messages[1].get("content")
    assert "Full output saved to: .deepcode/tool-results/abc.txt" in content
    assert "[tool output persisted]" in content
    assert "PREVIEW" not in content  # the bulky preview is gone
    assert MICROCOMPACT_PLACEHOLDER in content
    # Still opens with the persisted-result head, so the spill file keeps
    # being renewed (see renew_tool_result_references).
    assert content.startswith("[tool output persisted]\nFull output saved to: ")
    # The newest result (the model's pending batch) is untouched.
    assert result.messages[3].get("content") == "recent body"


def test_bash_spill_pointer_is_preserved() -> None:
    # The bash tool spills large output itself and writes the pointer mid-line:
    # "...output truncated (N chars). Full output saved to: <path>".
    policy = MicrocompactPolicy(keep_recent_tool_results=0, min_savings_tokens=1.0)
    for header in ("", "[exit 1]\n"):
        spilled = (
            f"{header}...output truncated (40001 chars). "
            "Full output saved to: /tmp/deepcode-bash-abc.txt\n\n" + "y" * 4000
        )
        messages = [
            _assistant_calls(["c0"]),
            _tool_result("c0", spilled),
            _assistant_calls(["c1"]),
            _tool_result("c1", "recent"),
        ]
        content = apply_microcompact(messages, policy).messages[1]["content"]
        assert "Full output saved to: /tmp/deepcode-bash-abc.txt" in content
        assert "y" * 100 not in content


def test_cleared_spill_keeps_its_file_renewed(tmp_path: Path) -> None:
    import os
    import time

    from core.agent_runtime.helpers import (
        maybe_persist_tool_result,
        renew_tool_result_references,
    )

    spilled = maybe_persist_tool_result(
        tmp_path, "sess", "c0", "Z" * 5000, max_chars=1000
    )
    messages = [
        _assistant_calls(["c0"]),
        _tool_result("c0", spilled),
        _assistant_calls(["c1"]),
        _tool_result("c1", "recent"),
    ]
    policy = MicrocompactPolicy(keep_recent_tool_results=0, min_savings_tokens=1.0)
    cleared = apply_microcompact(messages, policy).messages
    path = next((tmp_path / ".deepcode" / "tool-results" / "sess").iterdir())
    old = time.time() - 80 * 24 * 3600
    os.utime(path, (old, old))
    renew_tool_result_references(tmp_path, cleared)
    assert path.stat().st_mtime > old + 3600


def test_a_later_pass_counts_only_newly_cleared_results() -> None:
    policy = MicrocompactPolicy(keep_recent_tool_results=1, min_savings_tokens=1.0)
    first = apply_microcompact(_history(4), policy)
    assert first.cleared == 3
    grown = [
        *first.messages,
        _assistant_calls(["call_4"]),
        _tool_result("call_4", "x" * 4000),
    ]
    second = apply_microcompact(grown, policy)
    # Only call_3 aged out; the first three stay as they were.
    assert second.cleared == 1
    assert all(a is b for a, b in zip(second.messages[:7], first.messages[:7]))


def test_below_min_savings_is_a_no_op_and_a_second_pass_converges() -> None:
    # Tiny results: clearing saves less than the floor, so nothing happens.
    policy = MicrocompactPolicy(keep_recent_tool_results=0, min_savings_tokens=10_000.0)
    small = _history(2, chars=20)
    first = apply_microcompact(small, policy)
    assert not first.applied and first.messages == small

    # An aggressive pass over a big history converges: the second pass clears
    # nothing and reports no further saving.
    aggressive = MicrocompactPolicy(keep_recent_tool_results=1, min_savings_tokens=1.0)
    big = _history(4, chars=4000)
    once = apply_microcompact(big, aggressive)
    twice = apply_microcompact(once.messages, aggressive)
    assert once.applied and once.savings_tokens > 0
    assert not twice.applied and twice.savings_tokens == 0.0


def test_no_tool_results_is_a_no_op() -> None:
    messages = [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "yo"},
    ]
    result = apply_microcompact(messages, MicrocompactPolicy())
    assert not result.applied and result.messages == messages


def test_count_and_estimate_basics() -> None:
    assert count_tool_results(_history(3)) == 3
    assert estimate_tokens("") == 0.0
    assert estimate_tokens("x" * 8) == 2.0


# -- integration through AgentRunner._maybe_compact --------------------------
#
# The maintainer's acceptance bar: one test proves the summarizer is SKIPPED and
# tool-call pairs stay intact when the switch is on; one proves the history is
# UNCHANGED when it is off.

from core.agent_runtime.runner import AgentRunner, AgentRunSpec  # noqa: E402
from core.agent_runtime.tools.registry import ToolRegistry  # noqa: E402


class _CountingProvider:
    """Counts chat calls (the only thing the summarizer would make)."""

    def __init__(self) -> None:
        self.calls = 0

    def get_default_model(self) -> str:
        return "gpt-5.4"

    async def chat_with_retry(self, **kwargs: Any):  # pragma: no cover - not reached
        self.calls += 1
        raise AssertionError("the summarizer should not be called in these tests")


def _pressure_history(*, old: int = 6, batch: int = 6) -> list[dict[str, Any]]:
    """A history over the pressure trigger: `old` huge stale results + a latest
    parallel batch of `batch` small results the model has not read yet."""
    big = "lorem ipsum dolor sit amet " * 1500  # ~42K chars each
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "do the work"},
    ]
    for i in range(old):
        messages.append(_assistant_calls([f"old_{i}"]))
        messages.append(_tool_result(f"old_{i}", f"OLD{i} " + big))
    batch_ids = [f"b{j}" for j in range(batch)]
    messages.append(_assistant_calls(batch_ids))
    messages.extend(
        _tool_result(bid, f"BATCH{j} result body") for j, bid in enumerate(batch_ids)
    )
    return messages


def _spec(**kw: Any) -> AgentRunSpec:
    base: dict[str, Any] = dict(
        initial_messages=[],
        tools=ToolRegistry(),
        model="gpt-5.4",
        max_iterations=3,
        max_tool_result_chars=100_000,
        context_window_tokens=8_000,
        # Isolate microcompact: the middle-pruner must not get the credit.
        tool_result_pruner=None,
    )
    base.update(kw)
    return AgentRunSpec(**base)


def test_microcompact_on_skips_summarizer_and_keeps_tool_pairs() -> None:
    provider = _CountingProvider()
    messages = _pressure_history()
    spec = _spec(microcompact=MicrocompactPolicy(min_savings_tokens=1.0))
    out = asyncio.run(AgentRunner(provider)._maybe_compact(spec, messages))

    # The summarizer never ran — clearing the stale results alone relieved the
    # pressure the gate had detected.
    assert provider.calls == 0

    # The stale (already-read) results are replaced by the placeholder...
    old = [
        m.get("content")
        for m in out
        if str(m.get("tool_call_id", "")).startswith("old_")
    ]
    assert len(old) == 6 and all(c == MICROCOMPACT_PLACEHOLDER for c in old)
    # ...while the latest batch (the model is about to read it) survives whole.
    batch = [
        m.get("content") for m in out if str(m.get("tool_call_id", "")).startswith("b")
    ]
    assert batch == [f"BATCH{j} result body" for j in range(6)]

    # Tool-call pairs stay intact: every result still has its id and matches an
    # assistant tool_call — nothing dropped, nothing orphaned.
    declared = {tc["id"] for m in out for tc in (m.get("tool_calls") or [])}
    results = {m.get("tool_call_id") for m in out if m.get("role") == "tool"}
    assert results == declared


def test_microcompact_off_leaves_history_unchanged() -> None:
    from core.harness.hooks.engine import ContextOutcome

    provider = _CountingProvider()
    messages = _pressure_history()

    # Block the summarizer so the ONLY thing that could mutate history is the
    # microcompact rung (which runs before the PreCompact hook). With the
    # switch off it must not fire, so the history comes back byte-for-byte.
    async def _block(trigger: Any) -> ContextOutcome:
        return ContextOutcome(block=True, block_reason="skip")

    spec = _spec(microcompact=None, pre_compact_hook=_block)
    out = asyncio.run(AgentRunner(provider)._maybe_compact(spec, messages))

    assert out is messages  # unchanged, same object
    assert not any(m.get("content") == MICROCOMPACT_PLACEHOLDER for m in out)
    assert provider.calls == 0
