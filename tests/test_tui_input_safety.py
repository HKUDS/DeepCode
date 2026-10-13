"""Typing, Esc/Ctrl-C and command parsing never crash or misfire the TUI."""

from __future__ import annotations

import asyncio
import io
import os
import signal
import sys
from types import SimpleNamespace

import pytest
from prompt_toolkit.application import create_app_session
from prompt_toolkit.document import Document
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput
from rich.console import Console

import cli.tui.app as tui_app
import cli.tui.input as tui_input
from cli.service_catalogs import ServiceGoals
from cli.tui.goal_controller import TuiGoalController
from cli.tui.input import InputInterrupted, InputReader, TuiCompleter
from core.application.views import goal_outcome_view, thread_goal_view
from core.domain.thread_goal import (
    GoalDecisionSource,
    GoalOutcome,
    ThreadGoal,
    ThreadGoalStatus,
)

# -- @path completion ----------------------------------------------------------


def _completions(workspace, text):
    completer = TuiCompleter(str(workspace))
    return [c.text for c in completer.get_completions(Document(text), None)]


def test_absolute_and_escaping_paths_complete_to_nothing(tmp_path) -> None:
    workspace = tmp_path / "ws"
    (workspace / "src").mkdir(parents=True)
    (workspace / "src" / "app.py").write_text("x")
    (tmp_path / "outside.txt").write_text("x")

    assert _completions(workspace, "see @/") == []
    assert _completions(workspace, "see @/etc/ho") == []
    assert _completions(workspace, "see @~/") == []
    assert _completions(workspace, "see @../") == []
    assert _completions(workspace, "see @sr") == ["src/"]
    assert _completions(workspace, "see @src/a") == ["src/app.py"]


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits")
def test_unreadable_directory_completes_to_nothing(tmp_path) -> None:
    workspace = tmp_path / "ws"
    locked = workspace / "locked"
    locked.mkdir(parents=True)
    locked.chmod(0)
    try:
        assert _completions(workspace, "@locked/") == []
    finally:
        locked.chmod(0o700)


def test_a_failing_completion_source_yields_nothing(tmp_path) -> None:
    completer = TuiCompleter(str(tmp_path))
    completer.set_skill_provider(lambda: (_ for _ in ()).throw(RuntimeError("x")))

    def broken(_name, _prefix):
        raise RuntimeError("service down")

    completer.set_argument_provider(broken)

    assert list(completer.get_completions(Document("/resume ab"), None)) == []


# -- Esc and Ctrl-C at the prompt ---------------------------------------------


class _TtyStdin(io.StringIO):
    def isatty(self) -> bool:
        return True


async def _prompt(monkeypatch, tmp_path, keys: list[str]):
    monkeypatch.setattr(sys, "stdin", _TtyStdin())
    monkeypatch.setattr(tui_input, "_HISTORY_PATH", tmp_path / "history")
    with (
        create_pipe_input() as pipe,
        create_app_session(input=pipe, output=DummyOutput()),
    ):
        reader = InputReader(str(tmp_path))
        task = asyncio.create_task(reader.read())
        for chunk in keys:
            await asyncio.sleep(0.1)
            if chunk == "<pause>":
                # Longer than prompt_toolkit's key timeout (1s, for bindings
                # that start with Esc), so a lone Esc is handled on its own
                # instead of combining with the next key as Meta+key.
                await asyncio.sleep(1.6)
                continue
            pipe.send_text(chunk)
        return await asyncio.wait_for(task, 5)


def test_esc_with_a_draft_keeps_the_draft(monkeypatch, tmp_path) -> None:
    line = asyncio.run(
        _prompt(monkeypatch, tmp_path, ["keep this draft", "\x1b", "<pause>", "\r"])
    )
    assert line == "keep this draft"


def test_esc_from_an_empty_prompt_interrupts(monkeypatch, tmp_path) -> None:
    with pytest.raises(InputInterrupted):
        asyncio.run(_prompt(monkeypatch, tmp_path, ["\x1b", "<pause>"]))


def test_ctrl_c_clears_a_draft_before_it_interrupts(monkeypatch, tmp_path) -> None:
    line = asyncio.run(
        _prompt(monkeypatch, tmp_path, ["half typed", "\x03", "fresh", "\r"])
    )
    assert line == "fresh"

    with pytest.raises(InputInterrupted):
        asyncio.run(_prompt(monkeypatch, tmp_path, ["\x03"]))


# -- nested confirmation and Ctrl-C while a command runs ------------------------


def _app() -> tuple[tui_app.TuiApp, io.StringIO]:
    app = object.__new__(tui_app.TuiApp)
    output = io.StringIO()
    app.console = Console(file=output, color_system=None, width=200)
    return app, output


def test_esc_at_the_full_access_confirmation_cancels_only_that_command() -> None:
    app, _ = _app()

    class _Reader:
        interactive = True

        async def read(self):
            raise InputInterrupted()

    changed = []
    app.reader = _Reader()
    app.thread_client = SimpleNamespace(
        access_preset_override=None,
        set_access_preset=changed.append,
    )

    status = asyncio.run(app.set_access_preset("full_access"))

    assert status == "Full access was not enabled."
    assert changed == []


