"""Headless adapter for the shared Thread Goal extension."""

from __future__ import annotations

import asyncio
import os
import shlex
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import threading
import time
from cli.rpc_models import from_view
from cli.service_thread_client import ServiceThreadClient
from core.application.errors import GoalNotFoundError, InvalidArgumentError
from core.domain.execution_security import ExecutionAccessPreset
from core.domain.message_provenance import ClientSurface
from core.domain.thread_goal import (
    GoalCheck,
    GoalOutcome,
    ThreadGoal,
    ThreadGoalStatus,
    validate_goal_checks,
)
from core.events import Event


@dataclass(frozen=True, slots=True)
class GoalRunOptions:
    objective: str
    workspace: str
    connection_id: str | None = None
    model: str | None = None
    reasoning_effort: str | None = None
    skill_ids: tuple[str, ...] = ()
    skill_identifiers: tuple[str, ...] = ()
    completion_evidence_command: str = ""
    # More commands DeepCode runs itself before accepting completion.
    check_commands: tuple[str, ...] = ()
    token_budget: int | None = None
    time_budget_seconds: int | None = None
    trust_workspace: bool = False
    access_preset: ExecutionAccessPreset | None = None


@dataclass(frozen=True, slots=True)
class GoalResumeOptions:
    session_id: str
    workspace: str | None = None
    connection_id: str | None = None
    model: str | None = None
    reasoning_effort: str | None = None
    token_budget: int | None = None
    time_budget_seconds: int | None = None
    trust_workspace: bool = False
    access_preset: ExecutionAccessPreset | None = None


@dataclass(frozen=True, slots=True)
class GoalRunResult:
    goal: ThreadGoal
    session_id: str
    workspace: str
    outcome: GoalOutcome | None


ProgressHook = Callable[[ThreadGoal], None]
EventHook = Callable[[Event], None]


async def run_goal(
    options: GoalRunOptions, *, on_progress=None, on_event=None
) -> GoalRunResult:
    return await _run_attached(options, on_progress=on_progress, on_event=on_event)


async def resume_goal(
    options: GoalResumeOptions, *, on_progress=None, on_event=None
) -> GoalRunResult:
    if not options.session_id.strip():
        raise InvalidArgumentError("Session ID must not be empty")
    return await _run_attached(options, on_progress=on_progress, on_event=on_event)


async def _run_attached(options, *, on_progress, on_event):
    detached = threading.Event()
    try:
        return await asyncio.to_thread(
            _execute, options, on_progress, on_event, detached
        )
    finally:
        detached.set()


