"""Inspect a project's hooks and approve the ones it declares."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from core.application.application import DeepCodeApplication
from core.application.errors import ApplicationError, ProjectNotTrustedError
from core.application.views import hook_discovery_view
from core.domain.project import TrustState

_STATUS_TEXT = {
    "none": "none (this project declares no hooks)",
    "approved": "approved",
}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="deepcode hooks",
        description=(
            "Show a project's hooks and approve the ones it declares. Project "
            "hooks run only while their declarations are the approved ones."
        ),
    )
    commands = parser.add_subparsers(dest="command", required=True)
    status = commands.add_parser(
        "status", help="List the project's hooks and whether they are approved."
    )
    approve = commands.add_parser(
        "approve", help="Approve the hooks the project declares now."
    )
    approve.add_argument("--yes", action="store_true", help="Skip confirmation.")
    for command in (status, approve):
        command.add_argument(
            "--workspace", help="Project folder (default: current directory)."
        )
        command.add_argument("--json", action="store_true", help="Emit JSON output.")
    return parser


def run(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    application = DeepCodeApplication.open(
        host_surface="hooks-cli",
        run_automation_scheduler=False,
    )
    try:
        workspace = Path(args.workspace or ".").expanduser().resolve(strict=True)
        project = application.projects.add(str(workspace))
        listing = hook_discovery_view(application.extensions.hooks(project.id))
        if args.command == "status":
            if args.json:
                print(json.dumps(listing, ensure_ascii=False, indent=2))
            else:
                _print_listing(project.canonical_path, listing)
                if project.trust_state is not TrustState.TRUSTED:
                    print("This project is not trusted; none of its hooks run.")
            return 0

        approval = listing["projectHooks"]
        if not args.json:
            _print_listing(project.canonical_path, listing)
        if project.trust_state is not TrustState.TRUSTED:
            raise ProjectNotTrustedError(
                "project must be trusted before its hooks can be approved",
                user_message=(
                    "This project is not trusted, so its hooks cannot run. "
                    "Trust it first (for example `deepcode --trust` in this "
                    "folder); trusting approves the hooks it declares then."
                ),
            )
        if approval["status"] != "unapproved":
            print("Nothing to approve.", file=sys.stderr)
            if args.json:
                print(json.dumps(listing, ensure_ascii=False, indent=2))
            return 0
        if not args.yes:
            if not sys.stdin.isatty():
                raise ValueError("approve requires --yes when stdin is not interactive")
            answer = input("Approve these project hooks so they run? [y/N] ")
            if answer.strip().lower() not in {"y", "yes"}:
                print("cancelled")
                return 1
        approved = hook_discovery_view(
            application.extensions.approve_hooks(project.id, approval["digest"])
        )
        if args.json:
            print(json.dumps(approved, ensure_ascii=False, indent=2))
        else:
            print("Project hooks approved; they run from the next Turn.")
        return 0
    except (ApplicationError, OSError, ValueError) as exc:
        message = getattr(exc, "user_message", None) or str(exc)
        print(f"error: {message}", file=sys.stderr)
        return 2
    finally:
        application.close()


def _print_listing(project_path: str, listing: dict[str, Any]) -> None:
    approval = listing["projectHooks"]
    status = approval["status"]
    if status == "unapproved":
        text = (
            "unapproved (changed since they were approved)"
            if approval["approvedDigest"]
            else "unapproved (never approved)"
        )
    else:
        text = _STATUS_TEXT[status]
    print(f"Project: {project_path}")
    print(f"Project hooks: {text}")
    print(f"Digest: {approval['digest']}")
    hooks = listing["hooks"]
    if not hooks:
        print("No hooks are configured.")
    for hook in hooks:
        matcher = f" [{hook['matcher']}]" if hook["matcher"] else ""
        print(f"  {hook['source']:<8}{hook['eventName']}{matcher}: {hook['command']}")
        print(f"          from {hook['sourcePath']}")
    for warning in listing["warnings"]:
        print(f"warning: {warning}")
    if status == "unapproved":
        print(
            "The approval covers these declarations, not the scripts they run. "
            "User hooks (~/.deepcode/hooks.json) always run."
        )
