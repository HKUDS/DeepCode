"""Headless CLI adapter for the shared durable Goal application services."""

from __future__ import annotations

import asyncio
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from core.application.agent_adapter import ConfiguredAgentSessionFactory
from core.application.application import DeepCodeApplication
from core.application.errors import InvalidArgumentError
from core.application.goal_evaluator import SemanticDecision
from core.domain.goal import GoalBudget, GoalEvaluation, GoalRecord, GoalVerdict
from core.domain.project import TrustState
from core.harness.permissions import PermissionMode


@dataclass(frozen=True, slots=True)
class GoalRunOptions:
    objective: str
    workspace: str
    connection_id: str | None = None
    model: str | None = None
    skill_ids: tuple[str, ...] = ()
    acceptance_criteria: tuple[str, ...] = ()
    verification: str = ""
    max_attempts: int | None = None
    max_iterations: int | None = None


@dataclass(frozen=True, slots=True)
class GoalRunResult:
    record: GoalRecord
    session_id: str


ProgressHook = Callable[[GoalRecord, GoalEvaluation | None], None]


class VerificationSufficientEvaluator:
    """Declare completion only after GoalEvaluator has observed passing tests."""

    async def evaluate(self, _context) -> SemanticDecision:
        return SemanticDecision(
            verdict=GoalVerdict.COMPLETE,
            reason="The configured deterministic verification passed.",
            evidence_refs=(),
            provider_name="deterministic-verification",
            model_id="none",
            tokens_used=0,
        )


async def run_goal(
    options: GoalRunOptions,
    *,
    on_progress: ProgressHook | None = None,
) -> GoalRunResult:
    """Create a normal Session and drive its Goal through GoalCoordinator."""

    workspace = Path(options.workspace).expanduser().resolve()
    workspace.mkdir(parents=True, exist_ok=True)
    objective = options.objective.strip()
    if not objective:
        raise InvalidArgumentError("Goal objective must not be empty")
    factory = ConfiguredAgentSessionFactory(
        default_permission_mode=PermissionMode.FULL_AUTO,
        streaming=False,
        max_iterations=options.max_iterations,
    )
    application = DeepCodeApplication.open(
        session_factory=factory,
        semantic_goal_evaluator=(
            VerificationSufficientEvaluator()
            if options.verification.strip()
            else None
        ),
    )
    thread_id = ""
    try:
        project = application.projects.add(
            str(workspace),
            trust_state=TrustState.TRUSTED,
        )
        if project.trust_state is not TrustState.TRUSTED:
            raise InvalidArgumentError(
                "the project is untrusted; trust it in Desktop before execution"
            )
        thread = application.threads.start(
            project.id,
            title=objective.splitlines()[0][:60],
            connection_id=options.connection_id,
            model=options.model,
        )
        thread_id = thread.id
        verification_id = _verification_command_id(
            application,
            thread.id,
            options.verification,
        )
        default_budget = application.goals.default_budget(thread.id)
        record = application.goals.create(
            thread.id,
            objective=objective,
            acceptance_criteria=options.acceptance_criteria,
            budget=(
                GoalBudget(
                    max_attempts=options.max_attempts,
                    max_tokens=default_budget.max_tokens,
                    max_elapsed_seconds=default_budget.max_elapsed_seconds,
                )
                if options.max_attempts is not None
                else None
            ),
            skill_ids=options.skill_ids,
            verification_command_id=verification_id,
        )
        application.goal_coordinator.start(thread.id)
        seen_evaluations = 0
        try:
            while record.goal.status.automatically_continues:
                await asyncio.sleep(0.05)
                record = application.goals.read(thread.id) or record
                if on_progress is not None:
                    while seen_evaluations < len(record.evaluations):
                        evaluation = record.evaluations[seen_evaluations]
                        seen_evaluations += 1
                        on_progress(record, evaluation)
        except asyncio.CancelledError:
            current = application.goals.read(thread.id)
            if current is not None and current.goal.status.automatically_continues:
                record = application.goal_coordinator.pause(
                    thread.id,
                    expected_revision=current.goal.revision,
                )
            raise
        record = application.goals.read(thread.id) or record
        if on_progress is not None and not record.evaluations:
            on_progress(record, None)
        return GoalRunResult(record=record, session_id=thread.id)
    finally:
        application.close()


def _verification_command_id(
    application: DeepCodeApplication,
    thread_id: str,
    requested: str,
) -> str | None:
    clean = requested.strip()
    if not clean:
        return None
    available = application.tests.discover(thread_id)
    by_id = next((command for command in available if command.id == clean), None)
    if by_id is not None:
        return by_id.id
    try:
        wanted = _normalized_argv(tuple(shlex.split(clean)))
    except ValueError as exc:
        raise InvalidArgumentError(f"invalid verification command: {exc}") from exc
    matched = next(
        (
            command
            for command in available
            if _normalized_argv(command.argv) == wanted
        ),
        None,
    )
    if matched is not None:
        return matched.id
    choices = ", ".join(
        f"{command.id} ({shlex.join(command.argv)})" for command in available
    )
    suffix = f" Available: {choices}." if choices else " No tests were discovered."
    raise InvalidArgumentError(
        "verification must match a discovered allowlisted command." + suffix
    )


def _normalized_argv(argv: tuple[str, ...]) -> tuple[str, ...]:
    if not argv:
        return ()
    executable = Path(argv[0]).name.casefold()
    if executable in {"python", "python3", "py"}:
        executable = "python"
    return (executable, *argv[1:])


__all__ = [
    "GoalRunOptions",
    "GoalRunResult",
    "ProgressHook",
    "VerificationSufficientEvaluator",
    "run_goal",
]
