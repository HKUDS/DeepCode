"""Headless adapter for the shared Thread Goal extension."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from cli.project_trust import (
    open_workspace_project,
    require_project_trusted,
    set_project_trusted,
)
from core.application.agent_adapter import ConfiguredAgentSessionFactory
from core.application.application import DeepCodeApplication
from core.application.errors import (
    GoalNotFoundError,
    InvalidArgumentError,
    TurnNotFoundError,
)
from core.domain.execution_security import ExecutionAccessPreset
from core.domain.message_provenance import ClientSurface
from core.domain.thread_goal import GoalOutcome, ThreadGoal, ThreadGoalStatus
from core.events import Event
from core.harness.permissions import PermissionMode


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
    token_budget: int | None = None
    max_iterations: int | None = None
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
    max_iterations: int | None = None
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
    options: GoalRunOptions,
    *,
    on_progress: ProgressHook | None = None,
    on_event: EventHook | None = None,
) -> GoalRunResult:
    """Create a canonical Session and wait on its ordinary-Turn Goal lifecycle."""

    workspace = Path(options.workspace).expanduser().resolve()
    workspace.mkdir(parents=True, exist_ok=True)
    objective = _objective_with_completion_evidence(
        options.objective,
        options.completion_evidence_command,
    )
    factory = ConfiguredAgentSessionFactory(
        default_permission_mode=PermissionMode.DEFAULT,
        streaming=False,
        max_iterations=options.max_iterations,
    )
    application = DeepCodeApplication.open(
        session_factory=factory,
        host_surface="headless",
        run_automation_scheduler=False,
    )
    event_token: str | None = None
    try:
        project = open_workspace_project(
            application,
            str(workspace),
            grant_trust=options.trust_workspace,
        )
        require_project_trusted(project)
        if options.skill_ids and options.skill_identifiers:
            raise InvalidArgumentError("pass Skill IDs or Skill identifiers, not both")
        skill_ids = options.skill_ids or tuple(
            application.skills.select(project.id, identifier).id
            for identifier in options.skill_identifiers
        )
        thread = application.threads.start(
            project.id,
            title=objective.splitlines()[0][:60],
            # Automated goal runs choose their own composition; an
            # interactive default must not narrow them silently.
            inherit_default_preset=False,
            connection_id=options.connection_id,
            model=options.model,
            reasoning_effort=options.reasoning_effort,
            access_preset_override=options.access_preset,
        )
        if on_event is not None:
            event_token = application.turns.subscribe_thread_events(
                thread.id,
                on_event,
            )
        goal = application.goals.create(
            thread.id,
            objective=objective,
            token_budget=options.token_budget,
            skill_ids=skill_ids,
            start=True,
            client_surface=ClientSurface.HEADLESS,
        )
        return await _wait_for_goal(
            application,
            thread_id=thread.id,
            workspace=thread.workspace_path,
            initial=goal,
            on_progress=on_progress,
        )
    finally:
        if event_token is not None:
            application.turns.unsubscribe_thread_events(event_token)
        application.close()


async def resume_goal(
    options: GoalResumeOptions,
    *,
    on_progress: ProgressHook | None = None,
    on_event: EventHook | None = None,
) -> GoalRunResult:
    """Resume the existing Goal without replacing Session identity or history."""

    session_id = options.session_id.strip()
    if not session_id:
        raise InvalidArgumentError("Session ID must not be empty")
    workspace_override = (
        str(Path(options.workspace).expanduser().resolve())
        if options.workspace is not None
        else None
    )
    factory = ConfiguredAgentSessionFactory(
        default_permission_mode=PermissionMode.DEFAULT,
        streaming=False,
        max_iterations=options.max_iterations,
    )
    application = DeepCodeApplication.open(
        session_factory=factory,
        host_surface="headless",
        run_automation_scheduler=False,
    )
    event_token: str | None = None
    try:
        thread = application.threads.resume(
            session_id,
            workspace_path=workspace_override,
        )
        project = application.projects.read(thread.project_id)
        if options.trust_workspace:
            project = set_project_trusted(application, project)
        require_project_trusted(project)
        if options.access_preset is not None:
            thread = application.threads.set_access_preset(
                thread.id,
                options.access_preset,
            )
        goal = application.goals.read(thread.id)
        if goal is None:
            raise GoalNotFoundError(f"no Goal is attached to Session {thread.id}")
        if goal.status is ThreadGoalStatus.COMPLETE:
            return _result(
                application,
                goal=goal,
                workspace=thread.workspace_path,
            )
        if on_event is not None:
            event_token = application.turns.subscribe_thread_events(
                thread.id,
                on_event,
            )

        goal = _apply_budget_override(
            application,
            goal=goal,
            token_budget=options.token_budget,
        )
        execution_options = {
            "client_surface": ClientSurface.HEADLESS,
            "connection_id": options.connection_id,
            "model": options.model,
            "reasoning_effort": options.reasoning_effort,
        }
        if goal.status is ThreadGoalStatus.ACTIVE:
            continued = application.goals.continue_goal(
                thread.id,
                expected_goal_id=goal.id,
                **execution_options,
            )
            goal = continued.goal
        elif goal.status in {
            ThreadGoalStatus.PAUSED,
            ThreadGoalStatus.BLOCKED,
            ThreadGoalStatus.BUDGET_LIMITED,
        }:
            goal = application.goals.resume(
                thread.id,
                expected_goal_id=goal.id,
                **execution_options,
            )
        else:  # pragma: no cover - exhaustive guard for future statuses
            raise InvalidArgumentError(
                f"Goal status cannot be resumed: {goal.status.value}"
            )

        return await _wait_for_goal(
            application,
            thread_id=thread.id,
            workspace=thread.workspace_path,
            initial=goal,
            on_progress=on_progress,
        )
    finally:
        if event_token is not None:
            application.turns.unsubscribe_thread_events(event_token)
        application.close()


def _apply_budget_override(
    application: DeepCodeApplication,
    *,
    goal: ThreadGoal,
    token_budget: int | None,
) -> ThreadGoal:
    if token_budget is None:
        if (
            goal.status is ThreadGoalStatus.BUDGET_LIMITED
            and goal.token_budget is not None
        ):
            raise InvalidArgumentError(
                "the Goal exhausted its token budget; provide a larger "
                "--token-budget to resume"
            )
        return goal
    if token_budget <= goal.tokens_used:
        raise InvalidArgumentError(
            "the resumed token budget must be greater than tokens already used "
            f"({goal.tokens_used})"
        )
    return application.goals.edit(
        goal.thread_id,
        expected_goal_id=goal.id,
        objective=goal.objective,
        token_budget=token_budget,
        skill_ids=goal.skill_ids,
        continue_work=False,
        client_surface=ClientSurface.HEADLESS,
    )


async def _wait_for_goal(
    application: DeepCodeApplication,
    *,
    thread_id: str,
    workspace: str,
    initial: ThreadGoal,
    on_progress: ProgressHook | None,
) -> GoalRunResult:
    goal = initial
    last_snapshot: ThreadGoal | None = None
    while True:
        settled = _goal_execution_settled(application, goal)
        if (
            on_progress is not None
            and goal != last_snapshot
            and (goal.status is ThreadGoalStatus.ACTIVE or settled)
        ):
            on_progress(goal)
            last_snapshot = goal
        if settled:
            break
        await asyncio.sleep(0.05)
        goal = application.goals.read(thread_id) or goal
    return _result(application, goal=goal, workspace=workspace)


def _goal_execution_settled(
    application: DeepCodeApplication,
    goal: ThreadGoal,
) -> bool:
    if goal.status is ThreadGoalStatus.ACTIVE:
        return False
    outcome = application.goals.read_outcome(goal.thread_id)
    deciding_turn_id = outcome.decided_by_turn_id if outcome is not None else None
    if deciding_turn_id is None:
        return True
    try:
        deciding_turn = application.turns.read(deciding_turn_id).turn
    except TurnNotFoundError:
        return True
    return deciding_turn.status.is_terminal and application.goals.is_turn_accounted(
        goal.thread_id,
        goal_id=goal.id,
        turn_id=deciding_turn.id,
    )


def _result(
    application: DeepCodeApplication,
    *,
    goal: ThreadGoal,
    workspace: str,
) -> GoalRunResult:
    return GoalRunResult(
        goal=goal,
        session_id=goal.thread_id,
        workspace=workspace,
        outcome=application.goals.read_outcome(goal.thread_id),
    )


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
        f"- Run `{command}` and only mark the Goal complete if it passes."
    )


__all__ = [
    "EventHook",
    "GoalResumeOptions",
    "GoalRunOptions",
    "GoalRunResult",
    "ProgressHook",
    "resume_goal",
    "run_goal",
]
