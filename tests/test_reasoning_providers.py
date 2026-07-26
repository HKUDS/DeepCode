from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from core.providers.anthropic import AnthropicProvider
from core.providers.openai_compat import OpenAICompatProvider
from core.providers.openai_responses import parse_response_output
from core.providers.reasoning import (
    ANTHROPIC_THINKING_BLOCKS,
    OPENAI_RESPONSE_REASONING_ITEMS,
    OPENROUTER_REASONING_DETAILS,
)
from core.providers.registry import find_by_name


def _openai_compatible_provider(
    provider_name: str, default_model: str
) -> OpenAICompatProvider:
    spec = find_by_name(provider_name)
    assert spec is not None
    provider = object.__new__(OpenAICompatProvider)
    provider.default_model = default_model
    provider._spec = spec
    provider._effective_base = spec.default_api_base or None
    provider._responses_failures = {}
    provider._responses_tripped_at = {}
    return provider


def _openrouter_provider() -> OpenAICompatProvider:
    return _openai_compatible_provider("openrouter", "moonshotai/kimi-k3")


def _openai_provider() -> OpenAICompatProvider:
    return _openai_compatible_provider("openai", "gpt-5.4")


def _anthropic_provider() -> AnthropicProvider:
    # Request shaping and parsing are pure.  The Anthropic SDK is optional in
    # the default development environment, so these tests do not construct a
    # network client.
    provider = object.__new__(AnthropicProvider)
    provider.default_model = "claude-opus-4-6"
    provider.extra_headers = {}
    return provider


def test_openrouter_uses_unified_reasoning_request() -> None:
    provider = _openrouter_provider()

    kwargs = provider._build_kwargs(
        [{"role": "user", "content": "hello"}],
        None,
        "moonshotai/kimi-k3",
        1024,
        0.1,
        "high",
        None,
    )

    assert "reasoning_effort" not in kwargs
    assert kwargs["extra_body"]["reasoning"] == {"effort": "high"}


def test_openrouter_off_is_explicit_and_provider_state_round_trips() -> None:
    provider = _openrouter_provider()
    details = [{"type": "reasoning.encrypted", "data": "opaque", "id": "r1"}]

    kwargs = provider._build_kwargs(
        [
            {
                "role": "assistant",
                "content": "answer",
                "provider_state": {OPENROUTER_REASONING_DETAILS: details},
            },
            {"role": "user", "content": "continue"},
        ],
        None,
        "moonshotai/kimi-k3",
        1024,
        0.1,
        "none",
        None,
    )

    assert kwargs["extra_body"]["reasoning"] == {"enabled": False}
    assistant = next(msg for msg in kwargs["messages"] if msg["role"] == "assistant")
    assert assistant["reasoning_details"] == details
    assert "provider_state" not in assistant


def test_openrouter_raw_reasoning_never_becomes_visible_content() -> None:
    provider = _openrouter_provider()
    details = [{"type": "reasoning.text", "text": "private chain", "id": "r1"}]

    response = provider._parse(
        {
            "choices": [
                {
                    "message": {
                        "content": None,
                        "reasoning": "private chain",
                        "reasoning_details": details,
                    },
                    "finish_reason": "stop",
                }
            ]
        }
    )

    assert response.content is None
    assert response.reasoning_content == "private chain"
    assert response.reasoning_summary is None
    assert response.provider_state == {OPENROUTER_REASONING_DETAILS: details}


def test_openrouter_only_projects_provider_designated_summary() -> None:
    provider = _openrouter_provider()

    response = provider._parse(
        {
            "choices": [
                {
                    "message": {
                        "content": "answer",
                        "reasoning_details": [
                            {"type": "reasoning.text", "text": "private"},
                            {
                                "type": "reasoning.summary",
                                "summary": "Checked inputs.",
                            },
                        ],
                    }
                }
            ]
        }
    )

    assert response.content == "answer"
    assert response.reasoning_summary == "Checked inputs."


def test_openai_responses_requests_summary_and_replays_opaque_item() -> None:
    provider = _openai_provider()
    item = {
        "type": "reasoning",
        "id": "rs_1",
        "summary": [],
        "encrypted_content": "opaque",
    }

    body = provider._build_responses_body(
        [
            {
                "role": "assistant",
                "content": "answer",
                "provider_state": {OPENAI_RESPONSE_REASONING_ITEMS: [item]},
            },
            {"role": "user", "content": "continue"},
        ],
        None,
        "gpt-5.4",
        1024,
        0.1,
        "high",
        None,
    )

    assert body["reasoning"] == {"summary": "auto", "effort": "high"}
    assert body["include"] == ["reasoning.encrypted_content"]
    assert item in body["input"]


