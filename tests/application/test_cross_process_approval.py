from __future__ import annotations

import asyncio
import threading
import time
from pathlib import Path

import pytest

from core.application import DeepCodeApplication
from core.application.errors import ApprovalAlreadyResolvedError, ConflictError
from core.domain import TrustState
from core.domain.approval import ApprovalStatus
from core.domain.turn import TurnStatus
from core.events import AgentMessage, Event, TaskComplete, TurnStarted
from core.persistence import ApprovalGrantRepository
from core.sessions import SessionStore


class _ApprovalSession:
    def __init__(self, approval_callback, decisions: list[bool]) -> None:
        self._approval_callback = approval_callback
        self._decisions = decisions
        self.history: list[dict[str, str]] = []

    def load_history(self, messages) -> None:
        self.history = list(messages)

    async def run_stream(self, operation):
        self.history.append({"role": "user", "content": operation.text})
        yield Event("1", TurnStarted())
        approved = await self._approval_callback(
            "write",
            {"file_path": "result.txt", "content": "ok"},
            "mutating tool",
        )
        self._decisions.append(approved)
        yield Event("2", AgentMessage("done"))
        yield Event("3", TaskComplete("done", "completed"))
        self.history.append({"role": "assistant", "content": "done"})

    async def aclose(self) -> None:
        return None


class _ApprovalFactory:
    def __init__(self) -> None:
        self.decisions: list[bool] = []

    def create(self, *, workspace, model, approval_callback):
        return _ApprovalSession(approval_callback, self.decisions)


class _ConcurrentApprovalSession(_ApprovalSession):
    async def run_stream(self, operation):
        self.history.append({"role": "user", "content": operation.text})
        yield Event("1", TurnStarted())
        decisions = await asyncio.gather(
            self._approval_callback(
                "write",
                {"file_path": "first.txt", "content": "first"},
                "first mutation",
            ),
            self._approval_callback(
                "write",
                {"file_path": "second.txt", "content": "second"},
                "second mutation",
            ),
        )
        self._decisions.extend(decisions)
        yield Event("2", AgentMessage("done"))
        yield Event("3", TaskComplete("done", "completed"))
        self.history.append({"role": "assistant", "content": "done"})


class _ConcurrentApprovalFactory(_ApprovalFactory):
    def create(self, *, workspace, model, approval_callback):
        return _ConcurrentApprovalSession(approval_callback, self.decisions)


def _wait_for_status(
    application: DeepCodeApplication,
    turn_id: str,
    status: TurnStatus,
):
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        snapshot = application.turns.read(turn_id)
        if snapshot.turn.status is status:
            return snapshot
        time.sleep(0.01)
    raise AssertionError(
        f"Turn did not reach {status.value}: {application.turns.read(turn_id).turn}"
    )


def _wait_for_approvals(
    application: DeepCodeApplication,
    turn_id: str,
    count: int,
):
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        snapshot = application.turns.read(turn_id)
        if len(snapshot.approvals) == count:
            return snapshot
        time.sleep(0.01)
    raise AssertionError(
        f"Turn did not create {count} approvals: {application.turns.read(turn_id)}"
    )


def _applications(
    tmp_path: Path,
    factory,
) -> tuple[DeepCodeApplication, DeepCodeApplication, str]:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    database_path = tmp_path / "state.sqlite3"
    session_root = tmp_path / "sessions"
    owner = DeepCodeApplication.open(
        database_path,
        session_factory=factory,
        session_store=SessionStore(session_root),
        host_surface="approval-owner",
        run_automation_scheduler=False,
    )
    try:
        project = owner.projects.add(
            str(workspace),
            trust_state=TrustState.TRUSTED,
        )
        thread = owner.threads.start(project.id, title="Shared approval")
        responder = DeepCodeApplication.open(
            database_path,
            session_factory=factory,
            session_store=SessionStore(session_root),
            host_surface="approval-responder",
            run_automation_scheduler=False,
        )
    except BaseException:
        owner.close()
        raise
    return owner, responder, thread.id


