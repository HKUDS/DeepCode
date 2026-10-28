"""Microcompact — model-free clearing of old tool results.

When the runner's context-pressure gate fires, the cheapest first rung on the
compaction ladder is to replace *stale tool results* with a placeholder text
instead of deleting them, clearing real token pressure **before** the
summarization compactor (and its model round-trip) is ever considered.

Two different jobs, two different modules:

* :class:`~core.agent_runtime.pruner.ToolResultPruner` **middle-prunes
  oversized** results that a single turn blew up, keeping head + tail — the
  fix for "this one read was 800KB".
* microcompact **fully clears old, already-consumed** results once the turn
  has moved on — the fix for "40 tool results are still resident because the
  loop never compacted".

The runner owns the trigger decision: ``AgentRunner._maybe_compact`` runs only
*after* its own token-meter pressure gate passes, so this module does no
estimation of its own. An internal chars/4 estimate over message text would
miss tool-call arguments, tool schemas, and the provider's real token count —
exactly the Chinese / write-heavy sessions that most need clearing. The caller
decides *when*; this module only applies the clear.

Three invariants the clear must keep:

* **The latest tool batch is never touched** — the tool results the model has
  not yet read (those issued by the most recent assistant ``tool_calls``) are
  kept verbatim regardless of how many parallel calls there were, so the model
  always sees the full batch it is about to act on.
* **Recency is kept** — the last ``keep_recent_tool_results`` results are also
  never touched, so older-but-still-fresh evidence survives.
* **Pair integrity is kept** — a tool ``result`` message keeps its
  ``tool_call_id`` and role; only the *content* becomes the placeholder, so the
  assistant→tool pairing the providers require is never broken, and a media
  (image) block is passed through untouched.

A result that was spilled to disk keeps its ``Full output saved to:`` line
appended to the placeholder, so the model can still find the full output again.

The pass defaults to **off** (``None`` policy) so existing runs are
byte-for-byte unchanged until a host opts in via the user-facing
``agents.defaults.microcompact`` switch.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

__all__ = [
    "MICROCOMPACT_PLACEHOLDER",
    "MicrocompactPolicy",
    "MicrocompactResult",
    "apply_microcompact",
    "count_tool_results",
    "estimate_tokens",
]

# A cleared result is recognizable and never mistaken for real content.
MICROCOMPACT_PLACEHOLDER = "[Old tool result content cleared]"

# ~4 chars/token, used only to score the *savings* of a clear (never to decide
# whether to clear — that gate lives in the runner, which has the real count).
_CHARS_PER_TOKEN = 4.0

# The persisted-result reference head (kept as plain literals so this module
# stays import-cycle free; see ``core.agent_runtime.helpers``): a spilled tool
# result's content carries this marker and a ``Full output saved to: <path>``
# line pointing at the full output on disk.
_PERSISTED_MARKER = "[tool output persisted]"
_SAVED_OUTPUT_PREFIX = "Full output saved to:"


def estimate_tokens(text: str) -> float:
    """Approximate token count for ``text`` (~4 chars/token)."""
    if not text:
        return 0.0
    return len(text) / _CHARS_PER_TOKEN


def _content_text(content: Any) -> str:
    """Flatten a message ``content`` (str or block list) to plain text."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict):
                text = block.get("text")
                if isinstance(text, str):
                    parts.append(text)
            elif isinstance(block, str):
                parts.append(block)
        return "\n".join(parts)
    return "" if content is None else str(content)


def _is_tool_result(msg: dict[str, Any]) -> bool:
    return msg.get("role") == "tool"


def _has_media(msg: dict[str, Any]) -> bool:
    """True when the content carries a media (image) block that must survive."""
    content = msg.get("content")
    if not isinstance(content, list):
        return False
    for block in content:
        if isinstance(block, dict) and block.get("type") in {"image", "input_image"}:
            return True
    return False


def count_tool_results(messages: Sequence[dict[str, Any]]) -> int:
    """How many tool-result messages the policy would consider clearable."""
    return sum(1 for msg in messages if _is_tool_result(msg))


def _latest_tool_batch_ids(messages: Sequence[dict[str, Any]]) -> set[str]:
    """``tool_call_id``s of the most recent assistant message that issued calls.

    Those results form the batch the model has *not* read yet —
    ``_maybe_compact`` runs before it does — so the whole batch must survive a
    clear no matter how many parallel calls it contains.
    """
    for msg in reversed(messages):
        if msg.get("role") != "assistant":
            continue
        calls = msg.get("tool_calls")
        if isinstance(calls, list):
            return {
                str(call.get("id"))
                for call in calls
                if isinstance(call, dict) and call.get("id")
            }
        # The most recent assistant replied with plain text: its tool batch was
        # already consumed, so there is no pending batch to protect.
        return set()
    return set()


