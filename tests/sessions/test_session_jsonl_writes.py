"""Writing a session file never costs a line already in it.

A message is appended to ``session.jsonl``; the file is rewritten only when
its header has to change (a title, metadata, a task), and a line that cannot
be read, such as one cut short by a crash, is kept as it is.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from core.sessions import SessionStore

# What a crash in the middle of an append leaves: a line cut short, without
# its newline, here even inside a character.
_TORN = '{"_type": "message", "role": "assistant", "content": "回复'.encode()[:-1]


def _lines(store: SessionStore, session_id: str) -> list[bytes]:
    path = store.root / session_id / "session.jsonl"
    return path.read_bytes().splitlines()


def test_appending_a_message_appends(tmp_path: Path) -> None:
    store = SessionStore(tmp_path / "sessions")
    session = store.create_session(title="kept")
    path = store.root / session.session_id / "session.jsonl"
    store.append_message(session.session_id, "user", "first")
    before = path.read_bytes()
    inode = path.stat().st_ino

    last = store.append_message(session.session_id, "assistant", "second")

    assert path.stat().st_ino == inode
    assert path.read_bytes().startswith(before)
    # The newest message dates the session even though the header was not
    # rewritten for it.
    reopened = SessionStore(tmp_path / "sessions").get_session(session.session_id)
    assert reopened is not None
    assert reopened.updated_at == last.timestamp


@pytest.mark.parametrize("use_index", [True, False])
def test_the_session_with_the_newest_message_lists_first(
    tmp_path: Path, use_index: bool
) -> None:
    store = SessionStore(tmp_path / "sessions", use_index=use_index)
    older = store.create_session(title="older")
    store.create_session(title="newer")
    store.append_message(older.session_id, "user", "back to this one")

    reopened = SessionStore(tmp_path / "sessions", use_index=use_index)
    assert [summary.title for summary in reopened.list_sessions()] == [
        "older",
        "newer",
    ]


def test_a_line_cut_short_by_a_crash_survives_later_writes(tmp_path: Path) -> None:
    store = SessionStore(tmp_path / "sessions")
    session = store.create_session(title="")
    store.append_message(session.session_id, "user", "first")
    path = store.root / session.session_id / "session.jsonl"
    with path.open("ab") as handle:
        handle.write(_TORN)

    store.append_message(session.session_id, "assistant", "after the crash")
    store.rename_session(session.session_id, "renamed")
    store.update_metadata(session.session_id, {"note": "kept"})

    assert _TORN in _lines(store, session.session_id)
    reopened = SessionStore(tmp_path / "sessions").get_session(session.session_id)
    assert reopened is not None
    assert reopened.title == "renamed"
    assert [message.content for message in reopened.messages] == [
        "first",
        "after the crash",
    ]
