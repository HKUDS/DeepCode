"""TUI commands for the shared Thread Goal extension."""

from __future__ import annotations

import asyncio
import signal
from dataclasses import dataclass
from typing import TYPE_CHECKING

from core.application.application import DeepCodeApplication
from core.application.errors import ApplicationError
from core.config import ConfigError
from core.domain.message_provenance import ClientSurface
from core.domain.thread_goal import GoalOutcome, ThreadGoal, ThreadGoalStatus

if TYPE_CHECKING:
    from cli.tui.app import TuiApp


@dataclass(frozen=True, slots=True)
class GoalCommandResult:
    message: str
    refresh_session: bool = False


class TuiGoalController:
    """Translate explicit `/goal` commands without owning another runtime."""

    def __init__(self, owner: TuiApp) -> None:
        self.owner = owner

    async def execute(self, raw: str) -> GoalCommandResult:
        action, _separator, remainder = raw.strip().partition(" ")
        normalized = action.casefold()
        try:
            if not action or normalized in {"show", "status"}:
                return self._status()
            if normalized == "wait":
                return await self._wait()
            if normalized == "pause":
                return self._pause()
            if normalized == "resume":
                return self._resume()
            if normalized == "continue":
                return self._continue()
            if normalized == "clear":
                return self._clear()
            if normalized == "edit":
                return self._edit(remainder, resume=False)
            if normalized == "reopen":
                return self._edit(remainder, resume=True)
            objective = (
                remainder.strip() if normalized in {"set", "start"} else raw.strip()
            )
            if not objective:
                return GoalCommandResult(self._usage())
            return self._create(objective)
        except (ApplicationError, ConfigError, OSError, ValueError) as exc:
            return GoalCommandResult(f"Goal error: {exc}")

    def close(self) -> None:
        """The Goal extension is owned by the shared Application."""

    def _status(self) -> GoalCommandResult:
        application, thread_id = self._context()
        goal = application.goals.read(thread_id)
        outcome = (
            application.goals.read_outcome(thread_id) if goal is not None else None
        )
        return GoalCommandResult(self._format(goal, outcome))

    def _pause(self) -> GoalCommandResult:
        application, thread_id = self._context()
        current = self._required(application, thread_id)
        paused = application.goals.pause(
            thread_id,
            expected_goal_id=current.id,
        )
        return GoalCommandResult(self._format(paused), refresh_session=True)

    def _resume(self) -> GoalCommandResult:
        application, thread_id = self._context()
        current = self._required(application, thread_id)
        if current.status is ThreadGoalStatus.ACTIVE:
            return GoalCommandResult("the Session Goal is already active")
        resumed = application.goals.resume(
            thread_id,
            expected_goal_id=current.id,
            client_surface=ClientSurface.CLI,
        )
        return GoalCommandResult(
            self._format(resumed)
            + "\nGoal resumed on the shared ordinary-Turn runtime."
        )

    def _continue(self) -> GoalCommandResult:
        application, thread_id = self._context()
        current = self._required(application, thread_id)
        result = application.goals.continue_goal(
            thread_id,
            expected_goal_id=current.id,
            client_surface=ClientSurface.CLI,
        )
        message = (
            "Goal continuation started on the shared ordinary-Turn runtime."
            if result.disposition.value == "started"
            else f"Goal is already running in Turn {result.turn_id}."
        )
        return GoalCommandResult(
            self._format(result.goal) + f"\n{message}",
            refresh_session=True,
        )

    def _clear(self) -> GoalCommandResult:
        application, thread_id = self._context()
        current = self._required(application, thread_id)
        application.goals.clear(
            thread_id,
            expected_goal_id=current.id,
        )
        return GoalCommandResult(
            "Session Goal cleared; conversation history was preserved.",
            refresh_session=True,
        )

    def _edit(self, objective: str, *, resume: bool) -> GoalCommandResult:
        application, thread_id = self._context()
        current = self._required(application, thread_id)
        clean = objective.strip() or current.objective
        updated = application.goals.edit(
            thread_id,
            expected_goal_id=current.id,
            objective=clean,
            token_budget=current.token_budget,
            skill_ids=current.skill_ids,
            continue_work=True,
            client_surface=ClientSurface.CLI,
        )
        if resume and updated.status is not ThreadGoalStatus.ACTIVE:
            updated = application.goals.resume(
                thread_id,
                expected_goal_id=updated.id,
                client_surface=ClientSurface.CLI,
            )
        return GoalCommandResult(
            self._format(updated)
            + "\nGoal saved with the same identity; active work sees the update."
        )

    def _create(self, objective: str) -> GoalCommandResult:
        self.owner.bridge.set_title_from(objective)
        application, thread_id = self._context()
        existing = application.goals.read(thread_id)
        if existing is not None:
            return GoalCommandResult(
                "this Session already has a Goal; use /goal edit, /goal "
                "resume, /goal reopen, or /goal clear"
            )
        selected_skills = tuple(self.owner.selected_skill_ids)
        created = application.goals.create(
            thread_id,
            objective=objective,
            skill_ids=selected_skills,
            start=True,
            client_surface=ClientSurface.CLI,
        )
        self.owner.selected_skill_ids.clear()
        return GoalCommandResult(
            self._format(created)
            + "\nGoal started on the shared ordinary-Turn runtime."
        )

    async def _wait(self) -> GoalCommandResult:
        application, thread_id = self._context()
        current = self._required(application, thread_id)
        stop_waiting = asyncio.Event()
        loop = asyncio.get_running_loop()

        def request_stop() -> None:
            stop_waiting.set()

        signal_installed = False
        try:
            loop.add_signal_handler(signal.SIGINT, request_stop)
            signal_installed = True
        except (NotImplementedError, RuntimeError):
            pass
        try:
            while (
                current.status is ThreadGoalStatus.ACTIVE and not stop_waiting.is_set()
            ):
                await asyncio.sleep(0.05)
                current = application.goals.read(thread_id) or current
        finally:
            if signal_installed:
                try:
                    loop.remove_signal_handler(signal.SIGINT)
                except (NotImplementedError, RuntimeError, ValueError):
                    pass
        if not stop_waiting.is_set() and current.status is not ThreadGoalStatus.ACTIVE:
            # Goal completion can be persisted by a tool before its deciding
            # Turn has emitted and rendered the final tool/result events.
            await self.owner.thread_client.wait_until_idle()
        suffix = (
            "\nStopped waiting; the Goal remains active."
            if stop_waiting.is_set() and current.status is ThreadGoalStatus.ACTIVE
            else ""
        )
        return GoalCommandResult(
            self._format(current, application.goals.read_outcome(thread_id)) + suffix,
            refresh_session=True,
        )

    def _context(self) -> tuple[DeepCodeApplication, str]:
        return (
            self.owner.thread_client.application,
            self.owner.thread_client.session_id,
        )

    @staticmethod
    def _required(
        application: DeepCodeApplication,
        thread_id: str,
    ) -> ThreadGoal:
        goal = application.goals.read(thread_id)
        if goal is None:
            raise ValueError("no Goal is attached to this Session")
        return goal

    @staticmethod
    def _usage() -> str:
        return (
            "usage: /goal <text> | show | edit <text> | pause | resume | continue | "
            "wait | reopen [text] | clear"
        )

    @staticmethod
    def _format(
        goal: ThreadGoal | None,
        outcome: GoalOutcome | None = None,
    ) -> str:
        if goal is None:
            return "no Goal is attached to this Session"
        budget = f"/{goal.token_budget}" if goal.token_budget is not None else ""
        lines = [
            f"Goal {goal.status.value} · {goal.id} · "
            f"tokens {goal.tokens_used}{budget} · "
            f"time {goal.time_used_seconds}s",
            goal.objective,
        ]
        if outcome is not None:
            lines.append(f"Reason: {outcome.reason}")
            if outcome.decided_by_turn_id is not None:
                lines.append(f"Deciding Turn: {outcome.decided_by_turn_id}")
        return "\n".join(lines)


__all__ = ["GoalCommandResult", "TuiGoalController"]
