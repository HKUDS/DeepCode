"""Classify whether a Git change can affect the Desktop application.

Desktop bundles the Python App Server, so its CI scope includes both ``desktop``
and the Python runtime imported by the sidecar. Release-only scripts, tests, and
documentation intentionally stay outside this list.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Iterable


DESKTOP_IMPACT_PREFIXES = (
    ".github/actions/",
    "app_server/",
    "cli/",
    "core/",
    "desktop/",
    "prompts/",
    "protocol/",
    "schema/",
    "tools/",
    "utils/",
    "workflows/",
)

DESKTOP_IMPACT_FILES = frozenset(
    {
        ".github/workflows/desktop-ci.yml",
        "__init__.py",
        "deepcode.py",
        "requirements.txt",
        "rust-toolchain.toml",
        "scripts/desktop_ci_scope.py",
    }
)


def affects_desktop(paths: Iterable[str]) -> bool:
    """Return whether any repository-relative path affects Desktop artifacts."""

    return any(
        path in DESKTOP_IMPACT_FILES or path.startswith(DESKTOP_IMPACT_PREFIXES)
        for path in paths
    )


def _read_null_delimited_paths() -> list[str]:
    return [
        os.fsdecode(raw_path)
        for raw_path in sys.stdin.buffer.read().split(b"\0")
        if raw_path
    ]


def main() -> int:
    changed = affects_desktop(_read_null_delimited_paths())
    print(f"desktop_changed={'true' if changed else 'false'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
