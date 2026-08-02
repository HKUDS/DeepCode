"""Manage durable Automations through the shared application service."""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Callable
from typing import Any

from cli.automation_foreground import run_automation_foreground
from core.application import DeepCodeApplication
from core.application.errors import ApplicationError, ProjectNotTrustedError
from core.application.views import (
    automation_run_view,
    automation_view,
    thread_view,
)
from core.domain.automation import AutomationActivationStatus, AutomationScheduleKind


ApplicationFactory = Callable[[], DeepCodeApplication]


def _add_json_flag(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--json",
        action="store_true",
        default=argparse.SUPPRESS,
        help="Print a machine-readable result.",
    )


def _add_project_selector(parser: argparse.ArgumentParser) -> None:
    selector = parser.add_mutually_exclusive_group()
    selector.add_argument(
        "--project",
        metavar="PROJECT_ID",
        default=argparse.SUPPRESS,
        help="Use an existing canonical DeepCode Project.",
    )
    selector.add_argument(
        "--workspace",
        "-w",
        metavar="PATH",
        default=argparse.SUPPRESS,
        help="Resolve a Project from this workspace path.",
    )


def _add_pagination_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--limit",
        type=int,
        default=100,
        help="Maximum items to return (1-500; default: 100).",
    )
    parser.add_argument(
        "--offset",
        type=int,
        default=0,
        help="Zero-based result offset (default: 0).",
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="deepcode automation",
        description=(
            "Manage durable Automations backed by the same Goal and Turn runtime "
            "as CLI and Desktop."
        ),
    )
    parser.set_defaults(json=False, project=None, workspace=None)
    _add_json_flag(parser)
    _add_project_selector(parser)
    commands = parser.add_subparsers(dest="command", required=True)

    command_parsers: list[argparse.ArgumentParser] = []

    list_command = commands.add_parser(
        "list",
        help="List Automation definitions and their latest Runs.",
    )
    command_parsers.append(list_command)
    _add_project_selector(list_command)
    _add_pagination_options(list_command)

    create = commands.add_parser(
        "create",
        help="Create an Automation in a trusted Project.",
    )
    command_parsers.append(create)
    _add_project_selector(create)
    create.add_argument("name", help="Human-readable Automation name.")
    create.add_argument(
        "--prompt",
        required=True,
        help="Instruction submitted to the shared Agent runtime.",
    )
    create.add_argument(
        "--schedule",
        "--schedule-kind",
        dest="schedule_kind",
        choices=[kind.value for kind in AutomationScheduleKind],
        default=AutomationScheduleKind.MANUAL.value,
    )
    create.add_argument("--interval-seconds", type=int)
    create.add_argument(
        "--disabled",
        action="store_true",
        help="Create an interval Automation with its schedule paused.",
    )

    update = commands.add_parser(
        "update",
        help="Update an Automation definition.",
    )
    command_parsers.append(update)
    update.add_argument("automation_id")
    update.add_argument("--name")
    update.add_argument("--prompt")
    update.add_argument(
        "--schedule",
        "--schedule-kind",
        dest="schedule_kind",
        choices=[kind.value for kind in AutomationScheduleKind],
    )
    update.add_argument("--interval-seconds", type=int)

    for action in ("enable", "disable"):
        command = commands.add_parser(
            action,
            help=f"{action.title()} an interval Automation schedule.",
        )
        command_parsers.append(command)
        command.add_argument("automation_id")

    run_command = commands.add_parser(
        "run",
        help="Run an Automation in the foreground until it settles.",
    )
    command_parsers.append(run_command)
    run_command.add_argument("automation_id")
    run_command.add_argument(
        "--request-id",
        help="Optional idempotency key for retrying the same manual Run.",
    )

    runs = commands.add_parser(
        "runs",
        help="List durable Run history for an Automation.",
    )
    command_parsers.append(runs)
    runs.add_argument("automation_id")
    _add_pagination_options(runs)

    delete = commands.add_parser(
        "delete",
        help="Retire an Automation while retaining its Run history.",
    )
    command_parsers.append(delete)
    delete.add_argument("automation_id")
    delete.add_argument(
        "--yes",
        "-y",
        action="store_true",
        help="Skip the interactive retirement confirmation.",
    )

    for command in command_parsers:
        _add_json_flag(command)
    return parser


