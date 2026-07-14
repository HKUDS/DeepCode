"""External-command hooks (C3).

A Claude-Code-compatible hook system, ported from the reference agent: the
user configures shell commands in ``hooks.json`` that fire on lifecycle events
(``SessionStart``, ``UserPromptSubmit``, ``PreToolUse``, ``PostToolUse``,
``Stop``) and can block, rewrite, or inject context into the run.

``HooksEngine.discover(workspace, session_id)`` returns ``(engine, warnings)``
— ``engine`` is ``None`` when no hooks are configured, so a project without
hooks pays nothing.
"""

from core.harness.hooks.discovery import Handler, DiscoveryResult, discover_hooks
from core.harness.hooks.engine import (
    ContextOutcome,
    HooksEngine,
    PermissionRequestOutcome,
    PostToolUseOutcome,
    PreToolUseOutcome,
    StopOutcome,
)

__all__ = [
    "Handler",
    "DiscoveryResult",
    "discover_hooks",
    "HooksEngine",
    "PreToolUseOutcome",
    "PostToolUseOutcome",
    "ContextOutcome",
    "StopOutcome",
    "PermissionRequestOutcome",
]
