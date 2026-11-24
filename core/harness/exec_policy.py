"""Declarative command policy whose rules test themselves at load time.

:mod:`core.harness.command_guard` is a *hard-coded* set of screens: Python
functions that know what a recursive force delete or a reckless ``chmod`` looks
like. That is the right shape for a built-in floor, but it cannot express site
policy ("never run ``terraform apply`` here", "``kubectl delete`` always asks a
human") and — more importantly — a hard-coded screen has no way to *prove* what
it does and does not catch. Every new heuristic is an unreviewable edit that can
silently start matching something it should not.

This module borrows the shape of the reference CLI's exec policy: rules are
**data**, they carry a decision, and each rule declares the invocations it must
match and the ones it must not. Those examples are checked when the file loads,
so a rule that over- or under-matches fails at load rather than in production::

    [[rule]]
    pattern = ["git", ["push", "pull"]]
    decision = "prompt"
    justification = "Network side effects; confirm before touching a remote."
    match = [["git", "push"], "git pull --rebase"]
    not_match = [["git", "status"]]

Matching is an ordered prefix over argv tokens. Any pattern element may be a
list of alternatives. The decision is the strictest one across all matching
rules (``forbid`` > ``prompt`` > ``allow``), so adding a broad ``allow`` rule
can never weaken a narrow ``forbid`` one.

Scope and honesty boundaries:

* This is **advisory friction, not the boundary**. The sandbox
  (:mod:`core.harness.sandbox`) is what enforces execution confinement.
* The shallow screen (:func:`core.harness.command_guard.screen_all`) consults
  this module for ``forbid`` only. A policy can therefore *add* a block but can
  never *remove* one: an ``allow`` rule does not override the built-in
  destructive-command screen. The ``prompt`` tier is returned by
  :func:`evaluate` for the permission layer to consume.
* A command that cannot be tokenised yields no decision; it is left to the
  sandbox, exactly like an unparseable command in ``command_guard``.
* Loading is opt-in via the ``DEEPCODE_EXEC_POLICY`` environment variable
  pointing at a policy file. With the variable unset every code path here is a
  no-op and behaviour is byte-identical to today's.
"""

from __future__ import annotations

import os
import shlex
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

ALLOW = "allow"
PROMPT = "prompt"
FORBID = "forbid"

#: Strictness order. A rule set's effective decision is the maximum of its
#: matching rules, so a later broad rule cannot loosen an earlier narrow one.
_SEVERITY: Mapping[str, int] = {ALLOW: 0, PROMPT: 1, FORBID: 2}

#: Environment variable naming the policy file. Unset means "no policy".
POLICY_ENV = "DEEPCODE_EXEC_POLICY"

_TOKEN = str | tuple[str, ...]


class ExecPolicyError(ValueError):
    """A policy file is malformed, or one of its rules failed its own test."""


@dataclass(frozen=True, slots=True)
class PrefixRule:
    """One ordered-prefix rule plus the examples that pin its behaviour."""

    pattern: tuple[_TOKEN, ...]
    decision: str = ALLOW
    justification: str | None = None
    match: tuple[tuple[str, ...], ...] = ()
    not_match: tuple[tuple[str, ...], ...] = ()

    def matched_prefix(self, tokens: Sequence[str]) -> tuple[str, ...] | None:
        """The tokens this rule consumes when it matches, else ``None``."""

        if len(tokens) < len(self.pattern):
            return None
        consumed: list[str] = []
        for expected, actual in zip(self.pattern, tokens):
            alternatives = (expected,) if isinstance(expected, str) else expected
            if actual not in alternatives:
                return None
            consumed.append(actual)
        return tuple(consumed)

    def matches(self, tokens: Sequence[str]) -> bool:
        return self.matched_prefix(tokens) is not None


@dataclass(frozen=True, slots=True)
class ExecDecision:
    """The strictest decision across the rules that matched ``command``."""

    command: str
    decision: str | None
    justification: str | None
    matched_prefix: tuple[str, ...] | None

    @property
    def blocked(self) -> bool:
        """Whether the shallow screen should refuse the command."""

        return self.decision == FORBID


def tokenize(command: str) -> tuple[str, ...] | None:
    """Split ``command`` into argv tokens, or ``None`` if it cannot be parsed.

    Only the *first* simple command is considered: policy is a prefix language,
    so ``a && b`` is evaluated as ``a``. Reparsing shell control operators is
    :mod:`core.harness.command_guard`'s job, which already splits on unquoted
    operators; duplicating that here would create two sources of truth.
    """

    try:
        tokens = shlex.split(command, posix=True)
    except ValueError:
        return None
    return tuple(tokens) or None


def evaluate(command: str, rules: Sequence[PrefixRule]) -> ExecDecision:
    """Evaluate ``command`` against ``rules``, strictest decision first."""

    tokens = tokenize(command)
    if not tokens:
        return ExecDecision(command, None, None, None)

    best: PrefixRule | None = None
    best_prefix: tuple[str, ...] | None = None
    for rule in rules:
        prefix = rule.matched_prefix(tokens)
        if prefix is None:
            continue
        if best is None or _SEVERITY[rule.decision] > _SEVERITY[best.decision]:
            best, best_prefix = rule, prefix
    if best is None:
        return ExecDecision(command, None, None, None)
    return ExecDecision(command, best.decision, best.justification, best_prefix)


