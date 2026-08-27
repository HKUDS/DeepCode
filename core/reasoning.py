"""Provider-neutral reasoning data shared by the kernel and product surfaces.

Reasoning effort controls generation.  These types describe only what a
provider actually returned for display and replay.  In particular,
``provider_trace`` does not claim to be a model's private or complete chain of
thought; it is the provider-designated reasoning text received on the wire.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Any


class ReasoningChannel(str, Enum):
    """A displayable reasoning stream returned by a provider."""

    SUMMARY = "summary"
    PROVIDER_TRACE = "provider_trace"


class ReasoningAvailability(str, Enum):
    """Whether a provider returned displayable reasoning text."""

    AVAILABLE = "available"
    OPAQUE = "opaque"


@dataclass(frozen=True, slots=True)
class ReasoningPayload:
    """Versioned durable payload for one reasoning timeline item."""

    summary_text: str = ""
    trace_text: str = ""
    availability: ReasoningAvailability = ReasoningAvailability.AVAILABLE
    effort: str | None = None
    duration_ms: int | None = None
    streaming: bool = False

    SCHEMA_VERSION = 1

    def __post_init__(self) -> None:
        if self.duration_ms is not None and self.duration_ms < 0:
            raise ValueError("duration_ms must be non-negative")

    @property
    def display_text(self) -> str:
        """Prefer a provider summary, falling back to provider trace text."""

        return self.summary_text or self.trace_text

    def text_for(self, channel: ReasoningChannel) -> str:
        return (
            self.summary_text
            if channel is ReasoningChannel.SUMMARY
            else self.trace_text
        )

    def with_delta(
        self,
        channel: ReasoningChannel,
        delta: str,
    ) -> ReasoningPayload:
        if channel is ReasoningChannel.SUMMARY:
            return ReasoningPayload(
                summary_text=self.summary_text + delta,
                trace_text=self.trace_text,
                availability=ReasoningAvailability.AVAILABLE,
                effort=self.effort,
                duration_ms=self.duration_ms,
                streaming=True,
            )
        return ReasoningPayload(
            summary_text=self.summary_text,
            trace_text=self.trace_text + delta,
            availability=ReasoningAvailability.AVAILABLE,
            effort=self.effort,
            duration_ms=self.duration_ms,
            streaming=True,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schemaVersion": self.SCHEMA_VERSION,
            "summaryText": self.summary_text,
            "traceText": self.trace_text,
            "availability": self.availability.value,
            "effort": self.effort,
            "durationMs": self.duration_ms,
            "streaming": self.streaming,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any] | None) -> ReasoningPayload:
        """Decode current and legacy ``{"text": summary}`` item payloads."""

        raw = value or {}
        summary = raw.get("summaryText")
        if not isinstance(summary, str):
            legacy = raw.get("text")
            summary = legacy if isinstance(legacy, str) else ""
        trace = raw.get("traceText")
        if not isinstance(trace, str):
            trace = ""
        availability_value = raw.get("availability")
        try:
            availability = ReasoningAvailability(str(availability_value))
        except ValueError:
            availability = (
                ReasoningAvailability.AVAILABLE
                if summary or trace
                else ReasoningAvailability.OPAQUE
            )
        effort = raw.get("effort")
        if not isinstance(effort, str) or not effort.strip():
            effort = None
        duration = raw.get("durationMs")
        if not isinstance(duration, int) or isinstance(duration, bool) or duration < 0:
            duration = None
        return cls(
            summary_text=summary,
            trace_text=trace,
            availability=availability,
            effort=effort,
            duration_ms=duration,
            streaming=bool(raw.get("streaming", False)),
        )


__all__ = [
    "ReasoningAvailability",
    "ReasoningChannel",
    "ReasoningPayload",
]
