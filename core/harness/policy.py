"""Build a permission engine from configuration (P1 wiring).

Bridges ``deepcode_config.json``'s ``security`` block onto the permission
engine, with a precedence that keeps unattended runs safe:

    DEEPCODE_PERMISSION_MODE (env)  >  security.permission_mode (config)  >
    full_auto (default)

An unknown mode string (typo) resolves to ``full_auto`` so a misconfiguration
never silently blocks a batch reproduction run. Config rules feed
:func:`rules_from_config`; the non-overridable sensitive-path denylist always
applies underneath, regardless of what the rules say.
"""

from __future__ import annotations

import os
from typing import Any

from core.harness.permissions import (
    PermissionEngine,
    PermissionMode,
    rules_from_config,
)


def resolve_permission_mode(config_mode: str | None = None) -> PermissionMode:
    """Resolve the effective mode: env override > config > full_auto."""
    raw = os.environ.get("DEEPCODE_PERMISSION_MODE", "").strip().lower()
    if not raw:
        raw = (config_mode or "").strip().lower()
    try:
        return PermissionMode(raw) if raw else PermissionMode.FULL_AUTO
    except ValueError:
        return PermissionMode.FULL_AUTO


def build_permission_engine(
    security_config: Any | None,
    *,
    cwd: str | None = None,
) -> PermissionEngine:
    """Construct a :class:`PermissionEngine` from a ``SecurityConfig``.

    ``security_config`` is duck-typed (``.permission_mode`` / ``.permissions``)
    so callers can pass the pydantic model or ``None`` (→ safe defaults).
    """
    config_mode = getattr(security_config, "permission_mode", None)
    rules_cfg = getattr(security_config, "permissions", None)
    return PermissionEngine(
        mode=resolve_permission_mode(config_mode),
        rules=rules_from_config(rules_cfg),
        cwd=cwd,
    )
