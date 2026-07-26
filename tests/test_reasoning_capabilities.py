from __future__ import annotations

import pytest

from core.providers.reasoning import (
    ModelReasoningCapabilities,
    infer_reasoning_capabilities,
    resolve_reasoning_effort,
)


def test_capabilities_round_trip_uses_public_camel_case_shape() -> None:
    capabilities = ModelReasoningCapabilities(
        supported_efforts=("LOW", "high", "low"),
        default_effort="HIGH",
        default_enabled=True,
        supports_summary=True,
    )

    assert capabilities.supported_efforts == ("low", "high")
    assert ModelReasoningCapabilities.from_dict(capabilities.to_dict()) == capabilities


def test_explicit_auto_bypasses_configured_default() -> None:
    capabilities = ModelReasoningCapabilities(
        supported_efforts=("low", "high"),
        default_effort="high",
        default_enabled=True,
    )

    assert (
        resolve_reasoning_effort(
            requested="auto", configured="low", capabilities=capabilities
        )
        is None
    )


def test_explicit_off_is_rejected_for_mandatory_reasoning() -> None:
    capabilities = ModelReasoningCapabilities(
        supported_efforts=("low", "high"),
        default_effort="high",
        default_enabled=True,
        mandatory=True,
    )

    with pytest.raises(ValueError, match="cannot be disabled"):
        resolve_reasoning_effort(
            requested="off", configured=None, capabilities=capabilities
        )


def test_stale_configured_effort_falls_back_without_breaking_model_switch() -> None:
    capabilities = ModelReasoningCapabilities(
        supported_efforts=("low", "high", "max"),
        default_effort="max",
        default_enabled=True,
    )

    assert (
        resolve_reasoning_effort(
            requested=None, configured="medium", capabilities=capabilities
        )
        == "max"
    )


def test_explicit_unsupported_effort_fails_with_supported_values() -> None:
    capabilities = ModelReasoningCapabilities(supported_efforts=("low", "high"))

    with pytest.raises(ValueError, match="choose: low, high"):
        resolve_reasoning_effort(
            requested="medium", configured=None, capabilities=capabilities
        )


def test_kimi_k3_offline_capabilities_are_centralized() -> None:
    capabilities = infer_reasoning_capabilities("moonshotai/kimi-k3")

    assert capabilities is not None
    assert capabilities.supported_efforts == ("low", "high", "max")
    assert capabilities.default_effort == "max"


@pytest.mark.parametrize(
    "model_id",
    ["claude-opus-4-6", "anthropic/claude-sonnet-4.6"],
)
def test_claude_46_aliases_use_adaptive_effort_capabilities(model_id: str) -> None:
    capabilities = infer_reasoning_capabilities(model_id)

    assert capabilities is not None
    assert capabilities.supported_efforts == ("low", "medium", "high", "max")
    assert capabilities.supports_summary is True
