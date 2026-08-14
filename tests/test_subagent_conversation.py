"""Conversational sub-agents: soft interrupt, follow-ups, transcripts.

The dsh continuable-subagent shape under test, in DeepCode form:

- ``interrupt_agent`` stops only the CURRENT turn (dsh's keepInbox): the
  child parks idle with its conversation intact, and ``send_message`` gives
  it a new direction;
- a finished turn also parks idle — a follow-up continues the SAME
  conversation (the previous turns ride ``seed_history`` into the next);
- the parent's Turn teardown (``cancel_running``) remains a hard stop;
- every turn hands the host's transcript sink the child's full message
  list, and the application sink writes it under the parent Session's own
  directory as a self-describing JSONL snapshot.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.harness.agents.control import AgentControl


class _TurnScript(AgentControl):
    """Each call to the single-turn seam consumes one scripted behavior."""

    def __init__(self, workspace, script, **kw):
        super().__init__(workspace, **kw)
        self.script = list(script)
        self.turn_inputs: list[str] = []
        self.seeds: list[list] = []
        # Set when a turn coroutine actually STARTS executing. Interrupting
        # before that point cancels a never-run task — legal, but it would
        # leave this stub's scripted behavior unconsumed.
        self.turn_started = asyncio.Event()

    async def _run_subagent(self, sub, workspace):
        self.turn_started.set()
        self.turn_inputs.append(sub.task)
        self.seeds.append(list(sub.seed_history))
        behavior = self.script.pop(0)
        if behavior == "hang":
            await asyncio.Event().wait()
        if isinstance(behavior, Exception):
            raise behavior
        # Simulate the real seam's contract: the finished conversation is
        # carried forward for the next turn.
        sub.seed_history = list(sub.seed_history) + [
            {"role": "user", "content": sub.task},
            {"role": "assistant", "content": behavior},
        ]
        return behavior


def test_soft_interrupt_parks_idle_and_follow_up_redirects(tmp_path) -> None:
    async def scenario():
        ctrl = _TurnScript(str(tmp_path), ["hang", "took the new direction"])
        aid = ctrl.spawn("explore approach A", name="x", isolate=False)
        sub = ctrl.get(aid)
        await ctrl.turn_started.wait()  # the first turn is really executing

        assert "parks idle" in ctrl.interrupt(aid)
        await sub.settled.wait()
        first_status = sub.status
        first_result = sub.result

        sub.settled.clear()
        reply = ctrl.send_message(aid, "switch to approach B")
        await sub.settled.wait()  # second turn ran to completion
        snapshot = (
            first_status,
            first_result,
            reply,
            sub.status,
            sub.result,
            sub.completed_turns,
            list(ctrl.turn_inputs),
        )
        await ctrl.cancel_running()
        return snapshot

    (
        first_status,
        first_result,
        reply,
        status,
        result,
        completed,
        inputs,
    ) = asyncio.run(scenario())
    assert first_status == "idle"
    assert "interrupted" in first_result
    assert "resumes" in reply
    assert status == "idle" and result == "took the new direction"
    assert completed == 1  # the interrupted turn never counted
    assert inputs == ["explore approach A", "switch to approach B"]


def test_follow_up_continues_the_same_conversation(tmp_path) -> None:
    async def scenario():
        ctrl = _TurnScript(str(tmp_path), ["first answer", "second answer"])
        aid = ctrl.spawn("do the task", name="x", isolate=False)
        sub = ctrl.get(aid)
        await sub.settled.wait()
        sub.settled.clear()
        ctrl.send_message(aid, "refine it")
        await sub.settled.wait()
        seeds = [list(s) for s in ctrl.seeds]
        await ctrl.cancel_running()
        return seeds

    seeds = asyncio.run(scenario())
    assert seeds[0] == []  # first turn starts fresh
    # The follow-up turn sees the whole first exchange.
    assert seeds[1] == [
        {"role": "user", "content": "do the task"},
        {"role": "assistant", "content": "first answer"},
    ]


def test_hard_cancel_tears_down_an_idle_child(tmp_path) -> None:
    async def scenario():
        ctrl = _TurnScript(str(tmp_path), ["answer"])
        aid = ctrl.spawn("task", name="x", isolate=False)
        sub = ctrl.get(aid)
        await sub.settled.wait()
        assert sub.status == "idle"
        await ctrl.cancel_running()
        return sub.status, sub.result, sub.handle.done()

    status, result, done = asyncio.run(scenario())
    assert status == "failed" and result == "cancelled" and done


def test_turn_failure_is_terminal_and_posted(tmp_path) -> None:
    async def scenario():
        ctrl = _TurnScript(str(tmp_path), [RuntimeError("boom")])
        aid = ctrl.spawn("task", name="x", isolate=False)
        sub = ctrl.get(aid)
        await sub.settled.wait()
        await asyncio.gather(sub.handle, return_exceptions=True)
        injections = await ctrl.drain_injections()
        return sub.status, sub.result, injections

    status, result, injections = asyncio.run(scenario())
    assert status == "failed" and "boom" in result
    assert len(injections) == 1 and "Status: failed" in injections[0].payload


def test_each_turn_reaches_the_transcript_sink(tmp_path) -> None:
    dumps: list[tuple[str, str, str]] = []

    class _Recorded(AgentControl):
        async def _run_subagent(self, sub, workspace):
            # The real seam dumps in its finally; the stub mirrors that.
            self._dump_transcript(sub, _FakeSession())
            return "ok"

    class _FakeSession:
        history = [{"role": "user", "content": "hi"}]

    async def scenario():
        ctrl = _Recorded(
            str(tmp_path),
            transcript_sink=lambda aid, task, status, messages: dumps.append(
                (aid, status, json.dumps(messages))
            ),
        )
        aid = ctrl.spawn("task", name="x", isolate=False)
        sub = ctrl.get(aid)
        await sub.settled.wait()
        sub.settled.clear()
        ctrl.send_message(aid, "again")
        await sub.settled.wait()
        await ctrl.cancel_running()

    asyncio.run(scenario())
    assert len(dumps) == 2  # one per turn
    assert all(aid == "x" for aid, _status, _m in dumps)


def test_application_transcript_sink_writes_a_snapshot(tmp_path) -> None:
    """The session_runtime sink: companion JSONL under the Session's dir."""
    from core.application.session_runtime import SessionRuntimeRegistry
    from core.sessions.store import SessionStore

    store = SessionStore(tmp_path / "sessions")
    session = store.create_session(title="parent")
    registry = SessionRuntimeRegistry(store, factory=object())  # type: ignore[arg-type]

    sink = registry._subagent_transcript_sink(session.session_id)
    sink("worker/1", "review it", "idle", [{"role": "user", "content": "go"}])

    path = tmp_path / "sessions" / session.session_id / "subagents" / "worker-1.jsonl"
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    assert rows[0]["_type"] == "subagent_transcript"
    assert rows[0]["agentId"] == "worker/1"
    assert rows[0]["status"] == "idle"
    assert rows[1] == {"role": "user", "content": "go"}

    # A later turn replaces the snapshot whole — never appends a mixed file.
    sink("worker/1", "review it", "idle", [{"role": "user", "content": "go2"}])
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    assert len(rows) == 2 and rows[1]["content"] == "go2"