def _preserved_reference(content: Any) -> str:
    """Pull the on-disk reference lines out of a spilled result's content.

    A result saved to disk carries a ``Full output saved to: <path>`` line;
    clearing the preview must not lose the pointer to the full output.
    """
    if not isinstance(content, str):
        return ""
    kept = [
        line.rstrip()
        for line in content.splitlines()
        if line.strip() == _PERSISTED_MARKER
        or line.strip().startswith(_SAVED_OUTPUT_PREFIX)
    ]
    return "\n".join(kept)


@dataclass(frozen=True, slots=True)
class MicrocompactPolicy:
    """Microcompact parameters. ``None`` (the runner default) disables the pass.

    The trigger (token pressure) lives entirely in ``AgentRunner._maybe_compact``;
    this policy only carries the *shape* of the clear — how much recency to keep
    and how small a saving is not worth the bookkeeping.
    """

    # How many of the most recent tool results are kept verbatim (on top of the
    # entire latest tool batch, which is always kept).
    keep_recent_tool_results: int = 5
    # Never spend a pass to clear less than this — the saving must repay the
    # bookkeeping (scored against the ~4 chars/token estimate).
    min_savings_tokens: float = 256.0
    # Placeholder substituted for each cleared result's content.
    placeholder: str = MICROCOMPACT_PLACEHOLDER

    def __post_init__(self) -> None:
        if self.keep_recent_tool_results < 0:
            raise ValueError("keep_recent_tool_results must be >= 0")
        if self.min_savings_tokens < 0:
            raise ValueError("min_savings_tokens must be >= 0")


@dataclass(frozen=True, slots=True)
class MicrocompactResult:
    """Outcome of one policy application — ``messages`` is a fresh list."""

    messages: list[dict[str, Any]]
    cleared: int
    savings_tokens: float

    @property
    def applied(self) -> bool:
        return self.cleared > 0


def apply_microcompact(
    messages: Sequence[dict[str, Any]],
    policy: MicrocompactPolicy,
) -> MicrocompactResult:
    """Clear old tool results to the placeholder, keeping the recent ``N`` and
    the entire latest tool batch, and preserving any saved-output path line.

    The caller (``AgentRunner._maybe_compact``) has already decided there is
    token pressure using the provider's real token count, so this function does
    no estimation of its own and always applies the clear (subject to the
    ``min_savings_tokens`` floor). Non-tool messages and media blocks pass
    through untouched; the caller's history is never mutated in place.
    """
    tool_index = [i for i, msg in enumerate(messages) if _is_tool_result(msg)]
    if not tool_index:
        return MicrocompactResult(list(messages), 0, 0.0)

    # Protect the whole latest tool batch (the model is about to read it) plus
    # the most recent ``keep_recent`` results (still-fresh evidence).
    protected: set[int] = set()
    if policy.keep_recent_tool_results > 0:
        protected.update(tool_index[-policy.keep_recent_tool_results :])
    latest_ids = _latest_tool_batch_ids(messages)
    if latest_ids:
        for i in tool_index:
            if str(messages[i].get("tool_call_id")) in latest_ids:
                protected.add(i)

    clearable_positions = {i for i in tool_index if i not in protected}
    if not clearable_positions:
        return MicrocompactResult(list(messages), 0, 0.0)

    new_messages: list[dict[str, Any]] = []
    cleared = 0
    savings = 0.0
    for i, msg in enumerate(messages):
        if i not in clearable_positions or _has_media(msg):
            new_messages.append(msg)
            continue
        original = _content_text(msg.get("content"))
        # Keep the on-disk pointer so the model can still find the full output.
        reference = _preserved_reference(msg.get("content"))
        # Copy so the caller's history (and any host still holding the old
        # message) is never mutated in place — microcompact is persisted by the
        # runner reassigning ``messages[:]``, not by side effect here.
        copy = dict(msg)
        copy["content"] = (
            f"{policy.placeholder}\n{reference}" if reference else policy.placeholder
        )
        new_messages.append(copy)
        cleared += 1
        savings += estimate_tokens(original) - estimate_tokens(copy["content"])

    if cleared == 0 or savings < policy.min_savings_tokens:
        # Not worth the pass — hand back untouched copies so the caller sees
        # no change (convergence: a second pass over an already-cleared history
        # clears nothing and saves nothing).
        return MicrocompactResult(list(messages), 0, 0.0)

    return MicrocompactResult(new_messages, cleared, savings)
