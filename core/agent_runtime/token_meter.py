"""Injectable prompt-size measurement.

Two implementations behind one seam:

- :class:`HeuristicTokenMeter` prices a whole request with the estimator
  chain (tiktoken when installed, four characters per token otherwise).
- :class:`ProviderAnchoredTokenMeter` prices it the way dsh's ``token-meter``
  does: take the provider's own reported prompt size as an **anchor** and let
  the heuristic price only what the history gained since that sample.

The anchor matters because the estimator is a stand-in for a tokenizer it
does not have. ``cl100k_base`` is OpenAI's; every other vendor segments text
differently. Measured against DeepSeek's own reported ``prompt_tokens`` for
identical content:

    English source     1,336 estimated vs 1,392 reported    -4%
    JSON tool schemas  1,425 estimated vs 1,425 reported     0%
    Chinese prose      1,344 estimated vs   624 reported  +115%

The error is not noise and it is not uniform — it is concentrated on CJK,
where the estimator prices a conversation at more than double its real cost.
With the compaction gate at 0.9 of the budget, a Chinese conversation
triggers summarization at roughly half the context it could actually hold:
a model round-trip spent condensing history that still fit, and a head
discarded that did not need to go. The provider's own number has no such
gap, and every response carries one.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Protocol

from core.agent_runtime.helpers import (
    estimate_message_tokens,
    estimate_prompt_tokens_chain,
    history_signature as _shape,
)


class TokenMeter(Protocol):
    """Prices a prospective request."""

    def measure(
        self,
        provider: Any,
        model: str | None,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> int | None: ...

    def observe(
        self,
        usage: Mapping[str, Any] | None,
        messages: list[dict[str, Any]],
    ) -> None:
        """Record what a real request actually cost."""


class HeuristicTokenMeter:
    """Wraps ``estimate_prompt_tokens_chain``; ``None`` when pricing fails."""

    def measure(
        self,
        provider: Any,
        model: str | None,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> int | None:
        try:
            estimate, _ = estimate_prompt_tokens_chain(provider, model, messages, tools)
        except Exception:
            return None
        return estimate

    def observe(
        self,
        usage: Mapping[str, Any] | None,
        messages: list[dict[str, Any]],
    ) -> None:
        return None


def _body(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The conversation without the system block.

    The two call sites compose the system block differently (one merges
    transient context into it, one does not), so the anchor is kept over the
    part both agree on. The system block's cost is inside the anchored
    provider number either way.
    """
    return [message for message in messages if message.get("role") != "system"]


@dataclass(slots=True)
class _Anchor:
    """One real request's reported prompt size and the history it priced."""

    prompt_tokens: int
    shape: tuple[tuple[str, int], ...]


class ProviderAnchoredTokenMeter:
    """Anchors on reported usage; the heuristic prices only the delta.

    Falls back to the pure heuristic whenever there is no usable anchor —
    before the first response, and after a compaction rewrites the history
    the anchor was taken from.
    """

    def __init__(self) -> None:
        self._anchor: _Anchor | None = None
        self._heuristic = HeuristicTokenMeter()

    def observe(
        self,
        usage: Mapping[str, Any] | None,
        messages: list[dict[str, Any]],
    ) -> None:
        prompt_tokens = 0
        if isinstance(usage, Mapping):
            for key in ("prompt_tokens", "input_tokens"):
                value = usage.get(key)
                if isinstance(value, int) and not isinstance(value, bool) and value > 0:
                    prompt_tokens = value
                    break
        if prompt_tokens <= 0:
            return
        # A cached read is still part of the prompt the window has to hold.
        self._anchor = _Anchor(
            prompt_tokens=prompt_tokens,
            shape=_shape(_body(messages)),
        )

    def measure(
        self,
        provider: Any,
        model: str | None,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> int | None:
        anchor = self._anchor
        if anchor is not None:
            body = _body(messages)
            shape = _shape(body)
            anchored = len(anchor.shape)
            if len(shape) >= anchored and shape[:anchored] == anchor.shape:
                delta = sum(
                    estimate_message_tokens(message) for message in body[anchored:]
                )
                return anchor.prompt_tokens + delta
            # The history no longer extends what the provider priced: a
            # compaction or a snip rewrote it. The anchor is stale.
            self._anchor = None
        return self._heuristic.measure(provider, model, messages, tools)


# Deliberately NOT a module singleton: the anchor is per-conversation state,
# and one shared instance would price a fresh Session with another Session's
# last request. ``AgentRunSpec`` builds one per run.
DEFAULT_TOKEN_METER_FACTORY = ProviderAnchoredTokenMeter