@pytest.mark.parametrize(
    ("decision", "expected"),
    (
        (ApprovalStatus.APPROVED_ONCE, True),
        (ApprovalStatus.DENIED, False),
    ),
)
def test_non_owner_application_resolves_owner_approval(
    tmp_path: Path,
    decision: ApprovalStatus,
    expected: bool,
) -> None:
    factory = _ApprovalFactory()
    owner, responder, thread_id = _applications(tmp_path, factory)
    try:
        started = owner.turns.start(thread_id, prompt="Request approval")
        waiting = _wait_for_status(
            owner,
            started.turn.id,
            TurnStatus.WAITING_APPROVAL,
        )
        approval = waiting.approvals[0]

        # Waiting is unbounded by business policy.  Crossing several durable
        # reconciliation intervals must not expire or deny the request.
        time.sleep(0.45)
        still_waiting = responder.turns.read(started.turn.id)
        assert still_waiting.turn.status is TurnStatus.WAITING_APPROVAL
        assert still_waiting.approvals[0].status is ApprovalStatus.PENDING

        resolved = responder.approvals.respond(
            approval.id,
            decision=decision,
        )
        completed = _wait_for_status(
            owner,
            started.turn.id,
            TurnStatus.COMPLETED,
        )

        assert resolved.status is decision
        assert completed.approvals[0].status is decision
        assert factory.decisions == [expected]
    finally:
        responder.close()
        owner.close()


def test_approved_session_grant_is_shared_with_next_turn_on_second_application(
    tmp_path: Path,
) -> None:
    factory = _ApprovalFactory()
    owner, responder, thread_id = _applications(tmp_path, factory)
    try:
        first = owner.turns.start(thread_id, prompt="Remember this exact tool")
        waiting = _wait_for_status(
            owner,
            first.turn.id,
            TurnStatus.WAITING_APPROVAL,
        )
        responder.approvals.respond(
            waiting.approvals[0].id,
            decision=ApprovalStatus.APPROVED_SESSION,
        )
        _wait_for_status(owner, first.turn.id, TurnStatus.COMPLETED)

        # A different Application/worker starts the next Turn.  There is no
        # shared Python object between its ApprovalService and the owner; the
        # durable Thread grant is therefore the only way this can bypass a
        # second prompt.
        follow_up = responder.turns.start(
            thread_id,
            prompt="Use the same exact tool again",
        )
        completed = _wait_for_status(
            owner,
            follow_up.turn.id,
            TurnStatus.COMPLETED,
        )

        assert completed.approvals == ()
        assert factory.decisions == [True, True]
        with owner.database.read() as connection:
            grants = ApprovalGrantRepository(connection).list_for_thread(thread_id)
        assert len(grants) == 1
        assert grants[0].tool_name == "write"
        assert grants[0].source_approval_id == waiting.approvals[0].id
    finally:
        responder.close()
        owner.close()


def test_concurrent_approvals_keep_turn_waiting_until_all_are_resolved(
    tmp_path: Path,
) -> None:
    factory = _ConcurrentApprovalFactory()
    owner, responder, thread_id = _applications(tmp_path, factory)
    try:
        started = owner.turns.start(thread_id, prompt="Request two approvals")
        waiting = _wait_for_approvals(owner, started.turn.id, 2)
        assert waiting.turn.status is TurnStatus.WAITING_APPROVAL
        running_before_responses = sum(
            1
            for event in owner.events.replay(thread_id, limit=1000)
            if event.type == "turn.updated"
            and event.turn_id == started.turn.id
            and event.payload["turn"]["status"] == TurnStatus.RUNNING.value
        )

        responder.approvals.respond(
            waiting.approvals[0].id,
            decision=ApprovalStatus.APPROVED_ONCE,
        )
        partially_resolved = owner.turns.read(started.turn.id)
        assert partially_resolved.turn.status is TurnStatus.WAITING_APPROVAL
        assert [approval.status for approval in partially_resolved.approvals].count(
            ApprovalStatus.PENDING
        ) == 1
        assert (
            sum(
                1
                for event in owner.events.replay(thread_id, limit=1000)
                if event.type == "turn.updated"
                and event.turn_id == started.turn.id
                and event.payload["turn"]["status"] == TurnStatus.RUNNING.value
            )
            == running_before_responses
        )

        responder.approvals.respond(
            waiting.approvals[1].id,
            decision=ApprovalStatus.DENIED,
        )
        completed = _wait_for_status(
            owner,
            started.turn.id,
            TurnStatus.COMPLETED,
        )
        assert [approval.status for approval in completed.approvals] == [
            ApprovalStatus.APPROVED_ONCE,
            ApprovalStatus.DENIED,
        ]
        assert factory.decisions == [True, False]

        running_updates = [
            event
            for event in owner.events.replay(thread_id, limit=1000)
            if event.type == "turn.updated"
            and event.turn_id == started.turn.id
            and event.payload["turn"]["status"] == TurnStatus.RUNNING.value
        ]
        assert len(running_updates) == running_before_responses + 1
    finally:
        responder.close()
        owner.close()


