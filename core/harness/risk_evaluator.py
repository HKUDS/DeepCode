"""Action-triage risk evaluation for tool calls — advisory, not a boundary.

The permission engine (:mod:`core.harness.permissions`) decides allow / ask /
deny from the product security profile: sensitive-path protection, the
read-only upper bound, explicit rules, and the mode default. This module adds
one *optional, strictness-only* opinion on top of that decision: given a tool
call the engine would otherwise ``allow``, does it look risky enough to be
worth a human glance?

Scope, settled in issue #245 (action triage, Option A, rules-only):

* It only ever sees calls the mode default (step 4) would allow, and its only
  move is ``allow`` → ``ask``. It can never loosen a ``deny``, override an
  explicit user rule, or reinterpret the sensitive-path / read-only
  boundaries. "A risk evaluator can only make the engine more careful, never
  less" — so ``full_auto``'s "rules only" promise degrades to "rules, plus a
  soft ask when the evaluator objects", not a loss of the explicit rules.
* It is **default off**. With the feature disabled every decision and reason is
  byte-identical to today's.
* v1 is **rules only** — no model call in the permission path. The built-in
  evaluator reuses the screens :mod:`core.harness.command_guard` already has
  (``screen_all`` → destructive-command always, egress / install under
  ``DEEPCODE_COMMAND_SCREEN=strict``) rather than inventing a new scorer. A
  model-based evaluator, with its per-call latency and cost, would be a
  separate proposal.

Honesty note: this is advisory defense-in-depth, **not** a security boundary.
The sandbox (:mod:`core.harness.sandbox`) is the boundary. An evaluator that
raises, times out, or returns garbage must never crash the permission path and
must never be read as permission; the engine contains those failures and fails
closed to ``ask`` (and so to ``deny`` under a ``never`` approval policy). Only
an operator who opted in ever sees that ask.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Protocol, runtime_checkable

from core.harness.command_guard import screen_all

# Environment flag that turns the evaluator on. Off unless explicitly set to a
# truthy value, matching the other opt-in harness switches (DEEPCODE_SANDBOX,
# DEEPCODE_COMPACTION_MEMORY, …).
_ENABLE_ENV = "DEEPCODE_RISK_EVALUATOR"

_TRUTHY = frozenset({"1", "true", "on", "yes"})

# Argument keys that carry a shell command, mirroring the permission engine's
# own shell-argument extraction so the evaluator screens exactly the string the
# user would run.
_COMMAND_ARG_KEYS = ("command", "cmd", "commands", "script")


def risk_evaluator_enabled() -> bool:
    """Whether the operator opted the risk evaluator in. Off by default."""

    return os.environ.get(_ENABLE_ENV, "").strip().lower() in _TRUTHY


@runtime_checkable
class RiskEvaluator(Protocol):
    """An opinion on whether an otherwise-allowed call is worth confirming.

    Returns a short human-readable reason when the call looks risky, or
    ``None`` when it has no objection. It returns an *opinion*; the engine
    decides. It must not raise in normal use, but the engine contains it if it
    does.
    """

    def __call__(
        self, tool_name: str, arguments: Mapping[str, object]
    ) -> str | None: ...


def _command_argument(arguments: Mapping[str, object]) -> str | None:
    """The shell command carried by ``arguments``, if any."""

    for key in _COMMAND_ARG_KEYS:
        value = arguments.get(key)
        if isinstance(value, str) and value.strip():
            return value
        if isinstance(value, (list, tuple)):
            joined = " ".join(str(v) for v in value)
            if joined.strip():
                return joined
    return None


class CommandGuardRiskEvaluator:
    """Rules-only evaluator built on the existing command-guard screens.

    It inspects only the shell command a call would run and defers entirely to
    :func:`core.harness.command_guard.screen_all`, so the destructive-command
    screen always applies and the egress / install screens apply under
    ``DEEPCODE_COMMAND_SCREEN=strict`` — the same waiver semantics the sandbox
    front door already uses. Non-shell calls and calls without a command carry
    no opinion (``None``).

    Deterministic for a given command, so results are cached by the caller via
    the call signature; this evaluator holds no state of its own.
    """

    def __call__(self, tool_name: str, arguments: Mapping[str, object]) -> str | None:
        command = _command_argument(arguments)
        if command is None:
            return None
        return screen_all(command)


def default_risk_evaluator() -> RiskEvaluator:
    """The built-in rules-only evaluator used when the feature is enabled."""

    return CommandGuardRiskEvaluator()
