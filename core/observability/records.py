"""Schema for the three structured log streams DeepCode emits.

These are kept as :class:`dataclasses.dataclass` (not Pydantic) so the
hot logging path has zero validation overhead. They serialise to plain
JSON via :meth:`to_jsonl` for ``*.jsonl`` sinks.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class SystemLogRecord:
    """A generic loguru-derived record.

    System records are emitted automatically by the loguru patch
    installed by :func:`core.observability.bus.setup_logging`. Business
    code does not construct these directly.
    """

    timestamp: str
    level: str
    message: str
    logger: str = ""
    task_id: str | None = None
    session_id: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)
    exception: str | None = None

    def to_jsonl(self) -> str:
        payload = asdict(self)
        if not payload["extra"]:
            payload.pop("extra")
        if payload["exception"] is None:
            payload.pop("exception")
        return json.dumps(payload, ensure_ascii=False, default=str)


@dataclass
class LLMLogRecord:
    """One LLM call (request + response or error)."""

    timestamp: str
    task_id: str | None
    session_id: str | None
    provider: str
    model: str
    phase: str | None
    duration_ms: int
    status: str  # "ok" | "error" | "retry"
    finish_reason: str | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    cached_tokens: int | None = None
    request_preview: str | None = None
    response_preview: str | None = None
    reasoning_preview: str | None = None
    tool_calls: list[dict[str, Any]] | None = None
    error: str | None = None

    @classmethod
    def make(
        cls,
        *,
        task_id: str | None,
        session_id: str | None,
        provider: str,
        model: str,
        phase: str | None,
        duration_ms: int,
        status: str,
        finish_reason: str | None = None,
        usage: dict[str, int] | None = None,
        request_preview: str | None = None,
        response_preview: str | None = None,
        reasoning_preview: str | None = None,
        tool_calls: list[dict[str, Any]] | None = None,
        error: str | None = None,
    ) -> "LLMLogRecord":
        usage = usage or {}
        return cls(
            timestamp=_utcnow_iso(),
            task_id=task_id,
            session_id=session_id,
            provider=provider,
            model=model,
            phase=phase,
            duration_ms=duration_ms,
            status=status,
            finish_reason=finish_reason,
            prompt_tokens=usage.get("prompt_tokens"),
            completion_tokens=usage.get("completion_tokens"),
            total_tokens=usage.get("total_tokens"),
            cached_tokens=usage.get("cached_tokens"),
            request_preview=request_preview,
            response_preview=response_preview,
            reasoning_preview=reasoning_preview,
            tool_calls=tool_calls,
            error=error,
        )

    def to_jsonl(self) -> str:
        payload = {k: v for k, v in asdict(self).items() if v is not None}
        return json.dumps(payload, ensure_ascii=False, default=str)


@dataclass
class MCPLogRecord:
    """One MCP tool call."""

    timestamp: str
    task_id: str | None
    session_id: str | None
    server: str
    tool: str
    duration_ms: int
    status: str  # "ok" | "error"
    arguments_preview: str | None = None
    result_preview: str | None = None
    error: str | None = None

    @classmethod
    def make(
        cls,
        *,
        task_id: str | None,
        session_id: str | None,
        server: str,
        tool: str,
        duration_ms: int,
        status: str,
        arguments_preview: str | None = None,
        result_preview: str | None = None,
        error: str | None = None,
    ) -> "MCPLogRecord":
        return cls(
            timestamp=_utcnow_iso(),
            task_id=task_id,
            session_id=session_id,
            server=server,
            tool=tool,
            duration_ms=duration_ms,
            status=status,
            arguments_preview=arguments_preview,
            result_preview=result_preview,
            error=error,
        )

    def to_jsonl(self) -> str:
        payload = {k: v for k, v in asdict(self).items() if v is not None}
        return json.dumps(payload, ensure_ascii=False, default=str)


def truncate(text: Any, limit: int = 2000) -> str | None:
    """Cap *text* to ``limit`` chars, preserving JSON-serialisable shape.

    Used by the LLM/MCP loggers to avoid blowing up disk with multi-MB
    prompts. Returns ``None`` when the input is falsy.
    """
    if text is None:
        return None
    if not isinstance(text, str):
        try:
            text = json.dumps(text, ensure_ascii=False, default=str)
        except Exception:
            text = str(text)
    if len(text) <= limit:
        return text
    head = limit - 32
    return text[:head] + f"...[truncated {len(text) - head} chars]"


__all__ = [
    "LLMLogRecord",
    "MCPLogRecord",
    "SystemLogRecord",
    "truncate",
]
