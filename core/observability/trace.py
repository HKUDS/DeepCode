"""P2-A6 (GenAI lesson 17): tool-call trace chain — observable agent actions.

Lesson 17 names *visibility* as one of the three pillars of an agent
framework: a user/developer must be able to inspect what the model planned
and executed. DeepCode already records individual LLM/MCP calls
(``core.observability.records``) and emits hooks, but the "why this tool, with
what arguments, and what happened" chain is not serialisable as one unit.

This module adds a lightweight, pure-mechanism trace model: a
:class:`TraceSpan` for each tool call (name, argument/result previews,
duration, status, and optional reasoning snippet) grouped into a
:class:`TraceChain` (session, turn, ordered spans) that serialises to JSONL.
No LLM calls, no subprocess — just structured observability that future
frontends/audits can query.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from typing import Any
from uuid import uuid4

from core.observability.records import truncate


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


@dataclass
class TraceSpan:
    """One tool call inside a trace chain."""

    tool_name: str
    status: str  # "ok" | "error" | "blocked" | "denied" | "timeout"
    duration_ms: int
    arguments_preview: str | None = None
    result_preview: str | None = None
    reasoning_preview: str | None = None  # "why this tool" (lesson 17 visibility)
    error: str | None = None
    started_at: str = field(default_factory=lambda: _now_iso())

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if v is not None}


@dataclass
class TraceChain:
    """An ordered sequence of tool calls within one turn (or sub-agent)."""

    session_key: str
    turn_id: str
    model: str | None = None
    spans: list[TraceSpan] = field(default_factory=list)
    chain_id: str = field(default_factory=lambda: uuid4().hex)
    created_at: str = field(default_factory=_now_iso)

    def add(
        self,
        tool_name: str,
        status: str,
        duration_ms: int,
        *,
        arguments: Any = None,
        result: Any = None,
        reasoning: str | None = None,
        error: str | None = None,
        preview_limit: int = 2000,
    ) -> TraceSpan:
        span = TraceSpan(
            tool_name=tool_name,
            status=status,
            duration_ms=duration_ms,
            arguments_preview=truncate(arguments, preview_limit),
            result_preview=truncate(result, preview_limit),
            reasoning_preview=truncate(reasoning, 1000),
            error=error,
        )
        self.spans.append(span)
        return span

    def to_jsonl(self) -> str:
        payload = {
            "chain_id": self.chain_id,
            "session_key": self.session_key,
            "turn_id": self.turn_id,
            "model": self.model,
            "created_at": self.created_at,
            "span_count": len(self.spans),
            "spans": [s.to_dict() for s in self.spans],
        }
        return json.dumps(payload, ensure_ascii=False, default=str)

    def summary(self) -> dict[str, Any]:
        """Compact aggregate for dashboards/audit (lesson 17 visibility)."""
        by_status: dict[str, int] = {}
        total_ms = 0
        for span in self.spans:
            by_status[span.status] = by_status.get(span.status, 0) + 1
            total_ms += span.duration_ms
        return {
            "chain_id": self.chain_id,
            "session_key": self.session_key,
            "turn_id": self.turn_id,
            "spans": len(self.spans),
            "by_status": by_status,
            "total_duration_ms": total_ms,
            "tools": [s.tool_name for s in self.spans],
        }


def render_chain_text(chain: TraceChain) -> str:
    """Human-readable rendering of a chain (model-visible debug view)."""
    lines = [f"# Trace {chain.chain_id[:8]} ({chain.session_key} / {chain.turn_id})"]
    for index, span in enumerate(chain.spans, start=1):
        status = span.status
        reasoning = (
            f"\n    why: {span.reasoning_preview}" if span.reasoning_preview else ""
        )
        lines.append(
            f"{index}. {span.tool_name} [{status}] {span.duration_ms}ms{reasoning}"
        )
    return "\n".join(lines)


__all__ = [
    "TraceChain",
    "TraceSpan",
    "render_chain_text",
]
