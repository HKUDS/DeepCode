"""Kernel history ↔ canonical SessionMessage conversion.

The event protocol stays a display ledger. This module copies the
model-visible list (``AgentSession.history``) into jsonl and rebuilds it
on resume.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from copy import deepcopy
from typing import Any

from core.agent_runtime.context import EnvironmentContext
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


def record_fingerprint(message: SessionMessage) -> tuple[Any, ...]:
    metadata = message.metadata or {}
    return (
        message.role,
        message.content,
        tuple(
            str(call.get("id", ""))
            for call in metadata.get(TOOL_CALLS_META) or ()
            if isinstance(call, dict)
        ),
        str(metadata.get(TOOL_CALL_ID_META) or ""),
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


def visible_kernel_history(messages: Iterable[SessionMessage]) -> list[dict[str, Any]]:
    """Rebuild model history, honoring the latest compaction reset."""
    records = list(messages)
    retain = 0
    reset_at: int | None = None
    for index, message in enumerate(records):
        marker = (message.metadata or {}).get(COMPACTION_META)
        if isinstance(marker, dict) and marker.get("reset"):
            reset_at = index
            retain = int(marker.get("retain") or 0)
    if reset_at is not None:
        tail_start = max(0, reset_at - max(retain, 0))
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
    """Store records for kernel messages that are not already in ``stored``."""
    items = list(history)
    seen = {record_fingerprint(message) for message in stored}
    added: list[SessionMessage] = []
    for index, item in enumerate(items):
        record = kernel_message_to_record(item)
        if record is None:
            continue
        key = record_fingerprint(record)
        if key in seen:
            continue
        marker = item.get("compaction")
        if isinstance(marker, dict) and marker.get("reset"):
            retain = 0
            for later in items[index + 1 :]:
                later_record = kernel_message_to_record(later)
                if later_record is None:
                    continue
                if record_fingerprint(later_record) in seen:
                    retain += 1
                    continue
                break
            record.metadata = {
                **(record.metadata or {}),
                COMPACTION_META: {"reset": True, "retain": retain},
            }
        added.append(record)
        seen.add(record_fingerprint(record))
    return added
