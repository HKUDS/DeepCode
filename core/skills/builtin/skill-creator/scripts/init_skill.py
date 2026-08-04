#!/usr/bin/env python3
"""Create a minimal Agent Skill without overwriting existing work."""

from __future__ import annotations

import argparse
import os
import re
import shutil
import tempfile
from pathlib import Path

import yaml

_NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_RESOURCE_NAMES = frozenset({"scripts", "references", "assets"})


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("name")
    parser.add_argument(
        "--path",
        default=".agents/skills",
        help="Parent directory for the new Skill (default: .agents/skills)",
    )
    parser.add_argument(
        "--resources",
        default="",
        help="Comma-separated subset of scripts,references,assets",
    )
    parser.add_argument("--display-name")
    parser.add_argument("--short-description")
    parser.add_argument("--default-prompt")
    parser.add_argument(
        "--explicit-only",
        action="store_true",
        help="Disable implicit invocation while keeping explicit $name use",
    )
    return parser


def _resources(raw: str) -> tuple[str, ...]:
    values = tuple(dict.fromkeys(item.strip() for item in raw.split(",") if item.strip()))
    invalid = sorted(set(values) - _RESOURCE_NAMES)
    if invalid:
        raise ValueError(f"unknown resource directories: {', '.join(invalid)}")
    return values


def _validate_name(name: str) -> str:
    clean = name.strip()
    if len(clean) > 64 or not _NAME_RE.fullmatch(clean):
        raise ValueError(
            "name must be 1-64 characters of lowercase letters, digits, and "
            "single hyphens"
        )
    return clean


def _skill_markdown(name: str) -> str:
    title = " ".join(part.capitalize() for part in name.split("-"))
    frontmatter = yaml.safe_dump(
        {
            "name": name,
            "description": (
                "TODO: Explain what this Skill does and the requests that should "
                "trigger it."
            ),
        },
        sort_keys=False,
        allow_unicode=True,
    ).rstrip()
    return f"""---
{frontmatter}
---

# {title}

TODO: Write concise, imperative workflow instructions. Link bundled resources only where they are needed.
"""


def _metadata(args: argparse.Namespace, name: str) -> dict:
    interface = {
        key: value.strip()
        for key, value in (
            ("display_name", args.display_name),
            ("short_description", args.short_description),
            ("default_prompt", args.default_prompt),
        )
        if isinstance(value, str) and value.strip()
    }
    if "default_prompt" in interface and f"${name}" not in interface["default_prompt"]:
        raise ValueError(f"default prompt must mention ${name}")
    result: dict = {}
    if interface:
        result["interface"] = interface
    if args.explicit_only:
        result["policy"] = {"allow_implicit_invocation": False}
    return result


def create(args: argparse.Namespace) -> Path:
    name = _validate_name(args.name)
    resources = _resources(args.resources)
    parent = Path(args.path).expanduser().resolve(strict=False)
    destination = parent / name
    if destination.exists() or destination.is_symlink():
        raise FileExistsError(f"Skill already exists: {destination}")

    parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{name}.", dir=parent))
    try:
        (temporary / "SKILL.md").write_text(_skill_markdown(name), encoding="utf-8")
        for resource in resources:
            (temporary / resource).mkdir()
        metadata = _metadata(args, name)
        if metadata:
            agents = temporary / "agents"
            agents.mkdir()
            (agents / "openai.yaml").write_text(
                yaml.safe_dump(metadata, sort_keys=False, allow_unicode=True),
                encoding="utf-8",
            )
        os.replace(temporary, destination)
    except BaseException:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return destination


def main() -> int:
    args = _parser().parse_args()
    try:
        destination = create(args)
    except (OSError, ValueError) as exc:
        print(f"Error: {exc}")
        return 1
    print(destination)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