def _execute(options, on_progress, on_event, detached) -> GoalRunResult:
    resuming = isinstance(options, GoalResumeOptions)
    objective = (
        None
        if resuming
        else _objective_with_completion_evidence(
            options.objective, options.completion_evidence_command
        )
    )
    checks = () if resuming else _goal_checks(options)
    if not resuming and options.skill_ids and options.skill_identifiers:
        raise InvalidArgumentError("pass Skill IDs or Skill identifiers, not both")
    if options.workspace is not None:
        Path(options.workspace).expanduser().mkdir(parents=True, exist_ok=True)
    client = ServiceThreadClient(
        workspace=options.workspace,
        model=options.model,
        connection_id=options.connection_id,
        reasoning_effort=options.reasoning_effort,
        max_iterations=None,
        streaming=False,
        trust_workspace=options.trust_workspace,
        resume_id=options.session_id if resuming else None,
        event_sink=on_event,
        surface="headless",
    )
    try:
        if options.access_preset is not None:
            client.set_access_preset(options.access_preset)
        if resuming:
            goal, outcome = client.goals.read_with_outcome(client.thread.id)
            if goal is None:
                raise GoalNotFoundError(
                    f"no Goal is attached to Session {client.thread.id}"
                )
            if goal.status is ThreadGoalStatus.COMPLETE:
                return GoalRunResult(goal, client.thread.id, client.workspace, outcome)
            goal = _apply_budget_override(
                client.goals,
                goal=goal,
                token_budget=options.token_budget,
                time_budget_seconds=options.time_budget_seconds,
            )
            execution = dict(
                connection_id=options.connection_id,
                model=options.model,
                reasoning_effort=options.reasoning_effort,
            )
            if goal.status is ThreadGoalStatus.ACTIVE:
                goal = client.goals.continue_goal(
                    client.thread.id, expected_goal_id=goal.id, **execution
                ).goal
            elif goal.status in {
                ThreadGoalStatus.PAUSED,
                ThreadGoalStatus.BLOCKED,
                ThreadGoalStatus.BUDGET_LIMITED,
            }:
                goal = client.goals.resume(
                    client.thread.id, expected_goal_id=goal.id, **execution
                )
            else:
                raise InvalidArgumentError(
                    f"Goal status cannot be resumed: {goal.status.value}"
                )
        else:
            skills = options.skill_ids or tuple(
                client.skills.select(client.project.id, value).id
                for value in options.skill_identifiers
            )
            client.rename_thread(objective.splitlines()[0][:60])
            goal = client.goals.create(
                client.thread.id,
                objective=objective,
                token_budget=options.token_budget,
                skill_ids=skills,
                checks=checks,
                time_budget_seconds=options.time_budget_seconds,
            )
        last_snapshot = None
        while not detached.is_set():
            state = client.rpc.call("thread/goal/get", {"threadId": client.thread.id})
            if "executionSettled" not in state:
                raise InvalidArgumentError(
                    "Restart the service with the updated installation to inspect Goal completion"
                )
            current = from_view(ThreadGoal, state["goal"]) if state["goal"] else None
            if current is None or current.id != goal.id:
                raise InvalidArgumentError("The attached Goal was cleared or replaced")
            goal = current
            client.drain_events()
            settled = state["executionSettled"]
            if (
                on_progress
                and goal != last_snapshot
                and (goal.status is ThreadGoalStatus.ACTIVE or settled)
            ):
                on_progress(goal)
                last_snapshot = goal
            if settled:
                return GoalRunResult(
                    goal,
                    client.thread.id,
                    client.workspace,
                    from_view(GoalOutcome, state["outcome"])
                    if state["outcome"]
                    else None,
                )
            time.sleep(0.05)
        raise InterruptedError("Client detached; the Goal continues in the service")
    finally:
        asyncio.run(client.close())


def _apply_budget_override(
    goals,
    *,
    goal: ThreadGoal,
    token_budget: int | None,
    time_budget_seconds: int | None = None,
) -> ThreadGoal:
    tokens_spent = (
        goal.token_budget is not None and goal.tokens_used >= goal.token_budget
    )
    time_spent = (
        goal.time_budget_seconds is not None
        and goal.time_used_seconds >= goal.time_budget_seconds
    )
    if goal.status is ThreadGoalStatus.BUDGET_LIMITED:
        if tokens_spent and token_budget is None:
            raise InvalidArgumentError(
                "the Goal exhausted its token budget; provide a larger --token-budget to resume"
            )
        if time_spent and time_budget_seconds is None:
            raise InvalidArgumentError(
                "the Goal used up its time limit; provide a larger --time-budget to resume"
            )
    if token_budget is None and time_budget_seconds is None:
        return goal
    if token_budget is not None and token_budget <= goal.tokens_used:
        raise InvalidArgumentError(
            f"the resumed token budget must be greater than tokens already used ({goal.tokens_used})"
        )
    if (
        time_budget_seconds is not None
        and time_budget_seconds <= goal.time_used_seconds
    ):
        raise InvalidArgumentError(
            "the resumed time limit must be longer than the time already used "
            f"({goal.time_used_seconds}s)"
        )
    return goals.edit(
        goal.thread_id,
        expected_goal_id=goal.id,
        objective=goal.objective,
        token_budget=goal.token_budget if token_budget is None else token_budget,
        skill_ids=goal.skill_ids,
        **(
            {"time_budget_seconds": time_budget_seconds}
            if time_budget_seconds is not None
            else {}
        ),
        continue_work=False,
        client_surface=ClientSurface.HEADLESS,
    )