def test_openai_responses_parser_separates_summary_and_state() -> None:
    item = {
        "type": "reasoning",
        "id": "rs_1",
        "summary": [{"type": "summary_text", "text": "Checked the constraints."}],
        "encrypted_content": "opaque",
    }

    response = parse_response_output(
        {
            "status": "completed",
            "output": [
                item,
                {
                    "type": "message",
                    "content": [{"type": "output_text", "text": "answer"}],
                },
            ],
        }
    )

    assert response.content == "answer"
    assert response.reasoning_summary == "Checked the constraints."
    assert response.provider_state == {OPENAI_RESPONSE_REASONING_ITEMS: [item]}


def test_anthropic_modern_reasoning_uses_adaptive_summary_contract() -> None:
    provider = _anthropic_provider()

    kwargs = provider._build_kwargs(
        [{"role": "user", "content": "hello"}],
        None,
        "claude-opus-4-6",
        4096,
        0.1,
        "high",
        None,
        supports_caching=False,
    )

    assert kwargs["thinking"] == {"type": "adaptive", "display": "summarized"}
    assert kwargs["output_config"] == {"effort": "high"}
    assert "temperature" not in kwargs


def test_anthropic_none_disables_thinking() -> None:
    provider = _anthropic_provider()

    kwargs = provider._build_kwargs(
        [{"role": "user", "content": "hello"}],
        None,
        "claude-opus-4-6",
        4096,
        0.1,
        "none",
        None,
        supports_caching=False,
    )

    assert "thinking" not in kwargs
    assert kwargs["temperature"] == 0.1


def test_anthropic_summary_is_safe_while_signed_block_is_replayable() -> None:
    block = SimpleNamespace(type="thinking", thinking="Safe summary", signature="sig")
    response = AnthropicProvider._parse_response(
        SimpleNamespace(
            content=[block, SimpleNamespace(type="text", text="answer")],
            stop_reason="end_turn",
            usage=None,
        ),
        expose_reasoning_summary=True,
    )

    expected = [{"type": "thinking", "thinking": "Safe summary", "signature": "sig"}]
    assert response.reasoning_summary == "Safe summary"
    assert response.provider_state == {ANTHROPIC_THINKING_BLOCKS: expected}
    assert (
        AnthropicProvider._assistant_blocks(
            {
                "content": "answer",
                "provider_state": response.provider_state,
            }
        )[0]
        == expected[0]
    )


@pytest.mark.asyncio
async def test_anthropic_reasoning_events_keep_stream_alive_without_becoming_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = _anthropic_provider()
    visible_deltas: list[str] = []
    events = [
        {"delta": {"type": "thinking_delta", "thinking": "private-1"}},
        {"delta": {"type": "thinking_delta", "thinking": "private-2"}},
        {"delta": {"type": "text_delta", "text": "visible"}},
    ]

    class FakeStream:
        def __init__(self) -> None:
            self._events = iter(events)

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback):
            return False

        def __aiter__(self):
            return self

        async def __anext__(self):
            try:
                event = next(self._events)
            except StopIteration as exc:
                raise StopAsyncIteration from exc
            await asyncio.sleep(0.006)
            return event

        async def get_final_message(self):
            return SimpleNamespace(
                content=[SimpleNamespace(type="text", text="visible")],
                stop_reason="end_turn",
                usage=None,
            )

    class FakeMessages:
        def stream(self, **kwargs):
            return FakeStream()

    provider._client = SimpleNamespace(messages=FakeMessages())
    provider._build_kwargs = lambda *args, **kwargs: {}
    provider._emit_observability = lambda **kwargs: None
    monkeypatch.setenv("DEEPCODE_STREAM_IDLE_TIMEOUT_S", "0.01")

    async def on_content(delta: str) -> None:
        visible_deltas.append(delta)

    response = await provider.chat_stream(
        [{"role": "user", "content": "hello"}],
        reasoning_effort="high",
        on_content_delta=on_content,
    )

    assert response.finish_reason == "stop"
    assert response.content == "visible"
    assert visible_deltas == ["visible"]
