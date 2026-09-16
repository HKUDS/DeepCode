"""Immutable LLM selection captured when a Turn is accepted."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any

MIN_CONTEXT_WINDOW_TOKENS = 4_096
MAX_CONTEXT_WINDOW_TOKENS = 10_000_000


@dataclass(frozen=True, slots=True)
class ExecutionSelection:
    """Mutable-at-the-Session-boundary selection before it is resolved."""

    connection_id: str | None = None
    model_id: str | None = None
    reasoning_effort: str | None = None
    context_window: int | None = None

    def normalized(self) -> "ExecutionSelection":
        context_window = self.context_window
        if context_window is not None:
            if isinstance(context_window, bool) or not isinstance(context_window, int):
                raise ValueError("context_window must be an integer or None")
            if context_window < MIN_CONTEXT_WINDOW_TOKENS:
                raise ValueError(
                    f"context_window must be at least {MIN_CONTEXT_WINDOW_TOKENS}"
                )
            if context_window > MAX_CONTEXT_WINDOW_TOKENS:
                raise ValueError(
                    f"context_window must be at most {MAX_CONTEXT_WINDOW_TOKENS}"
                )
        return ExecutionSelection(
            connection_id=_clean_optional(self.connection_id),
            model_id=_clean_optional(self.model_id),
            reasoning_effort=_clean_optional(self.reasoning_effort),
            context_window=context_window,
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
    protocol: str = "auto"
    provider_revision: str | None = None
    input_modalities: tuple[str, ...] | None = None
    tool_calling: bool | None = None
    reasoning_supported: bool | None = None

    def __post_init__(self) -> None:
        if self.provider_revision is not None and (
            not isinstance(self.provider_revision, str)
            or not re.fullmatch(r"[a-f0-9]{64}", self.provider_revision)
        ):
            raise ValueError("invalid provider revision")
        if self.reasoning_supported is not None and not isinstance(
            self.reasoning_supported, bool
        ):
            raise ValueError("reasoning_supported must be a boolean")
        if self.protocol not in {
            "auto",
            "openai_chat",
            "openai_responses",
            "anthropic_messages",
        }:
            raise ValueError("invalid provider protocol")
        if self.input_modalities is not None and (
            not self.input_modalities or set(self.input_modalities) - {"text", "image"}
        ):
            raise ValueError("invalid input modalities")
        if self.tool_calling is not None and not isinstance(self.tool_calling, bool):
            raise ValueError("tool_calling must be a boolean")
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
            **({"protocol": self.protocol} if self.protocol != "auto" else {}),
            **(
                {"providerRevision": self.provider_revision}
                if self.provider_revision is not None
                else {}
            ),
            **(
                {"inputModalities": list(self.input_modalities)}
                if self.input_modalities is not None
                else {}
            ),
            **(
                {"toolCalling": self.tool_calling}
                if self.tool_calling is not None
                else {}
            ),
            **(
                {"reasoningSupported": self.reasoning_supported}
                if self.reasoning_supported is not None
                else {}
            ),
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
                protocol=value.get("protocol", "auto"),
                provider_revision=value.get("providerRevision"),
                input_modalities=tuple(value["inputModalities"])
                if value.get("inputModalities") is not None
                else None,
                tool_calling=value.get("toolCalling"),
                reasoning_supported=value.get("reasoningSupported"),
            )
        except (KeyError, TypeError, ValueError):
            return None


def _clean_optional(value: str | None) -> str | None:
    if value is None:
        return None
    clean = value.strip()
    return clean or None


__all__ = [
    "MIN_CONTEXT_WINDOW_TOKENS",
    "MAX_CONTEXT_WINDOW_TOKENS",
    "ExecutionProfile",
    "ExecutionSelection",
]