@pytest.mark.skipif(os.name == "nt", reason="POSIX signal delivery")
def test_ctrl_c_while_a_command_runs_stops_that_command_only() -> None:
    app, output = _app()
    app.reader = SimpleNamespace(interactive=True)
    finished = []

    async def slow_command(_text):
        await asyncio.sleep(5)
        finished.append(True)

    app._handle_line = slow_command

    async def scenario():
        loop = asyncio.get_running_loop()
        loop.call_later(0.2, os.kill, os.getpid(), signal.SIGINT)
        await app._run_line("/compact")
        return signal.getsignal(signal.SIGINT)

    handler_after = asyncio.run(scenario())

    assert finished == []
    assert "Stopped." in output.getvalue()
    # The previous SIGINT handler is restored for the next prompt.
    assert handler_after is not signal.SIG_IGN


# -- /goal parsing ----------------------------------------------------------------


class _Goals:
    def __init__(self, current: ThreadGoal | None) -> None:
        self.current = current
        self.cleared: list[str] = []
        self.created: list[str] = []
        self.reads = 0

    def read(self, _thread_id):
        self.reads += 1
        return self.current

    def read_with_outcome(self, _thread_id):
        self.reads += 1
        return self.current, None

    def clear(self, thread_id, *, expected_goal_id):
        self.cleared.append(expected_goal_id)

    def create(self, thread_id, *, objective, **_kwargs):
        self.created.append(objective)
        return ThreadGoal(thread_id=thread_id, objective=objective)


def _controller(goal: ThreadGoal | None):
    goals = _Goals(goal)
    titles: list[str] = []
    owner = SimpleNamespace(
        thread_client=SimpleNamespace(goals=goals, session_id="session-1"),
        bridge=SimpleNamespace(set_title_from=titles.append),
        selected_skill_ids=[],
    )
    return TuiGoalController(owner), goals, titles


def test_objective_starting_with_a_keyword_never_clears_the_goal() -> None:
    existing = ThreadGoal(thread_id="session-1", objective="Ship the parser")
    controller, goals, titles = _controller(existing)

    result = asyncio.run(controller.execute("clear out the dead feature flags"))

    assert goals.cleared == []
    assert "already has a Goal" in result.message
    assert titles == []


def test_objective_starting_with_a_keyword_creates_a_goal() -> None:
    controller, goals, _titles = _controller(None)

    asyncio.run(controller.execute("continue migrating the config loader"))

    assert goals.created == ["continue migrating the config loader"]


def test_bare_clear_still_clears() -> None:
    existing = ThreadGoal(thread_id="session-1", objective="Ship the parser")
    controller, goals, _titles = _controller(existing)

    asyncio.run(controller.execute("clear"))

    assert goals.cleared == [existing.id]


def test_cancelled_goal_wait_reports_and_keeps_the_goal() -> None:
    active = ThreadGoal(
        thread_id="session-1",
        objective="Long work",
        status=ThreadGoalStatus.ACTIVE,
    )
    controller, goals, _titles = _controller(active)

    async def scenario():
        task = asyncio.create_task(controller.execute("wait"))
        await asyncio.sleep(0.4)
        task.cancel()
        return await task

    result = asyncio.run(scenario())

    assert "Stopped waiting; the Goal remains active." in result.message
    assert goals.reads >= 2


# -- /goal reads the Goal and its outcome together -------------------------------


class _GoalService:
    """thread/goal/get answers with the next state each time it is asked."""

    def __init__(self, *states) -> None:
        self.states = list(states)
        self.gets = 0

    def call(self, method, params):
        assert method == "thread/goal/get"
        state = self.states[min(self.gets, len(self.states) - 1)]
        self.gets += 1
        return state


def _goal_state(status: ThreadGoalStatus, reason: str | None = None):
    goal = ThreadGoal(
        id="goal_000000000000000000000001",
        thread_id="session-1",
        objective="Ship the parser",
        status=status,
    )
    outcome = (
        GoalOutcome(
            status=status,
            reason=reason,
            source=GoalDecisionSource.RUNTIME,
            decided_by_turn_id=None,
            decided_at=goal.updated_at,
        )
        if reason is not None
        else None
    )
    return {
        "goal": thread_goal_view(goal),
        "outcome": goal_outcome_view(outcome) if outcome is not None else None,
    }


def _service_controller(service: _GoalService) -> TuiGoalController:
    async def wait_until_idle():
        return None

    owner = SimpleNamespace(
        thread_client=SimpleNamespace(
            goals=ServiceGoals(service),
            session_id="session-1",
            wait_until_idle=wait_until_idle,
        ),
    )
    return TuiGoalController(owner)


def test_goal_show_never_pairs_a_goal_with_a_later_reason() -> None:
    # A Turn settles between two reads: the Goal was active, then blocked.
    service = _GoalService(
        _goal_state(ThreadGoalStatus.ACTIVE),
        _goal_state(ThreadGoalStatus.BLOCKED, "The build is broken."),
    )

    result = asyncio.run(_service_controller(service).execute("show"))

    assert result.message.startswith("Goal active")
    assert "Reason:" not in result.message
    assert service.gets == 1


def test_goal_wait_reports_the_goal_and_reason_it_ended_with() -> None:
    # Polling sees the Goal blocked; by the final read it has been completed.
    service = _GoalService(
        _goal_state(ThreadGoalStatus.ACTIVE),
        _goal_state(ThreadGoalStatus.BLOCKED, "The build is broken."),
        _goal_state(ThreadGoalStatus.COMPLETE, "Shipped."),
    )

    result = asyncio.run(_service_controller(service).execute("wait"))

    assert result.message.startswith("Goal complete")
    assert "Reason: Shipped." in result.message
