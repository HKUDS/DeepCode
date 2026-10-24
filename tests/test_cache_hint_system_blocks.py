"""cacheHint hygiene on the non-Anthropic paths.

``cacheHint`` is a private agent-side key (see ``core.agent_setup``) that
marks the stable/dynamic boundary of a block-list system prompt for the
Anthropic cache layer. Providers that do not understand it must strip it
before the payload leaves the process, and must not lose the text it
annotates.

Contract under test:

- OpenAI-compat message sanitization removes ``cacheHint`` from content
  BLOCKS (top-level key filtering alone would leak it);
- the Responses-API converter joins block-list system prompts into plain
  text, ignoring ``cacheHint`` entirely — the hint key is Anthropic-only
  plumbing and must not appear anywhere in the converted payload.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.providers.openai_compat import OpenAICompatProvider
from core.providers.openai_responses.converters import convert_messages


def _hinted_system() -> list[dict[str, Any]]:
    return [
        {"type": "text", "text": "persona", "cacheHint": "stable"},
        {"type": "text", "text": "project docs", "cacheHint": "dynamic"},
    ]


def test_openai_compat_sanitizer_strips_cache_hint_from_blocks() -> None:
    provider = OpenAICompatProvider(api_key="test-key")
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": _hinted_system()},
        {"role": "user", "content": "hello"},
    ]
    sanitized = provider._sanitize_messages(messages)

    system_content = sanitized[0]["content"]
    assert isinstance(system_content, list)
    assert all("cacheHint" not in block for block in system_content)
    # The annotated text survives — only the private key is removed.
    assert [block["text"] for block in system_content] == ["persona", "project docs"]
    # The original caller list is untouched (sanitization is copy-on-write).
    assert all("cacheHint" in block for block in messages[0]["content"])


def test_responses_converter_joins_hinted_blocks_and_drops_the_key() -> None:
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": _hinted_system()},
        {"role": "user", "content": "hello"},
    ]
    system_prompt, input_items = convert_messages(messages)

    # Hint keys are Anthropic-only plumbing: the joined text is clean.
    assert system_prompt == "persona\n\nproject docs"
    assert "cacheHint" not in system_prompt
    assert "cacheHint" not in str(input_items)
    # The converter wraps string content into Responses-API input_text blocks.
    assert input_items == [
        {"role": "user", "content": [{"type": "input_text", "text": "hello"}]}
    ]


def test_responses_converter_string_system_passthrough() -> None:
    system_prompt, input_items = convert_messages(
        [{"role": "system", "content": "plain"}, {"role": "user", "content": "hi"}]
    )
    assert system_prompt == "plain"
    assert input_items == [
        {"role": "user", "content": [{"type": "input_text", "text": "hi"}]}
    ]