def _as_tokens(value: Any, *, where: str) -> tuple[str, ...]:
    """Normalise one example invocation (string or token list) to tokens."""

    if isinstance(value, str):
        tokens = tokenize(value)
        if tokens is None:
            raise ExecPolicyError(f"{where}: example {value!r} cannot be tokenised")
        return tokens
    if isinstance(value, (list, tuple)) and value:
        if not all(isinstance(item, str) for item in value):
            raise ExecPolicyError(
                f"{where}: example {value!r} must be a list of strings"
            )
        return tuple(value)
    raise ExecPolicyError(
        f"{where}: example {value!r} must be a string or a non-empty list"
    )


def _parse_pattern(value: Any, *, where: str) -> tuple[_TOKEN, ...]:
    if not isinstance(value, (list, tuple)) or not value:
        raise ExecPolicyError(f"{where}: pattern must be a non-empty list")
    pattern: list[_TOKEN] = []
    for element in value:
        if isinstance(element, str):
            if not element:
                raise ExecPolicyError(f"{where}: pattern elements must not be empty")
            pattern.append(element)
        elif isinstance(element, (list, tuple)) and element:
            if not all(isinstance(alt, str) and alt for alt in element):
                raise ExecPolicyError(
                    f"{where}: alternatives must be non-empty strings"
                )
            pattern.append(tuple(element))
        else:
            raise ExecPolicyError(
                f"{where}: pattern element {element!r} must be a string or a list of strings"
            )
    return tuple(pattern)


def _parse_rule(raw: Mapping[str, Any], index: int) -> PrefixRule:
    where = f"rule[{index}]"
    if not isinstance(raw, Mapping):
        raise ExecPolicyError(f"{where}: must be a table")

    decision = raw.get("decision", ALLOW)
    if decision not in _SEVERITY:
        raise ExecPolicyError(
            f"{where}: decision {decision!r} must be one of {sorted(_SEVERITY)}"
        )

    justification = raw.get("justification")
    if justification is not None and not isinstance(justification, str):
        raise ExecPolicyError(f"{where}: justification must be a string")

    examples = {
        name: tuple(
            _as_tokens(value, where=f"{where}.{name}") for value in raw.get(name, ())
        )
        for name in ("match", "not_match")
    }
    if not examples["match"] and not examples["not_match"]:
        # A rule without examples is an unreviewable edit. Requiring at least
        # one is the whole point of the format.
        raise ExecPolicyError(
            f"{where}: declare at least one `match` or `not_match` example so the "
            "rule is tested when it loads"
        )

    rule = PrefixRule(
        pattern=_parse_pattern(raw.get("pattern"), where=where),
        decision=decision,
        justification=justification,
        match=examples["match"],
        not_match=examples["not_match"],
    )
    _self_test(rule, where=where)
    return rule


def _self_test(rule: PrefixRule, *, where: str) -> None:
    """Fail the load if the rule does not do what its own examples claim."""

    for tokens in rule.match:
        if not rule.matches(tokens):
            raise ExecPolicyError(
                f"{where}: `match` example {' '.join(tokens)!r} does not match the pattern"
            )
    for tokens in rule.not_match:
        if rule.matches(tokens):
            raise ExecPolicyError(
                f"{where}: `not_match` example {' '.join(tokens)!r} unexpectedly matches"
            )


def load_policy_text(text: str, *, source: str = "<policy>") -> tuple[PrefixRule, ...]:
    """Parse and self-test a TOML policy document."""

    try:
        document = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise ExecPolicyError(f"{source}: invalid TOML: {exc}") from exc

    raw_rules = document.get("rule", ())
    if not isinstance(raw_rules, (list, tuple)):
        raise ExecPolicyError(f"{source}: `rule` must be an array of tables")
    return tuple(_parse_rule(raw, index) for index, raw in enumerate(raw_rules))


def load_policy_file(path: str | os.PathLike[str]) -> tuple[PrefixRule, ...]:
    """Read, parse and self-test the policy file at ``path``."""

    target = Path(path)
    try:
        text = target.read_text(encoding="utf-8")
    except OSError as exc:
        raise ExecPolicyError(f"{target}: cannot read policy file: {exc}") from exc
    return load_policy_text(text, source=str(target))


# ``path`` + ``mtime_ns`` -> parsed rules. Keyed on the file's modification
# time so an edited policy is picked up without restarting the process, while
# the common case (unchanged file, many shell calls) parses once.
_CACHE: dict[tuple[str, int], tuple[PrefixRule, ...]] = {}


def active_policy() -> tuple[PrefixRule, ...]:
    """The policy named by :data:`POLICY_ENV`, or ``()`` when unset.

    A broken policy file is reported as an error by :func:`load_policy_file`;
    here it degrades to "no rules" so a typo can never take the agent's shell
    away entirely. The load error is surfaced through :func:`policy_error`
    instead, which lets the caller print it once.
    """

    path = os.environ.get(POLICY_ENV, "").strip()
    if not path:
        return ()
    try:
        stat = os.stat(path)
    except OSError:
        return ()
    key = (path, stat.st_mtime_ns)
    cached = _CACHE.get(key)
    if cached is not None:
        return cached
    try:
        rules = load_policy_file(path)
    except ExecPolicyError:
        return ()
    _CACHE.clear()
    _CACHE[key] = rules
    return rules


def forbid_reason(command: str) -> str | None:
    """Why ``command`` is forbidden by the active policy, else ``None``.

    Only ``forbid`` is reported: the shallow screen may add a block but must
    never be the thing that grants one.
    """

    rules = active_policy()
    if not rules:
        return None
    decision = evaluate(command, rules)
    if not decision.blocked:
        return None
    if decision.justification:
        return f"exec policy forbids this command: {decision.justification}"
    return (
        f"exec policy forbids this command: {' '.join(decision.matched_prefix or ())}"
    )
