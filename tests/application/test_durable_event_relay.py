from __future__ import annotations

from pathlib import Path

import pytest

from core.application import DeepCodeApplication
from core.application.event_service import (
    DurableEventRelay,
    EventBroker,
    EventService,
)
from core.domain.event import DomainEvent
from core.domain.project import Project, TrustState
from core.domain.thread import Thread, ThreadMode
from core.persistence.database import Database
from core.persistence.event_repository import EventRepository
from core.persistence.project_repository import ProjectRepository
from core.persistence.thread_repository import ThreadRepository
from core.sessions import SessionStore


def _database_with_thread(tmp_path: Path) -> tuple[Database, Thread]:
    database = Database(tmp_path / "state.sqlite3")
    database.initialize()
    project = Project(
        canonical_path=str(tmp_path),
        display_name="Event relay",
    )
    thread = Thread(
        project_id=project.id,
        title="Durable stream",
        mode=ThreadMode.CODE,
        workspace_path=str(tmp_path),
    )
    with database.transaction() as connection:
        ProjectRepository(connection).add(project)
        ThreadRepository(connection).add(thread)
    return database, thread


def _append(database: Database, thread_id: str, event_type: str) -> DomainEvent:
    with database.transaction() as connection:
        return EventRepository(connection).append(
            thread_id=thread_id,
            type=event_type,
            payload={"type": event_type},
        )


def test_broker_deduplicates_sequences_without_hiding_older_events() -> None:
    broker = EventBroker()
    broker.seed_sequences({"thr_stream": 1})
    token = broker.subscribe()
    newer = DomainEvent(
        sequence=3,
        type="turn.completed",
        thread_id="thr_stream",
        payload={},
    )
    older = DomainEvent(
        sequence=2,
        type="turn.started",
        thread_id="thr_stream",
        payload={},
    )

    assert broker.publish(newer)
    assert broker.publish(older)
    assert not broker.publish(newer)

    assert broker.drain(token).events == (newer, older)


def test_relay_cursor_does_not_follow_newer_local_delivery(tmp_path: Path) -> None:
    reader_database, thread = _database_with_thread(tmp_path)
    writer_database = Database(reader_database.path)
    baseline = _append(reader_database, thread.id, "thread.started")
    broker = EventBroker()
    relay = DurableEventRelay(reader_database, broker)
    relay.start(background=False)
    token = broker.subscribe()

    external_older = _append(writer_database, thread.id, "approval.requested")
    local_newer = _append(reader_database, thread.id, "turn.completed")
    assert broker.publish(local_newer)
    assert broker.drain(token).events == (local_newer,)

    assert relay.poll_once() == 1
    assert broker.drain(token).events == (external_older,)
    assert relay.poll_once() == 0
    assert [
        event.sequence
        for event in EventService(reader_database, broker).replay(thread.id)
    ] == [baseline.sequence, external_older.sequence, local_newer.sequence]

    relay.close()


def test_background_relay_is_closeable_and_uses_injected_polling(
    tmp_path: Path,
) -> None:
    reader_database, thread = _database_with_thread(tmp_path)
    writer_database = Database(reader_database.path)
    broker = EventBroker()
    relay = DurableEventRelay(
        reader_database,
        broker,
        poll_interval=0.01,
        batch_size=1,
    )
    relay.start()
    token = broker.subscribe()

    external = _append(writer_database, thread.id, "automation.updated")

    assert broker.wait_for_events(token, timeout=1.0)
    assert broker.drain(token).events == (external,)
    assert relay.active

    relay.close()
    assert not relay.active
    _append(writer_database, thread.id, "automation.run.updated")
    assert not broker.wait_for_events(token, timeout=0.05)


