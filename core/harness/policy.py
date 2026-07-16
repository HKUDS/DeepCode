"""Build a permission engine from configuration without coupling client defaults.

Resolution order:

    DEEPCODE_PERMISSION_MODE (env) > explicitly configured mode >
    caller-selected default

The legacy CLI selects ``full_auto`` as its default to preserve its public
contract. The desktop application selects ``default`` and supplies a durable
approval callback. Explicit user configuration wins for both clients.
"""

from __future__ import annotations

import os
from typing import Any

from core.harness.permissions import (
    PermissionEngine,
    PermissionMode,
    rules_from_config,
)


def resolve_permission_mode(
    config_mode: str | PermissionMode | None = None,
    *,
    default_mode: str | PermissionMode = PermissionMode.FULL_AUTO,
) -> PermissionMode:
    """Resolve the effective mode: env override > config > client default."""

    raw = os.environ.get("DEEPCODE_PERMISSION_MODE", "").strip().lower()
    if not raw:
        raw = _mode_value(config_mode)
    try:
        return PermissionMode(raw) if raw else PermissionMode(_mode_value(default_mode))
    except ValueError:
        return PermissionMode(_mode_value(default_mode))


def _mode_value(mode: str | PermissionMode | None) -> str:
    return str(getattr(mode, "value", mode) or "").strip().lower()


def _explicit_config_mode(security_config: Any | None) -> str | None:
    """Return the mode only when it was explicitly supplied by the user.

    Pydantic models retain the fields provided during validation. That lets a
    desktop client choose an approval-first default without mistaking the
    model's legacy ``full_auto`` field default for explicit user intent.
    Duck-typed test/config objects have no field-set metadata, so their value is
    treated as explicit for backward compatibility.
    """

    if security_config is None:
        return None
    fields_set = getattr(security_config, "model_fields_set", None)
    if fields_set is None:
        fields_set = getattr(security_config, "__fields_set__", None)
    if fields_set is not None and "permission_mode" not in fields_set:
        return None
    value = getattr(security_config, "permission_mode", None)
    return str(value) if value is not None else None


def build_permission_engine(
    security_config: Any | None,
    *,
    cwd: str | None = None,
    default_mode: str | PermissionMode = PermissionMode.FULL_AUTO,
    mode_override: str | PermissionMode | None = None,
) -> PermissionEngine:
    """Construct a :class:`PermissionEngine` from a ``SecurityConfig``.

    ``security_config`` is duck-typed (``.permission_mode`` / ``.permissions``)
    so callers can pass the pydantic model or ``None``. ``mode_override`` is
    reserved for child sessions that must inherit an already-resolved parent
    policy exactly.
    """

    config_mode = _explicit_config_mode(security_config)
    rules_cfg = getattr(security_config, "permissions", None)
    mode = (
        PermissionMode(_mode_value(mode_override))
        if mode_override is not None
        else resolve_permission_mode(config_mode, default_mode=default_mode)
    )
    return PermissionEngine(
        mode=mode,
        rules=rules_from_config(rules_cfg),
        cwd=cwd,
    )