def _default_application_factory() -> DeepCodeApplication:
    return DeepCodeApplication.open(
        host_surface="automation_cli",
        run_automation_scheduler=False,
    )


def _confirm_delete(automation_id: str) -> bool:
    if not sys.stdin.isatty():
        print(
            "error: deleting an Automation requires --yes when stdin is "
            "not interactive",
            file=sys.stderr,
        )
        return False
    answer = input(
        f"Retire Automation {automation_id}? "
        "Its definition will be disabled; Run history is retained. [y/N] "
    )
    return answer.strip().lower() in {"y", "yes"}


def _resolve_project(
    application: DeepCodeApplication,
    args: argparse.Namespace,
    *,
    default_to_cwd: bool,
) -> str | None:
    if args.project is not None:
        return application.projects.read(args.project).id
    workspace = args.workspace
    if workspace is None and default_to_cwd:
        workspace = os.getcwd()
    if workspace is not None:
        return application.projects.add(workspace).id
    return None


def _dispatch(
    application: DeepCodeApplication,
    args: argparse.Namespace,
) -> dict[str, Any]:
    automations = application.automations

    if args.command == "list":
        project_id = _resolve_project(application, args, default_to_cwd=False)
        inventory = automations.list(
            project_id,
            limit=args.limit,
            offset=args.offset,
        )
        return {
            "automations": [
                automation_view(automation) for automation in inventory.automations
            ],
            "latestRuns": [automation_run_view(run) for run in inventory.latest_runs],
            "schedulerActive": inventory.scheduler_active,
            "executionMode": "requires_live_runtime",
            "hasMore": inventory.has_more,
            "nextOffset": inventory.next_offset,
        }

    if args.command == "create":
        project_id = _resolve_project(application, args, default_to_cwd=True)
        assert project_id is not None
        created = automations.create(
            project_id=project_id,
            name=args.name,
            prompt=args.prompt,
            schedule_kind=AutomationScheduleKind(args.schedule_kind),
            interval_seconds=args.interval_seconds,
            enabled=not args.disabled,
        )
        return {
            "automation": automation_view(created.automation),
            "thread": thread_view(created.thread),
        }

    if args.command == "update":
        if (
            args.name is None
            and args.prompt is None
            and args.schedule_kind is None
            and args.interval_seconds is None
        ):
            raise ValueError("update requires at least one changed field")
        updated = automations.update(
            args.automation_id,
            name=args.name,
            prompt=args.prompt,
            schedule_kind=(
                AutomationScheduleKind(args.schedule_kind)
                if args.schedule_kind is not None
                else None
            ),
            interval_seconds=args.interval_seconds,
        )
        return {"automation": automation_view(updated)}

    if args.command in {"enable", "disable"}:
        status = (
            AutomationActivationStatus.ENABLED
            if args.command == "enable"
            else AutomationActivationStatus.PAUSED
        )
        updated = automations.update(args.automation_id, status=status)
        return {"automation": automation_view(updated)}

    if args.command == "run":
        return run_automation_foreground(
            application,
            args.automation_id,
            request_id=args.request_id,
            interactive=bool(not args.json and sys.stdin.isatty()),
        )

    if args.command == "runs":
        page = automations.list_runs(
            args.automation_id,
            limit=args.limit,
            offset=args.offset,
        )
        return {
            "runs": [automation_run_view(run) for run in page.runs],
            "hasMore": page.has_more,
            "nextOffset": page.next_offset,
        }

    if args.command == "delete":
        return {
            "removed": automations.remove(args.automation_id),
            "automationId": args.automation_id,
        }

    raise ValueError(f"unsupported Automation command: {args.command}")


