import sqlite3
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import timedelta
from pathlib import Path

import pytest

from core.domain import (
    Approval,
    ApprovalCategory,
    Artifact,
    Automation,
    AutomationRun,
    AutomationRunStatus,
    AutomationScheduleKind,
    AutomationTrigger,
    ExecutionProfile,
    Item,
    ItemKind,
    ItemStatus,
    Project,
    Thread,
    ThreadMode,
    Turn,
    TurnExecutor,
    WorkflowRun,
)
from core.domain.automation import AutomationOccurrence, AutomationRevision
from core.domain.common import utc_now
from core.persistence import (
    ApprovalRepository,
    ArtifactRepository,
    AutomationRepository,
    AutomationRunRepository,
    Database,
    ItemRepository,
    ProjectRepository,
    ThreadRepository,
    TurnRepository,
    WorkflowRepository,
)
from core.persistence.automation_repository import (
    AutomationOccurrenceRepository,
    AutomationRevisionRepository,
)
from core.persistence.migrations import LATEST_SCHEMA_VERSION, current_version, migrate


def test_database_enables_wal_foreign_keys_and_migrations(tmp_path: Path) -> None:
    database = Database(tmp_path / "state.sqlite3")
    database.initialize()
    with database.read() as connection:
        assert current_version(connection) == LATEST_SCHEMA_VERSION
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        migrate(connection, 0)
        assert current_version(connection) == 0
        migrate(connection, LATEST_SCHEMA_VERSION)
        assert current_version(connection) == LATEST_SCHEMA_VERSION


def test_concurrent_initialization_converges_on_one_schema(tmp_path: Path) -> None:
    path = tmp_path / "state.sqlite3"
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda _: Database(path).initialize(), range(16)))
    with Database(path).read() as connection:
        assert current_version(connection) == LATEST_SCHEMA_VERSION
        assert (
            connection.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0]
            == LATEST_SCHEMA_VERSION
        )


def test_upgrade_creates_one_consistent_backup_before_concurrent_migration(
    tmp_path: Path,
) -> None:
    path = tmp_path / "state.sqlite3"
    database = Database(path)
    database.initialize(target_version=1)
    project = Project(canonical_path=str(tmp_path), display_name="Before upgrade")
    with database.transaction() as connection:
        ProjectRepository(connection).add(project)

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda _: Database(path).initialize(), range(16)))

    backups = list(
        (tmp_path / "backups").glob(
            f"state.pre-v1-to-v{LATEST_SCHEMA_VERSION}-*.sqlite3"
        )
    )
    assert len(backups) == 1
    with sqlite3.connect(backups[0]) as backup:
        assert current_version(backup) == 1
        assert backup.execute(
            "SELECT display_name FROM projects WHERE id = ?", (project.id,)
        ).fetchone() == ("Before upgrade",)
        assert backup.execute("PRAGMA quick_check").fetchone() == ("ok",)
    with database.read() as connection:
        assert current_version(connection) == LATEST_SCHEMA_VERSION


def test_fresh_and_current_database_do_not_create_migration_backups(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "state.sqlite3")

    database.initialize()
    database.initialize()

    assert not (tmp_path / "backups").exists()


def test_transaction_rolls_back_the_whole_write(tmp_path: Path) -> None:
    database = Database(tmp_path / "state.sqlite3")
    database.initialize()
    with pytest.raises(RuntimeError):
        with database.transaction() as connection:
            ProjectRepository(connection).add(
                Project(canonical_path=str(tmp_path), display_name="Will roll back")
            )
            raise RuntimeError("abort")
    with database.read() as connection:
        assert ProjectRepository(connection).list() == []


