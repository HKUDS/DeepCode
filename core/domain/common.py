"""Shared primitives for DeepCode's UI-independent domain model."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any


JsonObject = dict[str, Any]


def utc_now() -> datetime:
    """Return an aware UTC timestamp."""
    return datetime.now(timezone.utc)


def new_id(prefix: str) -> str:
    """Create an opaque, type-prefixed identifier suitable for persistence."""
    if not prefix or not prefix.isascii() or not prefix.replace("_", "").isalnum():
        raise ValueError("identifier prefix must be non-empty ASCII alphanumeric")
    return f"{prefix}_{uuid.uuid4().hex}"


def require_prefixed_id(value: str, prefix: str) -> None:
    if not value.startswith(f"{prefix}_") or len(value) <= len(prefix) + 1:
        raise ValueError(f"expected a {prefix}_ identifier")


def require_aware(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")


def require_non_empty(value: str, name: str) -> None:
    if not value.strip():
        raise ValueError(f"{name} must not be empty")


def validate_json_object(value: JsonObject, name: str) -> None:
    if not isinstance(value, dict):
        raise TypeError(f"{name} must be a JSON object")
    try:
        json.dumps(value, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must contain only JSON values") from exc


def enum_value(value: Enum | str) -> str:
    return value.value if isinstance(value, Enum) else str(value)
