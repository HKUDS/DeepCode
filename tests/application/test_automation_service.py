from __future__ import annotations

import asyncio
import time
from datetime import timedelta
from pathlib import Path

import pytest

from core.application import DeepCodeApplication
from core.application.errors import (
    AutomationBootstrapPendingError,
    AutomationNotFoundError,
    InvalidArgumentError,
    ProjectNotTrustedError,
)
from core.domain import (
    AutomationActivationStatus,
    AutomationRunStatus,
    AutomationScheduleKind,
    AutomationStatus,
    ExecutionPermissionMode,
    TrustState,
    TurnStatus,
)
from core.domain.common import utc_now
from core.events import AgentMessage, Event, TaskComplete, TurnStarted
from core.persistence.event_repository import EventRepository
from core.sessions import SessionStore


class AutomationSession:
    def __init__(self, *, hang: bool, goal_runtime) -> None:
        self.hang = hang
        self.goal_runtime = goal_runtime
        self.history: list[dict[str, str]] = []

    def load_history(self, messages) -> None:
        self.history = list(messages)

    async def run_stream(self, op):
        self.history.append({"role": "user", "content": op.text})
        yield Event("1", TurnStarted())
        if self.hang:
            await asyncio.Event().wait()
        self.goal_runtime.request(
            status="complete",
            reason="The requested automation and verification completed.",
        )
        yield Event("2", AgentMessage("automation complete"))
        yield Event("3", TaskComplete("automation complete", "completed"))
        self.history.append({"role": "assistant", "content": "automation complete"})

    async def aclose(self) -> None:
        return None


class AutomationFactory:
    def __init__(self, *, hang: bool = False) -> None:
        self.hang = hang
        self.sessions: list[AutomationSession] = []
        self.permission_modes: list[ExecutionPermissionMode | None] = []

    def create(
        self,
        *,
        workspace,
        model,
        approval_callback,
        goal_runtime,
        permission_mode_override=None,
    ):
        self.permission_modes.append(permission_mode_override)
        session = AutomationSession(
            hang=self.hang,
            goal_runtime=goal_runtime,
        )
        self.sessions.append(session)
        return session


def _wait_for_run(
    application: DeepCodeApplication,
    automation_id: str,
    status: AutomationRunStatus,
):
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        runs = application.automations.list_runs(automation_id)
        if runs and runs[0].status is status:
            return runs[0]
        time.sleep(0.01)
    raise AssertionError(
        f"automation did not reach {status}: "
        f"{application.automations.list_runs(automation_id)}"
    )


