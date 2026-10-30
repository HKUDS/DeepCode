"""Text that tool results carry for the model only.

The runner and the tool registry append these notes to results the model
reads. A result shown to people leaves them out (``for_people``). This
module imports nothing, so every layer can use it.
"""

from __future__ import annotations

# Appended to a result built from a failure.
ERROR_HINT = "\n\n[Analyze the error above and try a different approach.]"
# Begins each block of hook context that follows a result.
HOOK_CONTEXT_MARK = "\n\n[hook] "


def with_hook_context(result: str, contexts: list[str]) -> str:
    """``result`` followed by each piece of hook context, for the model."""
    return result + "".join(f"{HOOK_CONTEXT_MARK}{context}" for context in contexts)


def for_people(text: str) -> str:
    """A result without the notes only the model needs.

    Hook context always comes last, so the result ends where it begins; the
    error hint can sit before hook context, so it is removed wherever it is.
    """
    return text.split(HOOK_CONTEXT_MARK, 1)[0].replace(ERROR_HINT, "")
