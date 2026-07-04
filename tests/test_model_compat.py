"""Pin the declarative ModelCompat resolver against known model behavior.

These tests are the behavioral contract the openai_compat assembler must
honor — they encode exactly what the pre-refactor scattered logic did, so a
regression in wire shaping (temperature, token field, thinking) fails here.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.providers.model_compat import (  # noqa: E402
    is_kimi_thinking_model,
    is_reasoning_model,
    normalize_effort,
    resolve_model_compat,
)
from core.providers.registry import find_by_name  # noqa: E402

OPENAI = find_by_name("openai")
DASHSCOPE = find_by_name("dashscope")


# ---- primitives ------------------------------------------------------------


def test_reasoning_model_detection():
    assert is_reasoning_model("gpt-5.4")
    assert is_reasoning_model("o3-mini")
    assert not is_reasoning_model("gpt-4o")
    assert not is_reasoning_model("claude-sonnet-4.5")


def test_kimi_prefix_stripped_before_lookup():
    assert is_kimi_thinking_model("kimi-k2.5")
    assert is_kimi_thinking_model("moonshotai/kimi-k2.5")
    assert not is_kimi_thinking_model("kimi-k2")


def test_effort_normalization():
    assert normalize_effort("Minimum") == "minimal"
    assert normalize_effort("LOW") == "low"
    assert normalize_effort(None) is None


# ---- gpt-5.4 on the openai spec (the validated Poe path) -------------------


def test_gpt5_with_reasoning_drops_temperature_uses_completion_tokens():
    c = resolve_model_compat(model_name="gpt-5.4", spec=OPENAI, reasoning_effort="low")
    assert c.include_temperature is False
    assert c.token_limit_field == "max_completion_tokens"
    assert c.reasoning_effort_wire == "low"
    assert c.thinking_extra_body is None
    assert c.inject_empty_reasoning_content is False


def test_gpt5_without_reasoning_keeps_temperature():
    c = resolve_model_compat(model_name="gpt-5.4", spec=OPENAI, reasoning_effort=None)
    assert c.include_temperature is True


def test_openai_does_not_strip_prefix():
    # openai spec has strip_model_prefix=False; the bare name must survive.
    c = resolve_model_compat(model_name="gpt-5.4", spec=OPENAI, reasoning_effort=None)
    assert c.model_name == "gpt-5.4"


# ---- non-reasoning model ---------------------------------------------------


def test_plain_model_keeps_temperature_and_max_tokens():
    c = resolve_model_compat(model_name="gpt-4o", spec=OPENAI, reasoning_effort=None)
    assert c.include_temperature is True
    # openai spec sets supports_max_completion_tokens=True (applies to all
    # its models), so the field is completion tokens regardless.
    assert c.token_limit_field == "max_completion_tokens"


# ---- dashscope effort spelling ---------------------------------------------


def test_dashscope_minimal_becomes_minimum_on_the_wire():
    if DASHSCOPE is None:
        return
    c = resolve_model_compat(
        model_name="qwen-max", spec=DASHSCOPE, reasoning_effort="minimal"
    )
    assert c.reasoning_effort_wire == "minimum"


# ---- kimi thinking ---------------------------------------------------------


def test_kimi_thinking_injects_extra_body_and_reasoning_echo():
    c = resolve_model_compat(
        model_name="kimi-k2.5", spec=OPENAI, reasoning_effort="high"
    )
    assert c.thinking_extra_body == {"thinking": {"type": "enabled"}}
    assert c.inject_empty_reasoning_content is True


def test_kimi_thinking_disabled_at_minimal_effort():
    c = resolve_model_compat(
        model_name="kimi-k2.5", spec=OPENAI, reasoning_effort="minimal"
    )
    assert c.thinking_extra_body == {"thinking": {"type": "disabled"}}
    assert c.inject_empty_reasoning_content is False


def test_no_spec_is_safe():
    c = resolve_model_compat(model_name="whatever", spec=None, reasoning_effort=None)
    assert c.model_name == "whatever"
    assert c.token_limit_field == "max_tokens"
    assert c.include_temperature is True
