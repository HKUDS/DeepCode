"""P1-3: canonical named-event vocabulary (Claude Code telemetry lesson).

Claude Code ships ~1,800 well-named ``tengu_*`` events with a stable
``domain_action_result`` shape, making the whole product's behavior queryable.
DeepCode already has three structured JSONL streams (system/llm/mcp) plus the
SQ/EQ front-end events, but no *unified, enumerable* vocabulary for
agent-behavior events (permission verdicts, classifier decisions, memory
distillation, guard trips, tool grants/blocks). This module adds that layer
*incrementally*:

* ``emit_event(name, **fields)`` — the single entry point. Writes one JSON
  line to ``<task_dir>/events.jsonl`` and mirrors it to loguru, so it is both
  machine-queryable and visible in the console log.
* :data:`EventName` — the canonical vocabulary as an enum. Event names follow
  ``domain.action.result`` (e.g. ``guard.risk_classify.low``,
  ``permission.ask.resolved``, ``memory.distill.ok``, ``tool.use.blocked``).
  Adding a name is a one-line enum extension — nothing else changes.
* Fail-soft: emission never raises; a broken task dir or loguru failure is
  swallowed (observability is not a security boundary).

Existing streams (system/llm/mcp JSONL, SQ/EQ events) are untouched — this is
an additive layer. Migration of legacy call sites to ``emit_event`` is
incremental; new code should prefer it.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any

from loguru import logger

from core.observability.context import current_session_id, current_task_id


class EventName(str, Enum):
    """Canonical ``domain.action.result`` event vocabulary (P1-3)."""

    # Permission / risk classifier (P0-1)
    PERMISSION_ASK_RESOLVED = "permission.ask.resolved"
    GUARD_RISK_CLASSIFY_LOW = "guard.risk_classify.low"
    GUARD_RISK_CLASSIFY_MEDIUM = "guard.risk_classify.medium"
    GUARD_RISK_CLASSIFY_HIGH = "guard.risk_classify.high"
    GUARD_RISK_CLASSIFY_ERROR = "guard.risk_classify.error"
    # Tool use
    TOOL_USE_GRANTED = "tool.use.granted"
    TOOL_USE_BLOCKED = "tool.use.blocked"
    # Memory distillation (P0-2)
    MEMORY_DISTILL_OK = "memory.distill.ok"
    MEMORY_DISTILL_SKIP = "memory.distill.skip"
    MEMORY_DISTILL_ERROR = "memory.distill.error"
    # Guard rails (REASONIX port)
    GUARD_PROGRESS_TRIP = "guard.progress.trip"
    GUARD_STORM_TRIP = "guard.storm.trip"
    GUARD_DELEGATION_DENY = "guard.delegation.deny"
    # Session lifecycle
    SESSION_ENDED = "session.ended"
    SESSION_INTERRUPTED = "session.interrupted"
    SESSION_ERRORED = "session.errored"


def _task_dir() -> str | None:
    """Locate the current task's log directory (same source as the JSONL
    sinks) — the per-task dir registered via ``set_task_dir``, or None when
    unavailable (emission then only mirrors to loguru)."""
    try:
        from core.observability.bus import _resolve_task_dir

        d = _resolve_task_dir(current_task_id())
        return str(d) if d is not None else None
    except Exception:  # noqa: BLE001
        return None


@dataclass(slots=True)
class EventRecord:
    """One canonical event line."""

    event: str
    timestamp: str
    task_id: str | None = None
    session_id: str | None = None
    fields: dict[str, Any] = field(default_factory=dict)

    def to_jsonl(self) -> str:
        payload = asdict(self)
        if not payload["fields"]:
            payload.pop("fields")
        if payload["task_id"] is None:
            payload.pop("task_id")
        if payload["session_id"] is None:
            payload.pop("session_id")
        return json.dumps(payload, ensure_ascii=False, default=str)


def _write_events_jsonl(record: EventRecord) -> None:
    try:
        d = _task_dir()
        if not d:
            return
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "events.jsonl"), "a", encoding="utf-8") as fh:
            fh.write(record.to_jsonl() + "\n")
    except Exception:  # noqa: BLE001, S110 - observability never raises
        pass


def emit_event(name: str | EventName, **fields: Any) -> None:
    """Emit one canonical event (best-effort, never raises).

    Parameters
    ----------
    name:
        Event name; either an :class:`EventName` member or a raw string
        (unregistered names are allowed so forward use does not block).
    fields:
        Arbitrary structured fields attached to the event.
    """
    event = name.value if isinstance(name, EventName) else str(name)
    try:
        record = EventRecord(
            event=event,
            timestamp=datetime.now(UTC).isoformat(),
            task_id=current_task_id(),
            session_id=current_session_id(),
            fields=fields,
        )
    except Exception:  # noqa: BLE001
        return
    # Machine-readable JSONL.
    _write_events_jsonl(record)
    # Console mirror (loguru keeps its own timestamp/level).
    try:
        detail = " ".join(f"{k}={v}" for k, v in fields.items()) if fields else ""
        logger.info("event.{} {}", event, detail)
    except Exception:  # noqa: BLE001, S110
        pass


__all__ = ["EventName", "EventRecord", "emit_event"]
