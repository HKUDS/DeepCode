"""Visual vocabulary for the DeepCode TUI — one place, no magic strings.

Every color, symbol, and prefix the renderer uses lives here, so the whole
look can be tuned (or themed) without touching rendering logic. Styles are
rich style strings.
"""

from __future__ import annotations

# -- brand ------------------------------------------------------------------
BRAND = "✳ DeepCode"
ACCENT = "cyan"
DIM = "grey58"

# -- prompt -----------------------------------------------------------------
PROMPT = "› "
PROMPT_STYLE = "bold"
CONTINUATION = "… "

# -- assistant text ---------------------------------------------------------
ASSISTANT_STYLE = "default"
THINKING_STYLE = DIM

# -- tool cards -------------------------------------------------------------
TOOL_BULLET = "●"
TOOL_RESULT_ELBOW = "  ⎿"
TOOL_RUNNING_STYLE = ACCENT
TOOL_OK_STYLE = "green"
TOOL_ERR_STYLE = "red"
TOOL_DETAIL_STYLE = DIM

# -- status / meta ----------------------------------------------------------
META_STYLE = DIM
ERROR_STYLE = "bold red"
INTERRUPT_HINT = "(esc/ctrl-c to interrupt)"
DONE_OK = "✓"
DONE_ERR = "✗"

# -- approvals --------------------------------------------------------------
APPROVAL_STYLE = "bold yellow"
APPROVAL_PROMPT = "allow? [y/N] "
