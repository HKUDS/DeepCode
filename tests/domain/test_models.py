from dataclasses import replace
from pathlib import Path

import pytest

from core.domain import (
    Approval,
    ApprovalCategory,
    ApprovalStatus,
    Automation,
    AutomationScheduleKind,
    Project,
    Thread,
    ThreadMode,
    ThreadStatus,
    Turn,
    TurnStatus,
)
from core.domain.common import utc_now


def test_terminal_turn_requires_completion_timestamp() -> None:
    with pytest.raises(ValueError, match="terminal turns require completed_at"):
        Turn(
            thread_id="thr_test",
            ordinal=1,
            prompt="do work",
            status=TurnStatus.COMPLETED,
        )


def test_failed_turn_requires_structured_error() -> None:
    with pytest.raises(ValueError, match="failed turns require error_code"):
        Turn(
            thread_id="thr_test",
            ordinal=1,
            prompt="do work",
            status=TurnStatus.FAILED,
            completed_at=utc_now(),
        )


def test_archived_thread_cannot_exist_without_archive_timestamp(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="archived threads require archived_at"):
        Thread(
            project_id="proj_test",
            title="Archived",
            mode=ThreadMode.CODE,
            workspace_path=str(tmp_path),
            status=ThreadStatus.ARCHIVED,
        )


def test_resolved_approval_requires_auditable_decision() -> None:
    with pytest.raises(ValueError, match="resolved approvals require"):
        Approval(
            thread_id="thr_test",
            turn_id="turn_test",
            item_id="item_test",
            category=ApprovalCategory.COMMAND,
            request={"command": "rm file"},
            status=ApprovalStatus.DENIED,
        )


def test_project_rejects_non_json_settings(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="only JSON values"):
        Project(
            canonical_path=str(tmp_path),
            display_name="Project",
            settings={"invalid": object()},
        )


def test_thread_archive_state_is_explicit(tmp_path: Path) -> None:
    thread = Thread(
        project_id="proj_test",
        title="Work",
        mode=ThreadMode.CODE,
        workspace_path=str(tmp_path),
    )
    archived = replace(
        thread,
        status=ThreadStatus.ARCHIVED,
        archived_at=utc_now(),
        updated_at=utc_now(),
    )
    assert archived.status is ThreadStatus.ARCHIVED


def test_enabled_interval_automation_requires_a_due_time() -> None:
    with pytest.raises(
        ValueError,
        match="enabled interval automations require next_run_at",
    ):
        Automation(
            project_id="proj_test",
            thread_id="session-test",
            name="Nightly review",
            current_revision_id="arev_test",
            prompt="Review the repository",
            schedule_kind=AutomationScheduleKind.INTERVAL,
            interval_seconds=3600,
        )
