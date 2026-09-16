#!/usr/bin/env python3
"""Explicit process-owned launcher for diagnostics and migration recovery.

Usage: python scripts/run_compat.py {tui,exec,app-server} [arguments...]
This launcher shares the normal Agent implementation; it owns its application
and closes it on exit. It does not attach to or stop the shared service.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("surface", choices=("tui", "exec", "app-server"))
    parser.add_argument("arguments", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    if args.surface == "tui":
        from cli.tui.app import main as launch
    elif args.surface == "exec":
        from cli.exec_cli import main as launch
    else:
        from app_server.__main__ import main as launch
    return launch(args.arguments, shared_service=False)


if __name__ == "__main__":
    raise SystemExit(main())