def test_automation_creation_requires_trust_and_owns_a_canonical_goal_thread(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    application = DeepCodeApplication.open(tmp_path / "state.sqlite3")
    project = application.projects.add(str(workspace))
    try:
        with pytest.raises(ProjectNotTrustedError):
            application.automations.create(
                project_id=project.id,
                name="Repository review",
                prompt="Review the repository",
                schedule_kind=AutomationScheduleKind.MANUAL,
            )

        application.projects.update(project.id, trust_state=TrustState.TRUSTED)
        created = application.automations.create(
            project_id=project.id,
            name="Repository review",
            prompt="Review the repository",
            schedule_kind=AutomationScheduleKind.MANUAL,
        )
        assert created.automation.thread_id == created.thread.id
        assert created.thread.mode.value == "goal"
        assert created.automation.next_run_at is None
        session = application.session_store.get_session(created.thread.id)
        assert session is not None
        assert session.metadata["kind"] == "automation"
        assert session.metadata["automation_id"] == created.automation.id
        assert session.metadata["mode"] == "goal"
    finally:
        application.close()


def test_committed_automation_repairs_session_after_materialization_failure_on_restart(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    database_path = tmp_path / "state.sqlite3"
    session_root = tmp_path / "sessions"
    owner = DeepCodeApplication.open(
        database_path,
        session_store=SessionStore(session_root),
    )
    project = owner.projects.add(
        str(workspace),
        trust_state=TrustState.TRUSTED,
    )
    monkeypatch.setattr(
        owner.threads,
        "materialize_session",
        lambda _thread_id: (_ for _ in ()).throw(OSError("disk unavailable")),
    )
    try:
        with pytest.raises(AutomationBootstrapPendingError) as raised:
            owner.automations.create(
                project_id=project.id,
                name="Recoverable bootstrap",
                prompt="Inspect and verify the workspace",
                schedule_kind=AutomationScheduleKind.MANUAL,
            )
        details = raised.value.details
        automation_id = str(details["automationId"])
        thread_id = str(details["threadId"])
        assert details == {
            "automationId": automation_id,
            "threadId": thread_id,
            "accepted": True,
            "recovery": "refresh_or_reopen",
        }
        assert raised.value.retryable is False
        assert "durably created" in raised.value.user_message
        assert "do not retry Create" in raised.value.user_message
        assert owner.session_store.get_session(thread_id) is None
        with owner.database.read() as connection:
            assert connection.execute("SELECT COUNT(*) FROM threads").fetchone()[0] == 1
            assert (
                connection.execute("SELECT COUNT(*) FROM automations").fetchone()[0]
                == 1
            )
            assert (
                connection.execute(
                    "SELECT COUNT(*) FROM automation_revisions"
                ).fetchone()[0]
                == 1
            )
            events_before = tuple(
                (event.id, event.sequence, event.type)
                for event in EventRepository(connection).replay(thread_id)
            )
        assert [(sequence, kind) for _id, sequence, kind in events_before] == [
            (1, "thread.created"),
            (2, "automation.updated"),
        ]
    finally:
        owner.close()

    repaired = DeepCodeApplication.open(
        database_path,
        session_store=SessionStore(session_root),
    )
    try:
        automation = repaired.automations.read(automation_id)
        thread = repaired.threads.read(thread_id)
        session = repaired.session_store.get_session(thread_id)
        assert automation.thread_id == thread.id
        assert session is not None
        assert session.metadata["kind"] == "automation"
        assert session.metadata["automation_id"] == automation_id
        assert session.metadata["mode"] == "goal"
        assert session.created_at == thread.created_at.isoformat()
        assert session.updated_at == thread.updated_at.isoformat()

        repaired.threads.materialize_session(thread_id)
        repaired.threads.materialize_session(thread_id)
        repaired.threads.reconcile()
        with repaired.database.read() as connection:
            events_after = tuple(
                (event.id, event.sequence, event.type)
                for event in EventRepository(connection).replay(thread_id)
            )
        assert events_after == events_before
    finally:
        repaired.close()


def test_live_joiner_can_run_a_committed_pending_automation_bootstrap(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    database_path = tmp_path / "state.sqlite3"
    session_root = tmp_path / "sessions"
    owner = DeepCodeApplication.open(
        database_path,
        session_store=SessionStore(session_root),
        session_factory=AutomationFactory(),
    )
    project = owner.projects.add(
        str(workspace),
        trust_state=TrustState.TRUSTED,
    )
    joiner_factory = AutomationFactory()
    joiner = DeepCodeApplication.open(
        database_path,
        session_store=SessionStore(session_root),
        session_factory=joiner_factory,
    )
    monkeypatch.setattr(
        owner.threads,
        "materialize_session",
        lambda _thread_id: (_ for _ in ()).throw(OSError("creator stopped")),
    )
    try:
        with pytest.raises(AutomationBootstrapPendingError) as raised:
            owner.automations.create(
                project_id=project.id,
                name="Live bootstrap",
                prompt="Inspect and verify the workspace",
                schedule_kind=AutomationScheduleKind.MANUAL,
            )
        automation_id = str(raised.value.details["automationId"])
        thread_id = str(raised.value.details["threadId"])
        assert joiner.session_store.get_session(thread_id) is None

        execution = joiner.automations.run_now(
            automation_id,
            request_id="live-bootstrap-repair",
        )
        assert execution.turn is not None
        assert execution.turn.thread_id == thread_id
        settled = joiner.automations.wait_until_terminal(
            execution.run.id,
            timeout=3,
            poll_interval=0.01,
        )
        assert settled is not None
        assert settled.status is AutomationRunStatus.COMPLETED
        session = joiner.session_store.get_session(thread_id)
        assert session is not None
        assert session.metadata["automation_id"] == automation_id
        with joiner.database.read() as connection:
            creation_events = [
                event
                for event in EventRepository(connection).replay(thread_id)
                if event.type == "thread.created"
                or (event.type == "automation.updated" and "run" not in event.payload)
            ]
        assert [event.type for event in creation_events] == [
            "thread.created",
            "automation.updated",
        ]
        assert len(joiner_factory.sessions) == 1
    finally:
        joiner.close()
        owner.close()


def test_derived_session_index_failure_preserves_recoverable_canonical_bootstrap(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    application = DeepCodeApplication.open(tmp_path / "state.sqlite3")
    project = application.projects.add(
        str(workspace),
        trust_state=TrustState.TRUSTED,
    )
    original_index = application.session_store._index_session
    monkeypatch.setattr(
        application.session_store,
        "_index_session",
        lambda _session: (_ for _ in ()).throw(OSError("index unavailable")),
    )
    try:
        with pytest.raises(AutomationBootstrapPendingError) as raised:
            application.automations.create(
                project_id=project.id,
                name="Index-independent bootstrap",
                prompt="Canonical JSONL must survive",
                schedule_kind=AutomationScheduleKind.MANUAL,
            )
        thread_id = str(raised.value.details["threadId"])
        automation_id = str(raised.value.details["automationId"])
        session = application.session_store.get_session(thread_id)
        assert session is not None
        assert session.metadata["automation_id"] == automation_id

        monkeypatch.setattr(
            application.session_store,
            "_index_session",
            original_index,
        )
        recovered = application.threads.materialize_session(thread_id)
        assert recovered.session_id == thread_id
    finally:
        application.close()


def test_automation_creation_transaction_failure_leaves_no_ghost_thread(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    application = DeepCodeApplication.open(tmp_path / "state.sqlite3")
    project = application.projects.add(
        str(workspace),
        trust_state=TrustState.TRUSTED,
    )
    subscriber = application.broker.subscribe()
    original_append = EventRepository.append

    def fail_second_event(repository, **kwargs):
        if kwargs["type"] == "automation.updated":
            raise RuntimeError("injected transaction failure")
        return original_append(repository, **kwargs)

    monkeypatch.setattr(EventRepository, "append", fail_second_event)
    try:
        with pytest.raises(RuntimeError, match="injected transaction failure"):
            application.automations.create(
                project_id=project.id,
                name="Never committed",
                prompt="This work must not exist",
                schedule_kind=AutomationScheduleKind.MANUAL,
            )
        with application.database.read() as connection:
            for table in (
                "threads",
                "automations",
                "automation_revisions",
                "event_log",
            ):
                assert (
                    connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                    == 0
                )
        assert application.session_store.list_sessions() == []
        assert application.broker.drain(subscriber).events == ()
    finally:
        application.broker.unsubscribe(subscriber)
        application.close()


def test_project_remove_after_session_write_discards_only_new_automation_session(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    application = DeepCodeApplication.open(tmp_path / "state.sqlite3")
    project = application.projects.add(
        str(workspace),
        trust_state=TrustState.TRUSTED,
    )
    original_create = application.session_store.create_session
    created_session_id: str | None = None

    def remove_project_after_write(**kwargs):
        nonlocal created_session_id
        session = original_create(**kwargs)
        created_session_id = session.session_id
        assert session.metadata["kind"] == "automation"
        assert session.metadata["automation_id"]
        application.projects.remove(project.id)
        return session

    monkeypatch.setattr(
        application.session_store,
        "create_session",
        remove_project_after_write,
    )
    try:
        with pytest.raises(AutomationNotFoundError):
            application.automations.create(
                project_id=project.id,
                name="Removed concurrently",
                prompt="This definition loses its project",
                schedule_kind=AutomationScheduleKind.MANUAL,
            )
        assert created_session_id is not None
        assert application.session_store.get_session(created_session_id) is None
        with application.database.read() as connection:
            for table in (
                "projects",
                "threads",
                "automations",
                "automation_revisions",
                "event_log",
            ):
                assert (
                    connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                    == 0
                )
    finally:
        application.close()


def test_incompatible_preexisting_session_is_rejected_and_never_deleted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    application = DeepCodeApplication.open(tmp_path / "state.sqlite3")
    project = application.projects.add(
        str(workspace),
        trust_state=TrustState.TRUSTED,
    )
    original_create = application.session_store.create_session
    collision_id: str | None = None

    def inject_collision(**kwargs):
        nonlocal collision_id
        collision_id = str(kwargs["session_id"])
        original_create(
            session_id=collision_id,
            title="Unrelated canonical Session",
            metadata={
                "kind": "tui",
                "workspace": str(workspace),
                "project_path": str(workspace),
                "mode": "code",
                "archived": False,
            },
        )
        return original_create(**kwargs)

    monkeypatch.setattr(
        application.session_store,
        "create_session",
        inject_collision,
    )
    try:
        with pytest.raises(AutomationBootstrapPendingError):
            application.automations.create(
                project_id=project.id,
                name="Collision",
                prompt="Do not overwrite another Session",
                schedule_kind=AutomationScheduleKind.MANUAL,
            )
        assert collision_id is not None
        collision = application.session_store.get_session(collision_id)
        assert collision is not None
        assert collision.title == "Unrelated canonical Session"
        assert collision.metadata["kind"] == "tui"
        assert collision.messages == []
    finally:
        application.close()


def test_reconcile_never_deletes_an_empty_session_owned_by_another_database(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    session_root = tmp_path / "shared-sessions"
    first = DeepCodeApplication.open(
        tmp_path / "first.sqlite3",
        session_store=SessionStore(session_root),
    )
    project = first.projects.add(
        str(workspace),
        trust_state=TrustState.TRUSTED,
    )
    try:
        created = first.automations.create(
            project_id=project.id,
            name="Database-owned Automation",
            prompt="Keep this canonical Session",
            schedule_kind=AutomationScheduleKind.MANUAL,
        )
        thread_id = created.thread.id
        automation_id = created.automation.id
    finally:
        first.close()

    second = DeepCodeApplication.open(
        tmp_path / "second.sqlite3",
        session_store=SessionStore(session_root),
    )
    try:
        second.threads.reconcile()
        preserved = second.session_store.get_session(thread_id)
        assert preserved is not None
        assert preserved.messages == []
        assert preserved.metadata["kind"] == "automation"
        assert preserved.metadata["automation_id"] == automation_id
    finally:
        second.close()


def test_run_bootstrap_failure_settles_durably_instead_of_staying_queued(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    application = DeepCodeApplication.open(tmp_path / "state.sqlite3")
    project = application.projects.add(
        str(workspace),
        trust_state=TrustState.TRUSTED,
    )
    try:
        created = application.automations.create(
            project_id=project.id,
            name="Blocked bootstrap",
            prompt="Wait until the Session can be repaired",
            schedule_kind=AutomationScheduleKind.MANUAL,
        )
        assert application.session_store.delete_session(created.thread.id)
        monkeypatch.setattr(
            application.threads,
            "materialize_session",
            lambda _thread_id: (_ for _ in ()).throw(
                OSError("filesystem temporarily unavailable")
            ),
        )

        execution = application.automations.run_now(
            created.automation.id,
            request_id="blocked-bootstrap",
        )

        assert execution.turn is None
        assert execution.run.status is AutomationRunStatus.BLOCKED
        latest = application.automations.list_runs(created.automation.id)[0]
        assert latest.id == execution.run.id
        assert latest.status is AutomationRunStatus.BLOCKED
        assert "Session bootstrap is unavailable" in latest.detail

        application.automations.reconcile_runs()
        application.automations.reconcile_runs()
        assert application.automations.list_runs(created.automation.id)[0] == latest
    finally:
        application.close()


def test_automation_definitions_and_retired_run_history_are_explicitly_paged(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    application = DeepCodeApplication.open(
        tmp_path / "state.sqlite3",
        session_factory=AutomationFactory(),
    )
    project = application.projects.add(
        str(workspace),
        trust_state=TrustState.TRUSTED,
    )
    try:
        created = [
            application.automations.create(
                project_id=project.id,
                name=f"Paged automation {index}",
                prompt=f"Run paged task {index}",
                schedule_kind=AutomationScheduleKind.MANUAL,
            )
            for index in range(3)
        ]
        run_ids: list[str] = []
        for index in range(3):
            execution = application.automations.run_now(
                created[0].automation.id,
                request_id=f"page-{index}",
            )
            settled = application.automations.wait_until_terminal(
                execution.run.id,
                timeout=3,
                poll_interval=0.01,
            )
            assert settled is not None
            run_ids.append(settled.id)

        first = application.automations.list(project.id, limit=2, offset=0)
        second = application.automations.list(project.id, limit=2, offset=2)
        assert first.has_more is True
        assert first.next_offset == 2
        assert second.has_more is False
        assert second.next_offset is None
        assert len(first.automations) == 2
        assert len(second.automations) == 1
        assert {
            automation.id for automation in (*first.automations, *second.automations)
        } == {item.automation.id for item in created}
        assert {run.automation_id for run in first.latest_runs} <= {
            automation.id for automation in first.automations
        }
        assert {run.automation_id for run in second.latest_runs} <= {
            automation.id for automation in second.automations
        }

        assert application.automations.remove(created[0].automation.id)
        run_first = application.automations.list_runs(
            created[0].automation.id,
            limit=2,
            offset=0,
        )
        run_second = application.automations.list_runs(
            created[0].automation.id,
            limit=2,
            offset=2,
        )
        assert run_first.has_more is True
        assert run_first.next_offset == 2
        assert run_second.has_more is False
        assert run_second.next_offset is None
        assert {run.id for run in (*run_first.runs, *run_second.runs)} == set(run_ids)
    finally:
        application.close()


@pytest.mark.parametrize(
    ("limit", "offset"),
    [(0, 0), (501, 0), (1, -1)],
)
def test_automation_pagination_rejects_invalid_bounds(
    tmp_path: Path,
    limit: int,
    offset: int,
) -> None:
    application = DeepCodeApplication.open(tmp_path / "state.sqlite3")
    try:
        with pytest.raises(InvalidArgumentError, match="automation page"):
            application.automations.list(limit=limit, offset=offset)
    finally:
        application.close()


def test_manual_automation_runs_through_normal_turn_and_session_lifecycle(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    factory = AutomationFactory()
    application = DeepCodeApplication.open(
        tmp_path / "state.sqlite3",
        session_factory=factory,
    )
    project = application.projects.add(
        str(workspace),
        trust_state=TrustState.TRUSTED,
    )
    try:
        created = application.automations.create(
            project_id=project.id,
            name="Fix regressions",
            prompt="Find and fix regressions, then verify the result",
            schedule_kind=AutomationScheduleKind.MANUAL,
        )
        execution = application.automations.run_now(created.automation.id)
        assert execution.turn is not None
        run = application.automations.wait_until_terminal(
            execution.run.id,
            timeout=3,
            poll_interval=0.01,
        )
        assert run is not None
        assert run.status is AutomationRunStatus.COMPLETED
        assert run.turn_id == execution.turn.id
        assert (
            application.turns.read(execution.turn.id).turn.status
            is TurnStatus.COMPLETED
        )
        session = application.session_store.get_session(created.thread.id)
        assert session is not None
        assert [message.role for message in session.messages] == [
            "user",
            "assistant",
        ]
        assert session.messages[0].content == created.automation.prompt
        assert session.messages[0].metadata["client"] == "automation"
        assert session.messages[0].metadata["source"] == "automation"
        assert session.messages[1].metadata["client"] == "automation"
        assert factory.permission_modes == [ExecutionPermissionMode.DEFAULT]
    finally:
        application.close()


def test_same_automation_keeps_permission_policy_across_cli_and_app_server_workers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "deepcode_config.json").write_text(
        '{"security":{"permissionMode":"plan"}}',
        encoding="utf-8",
    )
    monkeypatch.setenv("DEEPCODE_HOME", str(home))
    database_path = tmp_path / "state.sqlite3"
    session_root = tmp_path / "sessions"

    cli_factory = AutomationFactory()
    cli = DeepCodeApplication.open(
        database_path,
        session_factory=cli_factory,
        session_store=SessionStore(session_root),
        host_surface="cli",
        run_automation_scheduler=False,
    )
    try:
        project = cli.projects.add(
            str(workspace),
            trust_state=TrustState.TRUSTED,
        )
        created = cli.automations.create(
            project_id=project.id,
            name="Cross-host policy",
            prompt="Inspect and verify the workspace",
            schedule_kind=AutomationScheduleKind.MANUAL,
        )
        first = cli.automations.run_now(created.automation.id)
        assert first.turn is not None
        first_run = cli.automations.wait_until_terminal(
            first.run.id,
            timeout=3,
            poll_interval=0.01,
        )
        assert first_run is not None
        assert first_run.status is AutomationRunStatus.COMPLETED
        assert (
            cli.turns.read(first.turn.id).turn.execution_permission_mode
            is ExecutionPermissionMode.PLAN
        )
        automation_id = created.automation.id
    finally:
        cli.close()

    server_factory = AutomationFactory()
    server = DeepCodeApplication.open(
        database_path,
        session_factory=server_factory,
        session_store=SessionStore(session_root),
        host_surface="app-server",
        run_automation_scheduler=False,
    )
    try:
        second = server.automations.run_now(automation_id)
        assert second.turn is not None
        second_run = server.automations.wait_until_terminal(
            second.run.id,
            timeout=3,
            poll_interval=0.01,
        )
        assert second_run is not None
        assert second_run.status is AutomationRunStatus.COMPLETED
        assert (
            server.turns.read(second.turn.id).turn.execution_permission_mode
            is ExecutionPermissionMode.PLAN
        )
        assert cli_factory.permission_modes == [ExecutionPermissionMode.PLAN]
        assert server_factory.permission_modes == [ExecutionPermissionMode.PLAN]
    finally:
        server.close()


def test_wait_for_run_has_caller_owned_timeout_without_a_runtime_budget(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    application = DeepCodeApplication.open(
        tmp_path / "state.sqlite3",
        session_factory=AutomationFactory(hang=True),
    )
    project = application.projects.add(
        str(workspace),
        trust_state=TrustState.TRUSTED,
    )
    try:
        created = application.automations.create(
            project_id=project.id,
            name="Long review",
            prompt="Continue until the review is complete",
            schedule_kind=AutomationScheduleKind.MANUAL,
        )
        execution = application.automations.run_now(created.automation.id)
        assert execution.turn is not None

        assert (
            application.automations.wait_until_terminal(
                execution.run.id,
                timeout=0,
            )
            is None
        )
        with pytest.raises(ValueError, match="timeout"):
            application.automations.wait_until_terminal(
                execution.run.id,
                timeout=-1,
            )

        application.turns.interrupt(
            execution.turn.thread_id,
            execution.turn.id,
        )
        blocked = _wait_for_run(
            application,
            created.automation.id,
            AutomationRunStatus.BLOCKED,
        )
        assert blocked.completed_at is None
    finally:
        application.close()


def test_interval_automation_coalesces_missed_runs_and_skips_when_busy(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    factory = AutomationFactory(hang=True)
    application = DeepCodeApplication.open(
        tmp_path / "state.sqlite3",
        session_factory=factory,
    )
    project = application.projects.add(
        str(workspace),
        trust_state=TrustState.TRUSTED,
    )
    try:
        created = application.automations.create(
            project_id=project.id,
            name="Continuous review",
            prompt="Review the current repository state",
            schedule_kind=AutomationScheduleKind.INTERVAL,
            interval_seconds=60,
        )
        first_due = created.automation.next_run_at
        assert first_due is not None
        first_runs = application.automations.run_due(first_due)
        assert len(first_runs) == 1
        running = _wait_for_run(
            application,
            created.automation.id,
            AutomationRunStatus.RUNNING,
        )
        assert running.turn_id is not None

        second_runs = application.automations.run_due(first_due + timedelta(seconds=61))
        assert len(second_runs) == 1
        assert second_runs[0].status is AutomationRunStatus.SKIPPED
        assert "still active" in second_runs[0].detail
        refreshed = application.automations.read(created.automation.id)
        assert refreshed.next_run_at == first_due + timedelta(seconds=120)

        application.turns.interrupt(running.thread_id, running.turn_id)
        _wait_for_run(
            application,
            created.automation.id,
            AutomationRunStatus.SKIPPED,
        )
    finally:
        application.close()


def test_pause_and_resume_recalculate_the_next_occurrence(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    application = DeepCodeApplication.open(tmp_path / "state.sqlite3")
    project = application.projects.add(
        str(workspace),
        trust_state=TrustState.TRUSTED,
    )
    try:
        created = application.automations.create(
            project_id=project.id,
            name="Scheduled maintenance",
            prompt="Perform repository maintenance",
            schedule_kind=AutomationScheduleKind.INTERVAL,
            interval_seconds=3600,
        )
        paused = application.automations.update(
            created.automation.id,
            status=AutomationActivationStatus.PAUSED,
        )
        assert paused.next_run_at is None
        resumed_at = utc_now()
        resumed = application.automations.update(
            created.automation.id,
            status=AutomationActivationStatus.ENABLED,
        )
        assert resumed.next_run_at is not None
        assert resumed.next_run_at >= resumed_at + timedelta(seconds=3599)
        with pytest.raises(InvalidArgumentError, match="enabled or paused"):
            application.automations.update(
                created.automation.id,
                status=AutomationStatus.RETIRED,  # type: ignore[arg-type]
            )
        assert (
            application.automations.read(created.automation.id).status
            is AutomationStatus.ENABLED
        )
    finally:
        application.close()


def test_activation_status_controls_interval_scheduling_not_manual_run_now(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    application = DeepCodeApplication.open(
        tmp_path / "state.sqlite3",
        session_factory=AutomationFactory(),
    )
    project = application.projects.add(
        str(workspace),
        trust_state=TrustState.TRUSTED,
    )
    try:
        with pytest.raises(
            InvalidArgumentError,
            match="manual automations are always enabled",
        ):
            application.automations.create(
                project_id=project.id,
                name="Invalid paused manual",
                prompt="This definition must not be created",
                schedule_kind=AutomationScheduleKind.MANUAL,
                enabled=False,
            )

        manual = application.automations.create(
            project_id=project.id,
            name="Manual review",
            prompt="Run only when requested",
            schedule_kind=AutomationScheduleKind.MANUAL,
        )
        with pytest.raises(
            InvalidArgumentError,
            match="manual automations are always enabled",
        ):
            application.automations.update(
                manual.automation.id,
                status=AutomationActivationStatus.PAUSED,
            )

        paused = application.automations.create(
            project_id=project.id,
            name="Paused interval",
            prompt="Run manually while the schedule is paused",
            schedule_kind=AutomationScheduleKind.INTERVAL,
            interval_seconds=3_600,
            enabled=False,
        )
        execution = application.automations.run_now(
            paused.automation.id,
            request_id="paused-run-now",
        )
        settled = application.automations.wait_until_terminal(
            execution.run.id,
            timeout=3,
            poll_interval=0.01,
        )
        assert settled is not None
        assert settled.status is AutomationRunStatus.COMPLETED

        normalized = application.automations.update(
            paused.automation.id,
            schedule_kind=AutomationScheduleKind.MANUAL,
        )
        assert normalized.schedule_kind is AutomationScheduleKind.MANUAL
        assert normalized.status is AutomationStatus.ENABLED
        assert normalized.interval_seconds is None
        assert normalized.next_run_at is None

        another_paused = application.automations.create(
            project_id=project.id,
            name="Still paused interval",
            prompt="Remain an interval definition",
            schedule_kind=AutomationScheduleKind.INTERVAL,
            interval_seconds=3_600,
            enabled=False,
        )
        with pytest.raises(
            InvalidArgumentError,
            match="manual automations are always enabled",
        ):
            application.automations.update(
                another_paused.automation.id,
                schedule_kind=AutomationScheduleKind.MANUAL,
                status=AutomationActivationStatus.PAUSED,
            )
    finally:
        application.close()
