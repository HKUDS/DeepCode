"""Model-egress policy for LLM provider endpoints (P0-1).

Why this module exists
----------------------

``core/network/hostnames.py`` + ``core/network/safe_http.py`` already answer
"which hosts may this agent *fetch*" and are used by the web tool. Model traffic
bypassed them completely: ``api_base`` was an unvalidated string handed straight
to the OpenAI/Anthropic SDK. That is the larger exposure of the two — the tool
result fetched from a website lands in the *next* request, so the endpoint that
receives prompts also receives everything the agent read. Whoever terminates TLS
for that endpoint (the vendor, a gateway, a reseller, or a hop the operator did
not configure) holds plaintext for every prompt and every tool result, and can
rewrite tool calls on the way back.

The policy therefore answers one question at provider-construction time: *is
this endpoint host one the operator allows?* The answer is a value, not an
exception, so a caller can decide to block or merely record it.

Layering
--------

- :func:`evaluate_provider_egress` is the pure kernel: no I/O, no environment,
  no config import. It is the part worth testing exhaustively.
- :func:`resolve_egress_policy` / :func:`egress_mode` are the thin impure edge
  that reads the environment and the DEEPCODE config.

Fail-closed semantics, inherited from :func:`is_domain_allowed`: an *empty*
allow-list means "no allow-list configured", i.e. every host is allowed unless
it is blocked. That is the historical meaning of an empty allow-list in this
repo (``core/harness/tools/web.py``) and it is preserved on purpose — turning an
unconfigured policy into "deny everything" would break every working setup on
upgrade. Configure an allow-list to get a closed policy.

Modes
-----

``DEEPCODE_EGRESS_MODE=warn`` exists so an operator can roll the policy out
against a live configuration without breaking it: every decision is computed and
logged, but nothing is blocked. Use it to discover which endpoints a setup
actually talks to, then switch to ``enforce`` (the default). ``warn`` is
deliberately not sticky: an unset or unrecognized value resolves to ``enforce``,
so a typo cannot silently disable the gate.
"""

from __future__ import annotations

import os
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from core.network.hostnames import is_domain_allowed

#: Mode that blocks a denied endpoint by raising in ``make_llm_provider``.
ENFORCE = "enforce"
#: Mode that records a denied endpoint without blocking (rollout aid).
WARN = "warn"
EGRESS_MODES: tuple[str, ...] = (ENFORCE, WARN)

MODE_ENV = "DEEPCODE_EGRESS_MODE"
ALLOW_ENV = "DEEPCODE_EGRESS_ALLOW_DOMAINS"
BLOCK_ENV = "DEEPCODE_EGRESS_BLOCK_DOMAINS"

_ALLOWED_SCHEMES = frozenset({"http", "https"})


@dataclass(frozen=True)
class EgressDecision:
    """Outcome of evaluating one provider endpoint against the egress policy.

    ``host`` is ``None`` when there was no endpoint to judge (the SDK will use
    its own default) or when the base could not be parsed. ``endpoint_class``
    is echoed back so a caller can report *what kind* of endpoint was denied
    without re-deriving it. ``reason`` is human-readable and only set when
    ``allowed`` is ``False``.
    """

    allowed: bool
    host: str | None
    endpoint_class: str
    reason: str | None = None


@dataclass(frozen=True)
class EgressPolicy:
    """Resolved policy: the two domain lists plus the effective mode.

    Kept as a value object so the decision function stays pure — callers pass
    the lists in rather than reaching for the environment themselves.
    """

    allowed_domains: tuple[str, ...] = ()
    blocked_domains: tuple[str, ...] = ()
    mode: str = ENFORCE


def parse_domain_list(raw: str | Iterable[str] | None) -> tuple[str, ...]:
    """Normalize a comma-separated string or iterable of domains.

    Trims whitespace, drops empty entries, lowercases, and de-duplicates while
    preserving order. Deliberately does *not* validate the syntax — that is
    :func:`is_domain_allowed`'s job at decision time, where an invalid entry
    fails closed rather than being silently discarded at parse time.
    """

    if raw is None:
        return ()
    if isinstance(raw, str):
        candidates: Iterable[Any] = raw.split(",")
    elif isinstance(raw, Iterable):
        candidates = raw
    else:
        return ()

    seen: dict[str, None] = {}
    for item in candidates:
        if not isinstance(item, str):
            continue
        value = item.strip().lower()
        if value:
            seen.setdefault(value, None)
    return tuple(seen)


