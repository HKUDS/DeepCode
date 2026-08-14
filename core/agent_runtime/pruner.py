"""Model-free middle-pruning of oversized tool results (dsh's pruner).

Borrowed from dsh's ``compaction-tool-result-pruner``: before spending a
model round-trip on a summarization pass, land a free reduction by cutting
the MIDDLE out of every oversized tool result — the head usually carries the
answer's shape and the tail its conclusion, while the bulk in between is
re-derivable by re-running the tool.

Design rules inherited from the blueprint:

- Selection is purely per-result size. No recency window and no tool-name
  list: any ``role == "tool"`` message whose string content exceeds the
  threshold qualifies, so MCP and custom tools are covered exactly like
  built-ins.
- Convergent by construction: a pruned result measures ``head + marker +
  tail``, which load-time validation forces under the threshold, so a second
  pass selects nothing. There is no hidden state to track.
- The pruner only ever runs under context pressure (the caller's gate) —
  never opportunistically — and its effect is durable: the caller persists
  the pruned history, so the model-visible prefix changes once and then
  stays byte-stable for provider prefix caching.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

PRUNE_MARKER = "\n\n[... tool result middle pruned ...]\n\n"


@dataclass(frozen=True, slots=True)
class ToolResultPruner:
    """Middle-prunes oversized tool results down to head + marker + tail."""

    threshold_chars: int = 8192
    head_chars: int = 4096
    tail_chars: int = 1024
    marker: str = PRUNE_MARKER

    def __post_init__(self) -> None:
        if self.threshold_chars <= 0 or self.head_chars < 0 or self.tail_chars < 0:
            raise ValueError(
                "tool-result pruner: threshold must be positive and "
                "head/tail non-negative"
            )
        replacement_chars = self.head_chars + len(self.marker) + self.tail_chars
        if replacement_chars > self.threshold_chars:
            # The convergence invariant: a pruned result must itself sit under
            # the threshold, or every pass would re-select and re-prune it.
            raise ValueError(
                "tool-result pruner: head + marker + tail "
                f"({replacement_chars} chars) must not exceed the threshold "
                f"({self.threshold_chars} chars)"
            )

    def prune_messages(
        self, messages: list[dict[str, Any]]
    ) -> tuple[list[dict[str, Any]], int]:
        """Return ``(pruned_messages, pruned_count)``.

        The input list and its messages are never mutated; when nothing
        qualifies the original list object is returned unchanged so callers
        can cheaply detect a no-op.
        """
        pruned: list[dict[str, Any]] | None = None
        count = 0
        for idx, message in enumerate(messages):
            content = message.get("content")
            if (
                message.get("role") != "tool"
                or not isinstance(content, str)
                or len(content) <= self.threshold_chars
            ):
                continue
            replacement = (
                content[: self.head_chars] + self.marker + content[-self.tail_chars :]
                if self.tail_chars
                else content[: self.head_chars] + self.marker
            )
            if pruned is None:
                pruned = [dict(m) for m in messages]
            pruned[idx]["content"] = replacement
            count += 1
        return (pruned, count) if pruned is not None else (messages, 0)
