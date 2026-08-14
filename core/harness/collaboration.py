"""Collaboration modes — the posture the agent takes toward a task (C1).

A general agent should work in different styles, not one fixed pipeline. We
ship the two postures a reference terminal agent actually exposes today (the
others fold into ``default``):

- **default** — work autonomously: make reasonable assumptions and execute,
  don't stop to ask unless genuinely blocked;
- **plan** — explore read-only and hand back a decision-complete plan *before*
  changing anything.

This module contributes only the *behavioral* half — a preamble appended to the
system prompt. The *security* half (denying mutating tools in plan mode) is
already the permission engine's job, so the mode is keyed off the resolved
:class:`~core.harness.permissions.PermissionMode` and the two stay consistent:
plan behavior ⇔ non-mutating permissions.
"""

from __future__ import annotations

from core.harness.permissions import PermissionMode

_DEFAULT = (
    "## Working style\n"
    "Work autonomously. When a detail is unspecified, make a reasonable "
    "assumption and proceed rather than stopping to ask; note any load-bearing "
    "assumptions briefly in your final summary. Ask the user only when you are "
    "genuinely blocked and no safe assumption exists."
)

_PLAN = (
    "## Working style: plan mode\n"
    "You are in plan mode. First explore the workspace read-only (read, grep, "
    "glob) to understand the task, then hand back a clear, decision-complete "
    "plan of the changes you would make — precise enough for another engineer "
    "to execute without further decisions. Do NOT modify files or run mutating "
    "commands; those stay disabled until the plan is approved. End with the plan."
)


def collaboration_preamble(mode: PermissionMode) -> str:
    """The working-style addendum for the system prompt, keyed off ``mode``."""
    return _PLAN if mode is PermissionMode.PLAN else _DEFAULT
