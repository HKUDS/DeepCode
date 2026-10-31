"""Kernel history ↔ canonical SessionMessage conversion.

The event protocol stays a display ledger. This module copies the
model-visible list (``AgentSession.history``) into jsonl and rebuilds it
on resume.
"""

from __future__ import annotations

from collections import Counter
from copy import deepcopy
from typing import Any, Iterable, Mapping

from core.agent_runtime.context import EnvironmentContext
from core.domain.message_provenance import TurnInputSource
from core.sessions.continuation import session_message_history_entry
from core.sessions.models import SessionMessage

COMPACTION_META = "compaction"
TOOL_CALLS_META = "toolCalls"
TOOL_CALL_ID_META = "toolCallId"
TOOL_NAME_META = "name"


def _as_text(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    return str(content)


def kernel_message_to_record(message: Mapping[str, Any]) -> SessionMessage | None:
    """Project one kernel history dict into a store record, or skip it."""
    role = message.get("role")
    if role not in {"user", "assistant", "tool"}:
        return None
    # Environment context is rewritten from the live workspace on each
    # process; persisting it would stale the date and steal the session title.
    if EnvironmentContext.is_history_message(message):
        return None
    content = _as_text(message.get("content"))
    tool_calls = message.get("tool_calls")
    if role == "assistant" and not content and not tool_calls:
        return None
    if role == "user" and not content:
        return None
    if role == "tool" and not content and not message.get("tool_call_id"):
        return None
    metadata: dict[str, Any] = {}
    if role == "assistant":
        if tool_calls:
            metadata[TOOL_CALLS_META] = deepcopy(list(tool_calls))
        provider_state = message.get("provider_state")
        if isinstance(provider_state, dict) and provider_state:
            metadata["providerState"] = deepcopy(provider_state)
        summary = message.get("reasoning_summary")
        if isinstance(summary, str) and summary.strip():
            metadata["reasoningSummary"] = summary.strip()
    if role == "tool":
        call_id = message.get("tool_call_id")
        if call_id:
            metadata[TOOL_CALL_ID_META] = str(call_id)
        name = message.get("name")
        if isinstance(name, str) and name:
            metadata[TOOL_NAME_META] = name
    compaction = message.get("compaction")
    if isinstance(compaction, dict) and compaction:
        metadata[COMPACTION_META] = dict(compaction)
    return SessionMessage(role=str(role), content=content, metadata=metadata or None)


def is_compaction_checkpoint(message: SessionMessage) -> bool:
    """True for the synthetic record a compaction wrote in place of a range.

    It rides a user-role record because that is the shape the model reads,
    but it is bookkeeping, not something a person said: consumers that group
    or title a conversation must not treat it as a prompt.
    """
    marker = (message.metadata or {}).get(COMPACTION_META)
    return isinstance(marker, dict) and bool(marker.get("reset"))


def is_context_note(message: SessionMessage) -> bool:
    """True for runner-injected context, not a conversational turn.

    The context-note sink stamps every note with a ``delivery`` marker
    (``mid_turn``/``between_turns``). Notes are model-visible log entries
    — repeat-guard reminders, sub-agent results, Goal updates — that sit
    BETWEEN a Turn's user prompt and its assistant reply in the canonical
    JSONL. Like a compaction checkpoint, a note rides a user-role record but
    nobody said it: the projection timeline must skip it when pairing
    Turns, and a transcript view must not show it as the user's words.

    The marker an interrupted Turn leaves for the model is the same kind
    of record: it closes that Turn, it does not open one.
    """
    if message.role != "user":
        return False
    metadata = message.metadata or {}
    if metadata.get("source") == TurnInputSource.TURN_INTERRUPT.value:
        return True
    # User input also carries delivery provenance (current_turn/next_turn).
    # Only the context-note sink's markers identify internal user-role notes.
    return metadata.get("delivery") in ("mid_turn", "between_turns")


def record_fingerprint(message: SessionMessage) -> tuple[Any, ...]:
    metadata = message.metadata or {}
    call_id = str(metadata.get(TOOL_CALL_ID_META) or "")
    return (
        message.role,
        # A tool result is the result of its call. Under context pressure the
        # runner shortens old results in the history itself; the shorter
        # text is not a second result to save.
        "" if message.role == "tool" and call_id else message.content,
        tuple(
            str(call.get("id", ""))
            for call in metadata.get(TOOL_CALLS_META) or ()
            if isinstance(call, dict)
        ),
        call_id,
    )


def kernel_fingerprint(message: Mapping[str, Any]) -> tuple[Any, ...]:
    record = kernel_message_to_record(message)
    if record is None:
        return ("", "", (), "")
    return record_fingerprint(record)


def session_message_to_kernel(message: SessionMessage) -> dict[str, Any] | None:
    """Rebuild one kernel history dict from a store record."""
    if message.role not in {"user", "assistant", "tool"}:
        return None
    metadata = message.metadata or {}
    if (
        message.role in {"user", "assistant"}
        and not message.content
        and not metadata.get(TOOL_CALLS_META)
    ):
        return None
    entry = session_message_history_entry(message)
    if message.role == "assistant":
        tool_calls = metadata.get(TOOL_CALLS_META)
        if tool_calls:
            entry["tool_calls"] = deepcopy(list(tool_calls))
    if message.role == "tool":
        call_id = metadata.get(TOOL_CALL_ID_META)
        if call_id:
            entry["tool_call_id"] = str(call_id)
        name = metadata.get(TOOL_NAME_META)
        if isinstance(name, str) and name:
            entry["name"] = name
    compaction = metadata.get(COMPACTION_META)
    if isinstance(compaction, dict) and compaction:
        entry["compaction"] = dict(compaction)
    return entry


def _latest_reset(records: list[SessionMessage]) -> tuple[int, int] | None:
    """The latest compaction checkpoint's index and its retained window's start."""
    latest: tuple[int, int] | None = None
    for index, message in enumerate(records):
        marker = (message.metadata or {}).get(COMPACTION_META)
        if isinstance(marker, dict) and marker.get("reset"):
            retain = int(marker.get("retain") or 0)
            latest = (index, max(0, index - max(retain, 0)))
    return latest


def visible_kernel_history(messages: Iterable[SessionMessage]) -> list[dict[str, Any]]:
    """Rebuild model history, honoring the latest compaction reset."""
    records = list(messages)
    latest = _latest_reset(records)
    if latest is not None:
        reset_at, tail_start = latest
        records = [
            records[reset_at],
            *records[tail_start:reset_at],
            *records[reset_at + 1 :],
        ]
    history: list[dict[str, Any]] = []
    for message in records:
        entry = session_message_to_kernel(message)
        if entry is not None:
            history.append(entry)
    return history


def new_records_from_history(
    history: Iterable[Mapping[str, Any]],
    stored: Iterable[SessionMessage],
) -> list[SessionMessage]:
    """Store records for kernel messages that are not already in ``stored``.

    Each stored record accounts for one kernel message: content is not
    identity, and a "yes" or "Done." said again is a new message. Only the
    records a history rebuilt from ``stored`` holds can account for one —
    those from the latest checkpoint's retained window on. That window holds
    every saved message the history holds (a resumed history is built from
    exactly it, and :func:`_retained_window` makes a new checkpoint's window
    cover what its compacted history kept), so a saved message is always
    found there and never written twice, while one a compaction folded away
    cannot hide a new message that repeats it.
    """
    items = list(history)
    records = list(stored)
    latest = _latest_reset(records)
    window = records[latest[1] :] if latest is not None else records
    unmatched = Counter(record_fingerprint(message) for message in window)
    added: list[SessionMessage] = []
    for index, item in enumerate(items):
        record = kernel_message_to_record(item)
        if record is None:
            continue
        key = record_fingerprint(record)
        if unmatched[key]:
            unmatched[key] -= 1
            continue
        marker = item.get("compaction")
        if isinstance(marker, dict) and marker.get("reset"):
            retain = _retained_window(items[index + 1 :], unmatched, [*records, *added])
            record.metadata = {
                **(record.metadata or {}),
                COMPACTION_META: {"reset": True, "retain": retain},
            }
        added.append(record)
    return added


def _retained_window(
    later: list[Mapping[str, Any]],
    unmatched: Counter,
    saved: list[SessionMessage],
) -> int:
    """How many saved records a new checkpoint's rebuilt history must keep.

    Every message after the checkpoint that is already saved has to be in
    that window: otherwise a resumed history loses it, and the next save no
    longer finds it there and writes it again. Messages saved while the Turn
    ran (its prompt, steering, notes) sit in the file in another order than
    in the history, so the window is the shortest tail of the file holding
    them all, not the number of them.
    """
    remaining = unmatched.copy()
    needed: Counter = Counter()
    for item in later:
        record = kernel_message_to_record(item)
        if record is None:
            continue
        key = record_fingerprint(record)
        if remaining[key]:
            remaining[key] -= 1
            needed[key] += 1
    retain = 0
    for record in reversed(saved):
        if not +needed:
            break
        key = record_fingerprint(record)
        if needed[key]:
            needed[key] -= 1
        retain += 1
    return retain
