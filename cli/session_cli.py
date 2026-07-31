"""Manage canonical DeepCode Sessions from the command line."""

from __future__ import annotations

import argparse
import json
import sys

from core.application import DeepCodeApplication
from core.application.errors import ApplicationError


def _confirm(session_id: str) -> bool:
    if not sys.stdin.isatty():
        print(
            "error: permanent deletion requires --yes when stdin is not interactive",
            file=sys.stderr,
        )
        return False
    answer = input(
        f"Permanently delete Session {session_id}? "
        "Conversation history cannot be recovered. [y/N] "
    )
    return answer.strip().lower() in {"y", "yes"}


def _delete(args: argparse.Namespace) -> int:
    if not args.yes and not _confirm(args.session_id):
        if sys.stdin.isatty():
            print("Deletion cancelled.")
        return 2

    application = DeepCodeApplication.open(
        host_surface="session_cli",
        run_automation_scheduler=False,
    )
    try:
        result = application.deletions.delete(args.session_id)
    except ApplicationError as exc:
        if args.json:
            print(
                json.dumps(
                    {
                        "deleted": False,
                        "sessionId": args.session_id,
                        "error": {
                            "code": exc.code,
                            "message": exc.user_message,
                            "details": exc.details,
                        },
                    },
                    ensure_ascii=False,
                )
            )
        else:
            print(f"error [{exc.code}]: {exc.user_message}", file=sys.stderr)
        return 1
    finally:
        application.close()

    payload = {
        "deleted": True,
        "sessionId": result.thread_id,
        "cleanupPending": result.cleanup_pending,
    }
    if args.json:
        print(json.dumps(payload, ensure_ascii=False))
    else:
        suffix = " (background cleanup pending)" if result.cleanup_pending else ""
        print(f"Deleted Session {result.thread_id}{suffix}.")
    return 0


def run(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="deepcode session",
        description="Manage canonical DeepCode conversation Sessions.",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    delete = commands.add_parser(
        "delete",
        help="Permanently delete a Session and its derived application state.",
    )
    delete.add_argument("session_id", help="Exact Session id to delete.")
    delete.add_argument(
        "--yes",
        "-y",
        action="store_true",
        help="Skip the interactive permanent-deletion confirmation.",
    )
    delete.add_argument(
        "--json",
        action="store_true",
        help="Print a machine-readable result.",
    )
    args = parser.parse_args(argv)
    if args.command == "delete":
        return _delete(args)
    parser.error(f"unsupported command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(run())