def test_two_applications_share_live_events_without_duplicate_delivery(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "state.sqlite3"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    session_root = tmp_path / "sessions"
    first = DeepCodeApplication.open(
        database_path,
        session_store=SessionStore(session_root),
        event_relay_poll_interval=0.01,
        run_automation_scheduler=False,
    )
    project = first.projects.add(
        str(workspace),
        trust_state=TrustState.TRUSTED,
    )
    thread = first.threads.start(project.id, title="Before second process")
    second = DeepCodeApplication.open(
        database_path,
        session_store=SessionStore(session_root),
        event_relay_poll_interval=0.01,
        run_automation_scheduler=False,
    )
    first_token = first.broker.subscribe()
    second_token = second.broker.subscribe()
    try:
        first.threads.rename(thread.id, "Visible in both processes")

        assert first.broker.wait_for_events(first_token, timeout=1.0)
        first_events = first.broker.drain(first_token).events
        assert second.broker.wait_for_events(second_token, timeout=1.0)
        second_events = second.broker.drain(second_token).events
        assert len(first_events) == 1
        assert len(second_events) == 1
        assert first_events[0].id == second_events[0].id
        assert first_events[0].type == "thread.renamed"

        first.event_relay.poll_once()
        second.event_relay.poll_once()
        assert first.broker.drain(first_token).events == ()
        assert second.broker.drain(second_token).events == ()
        assert second.events.replay(thread.id)[-1].id == first_events[0].id
    finally:
        second.close()
        first.close()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("poll_interval", 0),
        ("poll_interval", float("inf")),
        ("batch_size", 0),
        ("batch_size", True),
    ],
)
def test_relay_rejects_invalid_polling_configuration(
    tmp_path: Path,
    field: str,
    value: object,
) -> None:
    database, _ = _database_with_thread(tmp_path)
    options = {field: value}

    with pytest.raises(ValueError):
        DurableEventRelay(database, EventBroker(), **options)


def test_relay_backs_off_on_persistent_failure_and_recovers(
    tmp_path: Path, monkeypatch, caplog
) -> None:
    """A failing poll must not flood: one traceback, then one-line warnings
    with growing delays, then a recovery notice — and each retry opens a
    fresh connection, so the loop itself is the healing mechanism."""
    import logging as stdlib_logging
    import threading

    from core.application import event_service as module

    database = Database(tmp_path / "state.sqlite3")
    broker = EventBroker()
    relay = DurableEventRelay(database, broker, poll_interval=0.01)

    outcomes = iter([Exception, Exception, Exception, None, StopIteration])
    waits: list[float] = []

    def fake_poll() -> int:
        outcome = next(outcomes)
        if outcome is StopIteration:
            relay._stop.set()
            return 0
        if outcome is Exception:
            raise sqlite_error()
        return 0

    def sqlite_error() -> Exception:
        import sqlite3

        return sqlite3.OperationalError("disk I/O error")

    real_wait = relay._stop.wait

    def recording_wait(timeout: float | None = None) -> bool:
        waits.append(timeout or 0)
        return real_wait(0)  # no real sleeping in tests

    monkeypatch.setattr(relay, "poll_once", fake_poll)
    monkeypatch.setattr(relay._stop, "wait", recording_wait)

    with caplog.at_level(stdlib_logging.INFO, logger=module.logger.name):
        thread = threading.Thread(target=relay._run)
        thread.start()
        thread.join(timeout=5)
    assert not thread.is_alive()

    exception_records = [
        record for record in caplog.records if record.exc_info is not None
    ]
    warning_records = [
        record
        for record in caplog.records
        if record.levelno == stdlib_logging.WARNING and record.exc_info is None
    ]
    recovery_records = [
        record for record in caplog.records if "recovered" in record.getMessage()
    ]
    assert len(exception_records) == 1  # only the streak's first failure
    assert len(warning_records) == 2  # the repeats, one line each
    assert len(recovery_records) == 1

    # Backoff doubles per failure and resets after the successful poll.
    assert waits[0] == pytest.approx(0.02)
    assert waits[1] == pytest.approx(0.04)
    assert waits[2] == pytest.approx(0.08)
    assert waits[3] == pytest.approx(0.01)
