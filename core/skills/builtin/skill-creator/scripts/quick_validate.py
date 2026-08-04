#!/usr/bin/env python3
"""Validate an Agent Skill with DeepCode's production parser."""

from __future__ import annotations

import re
import sys
from pathlib import Path

try:
    from core.skills.catalog import validate_skill_directory
    from core.skills.models import SkillValidationError
except ModuleNotFoundError:
    # Source-checkout execution starts with this script directory on sys.path.
    # Locate the package root structurally; installed distributions import on
    # the first attempt and never use this fallback.
    for parent in Path(__file__).resolve().parents:
        if (parent / "core" / "skills" / "catalog.py").is_file():
            sys.path.insert(0, str(parent))
            break
    from core.skills.catalog import validate_skill_directory
    from core.skills.models import SkillValidationError

_NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def validate(directory: Path) -> tuple[bool, str]:
    try:
        record = validate_skill_directory(directory)
    except (OSError, SkillValidationError, ValueError) as exc:
        return False, str(exc)
    if not _NAME_RE.fullmatch(record.name) or len(record.name) > 64:
        return False, "name must use lowercase hyphen-case and be at most 64 characters"
    if directory.name != record.name:
        return False, "Skill directory name must match the frontmatter name"
    if record.metadata.warnings:
        return False, record.metadata.warnings[0]
    if "TODO:" in record.instructions:
        return False, "replace every TODO placeholder before validation"
    return True, f"Skill is valid: {record.name}"


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("Usage: quick_validate.py <skill-directory>")
        return 2
    valid, message = validate(Path(argv[1]).expanduser().resolve(strict=False))
    print(message)
    return 0 if valid else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
