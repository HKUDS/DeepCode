"""Immutable LLM selection captured when a Turn is accepted."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class ExecutionSelection:
    """Mutable-at-the-Session-boundary selection before it is resolved."""

    connection_id: str | None = None
    model_id: str | None = None
    reasoning_effort: str | None = None

    def normalized(self) -> "ExecutionSelection":
        return ExecutionSelection(
            connection_id=_clean_optional(self.connection_id),
            model_id=_clean_optional(self.model_id),
            reasoning_effort=_clean_optional(self.reasoning_effort),
        )


@dataclass(frozen=True, slots=True)
class ExecutionProfile:
    """Complete, secret-free execution settings for one immutable Turn.

    Credentials deliberately never enter this object: it is persisted in
    SQLite, canonical Session metadata, event replay, and frontend payloads.
    """

    connection_id: str
    provider_name: str
    adapter: str
    model_id: str
    context_window: int
    max_output_tokens: int
    max_tokens: int
    temperature: float
    reasoning_effort: str | None
    config_revision: str

    def __post_init__(self) -> None:
        for value, name in (
            (self.connection_id, "connection_id"),
            (self.provider_name, "provider_name"),
            (self.adapter, "adapter"),
            (self.model_id, "model_id"),
            (self.config_revision, "config_revision"),
        ):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must not be empty")
        if self.context_window < 1:
            raise ValueError("context_window must be positive")
        if self.max_output_tokens < 1:
            raise ValueError("max_output_tokens must be positive")
        if self.max_tokens < 1:
            raise ValueError("max_tokens must be positive")
        if not math.isfinite(self.temperature) or self.temperature < 0:
            raise ValueError("temperature must be a finite non-negative number")

    def to_dict(self) -> dict[str, Any]:
        return {
            "connectionId": self.connection_id,
            "providerName": self.provider_name,
            "adapter": self.adapter,
            "modelId": self.model_id,
            "contextWindow": self.context_window,
            "maxOutputTokens": self.max_output_tokens,
            "maxTokens": self.max_tokens,
            "temperature": self.temperature,
            "reasoningEffort": self.reasoning_effort,
            "configRevision": self.config_revision,
        }

    @classmethod
    def from_dict(cls, value: Any) -> "ExecutionProfile | None":
        """Decode persisted data, returning ``None`` for legacy/invalid rows."""

        if not isinstance(value, dict):
            return None
        try:
            return cls(
                connection_id=str(value["connectionId"]),
                provider_name=str(value["providerName"]),
                adapter=str(value["adapter"]),
                model_id=str(value["modelId"]),
                context_window=int(value["contextWindow"]),
                max_output_tokens=int(value["maxOutputTokens"]),
                max_tokens=int(value["maxTokens"]),
                temperature=float(value["temperature"]),
                reasoning_effort=(
                    str(value["reasoningEffort"])
                    if value.get("reasoningEffort") is not None
                    else None
                ),
                config_revision=str(value["configRevision"]),
            )
        except (KeyError, TypeError, ValueError):
            return None


def _clean_optional(value: str | None) -> str | None:
    if value is None:
        return None
    clean = value.strip()
    return clean or None


__all__ = ["ExecutionProfile", "ExecutionSelection"]
