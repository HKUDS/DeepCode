"""Tests for the P1-3 canonical named-event vocabulary."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.observability.events import (
    EventName,
    EventRecord,
    emit_event,
)


def test_event_name_enum_shape():
    # Names follow domain.action.result and use dots, never spaces.
    for name in EventName:
        parts = name.value.split(".")
        assert len(parts) >= 2
        assert all(p.isidentifier() for p in parts)


def test_known_events_exist():
    assert EventName.PERMISSION_ASK_RESOLVED.value == "permission.ask.resolved"
    assert EventName.GUARD_RISK_CLASSIFY_LOW.value == "guard.risk_classify.low"
    assert EventName.MEMORY_DISTILL_OK.value == "memory.distill.ok"
    assert EventName.TOOL_USE_BLOCKED.value == "tool.use.blocked"


def test_record_serialization_omits_empty_fields():
    rec = EventRecord(event="a.b.c", timestamp="2026-08-15T00:00:00Z")
    payload = json.loads(rec.to_jsonl())
    assert payload == {"event": "a.b.c", "timestamp": "2026-08-15T00:00:00Z"}


def test_record_serialization_with_fields():
    rec = EventRecord(
        event="a.b.c",
        timestamp="t",
        task_id="task-1",
        session_id="sess-1",
        fields={"tool": "bash", "level": "high"},
    )
    payload = json.loads(rec.to_jsonl())
    assert payload["task_id"] == "task-1"
    assert payload["session_id"] == "sess-1"
    assert payload["fields"] == {"tool": "bash", "level": "high"}


def test_emit_event_writes_events_jsonl(tmp_path, monkeypatch):
    from core.observability import events as ev

    # Point the task-dir resolver at a temp dir.
    monkeypatch.setattr(ev, "_task_dir", lambda: str(tmp_path))
    emit_event(EventName.MEMORY_DISTILL_OK, session="s", chars=123)
    lines = (tmp_path / "events.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    payload = json.loads(lines[0])
    assert payload["event"] == "memory.distill.ok"
    assert payload["fields"]["chars"] == 123


def test_emit_event_never_raises_on_bad_dir(tmp_path, monkeypatch):
    from core.observability import events as ev

    def broken_dir():
        raise RuntimeError("boom")

    monkeypatch.setattr(ev, "_task_dir", broken_dir)
    # Must not raise even though the resolver explodes.
    emit_event(EventName.SESSION_ENDED)


def test_emit_event_with_raw_string_name():
    # Unregistered names are allowed (forward use does not block).
    from core.observability import events as ev

    monkeypatch = __import__("pytest").MonkeyPatch()
    monkeypatch.setattr(ev, "_task_dir", lambda: None)
    emit_event("custom.thing.happened", detail=1)
    monkeypatch.undo()
