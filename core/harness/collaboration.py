"""Collaboration modes — the posture the agent takes toward a task (C1).

A general agent should work in different styles, not one fixed pipeline. We
ship the two postures a reference terminal agent actually exposes today (the
others fold into ``default``):

- **default** — work autonomously: make reasonable assumptions and execute,
  don't stop to ask unless genuinely blocked;
- **plan** — explore read-only and hand back a decision-complete plan *before*
  changing anything.

The preamble also tells the model which permission posture it runs under —
approval required or unattended — since only the log records it
otherwise, and each posture calls for different care.

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
    "assumptions briefly in your final message. Ask the user only when you are "
    "genuinely blocked and no safe assumption exists. Carry the task through "
    "end to end: implement, verify, then report. Do not stop at analysis or a "
    "proposal unless the user asked for a plan, an explanation, or an answer.\n\n"
    "Match ambition to the setting. In a new project, be bold: pick sensible "
    "defaults and build something complete and polished. In an existing "
    "codebase, be surgical: do exactly what was asked, in the codebase's own "
    "style, and leave everything else alone."
)

_PLAN = (
    "## Working style: plan mode\n"
    "You are in plan mode. First explore the workspace read-only (read, grep, "
    "glob) to understand the task, then hand back a clear, decision-complete "
    "plan of the changes you would make — precise enough for another engineer "
    "to execute without further decisions. Do NOT modify files or run mutating "
    "commands; those stay disabled until the plan is approved. End with the plan."
)

_APPROVAL = (
    "## Permissions: approval required\n"
    "Some tool calls, such as writing files or running commands, wait for the "
    "user to approve them.\n"
    "- Group your work so you ask for approval rarely, and say in one sentence "
    "what a call will do before you make it.\n"
    "- If the user declines a call, do not retry it with a reworded command. "
    "Find another way, or ask what they would prefer.\n"
    "- Prefer quick, targeted checks over long test suites while you iterate; "
    "run the broader checks once your change is ready."
)


_UNATTENDED = (
    "## Permissions: unattended\n"
    "Your tool calls run without asking anyone, and the user may not be "
    'watching. The rules under "Making changes" are the only safeguard, so '
    "follow them strictly. Prefer steps you can undo, keep your progress file "
    "current, and make your final message a complete account of what you changed."
)


def collaboration_preamble(
    mode: PermissionMode,
    *,
    unattended: bool | None = None,
) -> str:
    """The working-style and permission addendum for the system prompt.

    Plan mode gets its own working style and nothing else: it is read-only by
    construction, and the read-only access preset resolves to it. Otherwise the posture section follows the resolved security
    profile; ``unattended`` defaults to what ``full_auto`` implies.
    """
    if mode is PermissionMode.PLAN:
        return _PLAN
    if unattended is None:
        unattended = mode is PermissionMode.FULL_AUTO
    posture = _UNATTENDED if unattended else _APPROVAL
    return f"{_DEFAULT}\n\n{posture}"
