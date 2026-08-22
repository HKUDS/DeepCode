"""Injectable compaction policy.

``TailRetainingCompactionStrategy`` replaces a head-anchored range with one
checkpoint and keeps a window-proportional recent tail verbatim, tool-call
pairs intact. A deployment can inject another strategy without editing the
loop.
"""

from __future__ import annotations

from typing import Any, Mapping, Protocol

from core.agent_runtime.helpers import find_legal_message_start

COMPACT_TRIGGER_FRACTION = 0.9
COMPACT_KEEP_USER_CHARS = 60_000
# The estimator's own char/token ratio, reused so the window share and
# the conversation share are measured in the same unit.
_CHARS_PER_TOKEN = 4
SUMMARIZATION_PROMPT = (
    "You are performing a CONTEXT CHECKPOINT COMPACTION. Create a handoff "
    "summary for another agent that will resume this task.\n\n"
    "The conversation above is about to be replaced by your summary. "
    "Anything you leave out is gone: the next agent cannot look it up.\n\n"
    "Include:\n"
    "- Every file read or written and every command run, BY NAME, even when "
    "the result seemed unremarkable — an omitted artifact reads to the next "
    "agent as work never done, and it will redo it\n"
    "- Current progress and key decisions made\n"
    "- Important context, constraints, or user preferences\n"
    "- What remains to be done (clear next steps)\n"
    "- Any critical data, examples, file paths, or references needed to "
    "continue\n\n"
    "Be concise and structured, but never drop a concrete name to save room. "
    "Respond with the summary text only; do not call tools."
)
# The checkpoint speaks in the FIRST person on purpose. Framed as "an earlier
# agent produced this summary", a model treats it as hearsay and discounts it:
# observed verbatim in a pressure run — "the earlier handoff mentioned doc2.md,
# but only as a comparison; it was not actually read in this session" — after
# the file had in fact been read, by this same conversation, three turns
# earlier. The checkpoint is not a report from someone else. It is this
# conversation's own history, compacted.
SUMMARY_PREFIX = (
    "This is your own earlier conversation, compacted into a summary because "
    "it no longer fits in context. Everything below is a record of what you "
    "already did in THIS session — treat it exactly as you would treat the "
    "messages it replaced, not as a report from someone else. Do not repeat "
    "work it says is done. Summary:"
)


class CompactionStrategy(Protocol):
    """Builds a replacement history from a summary."""

    def build_history(
        self, messages: list[dict[str, Any]], summary: str
    ) -> list[dict[str, Any]]: ...


def _message_chars(message: Mapping[str, Any]) -> int:
    return len(str(message.get("content") or ""))


def _unit_start(messages: list[dict[str, Any]], end: int) -> int:
    """Inclusive start of the indivisible unit that ends at ``end``."""
    message = messages[end]
    if message.get("role") != "tool":
        return end
    call_id = message.get("tool_call_id")
    index = end
    while index > 0:
        previous = messages[index - 1]
        role = previous.get("role")
        if role == "tool":
            index -= 1
            continue
        if role == "assistant":
            declared = {
                str(call.get("id"))
                for call in previous.get("tool_calls") or ()
                if isinstance(call, dict) and call.get("id")
            }
            if call_id is None or str(call_id) in declared:
                return index - 1
        break
    return index


class TailRetainingCompactionStrategy:
    """Head-anchored checkpoint plus a bounded recent tail.

    The retained budget is the SMALLER of a window share and a share of the
    conversation itself. Deriving it from the window alone made manual
    ``/compact`` a no-op on any ordinary conversation: against a
    million-token window the budget is hundreds of thousands of characters,
    every real history fits inside it, the strategy keeps everything, and
    adding a checkpoint on top makes the result larger than its input — which
    the convergence rule then correctly rejects. Measured on a real session:
    12 messages / 5,859 characters, refused for every summary longer than
    ~390 characters. Binding the budget to the conversation makes the tail
    proportional to what there is to compact, at any window size.
    """

    def __init__(self, *, retain_ratio: float = 0.15) -> None:
        self.retain_ratio = retain_ratio

    def _budget(
        self,
        non_system: list[dict[str, Any]],
        context_window_tokens: int | None,
    ) -> int:
        total = sum(_message_chars(item) for item in non_system)
        budget = int(total * self.retain_ratio)
        if context_window_tokens and context_window_tokens > 0:
            window = int(context_window_tokens * self.retain_ratio * _CHARS_PER_TOKEN)
            budget = min(budget, window) if budget else window
        return max(budget, 0)

    def build_history(
        self,
        messages: list[dict[str, Any]],
        summary: str,
        *,
        context_window_tokens: int | None = None,
        **_: Any,
    ) -> list[dict[str, Any]]:
        system = [dict(item) for item in messages if item.get("role") == "system"]
        non_system = [dict(item) for item in messages if item.get("role") != "system"]
        budget = self._budget(non_system, context_window_tokens)
        kept: list[dict[str, Any]] = []
        used = 0
        index = len(non_system) - 1
        while index >= 0:
            start = _unit_start(non_system, index)
            chunk = non_system[start : index + 1]
            chunk_chars = sum(_message_chars(item) for item in chunk)
            # Always keep the newest indivisible unit, even when it alone
            # exceeds the budget: a history with no tail is not resumable.
            # Two floors override the budget. The newest indivisible unit
            # always survives (a history with no tail is not resumable), and
            # so does everything back to the newest USER message: dropping the
            # question the model is mid-way through answering is the exact
            # amnesia this shape exists to prevent.
            over_budget = bool(kept) and used + chunk_chars > budget
            if over_budget and any(item.get("role") == "user" for item in kept):
                break
            kept = chunk + kept
            used += chunk_chars
            index = start - 1
        checkpoint = {
            "role": "user",
            "content": f"{SUMMARY_PREFIX}\n{summary}",
            "compaction": {"reset": True, "retain": len(kept)},
        }
        return system + [checkpoint] + kept


DEFAULT_COMPACTION_STRATEGY = TailRetainingCompactionStrategy()


def legalize_tail(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Drop a truncated prefix so the remaining list is a legal tool sequence."""
    start = find_legal_message_start(messages)
    return messages[start:] if start else messages
