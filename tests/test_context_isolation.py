"""Copy-on-handoff invariants for parent -> sub-agent and spec -> run history.

Context isolation is structural: every seam that hands conversation history
from one owner to another — parent history to a forked seed, a finished turn
to the seed write-back, the run spec to the run's working message list — must
copy, so the two sides never share message dicts and a mutation on one side
cannot reach the other. A fork that leaks the parent's conversation, or a
child's edits that corrupt the parent's, is exactly the failure these copies
prevent, and a regression here is invisible to any single-owner test.

Message *filtering* (which parent turns a fork keeps) is pinned separately in
``tests/test_spawn_agent.py::test_fork_history_filter`` and deliberately not
re-tested here; these tests assert identity-level independence only.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.agent_runtime.hook import AgentHook, AgentHookContext
from core.agent_runtime.runner import AgentRunner, AgentRunSpec
from core.agent_runtime.tools.registry import ToolRegistry
from core.harness.agents.control import AgentControl
from core.providers.base import LLMResponse


class _HeldControl(AgentControl):
    """Sub-agents block on ``release`` so the seed can be inspected intact."""

    def __init__(self, workspace, **kw):
        super().__init__(workspace, **kw)
        self.release = asyncio.Event()

    async def _run_subagent(self, sub, workspace):
        await self.release.wait()
        return f"did: {sub.task}"


class _FakeChildSession:
    """Session double driving the real single-turn seam (``_run_subagent``).

    Mirrors ``core.events.session.Session`` where it matters for isolation:
    ``load_history`` copies, and ``history`` stays one list object a caller
    can hold and mutate after the turn.
    """

    def __init__(self) -> None:
        self.history: list[dict[str, Any]] = []
        self.closed = False

    def load_history(self, messages: list[dict[str, Any]]) -> None:
        self.history = [dict(m) for m in messages]

    async def run_stream(self, user_input: Any):
        self.history.append({"role": "user", "content": user_input.text})
        self.history.append({"role": "assistant", "content": "child answer"})
        yield SimpleNamespace(
            msg=SimpleNamespace(type="task_complete", final_text="child answer")
        )

    async def aclose(self) -> None:
        self.closed = True


class _ScriptedProvider:
    """Provider returning a fixed response list (repeating the last one)."""

    def __init__(self, responses: list[LLMResponse]):
        self.responses = list(responses)
        self.calls = 0

    def get_default_model(self) -> str:
        return "fake-model"

    async def chat_with_retry(self, **kwargs: Any) -> LLMResponse:
        index = min(self.calls, len(self.responses) - 1)
        self.calls += 1
        return self.responses[index]


class _SpecListPoker(AgentHook):
    """Mutates the spec's message list mid-run, after the run-start copy."""

    def __init__(self, spec_list: list[dict[str, Any]]):
        super().__init__()
        self._spec_list = spec_list
        self.pokes = 0

    async def on_model_response(self, context: AgentHookContext) -> None:
        self.pokes += 1
        self._spec_list.append({"role": "user", "content": "SENTINEL-MIDRUN"})


def test_fork_seed_is_independent_of_parent_history(tmp_path: Path) -> None:
    async def scenario():
        ctrl = _HeldControl(str(tmp_path))
        history = [
            {"role": "user", "content": "u1"},
            {"role": "assistant", "content": "a1"},
        ]
        ctrl.set_history_provider(lambda: history)
        aid = ctrl.spawn("t", isolate=False, fork_turns="all")

        seed = ctrl.get(aid).seed_history
        assert seed == history  # same values...
        assert seed is not history  # ...independent containers...
        assert all(a is not b for a, b in zip(seed, history))  # ...and dicts

        history[0]["content"] = "mutated-parent"
        assert seed[0]["content"] == "u1"
        seed[1]["content"] = "mutated-child"
        assert history[1]["content"] == "a1"

        ctrl.release.set()
        await asyncio.wait_for(ctrl.get(aid).settled.wait(), 5.0)

    asyncio.run(scenario())


def test_default_spawn_forks_no_parent_context(tmp_path: Path) -> None:
    async def scenario():
        ctrl = _HeldControl(str(tmp_path))
        ctrl.set_history_provider(
            lambda: [{"role": "user", "content": "secret parent context"}]
        )
        aid = ctrl.spawn("t", isolate=False)  # fork_turns defaults to "none"

        assert ctrl.get(aid).seed_history == []

        ctrl.release.set()
        await asyncio.wait_for(ctrl.get(aid).settled.wait(), 5.0)

    asyncio.run(scenario())


def test_turn_write_back_is_independent_of_the_child_session(
    tmp_path: Path, monkeypatch
) -> None:
    session = _FakeChildSession()
    monkeypatch.setattr(
        "core.agent_setup.build_agent_session",
        lambda **kwargs: (session, None, None),
    )

    async def scenario():
        ctrl = AgentControl(str(tmp_path))
        ctrl.set_history_provider(
            lambda: [
                {"role": "user", "content": "u1"},
                {"role": "assistant", "content": "a1"},
            ]
        )
        aid = ctrl.spawn("t", isolate=False, fork_turns="all")
        sub = ctrl.get(aid)
        await asyncio.wait_for(sub.settled.wait(), 5.0)

        # The write-back handoff kept the values...
        assert [m["content"] for m in sub.seed_history] == [
            "u1",
            "a1",
            "t",
            "child answer",
        ]
        # ...but copied: neither list nor dicts are shared with the session.
        assert sub.seed_history is not session.history
        assert all(a is not b for a, b in zip(sub.seed_history, session.history))

        session.history[0]["content"] = "session-side edit"
        assert sub.seed_history[0]["content"] == "u1"
        sub.seed_history[-1]["content"] = "seed-side edit"
        assert session.history[-1]["content"] == "child answer"

        await ctrl.close()

    asyncio.run(scenario())


def test_run_history_is_independent_of_the_spec_list(tmp_path: Path) -> None:
    provider = _ScriptedProvider(
        [LLMResponse(content="final answer", finish_reason="stop")]
    )
    spec_list: list[dict[str, Any]] = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "go"},
    ]
    poker = _SpecListPoker(spec_list)
    reported: list[list[dict[str, Any]]] = []
    spec = AgentRunSpec(
        initial_messages=spec_list,
        tools=ToolRegistry(),
        model=provider.get_default_model(),
        max_iterations=5,
        max_tool_result_chars=10_000,
        workspace=tmp_path,
        history_sink=lambda messages: reported.append([dict(m) for m in messages]),
        hook=poker,
    )

    result = asyncio.run(AgentRunner(provider).run(spec))

    assert result.stop_reason == "completed"
    assert result.final_content == "final answer"
    # The mid-run mutation really landed on the spec list (non-vacuous probe).
    assert poker.pokes == 1
    assert any(m.get("content") == "SENTINEL-MIDRUN" for m in spec_list)
    run_history = reported[-1]
    # The run worked on its own copy: the spec-list mutation never reached it...
    assert not any(m.get("content") == "SENTINEL-MIDRUN" for m in run_history)
    # ...and the run's own appends never leaked back into the spec list.
    assert any(
        m.get("role") == "assistant" and m.get("content") == "final answer"
        for m in run_history
    )
    assert not any(m.get("role") == "assistant" for m in spec_list)
