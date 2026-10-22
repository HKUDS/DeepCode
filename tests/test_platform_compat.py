from __future__ import annotations

import sys

from core.platform_compat import normalize_stdio_command


def test_normalize_stdio_command_maps_only_bare_python_names() -> None:
    command, _args, _environment = normalize_stdio_command(
        "python3",
        [],
        {},
        inherit_env=False,
    )
    assert command == sys.executable

    explicit = "/missing/virtualenv/bin/python"
    command, _args, _environment = normalize_stdio_command(
        explicit,
        [],
        {},
        inherit_env=False,
    )
    assert command == explicit


def test_normalize_stdio_command_can_preserve_project_python_name() -> None:
    command, _args, _environment = normalize_stdio_command(
        "python3",
        [],
        {},
        inherit_env=False,
        map_python_to_current_interpreter=False,
    )
    assert command == "python3"


def test_frozen_build_leaves_python_to_the_path(monkeypatch) -> None:
    """The Desktop build's executable is the App Server, not a Python: an MCP
    server configured as ``python server.py`` needs the user's Python."""

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    command, _args, _environment = normalize_stdio_command(
        "python3",
        ["server.py"],
        {},
        inherit_env=False,
    )
    assert command == "python3"
