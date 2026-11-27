"""P3-A (GenAI lesson 04): prompt cue guidance for structured / stepwise output.

Lesson 04 (prompt engineering) stresses that explicit *cues* — short
signposts telling the model what shape the answer should take — cut down on
free-form filler and keep multi-step work on rails. DeepCode already carries
heavy system-prompt scaffolding; this module adds one *thin, model-facing*
cue injected as transient turn context on the routed request.

Design constraints
-----------------
- The cue must NOT be appended to every provider request. The runner replays
  the routed request view as the prefix of its compaction summarizer call
  (the dsh rule — prefix/KV cache reuse). Injecting different text on the
  compaction path would desynchronize that prefix. We therefore attach the
  cue via ``transient_context_messages``, which the runner applies to routed
  requests and never to the summarizer prefix.
- The cue is advisory prose, not a hard schema: it must never contradict a
  caller-provided JSON schema or structured-output mode. It only biases
  *default* behavior (stepwise work, cite-before-claim, no filler).

Enable: env ``DEEPCODE_PROMPT_CUE`` (default on). Set ``0``/``false``/``off``
to disable.
"""

from __future__ import annotations

import os
from typing import Any

_CUE_TEXT = (
    "Work stepwise: state each step as you take it, use the available tools "
    "to verify claims before asserting them, and keep the final answer "
    "tight — no restating the task, no filler. If you cite a retrieved "
    "memory or tool result, reference it by its label ([n] / tool name)."
)


def prompt_cue_enabled() -> bool:
    """Whether the prompt cue is on (env ``DEEPCODE_PROMPT_CUE``; default on)."""
    value = os.environ.get("DEEPCODE_PROMPT_CUE", "").strip().lower()
    if not value:
        return True
    return value not in {"0", "false", "off", "no"}


def build_cue_context() -> tuple[dict[str, Any], ...]:
    """Transient turn-context messages carrying the cue, or empty when off.

    Returns a single ``user``-role message so the runner's
    ``_with_transient_context`` places it immediately before the canonical
    user request (user-priority turn context), never inside system-level
    instructions.
    """
    if not prompt_cue_enabled():
        return ()
    return ({"role": "user", "content": _CUE_TEXT},)


__all__ = [
    "_CUE_TEXT",
    "build_cue_context",
    "prompt_cue_enabled",
]