# Characters that mean something to a shell: such a command is run by bash.
_SHELL_CHARACTERS = frozenset("|&;<>()$`*?[]{}~!\n")
_REQUIREMENT_COMMAND_MAX_CHARS = 400


def parse_check_command(text: str) -> tuple[str, ...]:
    """The argument list for a check command the user typed.

    A plain command is split into arguments and run without a shell. One that
    uses shell syntax (pipes, ``&&``, globs, variables...) runs as the user
    wrote it, in ``bash -o pipefail -c``: a failing stage of a pipeline then
    fails the check instead of hiding behind the last one.
    """

    clean = text.strip()
    if not clean:
        raise InvalidArgumentError("a check command must not be empty")
    if _uses_shell_syntax(clean):
        if os.name == "nt" and "\\" in clean:
            raise InvalidArgumentError(
                f"the check command {clean!r} uses shell syntax, so it runs in "
                "bash, where \\ is not a path separator; write paths with /"
            )
        return ("bash", "-o", "pipefail", "-c", clean)
    try:
        if os.name == "nt":
            # Backslashes are path separators there; drop only the quotes.
            argv = tuple(
                part[1:-1] if len(part) > 1 and part[0] == part[-1] == '"' else part
                for part in shlex.split(clean, posix=False)
            )
        else:
            argv = tuple(shlex.split(clean))
    except ValueError as exc:
        raise InvalidArgumentError(
            f"cannot read the check command {clean!r}: {exc}"
        ) from exc
    if not argv:
        raise InvalidArgumentError("a check command must not be empty")
    return argv


def _uses_shell_syntax(text: str) -> bool:
    """Whether a shell would read ``text`` as more than words.

    Special characters count outside quotes, and ``$`` and backquotes inside
    double quotes, where a shell still expands them.
    """

    quote = ""
    escaped = False
    for character in text:
        if escaped:
            escaped = False
            continue
        if quote == "'":
            quote = "" if character == "'" else quote
            continue
        if character == "\\" and os.name != "nt":
            escaped = True
            continue
        if quote == '"':
            if character == '"':
                quote = ""
            elif character in "$`":
                return True
            continue
        if character in "'\"":
            quote = character
            continue
        if character in _SHELL_CHARACTERS:
            return True
    return False


def check_requirement(prefix: str, command: str) -> str:
    """A requirement naming the command, short enough to keep."""

    shown = command.strip()
    if len(shown) > _REQUIREMENT_COMMAND_MAX_CHARS:
        shown = shown[: _REQUIREMENT_COMMAND_MAX_CHARS - 1] + "…"
    return f"{prefix}: {shown}"


def _goal_checks(options: GoalRunOptions) -> tuple[GoalCheck, ...]:
    commands = [
        ("The test command passes", options.completion_evidence_command.strip()),
        *(("The command exits 0", text.strip()) for text in options.check_commands),
    ]
    try:
        return validate_goal_checks(
            tuple(
                GoalCheck(
                    requirement=check_requirement(prefix, text),
                    command=parse_check_command(text),
                )
                for prefix, text in commands
                if text
            )
        )
    except ValueError as exc:
        raise InvalidArgumentError(str(exc)) from exc


def _objective_with_completion_evidence(objective: str, command: str) -> str:
    clean = objective.strip()
    if not clean:
        raise InvalidArgumentError("Goal objective must not be empty")
    command = command.strip()
    if not command:
        return clean
    return (
        f"{clean}\n\n"
        "User-requested completion evidence:\n"
        f"- Run `{command}` and only mark the Goal complete if it passes. "
        "DeepCode also runs it itself when you claim completion."
    )


__all__ = [
    "EventHook",
    "GoalResumeOptions",
    "GoalRunOptions",
    "GoalRunResult",
    "ProgressHook",
    "parse_check_command",
    "resume_goal",
    "run_goal",
]
