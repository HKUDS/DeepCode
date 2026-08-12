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