def _print_result(command: str, result: dict[str, Any], *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    if command == "list":
        definitions = result["automations"]
        if not definitions:
            print("No Automations found.")
            return
        latest_by_automation = {
            run["automationId"]: run for run in result["latestRuns"]
        }
        print(f"{'STATUS':<9} {'SCHEDULE':<15} {'LATEST RUN':<12} NAME · ID")
        for automation in definitions:
            schedule = automation["scheduleKind"]
            if automation["intervalSeconds"] is not None:
                schedule = f"every {automation['intervalSeconds']}s"
            latest = latest_by_automation.get(automation["id"])
            latest_status = latest["status"] if latest is not None else "never"
            print(
                f"{automation['status']:<9} {schedule:<15} "
                f"{latest_status:<12} {automation['name']} · {automation['id']}"
            )
        print(
            "Interval schedules run only while a scheduler-enabled DeepCode "
            "Desktop or App Server is active."
        )
        _print_pagination_hint(result, "Automations")
        return

    if command in {"create", "update", "enable", "disable"}:
        automation = result["automation"]
        print(
            f"{automation['name']} · {automation['status']} · "
            f"{automation['scheduleKind']}"
        )
        print(f"id: {automation['id']}")
        print(f"thread: {automation['threadId']}")
        if command == "create" and automation["scheduleKind"] == "interval":
            print(
                "Interval schedules run only while a scheduler-enabled DeepCode "
                "Desktop or App Server is active."
            )
        return

    if command == "run":
        run = result["run"]
        print(f"Run {run['id']} · {run['status']}")
        print(f"thread: {run['threadId']}")
        if run["goalId"] is not None:
            print(f"goal: {run['goalId']}")
        if run["turnId"] is not None:
            print(f"turn: {run['turnId']}")
        if run["detail"]:
            print(f"detail: {run['detail']}")
        return

    if command == "runs":
        runs = result["runs"]
        if not runs:
            print("No Runs found.")
            return
        print(f"{'STATUS':<12} {'TRIGGER':<10} {'SCHEDULED FOR':<24} ID")
        for run in runs:
            print(
                f"{run['status']:<12} {run['trigger']:<10} "
                f"{run['scheduledFor']:<24} {run['id']}"
            )
        _print_pagination_hint(result, "Runs")
        return

    if result["removed"]:
        print(
            f"Retired Automation {result['automationId']}; "
            "its Run history was retained."
        )
    else:
        print(f"Automation {result['automationId']} was already retired.")


def _print_pagination_hint(result: dict[str, Any], noun: str) -> None:
    if not result["hasMore"]:
        return
    next_offset = result["nextOffset"]
    assert next_offset is not None
    print(f"More {noun} are available; continue with --offset {next_offset}.")


def _print_error(exc: Exception, *, as_json: bool) -> None:
    if isinstance(exc, ApplicationError):
        message = exc.user_message
        if isinstance(exc, ProjectNotTrustedError):
            message = (
                f"{message}. Trust the Project explicitly in DeepCode before "
                "creating or enabling Automations; this command never grants trust"
            )
        payload: dict[str, Any] = {
            "code": exc.code,
            "message": message,
            "details": exc.details,
        }
    else:
        payload = {
            "code": "INVALID_REQUEST",
            "message": str(exc),
            "details": {},
        }
    if as_json:
        print(json.dumps({"error": payload}, ensure_ascii=False))
    else:
        print(f"error [{payload['code']}]: {payload['message']}", file=sys.stderr)


def run(
    argv: list[str] | None = None,
    *,
    application_factory: ApplicationFactory | None = None,
) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if args.command not in {"list", "create"} and (
        args.project is not None or args.workspace is not None
    ):
        parser.error("--project/--workspace are only valid for list and create")

    if args.command == "delete" and not args.yes:
        if not _confirm_delete(args.automation_id):
            if sys.stdin.isatty():
                print("Deletion cancelled.")
            return 2

    factory = application_factory or _default_application_factory
    application: DeepCodeApplication | None = None
    try:
        application = factory()
        result = _dispatch(application, args)
    except (ApplicationError, ValueError) as exc:
        _print_error(exc, as_json=args.json)
        return 1
    finally:
        if application is not None:
            application.close()

    _print_result(args.command, result, as_json=args.json)
    return _result_exit_code(args.command, result)


def _result_exit_code(command: str, result: dict[str, Any]) -> int:
    if command != "run":
        return 0
    return 0 if result["run"]["status"] == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(run())