def test_pending_compare_and_swap_allows_only_one_responder(
    tmp_path: Path,
) -> None:
    factory = _ApprovalFactory()
    owner, first, thread_id = _applications(tmp_path, factory)
    second = DeepCodeApplication.open(
        owner.database.path,
        session_factory=factory,
        session_store=SessionStore(tmp_path / "sessions"),
        host_surface="second-responder",
        run_automation_scheduler=False,
    )
    try:
        started = owner.turns.start(thread_id, prompt="Race two responders")
        waiting = _wait_for_status(
            owner,
            started.turn.id,
            TurnStatus.WAITING_APPROVAL,
        )
        approval_id = waiting.approvals[0].id
        barrier = threading.Barrier(2)
        results: list[ApprovalStatus] = []
        errors: list[BaseException] = []

        def respond(
            application: DeepCodeApplication,
            decision: ApprovalStatus,
        ) -> None:
            barrier.wait()
            try:
                results.append(
                    application.approvals.respond(
                        approval_id,
                        decision=decision,
                    ).status
                )
            except BaseException as exc:  # captured for deterministic assertion
                errors.append(exc)

        left = threading.Thread(
            target=respond,
            args=(first, ApprovalStatus.APPROVED_SESSION),
        )
        right = threading.Thread(
            target=respond,
            args=(second, ApprovalStatus.DENIED),
        )
        left.start()
        right.start()
        left.join(timeout=5.0)
        right.join(timeout=5.0)

        assert not left.is_alive()
        assert not right.is_alive()
        assert len(results) == 1
        assert len(errors) == 1
        assert isinstance(errors[0], ApprovalAlreadyResolvedError)
        terminal = _wait_for_status(
            owner,
            started.turn.id,
            TurnStatus.COMPLETED,
        )
        assert terminal.approvals[0].status is results[0]
        with owner.database.read() as connection:
            grants = ApprovalGrantRepository(connection).list_for_thread(thread_id)
        if results[0] is ApprovalStatus.APPROVED_SESSION:
            assert len(grants) == 1
            assert grants[0].source_approval_id == approval_id
        else:
            assert grants == []
    finally:
        second.close()
        first.close()
        owner.close()


def test_approved_session_grant_and_resolution_roll_back_together(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    factory = _ApprovalFactory()
    owner, responder, thread_id = _applications(tmp_path, factory)
    try:
        started = owner.turns.start(thread_id, prompt="Rollback approval grant")
        waiting = _wait_for_status(
            owner,
            started.turn.id,
            TurnStatus.WAITING_APPROVAL,
        )
        approval_id = waiting.approvals[0].id

        original_add = ApprovalGrantRepository.add_if_absent

        def fail_add(
            repository: ApprovalGrantRepository,
            grant,
        ) -> bool:
            raise RuntimeError("simulated grant persistence failure")

        monkeypatch.setattr(ApprovalGrantRepository, "add_if_absent", fail_add)
        with pytest.raises(
            RuntimeError,
            match="simulated grant persistence failure",
        ):
            responder.approvals.respond(
                approval_id,
                decision=ApprovalStatus.APPROVED_SESSION,
            )

        rolled_back = owner.turns.read(started.turn.id)
        assert rolled_back.turn.status is TurnStatus.WAITING_APPROVAL
        assert rolled_back.approvals[0].status is ApprovalStatus.PENDING
        assert rolled_back.items[-1].status.value == "pending"
        with owner.database.read() as connection:
            assert ApprovalGrantRepository(connection).list_for_thread(thread_id) == []

        monkeypatch.setattr(
            ApprovalGrantRepository,
            "add_if_absent",
            original_add,
        )
        responder.approvals.respond(
            approval_id,
            decision=ApprovalStatus.DENIED,
        )
        _wait_for_status(owner, started.turn.id, TurnStatus.COMPLETED)
    finally:
        responder.close()
        owner.close()


def test_durable_cancel_request_wins_over_late_remote_approval(
    tmp_path: Path,
) -> None:
    factory = _ApprovalFactory()
    owner, responder, thread_id = _applications(tmp_path, factory)
    try:
        started = owner.turns.start(thread_id, prompt="Cancel before approval")
        waiting = _wait_for_status(
            owner,
            started.turn.id,
            TurnStatus.WAITING_APPROVAL,
        )
        approval_id = waiting.approvals[0].id

        requested = owner.turns._request_cancellation(started.turn.id)
        assert requested.cancel_requested_at is not None
        with pytest.raises(
            ConflictError,
            match="cancellation was requested",
        ):
            responder.approvals.respond(
                approval_id,
                decision=ApprovalStatus.APPROVED_ONCE,
            )

        accepted, _ = owner.turns.interrupt(thread_id, started.turn.id)
        assert accepted is True
        interrupted = _wait_for_status(
            owner,
            started.turn.id,
            TurnStatus.INTERRUPTED,
        )
        assert interrupted.approvals[0].status is ApprovalStatus.CANCELLED
        assert factory.decisions == []
    finally:
        responder.close()
        owner.close()
