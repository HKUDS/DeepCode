"""Text that tool results carry for the model only.

The runner and the tool registry append these notes to results the model
reads; a result shown to people leaves them out. This module imports
nothing itself.
"""

from __future__ import annotations

from collections.abc import Iterable

# Appended to a result built from a failure.
ERROR_HINT = "\n\n[Analyze the error above and try a different approach.]"
# Begins each block of hook context that follows a result. Whoever appends
# it records where it begins (``ToolEvent.hook_context_at``), so a display can
# cut there without searching the text.
HOOK_CONTEXT_MARK = "\n\n[hook] "


def with_hook_context(result: str, contexts: Iterable[str]) -> str:
    """``result`` followed by each piece of hook context, for the model."""
    return result + "".join(f"{HOOK_CONTEXT_MARK}{context}" for context in contexts)


def without_error_hint(text: str) -> str:
    """``text`` without the error hint, which only the model needs."""
    return text.replace(ERROR_HINT, "")


def before_hook_context(text: str) -> str:
    """``text`` up to the first hook-context mark.

    Only for a result whose composition was not recorded (one rebuilt from
    the session file): output that itself contains the mark is cut early.
    """
    return text.split(HOOK_CONTEXT_MARK, 1)[0]