def test_repositories_round_trip_every_p1_entity(tmp_path: Path) -> None:
    database = Database(tmp_path / "state.sqlite3")
    database.initialize()
    project = Project(canonical_path=str(tmp_path), display_name="DeepCode")
    thread = Thread(
        project_id=project.id,
        title="P1",
        mode=ThreadMode.CODE,
        model="moonshotai/kimi-k2.6",
        connection_id="router-test",
        reasoning_effort="high",
        workspace_path=str(tmp_path),
    )
    turn = Turn(
        thread_id=thread.id,
        ordinal=1,
        prompt="Build P1",
        executor=TurnExecutor.WORKFLOW,
        execution_profile=ExecutionProfile(
            connection_id="router-test",
            provider_name="openrouter",
            adapter="openai_compat",
            model_id="moonshotai/kimi-k2.6",
            context_window=256_000,
            max_output_tokens=128_000,
            max_tokens=8192,
            temperature=0.1,
            reasoning_effort=None,
            config_revision="0123456789abcdef",
        ),
    )
    item = Item(
        thread_id=thread.id,
        turn_id=turn.id,
        ordinal=1,
        kind=ItemKind.TOOL_CALL,
        status=ItemStatus.PENDING,
        summary="Run tests",
        payload={"command": "pytest"},
    )
    approval = Approval(
        thread_id=thread.id,
        turn_id=turn.id,
        item_id=item.id,
        category=ApprovalCategory.COMMAND,
        request={"command": "pytest"},
    )
    workflow = WorkflowRun(
        thread_id=thread.id,
        turn_id=turn.id,
        kind="paper",
    )
    artifact = Artifact(
        thread_id=thread.id,
        turn_id=turn.id,
        workflow_run_id=workflow.id,
        kind="report",
        name="report.md",
        media_type="text/markdown",
        storage_path="artifacts/report.md",
        byte_size=12,
    )

    with database.transaction() as connection:
        ProjectRepository(connection).add(project)
        ThreadRepository(connection).add(thread)
        TurnRepository(connection).add(turn)
        ItemRepository(connection).add(item)
        ApprovalRepository(connection).add(approval)
        WorkflowRepository(connection).add(workflow)
        ArtifactRepository(connection).add(artifact)

    with database.read() as connection:
        assert ProjectRepository(connection).get(project.id) == project
        assert ThreadRepository(connection).get(thread.id) == thread
        assert TurnRepository(connection).get(turn.id) == turn
        assert ItemRepository(connection).get(item.id) == item
        assert ApprovalRepository(connection).get(approval.id) == approval
        assert WorkflowRepository(connection).get(workflow.id) == workflow
        assert ArtifactRepository(connection).get(artifact.id) == artifact


def test_automation_repositories_round_trip_and_find_due_jobs(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "state.sqlite3")
    database.initialize()
    now = utc_now()
    project = Project(canonical_path=str(tmp_path), display_name="Automation")
    thread = Thread(
        project_id=project.id,
        title="Scheduled goal",
        mode=ThreadMode.GOAL,
        workspace_path=str(tmp_path),
    )
    automation_id = "auto_repository_review"
    revision = AutomationRevision(
        automation_id=automation_id,
        ordinal=1,
        instruction="Review the repository and fix regressions",
    )
    automation = Automation(
        id=automation_id,
        project_id=project.id,
        thread_id=thread.id,
        name="Repository review",
        current_revision_id=revision.id,
        prompt=revision.instruction,
        schedule_kind=AutomationScheduleKind.INTERVAL,
        interval_seconds=3600,
        next_run_at=now,
    )
    occurrence = AutomationOccurrence(
        automation_id=automation.id,
        kind=AutomationTrigger.SCHEDULED,
        occurrence_key=f"scheduled:{now.isoformat()}",
        nominal_at=now,
        observed_at=now,
    )
    run = AutomationRun(
        automation_id=automation.id,
        revision_id=revision.id,
        occurrence_id=occurrence.id,
        thread_id=thread.id,
        trigger=AutomationTrigger.SCHEDULED,
        status=AutomationRunStatus.QUEUED,
        scheduled_for=now,
    )

    with database.transaction() as connection:
        ProjectRepository(connection).add(project)
        ThreadRepository(connection).add(thread)
        AutomationRevisionRepository(connection).add(revision)
        AutomationRepository(connection).add(automation)
        AutomationOccurrenceRepository(connection).add(occurrence)
        AutomationRunRepository(connection).add(run)

    with database.read() as connection:
        automations = AutomationRepository(connection)
        runs = AutomationRunRepository(connection)
        assert automations.get(automation.id) == automation
        assert automations.list_due(now) == [automation]
        assert automations.next_due_at() == now
        assert runs.get(run.id) == run
        assert runs.latest_for_automation(automation.id) == run