def _normalize_mode(raw: str | None) -> str | None:
    """Return a known mode, or ``None`` when unset/unrecognized.

    An unknown value resolves to ``None`` so the caller falls back to
    ``enforce``; a typo must not be able to downgrade the gate to ``warn``.
    """

    if not raw or not isinstance(raw, str):
        return None
    value = raw.strip().lower()
    return value if value in EGRESS_MODES else None


def egress_mode() -> str:
    """Effective egress mode from the environment (``enforce`` by default).

    Config-provided mode is handled by :func:`resolve_egress_policy`; this
    accessor is for callers that only need the process-wide switch.
    """

    return _normalize_mode(os.environ.get(MODE_ENV)) or ENFORCE


def _policy_field(source: Any, *names: str) -> Any:
    """Read the first present of ``names`` from a mapping or an object."""

    if isinstance(source, Mapping):
        for name in names:
            if name in source:
                return source[name]
        return None
    for name in names:
        value = getattr(source, name, None)
        if value is not None:
            return value
    return None


def _policy_from_config(config_or_none: Any) -> EgressPolicy:
    """Read ``providers.egress`` if the object looks like a DEEPCODE config.

    Tolerant by design: a ``None``, a partially built config, or a plain dict
    (tests, JSON round-trips) must not raise — a broken policy read would turn
    an unconfigured setup into a hard failure.
    """

    if config_or_none is None:
        return EgressPolicy()
    try:
        providers = _policy_field(config_or_none, "providers")
        if providers is None:
            return EgressPolicy()
        egress = _policy_field(providers, "egress")
        if egress is None:
            return EgressPolicy()
        allowed = parse_domain_list(
            _policy_field(egress, "allowed_domains", "allowedDomains")
        )
        blocked = parse_domain_list(
            _policy_field(egress, "blocked_domains", "blockedDomains")
        )
        mode = _normalize_mode(_policy_field(egress, "mode"))
    except (AttributeError, TypeError, ValueError):  # pragma: no cover - defensive
        # Never let an unreadable policy turn provider setup into a crash: a
        # broken config object yields the neutral (unconfigured) policy, which
        # the mode switch still governs.
        return EgressPolicy()
    return EgressPolicy(
        allowed_domains=allowed,
        blocked_domains=blocked,
        mode=mode or ENFORCE,
    )


def resolve_egress_policy(config_or_none: Any = None) -> EgressPolicy:
    """Merge the configured policy with environment overrides.

    Environment lists *extend* the config lists rather than replacing them, and
    a block still beats an allow. Either source can therefore only ever tighten
    the policy from the other's point of view — an env var cannot be used to
    un-block a host the config blocks, which keeps a stray shell variable from
    silently re-opening a closed policy.

    ``DEEPCODE_EGRESS_MODE`` wins over the config mode; an unrecognized value
    falls back to ``enforce`` (see :func:`egress_mode`).
    """

    from_config = _policy_from_config(config_or_none)
    env_allowed = parse_domain_list(os.environ.get(ALLOW_ENV))
    env_blocked = parse_domain_list(os.environ.get(BLOCK_ENV))
    env_mode = _normalize_mode(os.environ.get(MODE_ENV))
    return EgressPolicy(
        allowed_domains=parse_domain_list((*from_config.allowed_domains, *env_allowed)),
        blocked_domains=parse_domain_list((*from_config.blocked_domains, *env_blocked)),
        mode=env_mode or from_config.mode,
    )


