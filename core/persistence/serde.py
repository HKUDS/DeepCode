"""Lossless conversions shared by repositories."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any


def dump_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def load_json(value: str | None) -> dict[str, Any]:
    if not value:
        return {}
    loaded = json.loads(value)
    if not isinstance(loaded, dict):
        raise ValueError("persisted JSON value is not an object")
    return loaded


def load_json_list(value: str | None) -> list[Any]:
    if not value:
        return []
    loaded = json.loads(value)
    if not isinstance(loaded, list):
        raise ValueError("persisted JSON value is not an array")
    return loaded


def dump_datetime(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def load_datetime(value: str | None) -> datetime | None:
    if value is None:
        return None
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        raise ValueError("persisted timestamp is not timezone-aware")
    return parsed.astimezone(UTC)


def load_required_datetime(value: str) -> datetime:
    parsed = load_datetime(value)
    if parsed is None:  # pragma: no cover - guarded by the database schema
        raise ValueError("persisted timestamp is missing")
    return parsed
