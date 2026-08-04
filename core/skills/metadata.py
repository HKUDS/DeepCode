"""Optional ``agents/openai.yaml`` metadata for Skills."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

from core.skills.models import SkillInterface, SkillMetadata

OPENAI_METADATA_PATH = Path("agents") / "openai.yaml"
MAX_METADATA_BYTES = 32 * 1024
_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")


def read_skill_metadata(directory: Path) -> tuple[SkillMetadata, bytes | None]:
    """Read optional UI/policy metadata without invalidating the Skill."""

    path = directory / OPENAI_METADATA_PATH
    try:
        payload = path.read_bytes()
    except FileNotFoundError:
        return SkillMetadata(), None
    except OSError as exc:
        return SkillMetadata(warnings=(f"{path}: {exc}",)), None

    try:
        if len(payload) > MAX_METADATA_BYTES:
            raise ValueError(f"metadata exceeds {MAX_METADATA_BYTES} bytes")
        raw = yaml.safe_load(payload.decode("utf-8")) or {}
        if not isinstance(raw, dict):
            raise ValueError("metadata must be a mapping")
        interface_raw = raw.get("interface", {})
        policy_raw = raw.get("policy", {})
        interface_raw = {} if interface_raw is None else interface_raw
        policy_raw = {} if policy_raw is None else policy_raw
        if not isinstance(interface_raw, dict):
            raise ValueError("interface must be a mapping")
        if not isinstance(policy_raw, dict):
            raise ValueError("policy must be a mapping")

        interface = SkillInterface(
            display_name=_optional_text(
                interface_raw.get("display_name"),
                field="interface.display_name",
                limit=120,
            ),
            short_description=_optional_text(
                interface_raw.get("short_description"),
                field="interface.short_description",
                limit=300,
            ),
            icon_small=_icon_path(
                interface_raw.get("icon_small"),
                field="interface.icon_small",
                directory=directory,
            ),
            icon_large=_icon_path(
                interface_raw.get("icon_large"),
                field="interface.icon_large",
                directory=directory,
            ),
            brand_color=_brand_color(interface_raw.get("brand_color")),
            default_prompt=_optional_text(
                interface_raw.get("default_prompt"),
                field="interface.default_prompt",
                limit=2_000,
            ),
        )
        allow_implicit = policy_raw.get("allow_implicit_invocation", True)
        if not isinstance(allow_implicit, bool):
            raise ValueError("policy.allow_implicit_invocation must be a boolean")
        return (
            SkillMetadata(
                interface=interface,
                allow_implicit_invocation=allow_implicit,
            ),
            payload,
        )
    except (UnicodeDecodeError, ValueError, yaml.YAMLError) as exc:
        return SkillMetadata(warnings=(f"{path}: invalid metadata: {exc}",)), payload


def _optional_text(value: Any, *, field: str, limit: int) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    clean = value.strip()
    if len(clean) > limit:
        raise ValueError(f"{field} exceeds {limit} characters")
    return clean


def _brand_color(value: Any) -> str | None:
    color = _optional_text(value, field="interface.brand_color", limit=7)
    if color is not None and not _COLOR_RE.fullmatch(color):
        raise ValueError("interface.brand_color must use #RRGGBB")
    return color.upper() if color is not None else None


def _icon_path(value: Any, *, field: str, directory: Path) -> str | None:
    raw = _optional_text(value, field=field, limit=500)
    if raw is None:
        return None
    relative = Path(raw)
    if relative.is_absolute():
        raise ValueError(f"{field} must be relative to the Skill directory")
    resolved = (directory / relative).resolve(strict=False)
    try:
        resolved.relative_to(directory.resolve(strict=False))
    except ValueError as exc:
        raise ValueError(f"{field} must stay inside the Skill directory") from exc
    if not resolved.is_file():
        raise ValueError(f"{field} does not reference a file")
    return relative.as_posix()


__all__ = [
    "MAX_METADATA_BYTES",
    "OPENAI_METADATA_PATH",
    "read_skill_metadata",
]
