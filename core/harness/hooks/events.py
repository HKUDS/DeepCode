"""Hook event names and matcher semantics (C3).

A faithful port of the reference agent's hook matcher logic
(``hooks/src/events/common.rs``), adapted to Python. The wire format is the
Claude-Code-compatible hooks schema, so a hook the user already wrote for that
ecosystem runs here unchanged.

Event names (as they appear in ``hooks.json`` and in the JSON payload's
``hook_event_name`` field):

- ``SessionStart``    — once, when a session begins
- ``UserPromptSubmit``— each time the user submits a prompt
- ``PreToolUse``      — before a tool call runs (may block / rewrite it)
- ``PostToolUse``     — after a tool call runs (may inject feedback)
- ``Stop``            — when the agent would end its turn (may force-continue)

The remaining reference events (``PermissionRequest``, ``PreCompact``,
``PostCompact``, ``SubagentStart``, ``SubagentStop``) are recognized by the
engine but wired opportunistically as DeepCode grows the matching kernel seams.
"""

from __future__ import annotations

import re

# All recognized event names, in the reference agent's canonical order.
HOOK_EVENT_NAMES: tuple[str, ...] = (
    "PreToolUse",
    "PermissionRequest",
    "PostToolUse",
    "PreCompact",
    "PostCompact",
    "SessionStart",
    "UserPromptSubmit",
    "SubagentStart",
    "SubagentStop",
    "Stop",
)

# Events whose ``matcher`` field is meaningful. ``UserPromptSubmit`` and ``Stop``
# fire unconditionally, so their matchers are ignored (mirrors the reference).
_EVENTS_WITHOUT_MATCHER: frozenset[str] = frozenset({"UserPromptSubmit", "Stop"})


def matcher_applies_to_event(event_name: str, matcher: str | None) -> str | None:
    """The effective matcher for an event: ``None`` for events that ignore it."""
    if event_name in _EVENTS_WITHOUT_MATCHER:
        return None
    return matcher


def _is_match_all(matcher: str) -> bool:
    return matcher == "" or matcher == "*"


def _is_exact(matcher: str) -> bool:
    # A matcher of only word chars and ``|`` is treated as exact alternatives,
    # never as a regex (so ``Edit|Write`` means those two tools, not a regex).
    return all(ch.isascii() and (ch.isalnum() or ch in "_|") for ch in matcher)


def validate_matcher(matcher: str) -> str | None:
    """Return an error string if ``matcher`` is an invalid regex, else ``None``.

    Match-all (``*``/``""``) and exact (word-chars + ``|``) matchers never need
    regex compilation, so they are always valid.
    """
    if _is_match_all(matcher) or _is_exact(matcher):
        return None
    try:
        re.compile(matcher)
    except re.error as exc:
        return str(exc)
    return None


def matches_matcher(matcher: str | None, tool_name: str | None) -> bool:
    """Whether a handler's ``matcher`` matches a dispatch input (a tool name).

    - ``None`` / ``*`` / ``""`` match everything.
    - An exact matcher (word chars + ``|``) matches by equality of any
      ``|``-separated alternative.
    - Anything else is a regex (``re.search`` — unanchored, like the reference).
    - An invalid regex matches nothing.
    """
    if matcher is None or _is_match_all(matcher):
        return True
    if tool_name is None:
        return False
    if _is_exact(matcher):
        return any(candidate == tool_name for candidate in matcher.split("|"))
    try:
        return re.search(matcher, tool_name) is not None
    except re.error:
        return False