def evaluate_provider_egress(
    api_base: str | None,
    *,
    endpoint_class: str = "unknown",
    allowed_domains: tuple[str, ...] | Iterable[str] = (),
    blocked_domains: tuple[str, ...] | Iterable[str] = (),
) -> EgressDecision:
    """Decide whether model traffic may be sent to ``api_base``.

    Pure: no I/O, no environment, no config import. Rules, in order:

    1. No ``api_base`` at all -> allowed. The SDK falls back to its own default
       endpoint, which this function cannot see; pretending to police it would
       produce a policy that lies about what it covers. A spec that cares must
       set ``default_api_base`` (``make_llm_provider`` resolves it before
       calling here, so in practice this branch is the truly endpointless case).
    2. Unparseable, or a scheme other than http/https -> denied. A model
       endpoint that is not plain HTTP(S) cannot be reasoned about at all.
    3. Otherwise the hostname goes through
       :func:`core.network.hostnames.is_domain_allowed`, so a block match or a
       configured-but-non-matching allow-list denies with a reason naming the
       host.
    """

    base = api_base.strip() if isinstance(api_base, str) else ""
    if not base:
        return EgressDecision(
            allowed=True, host=None, endpoint_class=endpoint_class, reason=None
        )

    try:
        parsed = urlparse(base)
        scheme = (parsed.scheme or "").lower()
        host = parsed.hostname
    except ValueError as exc:
        return EgressDecision(
            allowed=False,
            host=None,
            endpoint_class=endpoint_class,
            reason=(
                f"api_base {base!r} is not a parseable URL ({exc}); "
                "fix providers.<name>.apiBase or unset it"
            ),
        )

    if scheme not in _ALLOWED_SCHEMES:
        return EgressDecision(
            allowed=False,
            host=host,
            endpoint_class=endpoint_class,
            reason=(
                f"api_base {base!r} uses scheme {scheme or '<none>'!r}; only "
                "http/https model endpoints are allowed"
            ),
        )

    if not host:
        return EgressDecision(
            allowed=False,
            host=None,
            endpoint_class=endpoint_class,
            reason=f"api_base {base!r} has no hostname",
        )

    allowed = parse_domain_list(allowed_domains)
    blocked = parse_domain_list(blocked_domains)
    if is_domain_allowed(host, allowed_domains=allowed, blocked_domains=blocked):
        return EgressDecision(
            allowed=True, host=host, endpoint_class=endpoint_class, reason=None
        )

    # Distinguish the two failure shapes for the operator: "you blocked this"
    # and "this is not on your allow-list" need different fixes. With an empty
    # allow-list `is_domain_allowed` returns True, so this call only reports
    # False for an actual block match.
    if not is_domain_allowed(host, blocked_domains=blocked):
        detail = "is blocked by the egress block-list"
    else:
        detail = "is not on the egress allow-list"
    return EgressDecision(
        allowed=False,
        host=host,
        endpoint_class=endpoint_class,
        reason=f"host {host!r} {detail} (endpoint_class={endpoint_class})",
    )


def describe_denial(
    *,
    provider: str,
    phase: str,
    decision: EgressDecision,
    policy: EgressPolicy,
    api_base: str | None,
) -> str:
    """Build the actionable message used when a provider endpoint is denied.

    Names the host and endpoint class, then states the exact knobs that would
    allow it (env vars and the config path), plus the rollout escape hatch.
    """

    return (
        f"Model egress blocked for provider '{provider}' (phase '{phase}'): "
        f"{decision.reason}. api_base={api_base!r}. "
        "To allow it, add the domain to DEEPCODE_EGRESS_ALLOW_DOMAINS "
        "(comma-separated) or providers.egress.allowedDomains in "
        "deepcode_config.json; if it is blocked, remove it from "
        f"{BLOCK_ENV} / providers.egress.blockedDomains. "
        f"Set {MODE_ENV}=warn to log denials without blocking. "
        f"(effective policy: allowed={list(policy.allowed_domains)}, "
        f"blocked={list(policy.blocked_domains)}, mode={policy.mode})"
    )


__all__ = [
    "ALLOW_ENV",
    "BLOCK_ENV",
    "EGRESS_MODES",
    "ENFORCE",
    "MODE_ENV",
    "WARN",
    "EgressDecision",
    "EgressPolicy",
    "describe_denial",
    "egress_mode",
    "evaluate_provider_egress",
    "parse_domain_list",
    "resolve_egress_policy",
]
