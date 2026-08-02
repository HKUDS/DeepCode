"""Durable permission policy selected for one execution."""

from __future__ import annotations

from enum import StrEnum


class ExecutionPermissionMode(StrEnum):
    """Typed permission mode captured when a Turn is admitted.

    ``None`` on ``Turn.execution_permission_mode`` remains meaningful: it
    preserves the host's established default for ordinary interactive Turns.
    Automatic execution always stores one of these explicit values so a
    different worker cannot silently reinterpret its policy.
    """

    DEFAULT = "default"
    PLAN = "plan"
    FULL_AUTO = "full_auto"


__all__ = ["ExecutionPermissionMode"]
