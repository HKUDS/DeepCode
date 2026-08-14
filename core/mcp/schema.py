"""Provider-compatible normalization for MCP JSON Schema fragments."""

from __future__ import annotations

from typing import Any


def normalize_schema_for_openai(schema: Any) -> dict[str, Any]:
    if not isinstance(schema, dict):
        return {"type": "object", "properties": {}}
    normalized = dict(schema)
    raw_type = normalized.get("type")
    if isinstance(raw_type, list):
        non_null = [item for item in raw_type if item != "null"]
        if "null" in raw_type and len(non_null) == 1:
            normalized["type"] = non_null[0]
            normalized["nullable"] = True
    for key in ("oneOf", "anyOf"):
        branch = _nullable_branch(normalized.get(key))
        if branch is not None:
            merged = {name: value for name, value in normalized.items() if name != key}
            merged.update(branch)
            merged["nullable"] = True
            normalized = merged
            break
    properties = normalized.get("properties")
    if isinstance(properties, dict):
        normalized["properties"] = {
            name: normalize_schema_for_openai(value)
            if isinstance(value, dict)
            else value
            for name, value in properties.items()
        }
    if isinstance(normalized.get("items"), dict):
        normalized["items"] = normalize_schema_for_openai(normalized["items"])
    if normalized.get("type") == "object":
        normalized.setdefault("properties", {})
        normalized.setdefault("required", [])
    return normalized


def _nullable_branch(options: Any) -> dict[str, Any] | None:
    if not isinstance(options, list):
        return None
    non_null: list[dict[str, Any]] = []
    saw_null = False
    for option in options:
        if not isinstance(option, dict):
            return None
        if option.get("type") == "null":
            saw_null = True
        else:
            non_null.append(option)
    return non_null[0] if saw_null and len(non_null) == 1 else None


__all__ = ["normalize_schema_for_openai"]
