"""Durable foreground monitoring for manual Automation Runs."""

from __future__ import annotations

import builtins
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from core.application.application import DeepCodeApplication
from core.application.errors import (
    ApplicationError,
    ApprovalAlreadyResolvedError,
)
from core.application.views import automation_run_view, turn_view
from core.domain.approval import Approval, ApprovalStatus
from core.domain.automation import AutomationRunStatus
from core.domain.event import DomainEvent

_EVENT_PAGE_SIZE = 500
_LIVE_WAKE_SECONDS = 0.25


class ForegroundApprovalRequiredError(ApplicationError):
    """A non-interactive foreground Run reached a human approval boundary."""

    code = "APPROVAL_REQUIRED"


@dataclass(slots=True)
class _RunCursor:
    thread_id: str
    run_id: str
    sequence: int
    run: dict[str, Any]

    def replay(self, application: DeepCodeApplication) -> None:
        """Consume every currently committed Run update after ``sequence``."""

        while True:
            page = application.events.replay_page(
                self.thread_id,
                after=self.sequence,
                limit=_EVENT_PAGE_SIZE,
            )
            for event in page.events:
                self.sequence = event.sequence
                self._accept(event)
            if not page.has_more:
                return

    def _accept(self, event: DomainEvent) -> None:
        if event.type != "automation.updated":
            return
        candidate = event.payload.get("run")
        if not isinstance(candidate, dict) or candidate.get("id") != self.run_id:
            return
        self.run = dict(candidate)


def run_automation_foreground(
    application: DeepCodeApplication,
    automation_id: str,
    *,
    request_id: str | None,
    interactive: bool,
    input_fn: Callable[[str], str] | None = None,
) -> dict[str, Any]:
    """Trigger and monitor one Run without imposing an execution timeout.

    The broker is only a wake-up channel. State is reconstructed from the
    committed per-Thread event stream, and approval decisions go through the
    shared :class:`ApprovalService`.
    """

    definition = application.automations.read(automation_id)
    sequence = application.events.head(definition.thread_id)
    token = application.broker.subscribe()
    try:
        execution = application.automations.run_now(
            automation_id,
            request_id=request_id,
        )
        cursor = _RunCursor(
            thread_id=execution.run.thread_id,
            run_id=execution.run.id,
            sequence=sequence,
            run=automation_run_view(execution.run),
        )
        reader = input_fn or builtins.input

        while True:
            # Reconciliation closes a process-crash window between Goal/Turn
            # settlement and its Automation Run projection.
            application.automations.reconcile_runs()
            cursor.replay(application)
            if _foreground_settled(cursor.run):
                return _result(application, cursor.run)

            approvals = _pending_approvals(application, cursor.run)
            if approvals:
                if not interactive:
                    _raise_approval_required(
                        application,
                        automation_id=automation_id,
                        cursor=cursor,
                        approval=approvals[0],
                    )
                for approval in approvals:
                    try:
                        _resolve_interactively(application, approval, reader)
                    except EOFError:
                        _raise_approval_required(
                            application,
                            automation_id=automation_id,
                            cursor=cursor,
                            approval=approval,
                        )
                continue

            application.broker.wait_for_events(
                token,
                timeout=_LIVE_WAKE_SECONDS,
            )
            # Drain only to bound the in-process queue. Durable replay above is
            # authoritative and therefore also recovers overflow.
            application.broker.drain(token)
    finally:
        application.broker.unsubscribe(token)


def _foreground_settled(run: dict[str, Any]) -> bool:
    try:
        status = AutomationRunStatus(str(run.get("status")))
    except ValueError:
        return False
    return status.is_terminal or status is AutomationRunStatus.BLOCKED


def _pending_approvals(
    application: DeepCodeApplication,
    run: dict[str, Any],
) -> tuple[Approval, ...]:
    active = application.turns.active_for_thread(str(run["threadId"]))
    if active is None or active.goal_id != run.get("goalId"):
        return ()
    snapshot = application.turns.read(active.id)
    return tuple(
        approval
        for approval in snapshot.approvals
        if approval.status is ApprovalStatus.PENDING
    )


def _resolve_interactively(
    application: DeepCodeApplication,
    approval: Approval,
    reader: Callable[[str], str],
) -> None:
    request = approval.request
    tool_name = str(request.get("toolName") or "tool")
    reason = str(request.get("reason") or "sensitive operation")
    prompt = (
        f"Approval required · {tool_name}: {reason}\n"
        "Approve once [y], allow this tool for the Session [a], or deny [n]? "
    )
    choices = {
        "y": ApprovalStatus.APPROVED_ONCE,
        "yes": ApprovalStatus.APPROVED_ONCE,
        "a": ApprovalStatus.APPROVED_SESSION,
        "always": ApprovalStatus.APPROVED_SESSION,
        "session": ApprovalStatus.APPROVED_SESSION,
        "n": ApprovalStatus.DENIED,
        "no": ApprovalStatus.DENIED,
    }
    while True:
        raw = reader(prompt)
        decision = choices.get(raw.strip().casefold())
        if decision is None:
            prompt = "Enter y (once), a (Session), or n (deny): "
            continue
        try:
            application.approvals.respond(
                approval.id,
                decision=decision,
            )
        except ApprovalAlreadyResolvedError:
            # Another connected client won the durable compare-and-swap.
            pass
        return


def _interrupt_local_owner(
    application: DeepCodeApplication,
    run: dict[str, Any],
) -> str:
    active = application.turns.active_for_thread(str(run["threadId"]))
    if active is None or active.goal_id != run.get("goalId"):
        return "already_settled"
    if active.execution_owner_id != application.execution_coordinator.worker_id:
        return "owned_by_another_runtime"
    application.turns.interrupt(active.thread_id, active.id)
    return "interrupted_local_owner"


def _raise_approval_required(
    application: DeepCodeApplication,
    *,
    automation_id: str,
    cursor: _RunCursor,
    approval: Approval,
) -> None:
    cleanup = _interrupt_local_owner(application, cursor.run)
    application.automations.reconcile_runs()
    cursor.replay(application)
    raise ForegroundApprovalRequiredError(
        "Automation requires human approval in an interactive DeepCode client",
        details={
            "automationId": automation_id,
            "runId": cursor.run["id"],
            "threadId": cursor.run["threadId"],
            "turnId": approval.turn_id,
            "approvalId": approval.id,
            "toolName": approval.request.get("toolName"),
            "reason": approval.request.get("reason"),
            "cleanup": cleanup,
        },
    )


def _result(
    application: DeepCodeApplication,
    run: dict[str, Any],
) -> dict[str, Any]:
    turn_id = run.get("turnId")
    turn = (
        turn_view(application.turns.read(str(turn_id)).turn)
        if isinstance(turn_id, str)
        else None
    )
    return {"run": run, "turn": turn}


__all__ = [
    "ForegroundApprovalRequiredError",
    "run_automation_foreground",
]
