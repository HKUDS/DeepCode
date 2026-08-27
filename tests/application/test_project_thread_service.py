from pathlib import Path
from unittest.mock import patch

import pytest

from core.application import DeepCodeApplication
from core.application.errors import WorkspaceOutOfScopeError
from core.application.event_service import EventBroker
from core.domain import ThreadStatus, TrustState


def test_project_and_thread_survive_application_restart(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    database_path = tmp_path / "state.sqlite3"

    first = DeepCodeApplication.open(database_path)
    project = first.projects.add(str(workspace), trust_state=TrustState.TRUSTED)
    thread = first.threads.start(project.id, title="Persistent thread")

    second = DeepCodeApplication.open(database_path)
    assert second.projects.read(project.id) == project
    assert second.threads.read(thread.id) == thread
    assert second.threads.list(project.id) == [thread]


def test_adding_same_canonical_project_is_idempotent(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    application = DeepCodeApplication.open(tmp_path / "state.sqlite3")

    first = application.projects.add(str(workspace), display_name="First")
    second = application.projects.add(str(workspace / "."), display_name="Ignored")

    assert second.id == first.id
    assert second.display_name == "First"
    assert len(application.projects.list()) == 1


def test_workspace_cannot_escape_project_through_symlink(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    outside = tmp_path / "outside"
    workspace.mkdir()
    outside.mkdir()
    escape = workspace / "escape"
    escape.symlink_to(outside, target_is_directory=True)
    application = DeepCodeApplication.open(tmp_path / "state.sqlite3")
    project = application.projects.add(str(workspace))

    with pytest.raises(WorkspaceOutOfScopeError):
        application.threads.start(
            project.id,
            title="Escape",
            workspace_path=str(escape),
        )


def test_thread_mutation_and_replay_have_monotonic_sequences(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    application = DeepCodeApplication.open(tmp_path / "state.sqlite3")
    project = application.projects.add(str(workspace))
    thread = application.threads.start(project.id, title="Original")
    renamed = application.threads.rename(thread.id, "Renamed")
    archived = application.threads.archive(thread.id)

    events = application.events.replay(thread.id)
    assert [event.sequence for event in events] == [1, 2, 3]
    assert [event.type for event in events] == [
        "thread.created",
        "thread.renamed",
        "thread.archived",
    ]
    assert renamed.title == "Renamed"
    assert archived.status is ThreadStatus.ARCHIVED
    assert application.events.replay(thread.id, after=1) == events[1:]


def test_thread_and_authoritative_event_commit_atomically(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    application = DeepCodeApplication.open(tmp_path / "state.sqlite3")
    project = application.projects.add(str(workspace))

    with (
        patch(
            "core.application.thread_service.EventRepository.append",
            side_effect=RuntimeError("event write failed"),
        ),
        pytest.raises(RuntimeError, match="event write failed"),
    ):
        application.threads.start(project.id, title="Must roll back")

    assert application.threads.list(project.id) == []


def test_fork_stays_in_project_and_records_parent(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    application = DeepCodeApplication.open(tmp_path / "state.sqlite3")
    project = application.projects.add(str(workspace))
    source = application.threads.start(project.id, title="Source")
    fork = application.threads.fork(source.id)

    assert fork.parent_thread_id == source.id
    assert fork.project_id == project.id
    assert fork.title == "Fork of Source"


def test_live_event_broker_is_bounded_and_reports_loss() -> None:
    broker = EventBroker(default_capacity=2)
    token = broker.subscribe()
    from core.domain import DomainEvent

    for sequence in range(1, 5):
        broker.publish(
            DomainEvent(
                sequence=sequence,
                type="thread.updated",
                thread_id="thr_test",
                payload={"sequence": sequence},
            )
        )

    batch = broker.drain(token)
    assert [event.sequence for event in batch.events] == [3, 4]
    assert batch.dropped == 2
    assert broker.drain(token).events == ()
