"""Safe provider continuation state in canonical Session messages."""

from __future__ import annotations

from collections.abc import Iterable
from copy import deepcopy
from typing import Any

from core.sessions.models import SessionMessage


def assistant_continuation_metadata(
    history: Iterable[dict[str, Any]],
) -> dict[str, Any]:
    """Extract only resumable opaque state and a safe display summary."""

    assistant = next(
        (
            message
            for message in reversed(list(history))
            if message.get("role") == "assistant"
        ),
        None,
    )
    if assistant is None:
        return {}
    metadata: dict[str, Any] = {}
    provider_state = assistant.get("provider_state")
    if isinstance(provider_state, dict) and provider_state:
        metadata["providerState"] = deepcopy(provider_state)
    summary = assistant.get("reasoning_summary")
    if isinstance(summary, str) and summary.strip():
        metadata["reasoningSummary"] = summary.strip()
    return metadata


def session_message_history_entry(message: SessionMessage) -> dict[str, Any]:
    """Rehydrate visible text plus private state for the shared AgentSession."""

    entry: dict[str, Any] = {"role": message.role, "content": message.content}
    metadata = message.metadata or {}
    if message.role != "assistant":
        return entry
    provider_state = metadata.get("providerState")
    if isinstance(provider_state, dict) and provider_state:
        entry["provider_state"] = deepcopy(provider_state)
    summary = metadata.get("reasoningSummary")
    if isinstance(summary, str) and summary.strip():
        entry["reasoning_summary"] = summary.strip()
    return entry


__all__ = ["assistant_continuation_metadata", "session_message_history_entry"]
