"""One-way data flow: JSONL owns identity; the projection never resurrects it.

The dsh invariant under test. A Thread row whose canonical Session file is
gone is a stale shadow and must be dropped from the projection — observed
live: deleting session JSONL files by hand brought every session back on the
next reconcile, because the projection adopted them into fresh JSONL. The
two legitimate exceptions stay adopted: a P1-P6 legacy import (covered in
test_session_alignment) and an Automation bootstrap, where SQLite genuinely
is the system of record.

Also under test: context notes (runner-injected mid-turn model context,
stamped with a ``delivery`` metadata marker) are log entries, not
conversational turns — projection pair-matching must skip them instead of
declaring a projection conflict on every noted Turn.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from core.application import DeepCodeApplication
from core.domain.automation import AutomationScheduleKind
from core.domain.project import TrustState
from core.persistence.event_repository import EventRepository
from core.persistence.execution_repository import ItemRepository
from core.persistence.thread_repository import ThreadRepository
from core.sessions import SessionStore


def _seed_session(store: SessionStore, workspace: Path, title: str = "CLI work"):
    session = store.create_session(
        title=title,
        metadata={"kind": "tui", "workspace": str(workspace)},
    )
    store.append_message(session.session_id, "user", "remember this")
    store.append_message(session.session_id, "assistant", "remembered")
    return store.get_session(session.session_id)


def test_deleting_canonical_jsonl_drops_the_projection_instead_of_resurrecting(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = SessionStore(tmp_path / "sessions")
    session = _seed_session(store, workspace)
    database = tmp_path / "state.sqlite3"

    first = DeepCodeApplication.open(database, session_store=store)
    assert [t.id for t in first.threads.list()] == [session.session_id]
    first.close()

    # Out-of-band deletion — exactly what a user's `rm -rf` does.
    shutil.rmtree(store.root / session.session_id)

    reopened = DeepCodeApplication.open(
        database,
        session_store=SessionStore(tmp_path / "sessions"),
    )
    try:
        assert reopened.threads.list() == []
        with reopened.database.read() as connection:
            assert ThreadRepository(connection).get(session.session_id) is None
        # Nothing was written back to the canonical store.
        assert not (store.root / session.session_id).exists()
    finally:
        reopened.close()


def test_automation_thread_is_rematerialized_not_dropped(tmp_path: Path) -> None:
    """For an Automation bootstrap SQLite IS the record: a missing Session
    file is repaired through the sanctioned materialization path."""

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    database = tmp_path / "state.sqlite3"

    application = DeepCodeApplication.open(database)
    project = application.projects.add(str(workspace))
    application.projects.update(project.id, trust_state=TrustState.TRUSTED)
    created = application.automations.create(
        project_id=project.id,
        name="Repository review",
        prompt="Review the repository",
        schedule_kind=AutomationScheduleKind.MANUAL,
    )
    thread_id = created.thread.id
    store_root = application.session_store.root
    application.close()

    shutil.rmtree(store_root / thread_id)

    reopened = DeepCodeApplication.open(database)
    try:
        assert any(t.id == thread_id for t in reopened.threads.list())
        session = reopened.session_store.get_session(thread_id)
        assert session is not None
        assert session.metadata["kind"] == "automation"
    finally:
        reopened.close()


def test_context_notes_do_not_poison_projection_pair_matching(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = SessionStore(tmp_path / "sessions")
    session = store.create_session(
        title="noted",
        metadata={"kind": "tui", "workspace": str(workspace)},
    )
    store.append_message(session.session_id, "user", "find the bug")
    # A runner-injected mid-turn note, exactly as _context_note_sink writes it.
    store.append_message(
        session.session_id,
        "user",
        "SYSTEM REMINDER: you are repeating the exact same tool call",
        metadata={
            "schemaVersion": 3,
            "delivery": "mid_turn",
            "source": "repeat_guard",
        },
    )
    store.append_message(session.session_id, "assistant", "found and fixed")

    application = DeepCodeApplication.open(
        tmp_path / "state.sqlite3",
        session_store=store,
    )
    try:
        thread = application.threads.read(session.session_id)
        with application.database.read() as connection:
            items = ItemRepository(connection).conversation_for_thread(thread.id)
            conflicted = EventRepository(connection).has_type(
                thread.id,
                "thread.projection_conflict",
            )
        # The note is model context, not a turn: the timeline shows the real
        # exchange and no conflict was declared.
        assert not conflicted
        assert [item.payload["text"] for item in items] == [
            "find the bug",
            "found and fixed",
        ]
    finally:
        application.close()


@pytest.mark.parametrize("delivery", ["current_turn", "next_turn"])
def test_user_input_provenance_survives_projection_rebuild(tmp_path, delivery):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = SessionStore(tmp_path / "sessions")
    session = store.create_session(
        title="User input", metadata={"workspace": str(workspace)}
    )
    store.append_message(
        session.session_id,
        "user",
        "implement the change",
        metadata={
            "schemaVersion": 3,
            "client": "web",
            "delivery": delivery,
            "source": "start",
        },
    )
    store.append_message(
        session.session_id,
        "user",
        "internal runtime reminder",
        metadata={
            "delivery": "between_turns",
            "source": "repeat_guard",
        },
    )
    store.append_message(session.session_id, "assistant", "implemented")

    app = DeepCodeApplication.open(tmp_path / "state.sqlite3", session_store=store)
    try:
        app.threads.read(session.session_id)
        app.threads.reconcile()
        with app.database.read() as connection:
            messages = ItemRepository(connection).conversation_for_thread(
                session.session_id
            )
            assert [item.payload["text"] for item in messages] == [
                "implement the change",
                "implemented",
            ]
            assert not EventRepository(connection).has_type(
                session.session_id, "thread.projection_conflict"
            )
    finally:
        app.close()


def test_actual_transcript_disagreement_remains_visible(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = SessionStore(tmp_path / "sessions")
    session = _seed_session(store, workspace)
    app = DeepCodeApplication.open(tmp_path / "state.sqlite3", session_store=store)
    try:
        with app.database.transaction() as connection:
            item = ItemRepository(connection).conversation_for_thread(
                session.session_id
            )[0]
            connection.execute(
                "UPDATE items SET payload_json = ? WHERE id = ?",
                (json.dumps({"text": "a different user input"}), item.id),
            )
        app.threads.read(session.session_id)
        app.threads.read(session.session_id)
        with app.database.read() as connection:
            events = EventRepository(connection).replay(session.session_id)
            assert sum(e.type == "thread.projection_conflict" for e in events) == 1
            projected = ItemRepository(connection).conversation_for_thread(
                session.session_id
            )
            assert projected[0].payload["text"] == "a different user input"
        assert (
            store.get_session(session.session_id).messages[0].content == "remember this"
        )
    finally:
        app.close()


def test_tool_only_assistant_record_does_not_conflict_with_visible_conversation(
    tmp_path,
):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = SessionStore(tmp_path / "sessions")
    session = store.create_session(
        title="Tool-only response", metadata={"workspace": str(workspace)}
    )
    store.append_message(session.session_id, "user", "inspect a file")
    store.append_message(
        session.session_id,
        "assistant",
        "",
        metadata={
            "toolCalls": [
                {
                    "id": "read-1",
                    "type": "function",
                    "function": {"name": "read", "arguments": "{}"},
                }
            ],
        },
    )
    store.append_message(
        session.session_id,
        "tool",
        "file contents",
        metadata={"name": "read", "toolCallId": "read-1"},
    )
    store.append_message(session.session_id, "assistant", "file inspected")
    app = DeepCodeApplication.open(tmp_path / "state.sqlite3", session_store=store)
    try:
        app.threads.read(session.session_id)
        app.threads.reconcile()
        with app.database.read() as connection:
            assert not EventRepository(connection).has_type(
                session.session_id, "thread.projection_conflict"
            )
            # The tool record still belongs to the reconstructed timeline.
            count = connection.execute(
                "SELECT COUNT(*) FROM items WHERE thread_id = ? AND kind = 'tool_call'",
                (session.session_id,),
            ).fetchone()[0]
            assert count == 1
        assert len(store.get_session(session.session_id).messages) == 4
    finally:
        app.close()