def test_due_automation_is_claimed_once_across_connections(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "state.sqlite3")
    database.initialize()
    now = utc_now()
    project = Project(canonical_path=str(tmp_path), display_name="Claim")
    thread = Thread(
        project_id=project.id,
        title="Claimed goal",
        mode=ThreadMode.GOAL,
        workspace_path=str(tmp_path),
    )
    automation_id = "auto_claim_once"
    revision = AutomationRevision(
        automation_id=automation_id,
        ordinal=1,
        instruction="Run exactly once",
    )
    automation = Automation(
        id=automation_id,
        project_id=project.id,
        thread_id=thread.id,
        name="Claim once",
        current_revision_id=revision.id,
        prompt=revision.instruction,
        schedule_kind=AutomationScheduleKind.INTERVAL,
        interval_seconds=60,
        next_run_at=now,
    )
    advanced = replace(
        automation,
        next_run_at=now + timedelta(seconds=60),
        last_run_at=now,
        updated_at=now,
    )
    with database.transaction() as connection:
        ProjectRepository(connection).add(project)
        ThreadRepository(connection).add(thread)
        AutomationRevisionRepository(connection).add(revision)
        AutomationRepository(connection).add(automation)

    def claim(_index: int) -> bool:
        with database.transaction() as connection:
            return AutomationRepository(connection).claim_due(
                advanced,
                expected_next_run_at=now,
            )

    with ThreadPoolExecutor(max_workers=2) as pool:
        claimed = list(pool.map(claim, range(2)))
    assert claimed.count(True) == 1
    assert claimed.count(False) == 1


def test_database_foreign_keys_reject_orphans(tmp_path: Path) -> None:
    database = Database(tmp_path / "state.sqlite3")
    database.initialize()
    orphan = Thread(
        project_id="proj_missing",
        title="Orphan",
        mode=ThreadMode.CODE,
        workspace_path=str(tmp_path),
    )
    with pytest.raises(sqlite3.IntegrityError):
        with database.transaction() as connection:
            ThreadRepository(connection).add(orphan)


def test_database_rejects_cross_thread_execution_records(tmp_path: Path) -> None:
    database = Database(tmp_path / "state.sqlite3")
    database.initialize()
    project = Project(canonical_path=str(tmp_path), display_name="Scope")
    first = Thread(
        project_id=project.id,
        title="First",
        mode=ThreadMode.CODE,
        workspace_path=str(tmp_path),
    )
    second = Thread(
        project_id=project.id,
        title="Second",
        mode=ThreadMode.CODE,
        workspace_path=str(tmp_path),
    )
    turn = Turn(thread_id=first.id, ordinal=1, prompt="First turn")
    cross_thread_item = Item(
        thread_id=second.id,
        turn_id=turn.id,
        ordinal=1,
        kind=ItemKind.TOOL_CALL,
        status=ItemStatus.PENDING,
        summary="Must fail",
    )
    with database.transaction() as connection:
        ProjectRepository(connection).add(project)
        ThreadRepository(connection).add(first)
        ThreadRepository(connection).add(second)
        TurnRepository(connection).add(turn)
    with pytest.raises(sqlite3.IntegrityError):
        with database.transaction() as connection:
            ItemRepository(connection).add(cross_thread_item)
