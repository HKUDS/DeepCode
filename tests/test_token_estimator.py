"""The context-governance helpers price a request the same way every run.

``estimate_prompt_tokens`` and ``estimate_message_tokens`` decide when the
pressure gate compacts or discards history, and ``history_signature`` is the
shared contract by which the token meter's anchor and the compaction memo
agree on what "unchanged" means.  All three load-bearing helpers were exercised
only indirectly, through the meter.  These tests pin their arithmetic and their
field collection directly, so a silent change here surfaces as a red test
rather than as a context overflow or an anchor that quietly stops anchoring.

The offline estimator (no ``tiktoken``) is the branch a minimal install runs,
and it is the branch whose ``// 4`` arithmetic these tests lock.  Forcing the
tokenizer off makes every value below exact regardless of what the machine
happens to have installed, matching the tokenizer-agnostic style of
``test_token_meter.py``.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.agent_runtime import helpers
from core.agent_runtime.helpers import (
    estimate_message_tokens,
    estimate_prompt_tokens,
    history_signature,
)


@pytest.fixture
def offline(monkeypatch: pytest.MonkeyPatch):
    """Run the length-based fallback: the path a tokenizer-less install takes."""
    monkeypatch.setattr(helpers, "tiktoken", None)


def test_empty_content_is_priced_as_overhead_only(offline) -> None:
    """A message carries its 4-token overhead even when it says nothing."""
    assert estimate_prompt_tokens([{"role": "user", "content": ""}]) == 4


def test_the_offline_estimate_is_length_over_four_plus_overhead(offline) -> None:
    """Lock the ``len(payload) // 4 + 4 * messages`` arithmetic.

    ``// 4`` is the deliberate, documented approximation; an exact value here
    turns any future change to it into a reviewed decision instead of a
    silent drift in when the gate fires.
    """
    assert estimate_prompt_tokens([{"role": "user", "content": "abcd"}]) == 5
    assert estimate_prompt_tokens([{"role": "user", "content": "hello world"}]) == 6
    # Overhead is per message, so two short messages cost more than one.
    assert (
        estimate_prompt_tokens(
            [{"role": "user", "content": "abcd"}, {"role": "user", "content": "efgh"}]
        )
        == 10
    )


def test_text_parts_are_priced_like_the_equivalent_string(offline) -> None:
    """A ``[{"type": "text", ...}]`` block is the same prompt as its text."""
    as_string = estimate_prompt_tokens([{"role": "user", "content": "abcd"}])
    as_parts = estimate_prompt_tokens(
        [{"role": "user", "content": [{"type": "text", "text": "abcd"}]}]
    )
    assert as_string == as_parts == 5


def test_non_text_parts_are_not_priced(offline) -> None:
    """Only ``type: "text"`` blocks reach the payload; an image block is free.

    Dropping this filter would overcharge every multimodal turn, so the
    boundary is worth stating as behaviour.
    """
    assert (
        estimate_prompt_tokens(
            [{"role": "user", "content": [{"type": "image", "url": "x"}]}]
        )
        == 4
    )


def test_every_field_of_a_tool_turn_reaches_the_payload(offline) -> None:
    """Each field a tool turn can carry adds to the price.

    ``tool_calls``, ``reasoning_content``, ``name`` and ``tool_call_id`` are
    model-visible tokens.  Losing any one of them undercharges the request and
    lets the gate fire late, so each is asserted to move the number.
    """
    bare = estimate_prompt_tokens([{"role": "assistant", "content": "x"}])
    with_calls = estimate_prompt_tokens(
        [{"role": "assistant", "content": "x", "tool_calls": [{"id": "1"}]}]
    )
    with_reasoning = estimate_prompt_tokens(
        [{"role": "assistant", "content": "x", "reasoning_content": "why"}]
    )
    with_name = estimate_prompt_tokens(
        [{"role": "assistant", "content": "x", "name": "tool"}]
    )
    with_call_id = estimate_prompt_tokens(
        [{"role": "assistant", "content": "x", "tool_call_id": "call1"}]
    )

    assert with_calls > bare
    assert with_reasoning > bare
    assert with_name > bare
    assert with_call_id > bare


def test_the_tool_schema_is_priced(offline) -> None:
    """The tools a request declares are part of what the window has to hold."""
    messages = [{"role": "user", "content": "x"}]
    assert estimate_prompt_tokens(messages, tools=[{"name": "bash"}]) > (
        estimate_prompt_tokens(messages)
    )


def test_a_cjk_prompt_and_an_ascii_prompt_of_equal_length_price_the_same(
    offline,
) -> None:
    """The offline estimator is script-blind: it counts length, not tokens.

    Four CJK characters and four ASCII characters are both four characters, so
    both price at ``4 // 4 + 4``.  This locks the current, deliberate
    approximation so a future script-aware change is a reviewed decision.  (The
    ``+115%`` CJK overestimate the token meter documents is a property of the
    ``cl100k_base`` tokenizer path, asserted separately below.)
    """
    cjk = estimate_prompt_tokens([{"role": "user", "content": "你好世界"}])
    ascii_ = estimate_prompt_tokens([{"role": "user", "content": "abcd"}])
    assert cjk == ascii_ == 5


def test_the_tokenizer_prices_cjk_characters_above_ascii() -> None:
    """Under ``cl100k_base`` a CJK character costs more than an ASCII one.

    This is the seed of the overestimate the token meter's docstring
    quantifies: the estimator charges CJK heavily by character while the
    providers that report a real number charge it lightly.  Qualified on the
    tokenizer so the exact-value tests above stay deterministic on a
    tokenizer-less machine.
    """
    pytest.importorskip("tiktoken")
    cjk = estimate_prompt_tokens([{"role": "user", "content": "你好世界你好世界"}])
    ascii_ = estimate_prompt_tokens([{"role": "user", "content": "abcdefghijkl"}])
    assert cjk > ascii_


def test_message_tokens_has_a_floor_of_four(offline) -> None:
    """A single message never prices below four tokens."""
    assert estimate_message_tokens({"role": "user"}) == 4
    assert estimate_message_tokens({"role": "user", "content": "ab"}) == 4
    assert estimate_message_tokens({"role": "user", "content": "abcd"}) == 5


def test_the_signature_skips_the_system_block() -> None:
    """The system block is priced by the provider, not by the anchor.

    The anchor and the compaction memo both compare non-system history, so a
    system message must never enter the signature.
    """
    assert history_signature(
        [{"role": "system", "content": "sys"}, {"role": "user", "content": "hi"}]
    ) == (("user", 2),)


def test_the_signature_prices_string_content_by_length() -> None:
    """A string message is priced by its character length."""
    assert history_signature([{"role": "user", "content": "hello"}]) == (("user", 5),)


def test_tool_calls_are_priced_into_the_signature() -> None:
    """Each tool call adds a fixed weight, and content length is still counted.

    ``length + 16 * len(tool_calls)`` is what lets the signature tell "the
    same text with more tool activity" apart from "the same text unchanged".
    """
    assert history_signature(
        [
            {
                "role": "assistant",
                "content": "ab",
                "tool_calls": [{"id": "1"}, {"id": "2"}],
            }
        ]
    ) == (("assistant", 2 + 32),)


def test_a_signature_is_a_prefix_of_the_same_history_with_more_appended() -> None:
    """Appending preserves the prefix — the exact property the anchor relies on.

    ``ProviderAnchoredTokenMeter`` keeps its anchor only while the new history
    starts with the signature it sampled.  A change to the signature's shape or
    weighting that broke this prefix would silently drop every anchor (no
    error, just a wasted model round-trip per compaction).  Non-string content
    is included because a list-content message must also survive the prefix
    check when it is re-sent unchanged.
    """
    base = [
        {"role": "user", "content": "q1"},
        {"role": "assistant", "content": "a1"},
        {"role": "user", "content": [{"type": "text", "text": "q2"}]},
    ]
    grown = [*base, {"role": "assistant", "content": "a2"}]

    base_signature = history_signature(base)
    assert base_signature == (("user", 2), ("assistant", 2), ("user", 0))
    # The grown history starts with exactly the signature the anchor sampled.
    assert history_signature(grown)[: len(base_signature)] == base_signature
