"""LLM risk classifier for the permission gate (P0-1, Claude Code Auto-mode
lesson).

The static permission engine (:mod:`core.harness.permissions`) plus hooks can
resolve most tool calls, but an ``ask`` verdict still falls through to a human
approver. In non-interactive runs there is no approver, so every ``ask`` is
denied — the agent stalls on anything the rules didn't anticipate. Claude
Code's Auto mode solves this with an LLM safety classifier that scores each
action and only escalates the genuinely risky ones. This module ports that
idea as an *optional, pluggable* layer:

* It sits between the PermissionRequest hook and the human approver (in
  ``AgentRunSpec``'s approval path), exactly where an ``ask`` would otherwise
  block.
* Only ever upgrades an ``ask``: ``low`` risk → auto-allow; ``medium`` /
  ``high`` → fall through to the human (or denial in headless runs). It never
  turns an ``allow`` into a ``deny`` and never acts when disabled.
* Failure is fail-open to the human path — a classifier crash or timeout must
  not escalate risk, only fall back to the existing behavior.
* Off by default: enabled with ``DEEPCODE_RISK_CLASSIFIER=1`` (model via
  ``DEEPCODE_RISK_CLASSIFIER_MODEL``); absent provider support degrades to
  "no verdict" (human path).

Design rule (mirrors ``core.harness.__init__``): this module is pure
mechanism — it scores an action and returns a verdict; it never prompts,
executes, or talks to UIs. Enforcement stays in the kernel.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from enum import Enum
from typing import Any

from loguru import logger

# ---------------------------------------------------------------------------
# Public surface
# ---------------------------------------------------------------------------


class RiskLevel(str, Enum):
    """Tri-state verdict the classifier returns."""

    LOW = "low"  # auto-allow the ask
    MEDIUM = "medium"  # fall through to the human approver
    HIGH = "high"  # fall through to the human approver (and be loud about it)


@dataclass(slots=True)
class RiskVerdict:
    """One classification result."""

    level: RiskLevel
    reason: str = ""
    model: str | None = None
    latency_ms: float | None = None
    error: str | None = None

    @property
    def decisive(self) -> bool:
        """A verdict is decisive only when the classifier actually ran."""
        return self.error is None and self.level is not None


# ---------------------------------------------------------------------------
# Prompt
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = (
    "You are a conservative safety classifier for a coding agent's tool calls. "
    "Given a tool call that the agent wants to perform, decide how risky it is. "
    'Respond with ONLY a JSON object: {"risk": "low"|"medium"|"high", '
    '"reason": "<one short sentence>"}.\n'
    "Guidance:\n"
    "- low: routine, reversible, scoped to the workspace (e.g. editing a file "
    "inside the project, running a normal test command).\n"
    "- medium: modifies outside the workspace, installs packages, changes "
    "system state, or has side effects that are not obviously reversible.\n"
    "- high: destructive, exfiltrates data, touches credentials, network "
    "writes, removes files, or anything a careful engineer would double-check "
    "before approving.\n"
    "When unsure, prefer medium over low. Never answer with anything but JSON."
)

_USER_TEMPLATE = (
    "Tool call to classify:\n"
    "tool: {tool_name}\n"
    "arguments: {arguments}\n"
    "policy note (why this needs confirmation): {reason}"
)


# ---------------------------------------------------------------------------
# Verdict parsing
# ---------------------------------------------------------------------------

_JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)


def parse_risk_verdict(text: str | None) -> RiskVerdict | None:
    """Parse the model's JSON reply into a :class:`RiskVerdict`.

    Tolerates markdown fences and stray prose around the JSON object.
    Returns ``None`` when the reply cannot be parsed — callers treat that as
    "no verdict" (fail open to the human path).
    """
    if not text:
        return None
    match = _JSON_OBJECT_RE.search(text)
    if not match:
        return None
    try:
        payload = json.loads(match.group(0))
    except json.JSONDecodeError:
        # Best-effort: some models wrap keys and string values in single
        # quotes. Normalize single-quoted strings to double quotes without
        # touching escaped quotes inside.
        try:
            body = match.group(0)
            cleaned = re.sub(
                r"'((?:[^'\\]|\\.)*)'",
                lambda m: '"' + m.group(1).replace('"', '\\"') + '"',
                body,
            )
            payload = json.loads(cleaned)
        except json.JSONDecodeError:
            return None
    if not isinstance(payload, dict):
        return None
    raw_risk = str(payload.get("risk", "")).strip().lower()
    if raw_risk not in {level.value for level in RiskLevel}:
        return None
    reason = str(payload.get("reason", "")).strip()
    return RiskVerdict(level=RiskLevel(raw_risk), reason=reason[:300])


# ---------------------------------------------------------------------------
# Classifier
# ---------------------------------------------------------------------------


def classifier_enabled() -> bool:
    """Whether the risk classifier is on (env: ``DEEPCODE_RISK_CLASSIFIER``)."""
    value = os.environ.get("DEEPCODE_RISK_CLASSIFIER", "").strip().lower()
    return value in {"1", "true", "yes", "on"}


def classifier_model() -> str | None:
    """Optional explicit model for the classifier (env:
    ``DEEPCODE_RISK_CLASSIFIER_MODEL``)."""
    value = os.environ.get("DEEPCODE_RISK_CLASSIFIER_MODEL", "").strip()
    return value or None


class LLMRiskClassifier:
    """Score an ``ask``-level tool call with a lightweight LLM.

    Parameters
    ----------
    provider:
        Any object with ``async chat(messages, model=..., max_tokens=...)``
        returning an ``LLMResponse`` (the ``core.providers`` base interface).
    model:
        Optional model override; defaults to the provider's own default.
    max_tokens:
        Tiny budget — a classifier needs a short JSON answer.
    timeout_s:
        Per-call timeout; on expiry the classifier yields "no verdict".
    """

    def __init__(
        self,
        provider: Any,
        *,
        model: str | None = None,
        max_tokens: int = 128,
        timeout_s: float = 15.0,
    ) -> None:
        self._provider = provider
        self._model = model or classifier_model()
        self._max_tokens = max_tokens
        self._timeout_s = timeout_s

    async def classify(
        self,
        tool_name: str,
        arguments: dict[str, Any] | None,
        reason: str,
    ) -> RiskVerdict:
        """Score one tool call; never raises, always returns a verdict."""
        import time

        started = time.perf_counter()
        try:
            user = _USER_TEMPLATE.format(
                tool_name=tool_name,
                arguments=json.dumps(arguments or {}, ensure_ascii=False)[:2000],
                reason=(reason or "")[:500],
            )
            response = await asyncio_wait_for(
                self._provider.chat(
                    [
                        {"role": "system", "content": _SYSTEM_PROMPT},
                        {"role": "user", "content": user},
                    ],
                    model=self._model,
                    max_tokens=self._max_tokens,
                    temperature=0.0,
                ),
                timeout=self._timeout_s,
            )
        except Exception as exc:  # noqa: BLE001 - provider down, timeout, anything
            logger.opt(exception=False).warning(
                "risk classifier failed for {}: {}", tool_name, exc
            )
            return RiskVerdict(
                level=RiskLevel.MEDIUM,
                error=str(exc)[:200],
                latency_ms=(time.perf_counter() - started) * 1000,
            )

        verdict = parse_risk_verdict(response.content)
        latency = (time.perf_counter() - started) * 1000
        if verdict is None:
            return RiskVerdict(
                level=RiskLevel.MEDIUM,
                error="unparseable classifier reply",
                model=self._model,
                latency_ms=latency,
            )
        verdict.model = self._model
        verdict.latency_ms = latency
        return verdict


def asyncio_wait_for(awaitable: Any, *, timeout: float) -> Any:
    """Small indirection so the module is importable without asyncio quirks
    in sync contexts (the awaitable is only awaited here)."""
    import asyncio

    return asyncio.wait_for(awaitable, timeout=timeout)


__all__ = [
    "LLMRiskClassifier",
    "RiskLevel",
    "RiskVerdict",
    "classifier_enabled",
    "classifier_model",
    "parse_risk_verdict",
]
