"""Provider-neutral reasoning capabilities and effort resolution.

The rest of DeepCode deals in semantic effort names.  Provider adapters own
the final wire representation; UI and Session code must not grow model-name
conditionals of their own.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable


_OFF_VALUES = frozenset({"none", "off", "disabled"})
_EFFORT_ALIASES = {"minimum": "minimal"}

# Stable internal keys stored in canonical Session metadata.  They are never
# exposed as conversation content; each adapter rehydrates only its own key.
OPENROUTER_REASONING_DETAILS = "openrouterReasoningDetails"
OPENAI_RESPONSE_REASONING_ITEMS = "openaiResponseReasoningItems"
ANTHROPIC_THINKING_BLOCKS = "anthropicThinkingBlocks"


def normalize_reasoning_effort(value: str | None) -> str | None:
    """Return one canonical semantic effort, or ``None`` when unspecified."""

    if value is None:
        return None
    clean = value.strip().lower()
    if not clean:
        return None
    if clean in _OFF_VALUES:
        return "none"
    return _EFFORT_ALIASES.get(clean, clean)


@dataclass(frozen=True, slots=True)
class ModelReasoningCapabilities:
    """Reasoning controls advertised by one concrete model.

    An empty ``supported_efforts`` means that the model supports reasoning,
    but the provider did not publish named effort levels.  In that case the
    safe product surface is Auto/Off rather than guessed provider values.
    """

    supported_efforts: tuple[str, ...] = ()
    default_effort: str | None = None
    default_enabled: bool = False
    mandatory: bool = False
    supports_summary: bool = False

    def __post_init__(self) -> None:
        efforts = _normalize_efforts(self.supported_efforts)
        default = normalize_reasoning_effort(self.default_effort)
        if default in {"auto", "none"}:
            default = None
        if default is not None and efforts and default not in efforts:
            raise ValueError("default reasoning effort must be supported")
        object.__setattr__(self, "supported_efforts", efforts)
        object.__setattr__(self, "default_effort", default)

    def to_dict(self) -> dict[str, Any]:
        return {
            "supportedEfforts": list(self.supported_efforts),
            "defaultEffort": self.default_effort,
            "defaultEnabled": self.default_enabled,
            "mandatory": self.mandatory,
            "supportsSummary": self.supports_summary,
        }

    @classmethod
    def from_dict(cls, value: Any) -> "ModelReasoningCapabilities | None":
        if not isinstance(value, dict):
            return None
        raw_efforts = value.get("supportedEfforts", value.get("supported_efforts", ()))
        if not isinstance(raw_efforts, (list, tuple)):
            raw_efforts = ()
        try:
            return cls(
                supported_efforts=tuple(
                    item for item in raw_efforts if isinstance(item, str)
                ),
                default_effort=_optional_string(
                    value.get("defaultEffort", value.get("default_effort"))
                ),
                default_enabled=bool(
                    value.get("defaultEnabled", value.get("default_enabled", False))
                ),
                mandatory=bool(value.get("mandatory", False)),
                supports_summary=bool(
                    value.get("supportsSummary", value.get("supports_summary", False))
                ),
            )
        except ValueError:
            return None


def resolve_reasoning_effort(
    *,
    requested: str | None,
    configured: str | None,
    capabilities: ModelReasoningCapabilities | None,
) -> str | None:
    """Resolve a Session choice into the immutable Turn effort.

    ``requested`` is the explicit Session selection.  ``None`` inherits the
    configuration default; ``auto`` deliberately bypasses that default and
    lets the provider/model choose.  Invalid explicit values fail loudly,
    while a stale global default safely falls back to the model default.
    """

    explicit = normalize_reasoning_effort(requested)
    inherited = requested is None
    candidate = normalize_reasoning_effort(configured) if inherited else explicit

    if candidate in {None, "auto"}:
        return None
    if candidate == "none":
        if capabilities is not None and capabilities.mandatory:
            if inherited:
                return capabilities.default_effort
            raise ValueError("reasoning cannot be disabled for this model")
        return "none"

    if capabilities is None or not capabilities.supported_efforts:
        return candidate
    if candidate in capabilities.supported_efforts:
        return candidate
    if inherited:
        return capabilities.default_effort if capabilities.default_enabled else None
    supported = ", ".join(capabilities.supported_efforts)
    raise ValueError(
        f"reasoning effort '{candidate}' is not supported; choose: {supported}"
    )


#: Parameter names a remote catalog uses to advertise a reasoning control.
_REASONING_PARAMETERS = frozenset(
    {"reasoning", "reasoning_effort", "include_reasoning", "thinking"}
)


def infer_reasoning_capabilities(
    model_id: str,
    *,
    provider_name: str | None = None,
    supported_parameters: Iterable[str] = (),
) -> ModelReasoningCapabilities | None:
    """Resolve the reasoning controls a model advertises, offline.

    Two ordered sources, no model-name conditionals:

    1. The model catalog (:mod:`core.providers.catalog`), whose
       normalize → snapshot → seed → family cascade already answers "what is
       this model" for context window and pricing. An unseen ``deepseek-r2``
       inherits the ``deepseek-r`` family row here exactly as it does there.
    2. ``supported_parameters`` published by a remote catalog, which proves a
       reasoning control exists even when the model is absent from the seed.
       No named levels come with it, so the surface is Auto/Off.

    Returns ``None`` when neither source knows of a reasoning mode. Callers
    treat that as "no thinking controls", so guessing here would put dead
    options in the product; a missing model belongs in the seed table, not in
    a branch added to this function.
    """

    # Imported lazily: the catalog imports this module for ``ModelInfo``.
    from core.providers.catalog import resolve_model_info

    known = resolve_model_info(model_id).reasoning
    if known is not None:
        return known

    parameters = {str(item).strip().lower() for item in supported_parameters}
    if parameters & _REASONING_PARAMETERS:
        provider = (provider_name or "").strip().lower()
        return ModelReasoningCapabilities(supports_summary=provider != "anthropic")
    return None


def _normalize_efforts(values: Iterable[str]) -> tuple[str, ...]:
    result: list[str] = []
    for value in values:
        normalized = normalize_reasoning_effort(value)
        if normalized in {None, "auto", "none"} or normalized in result:
            continue
        result.append(normalized)
    return tuple(result)


def _optional_string(value: Any) -> str | None:
    return value if isinstance(value, str) else None


__all__ = [
    "ANTHROPIC_THINKING_BLOCKS",
    "ModelReasoningCapabilities",
    "OPENAI_RESPONSE_REASONING_ITEMS",
    "OPENROUTER_REASONING_DETAILS",
    "infer_reasoning_capabilities",
    "normalize_reasoning_effort",
    "resolve_reasoning_effort",
]
