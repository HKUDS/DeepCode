"""Thin TUI adapter for the shared durable Goal application services."""

from __future__ import annotations

import asyncio
import signal
from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable

from core.application.agent_adapter import ConfiguredAgentSessionFactory
from core.application.application import DeepCodeApplication
from core.application.errors import ApplicationError
from core.config import ConfigError
from core.domain.goal import GoalRecord, GoalStatus
from core.domain.project import TrustState
from core.events import Event
from core.harness.permissions import PermissionMode

if TYPE_CHECKING:
    from cli.tui.app import TuiApp


@dataclass(frozen=True, slots=True)
class GoalCommandResult:
    message: str
    refresh_session: bool = False


class TuiGoalController:
    """Translate slash commands; all lifecycle decisions remain in core."""

    def __init__(self, owner: TuiApp) -> None:
        self.owner = owner

    async def execute(self, raw: str) -> GoalCommandResult:
        action, _separator, remainder = raw.strip().partition(" ")
        normalized = action.casefold()
        if not action or normalized == "status":
            return self._with_application(self._status)
        if normalized == "pause":
            return self._with_application(self._pause)
        if normalized == "clear":
            return self._with_application(self._clear)
        if normalized == "resume":
            return await self._resume()
        objective = remainder.strip() if normalized in {"set", "start"} else raw.strip()
        if not objective:
            return GoalCommandResult(
                "usage: /goal <objective> | status | pause | resume | clear"
            )
        return await self._create_and_run(objective)

    def _with_application(self, operation) -> GoalCommandResult:
        application: DeepCodeApplication | None = None
        try:
            application, thread_id = self._open()
            return operation(application, thread_id)
        except (ApplicationError, ConfigError, OSError, ValueError) as exc:
            return GoalCommandResult(f"Goal error: {exc}")
        finally:
            if application is not None:
                application.close()

    def _status(
        self,
        application: DeepCodeApplication,
        thread_id: str,
    ) -> GoalCommandResult:
        return GoalCommandResult(self._format(application.goals.read(thread_id)))

    def _pause(
        self,
        application: DeepCodeApplication,
        thread_id: str,
    ) -> GoalCommandResult:
        record = application.goals.read(thread_id)
        if record is None:
            return GoalCommandResult("no Goal is attached to this Session")
        paused = application.goal_coordinator.pause(
            thread_id,
            expected_revision=record.goal.revision,
        )
        return GoalCommandResult(self._format(paused))

    def _clear(
        self,
        application: DeepCodeApplication,
        thread_id: str,
    ) -> GoalCommandResult:
        record = application.goals.read(thread_id)
        if record is None:
            return GoalCommandResult("no Goal is attached to this Session")
        application.goal_coordinator.clear(
            thread_id,
            expected_revision=record.goal.revision,
        )
        return GoalCommandResult("cleared the Session Goal")

    async def _resume(self) -> GoalCommandResult:
        application: DeepCodeApplication | None = None
        observer: Callable[[Event], None] | None = None
        thread_id = ""
        try:
            application, thread_id = self._open()
            record = application.goals.read(thread_id)
            if record is None:
                return GoalCommandResult("no Goal is attached to this Session")
            if record.goal.status is GoalStatus.ACTIVE:
                return GoalCommandResult("the Session Goal is already active")
            queue, observer = self._observe(application, thread_id)
            resumed = application.goal_coordinator.resume(
                thread_id,
                expected_revision=record.goal.revision,
            )
            settled = await self._watch(application, thread_id, queue, resumed)
            return GoalCommandResult(self._format(settled), refresh_session=True)
        except (ApplicationError, ConfigError, OSError, ValueError) as exc:
            return GoalCommandResult(f"Goal error: {exc}")
        finally:
            if application is not None:
                if observer is not None:
                    application.goal_coordinator.remove_event_observer(
                        thread_id,
                        observer,
                    )
                application.close()

    async def _create_and_run(self, objective: str) -> GoalCommandResult:
        application: DeepCodeApplication | None = None
        observer: Callable[[Event], None] | None = None
        thread_id = ""
        try:
            self.owner.bridge.set_title_from(objective)
            application, thread_id = self._open()
            existing = application.goals.read(thread_id)
            if existing is not None and not existing.goal.status.is_terminal:
                return GoalCommandResult(
                    "this Session already has an unfinished Goal; use "
                    "/goal status, /goal resume, or /goal clear"
                )
            selected_skills = tuple(self.owner.selected_skill_ids)
            application.goals.create(
                thread_id,
                objective=objective,
                skill_ids=selected_skills,
            )
            self.owner.selected_skill_ids.clear()
            queue, observer = self._observe(application, thread_id)
            started = application.goal_coordinator.start(thread_id)
            settled = await self._watch(application, thread_id, queue, started)
            return GoalCommandResult(self._format(settled), refresh_session=True)
        except (ApplicationError, ConfigError, OSError, ValueError) as exc:
            return GoalCommandResult(f"Goal error: {exc}")
        finally:
            if application is not None:
                if observer is not None:
                    application.goal_coordinator.remove_event_observer(
                        thread_id,
                        observer,
                    )
                application.close()

    def _open(self) -> tuple[DeepCodeApplication, str]:
        factory = ConfiguredAgentSessionFactory(
            default_permission_mode=PermissionMode.FULL_AUTO,
            streaming=self.owner.reader.interactive,
            max_iterations=self.owner.max_iterations,
        )
        application = DeepCodeApplication.open(
            session_factory=factory,
            session_store=self.owner.bridge.store,
        )
        try:
            project = application.projects.add(
                self.owner.workspace,
                trust_state=TrustState.TRUSTED,
            )
            if project.trust_state is TrustState.UNTRUSTED and bool(
                project.settings.get("sessionDiscovered")
            ):
                # The established CLI already executes in its launch folder.
                # Reconciliation may have created a conservative projection
                # moments before this explicit /goal command; promote only
                # that auto-discovered projection, never a user's deliberate
                # untrusted Desktop project.
                project = application.projects.update(
                    project.id,
                    trust_state=TrustState.TRUSTED,
                )
            if project.trust_state is not TrustState.TRUSTED:
                raise ValueError(
                    "this folder is untrusted; trust it in Desktop before "
                    "starting a durable Goal"
                )
            thread = application.threads.resume(
                self.owner.bridge.session_id,
                workspace_path=self.owner.workspace,
            )
            return application, thread.id
        except BaseException:
            application.close()
            raise

    def _observe(
        self,
        application: DeepCodeApplication,
        thread_id: str,
    ) -> tuple[asyncio.Queue[Event], Callable[[Event], None]]:
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[Event] = asyncio.Queue()

        def observer(event: Event) -> None:
            loop.call_soon_threadsafe(queue.put_nowait, event)

        application.goal_coordinator.add_event_observer(thread_id, observer)
        return queue, observer

    async def _watch(
        self,
        application: DeepCodeApplication,
        thread_id: str,
        queue: asyncio.Queue[Event],
        initial: GoalRecord,
    ) -> GoalRecord:
        pause_requested = asyncio.Event()
        loop = asyncio.get_running_loop()

        def request_pause() -> None:
            pause_requested.set()

        signal_installed = False
        try:
            loop.add_signal_handler(signal.SIGINT, request_pause)
            signal_installed = True
        except (NotImplementedError, RuntimeError):
            pass

        record = initial
        try:
            while record.goal.status is GoalStatus.ACTIVE:
                if pause_requested.is_set():
                    record = application.goal_coordinator.pause(
                        thread_id,
                        expected_revision=record.goal.revision,
                    )
                    break
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=0.1)
                except TimeoutError:
                    pass
                else:
                    self.owner.renderer.on_event(event)
                record = application.goals.read(thread_id) or record
            while not queue.empty():
                self.owner.renderer.on_event(queue.get_nowait())
            return record
        finally:
            if signal_installed:
                try:
                    loop.remove_signal_handler(signal.SIGINT)
                except (NotImplementedError, RuntimeError, ValueError):
                    pass

    @staticmethod
    def _format(record: GoalRecord | None) -> str:
        if record is None:
            return "no Goal is attached to this Session"
        goal = record.goal
        budget = goal.budget
        attempts = (
            f"{record.attempt_count}/{budget.max_attempts}"
            if budget.max_attempts is not None
            else str(record.attempt_count)
        )
        lines = [
            f"Goal {goal.status.value} · attempts {attempts} · "
            f"tokens {goal.tokens_used}",
            goal.objective,
        ]
        if goal.last_reason:
            lines.append(f"reason: {goal.last_reason}")
        return "\n".join(lines)


__all__ = ["GoalCommandResult", "TuiGoalController"]
