"""``deepcode exec`` — one headless Turn on the shared Session runtime."""

from __future__ import annotations

import argparse
import json
import os
import sys

from cli.config_errors import format_config_error
from cli.execution_options import (
    add_access_preset_argument,
    add_reasoning_effort_argument,
    add_workspace_trust_argument,
    parse_access_preset,
)
from cli.headless_turn import HeadlessTurnOptions, run_headless_turn, succeeded
from cli.transcript import TranscriptMode
from core.application.errors import ApplicationError
from core.config import ConfigError
from core.domain.approval import Approval, ApprovalStatus
from core.events import serialize_event
from core.skills.models import MAX_SELECTED_SKILLS


def _reasoning_preview(text: str, limit: int = 240) -> str:
    first = next((line.strip() for line in text.splitlines() if line.strip()), "")
    return first[:limit] + ("…" if len(first) > limit else "")


def _emit_human(event, transcript_mode: TranscriptMode) -> None:
    msg = event.msg
    event_type = msg.type
    if event_type == "turn_started":
        print("· turn started", flush=True)
    elif event_type == "tool_started":
        if transcript_mode is not TranscriptMode.SUMMARY:
            print(f"  → {msg.name}", flush=True)
    elif event_type == "tool_completed":
        if transcript_mode is not TranscriptMode.SUMMARY:
            mark = "✗" if msg.is_error else "✓"
            print(f"  {mark} {msg.name}", flush=True)
    elif event_type == "skill_loaded":
        if transcript_mode is not TranscriptMode.SUMMARY:
            print(
                f"  ◇ skill {msg.invocation.name} ({msg.invocation.kind.value})",
                flush=True,
            )
    elif event_type == "skill_load_failed":
        print(f"! skill error: {msg.message}", file=sys.stderr, flush=True)
    elif event_type == "agent_message":
        print(f"\n{msg.text}\n", flush=True)
    elif event_type == "agent_reasoning_started":
        if transcript_mode is not TranscriptMode.SUMMARY:
            effort = (msg.effort or "auto").title()
            print(f"  ◇ thinking · {effort}", flush=True)
    elif event_type == "agent_reasoning_completed":
        if transcript_mode is TranscriptMode.SUMMARY:
            return
        if msg.availability.value == "opaque":
            print("    details unavailable from this model", flush=True)
            return
        if transcript_mode is TranscriptMode.NORMAL:
            text = _reasoning_preview(msg.summary_text or msg.trace_text)
            if text:
                print(f"    {text}", flush=True)
            return
        if msg.summary_text:
            print(f"    {msg.summary_text}", flush=True)
        if msg.trace_text and msg.trace_text != msg.summary_text:
            if msg.summary_text:
                print("    provider reasoning details", flush=True)
            print(f"    {msg.trace_text}", flush=True)
    elif event_type == "error":
        print(f"! error: {msg.message}", file=sys.stderr, flush=True)
    elif event_type == "task_complete":
        print(f"· done ({msg.stop_reason})", flush=True)


def _approval_decider(approval: Approval) -> ApprovalStatus:
    request = approval.request
    tool = str(request.get("toolName") or "tool")
    reason = str(request.get("reason") or "sensitive operation")
    if not sys.stdin.isatty():
        print(
            f"! denied approval for {tool}: non-interactive Ask mode; use "
            "--access full-access only if unrestricted execution is intended",
            file=sys.stderr,
            flush=True,
        )
        return ApprovalStatus.DENIED
    print(
        f"\nApproval required for {tool}: {reason}\n"
        "  y = approve once · a = approve this tool for the Session · "
        "anything else = deny\n> ",
        end="",
        file=sys.stderr,
        flush=True,
    )
    answer = sys.stdin.readline().strip().casefold()
    if answer in {"y", "yes"}:
        return ApprovalStatus.APPROVED_ONCE
    if answer in {"a", "always"}:
        return ApprovalStatus.APPROVED_SESSION
    return ApprovalStatus.DENIED


def _run(args: argparse.Namespace) -> int:
    transcript_mode = TranscriptMode.parse(
        "verbose" if args.verbose else args.transcript
    )
    workspace = os.path.abspath(args.workspace) if args.workspace is not None else None
    if not args.json:
        print(
            f"deepcode exec · workspace={workspace or '(stored Session workspace)'} "
            f"· access={args.access or 'inherit'}",
            file=sys.stderr,
            flush=True,
        )

    def on_event(event) -> None:
        if args.json:
            print(json.dumps(serialize_event(event), ensure_ascii=False), flush=True)
        else:
            _emit_human(event, transcript_mode)

    try:
        result = run_headless_turn(
            HeadlessTurnOptions(
                prompt=args.prompt,
                workspace=workspace,
                resume_id=args.resume,
                connection_id=args.connection,
                model=args.model,
                reasoning_effort=args.reasoning_effort,
                skill_identifiers=tuple(args.skill),
                max_iterations=args.max_iterations,
                trust_workspace=args.trust,
                access_preset=parse_access_preset(args.access),
                agent_preset=args.preset,
            ),
            on_event=on_event,
            decide_approval=_approval_decider,
        )
    except ConfigError as exc:
        print(format_config_error(exc), file=sys.stderr, flush=True)
        return 1
    except (ApplicationError, OSError, ValueError) as exc:
        message = exc.user_message if isinstance(exc, ApplicationError) else str(exc)
        print(f"error: {message}", file=sys.stderr, flush=True)
        return 1
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr, flush=True)
        return 130

    print(
        f"session={result.session_id} · status={result.turn.status.value} "
        f"· model={result.turn.execution_profile.model_id if result.turn.execution_profile else 'unknown'} "
        f"· effort={result.turn.execution_profile.reasoning_effort if result.turn.execution_profile else 'auto'} "
        f"· workspace={result.workspace}",
        file=sys.stderr,
        flush=True,
    )
    return 0 if succeeded(result) else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="deepcode exec",
        description="Run one durable coding Turn headlessly.",
    )
    parser.add_argument("prompt", help="The coding task to perform.")
    parser.add_argument(
        "--workspace",
        "-w",
        default=None,
        help=(
            "Workspace for a new Session (default: current directory), or an "
            "explicit process-local override with --resume."
        ),
    )
    parser.add_argument(
        "--resume",
        metavar="SESSION_ID",
        help="Run this Turn in an existing canonical Session.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit one JSON event per line (NDJSON) instead of a transcript.",
    )
    transcript = parser.add_mutually_exclusive_group()
    transcript.add_argument(
        "--transcript",
        choices=[mode.value for mode in TranscriptMode],
        default=TranscriptMode.NORMAL.value,
        help="Choose how much reasoning and tool detail to print.",
    )
    transcript.add_argument(
        "--verbose",
        action="store_true",
        help="Alias for --transcript verbose.",
    )
    parser.add_argument("--model", "-m", default=None, help="Override the model id.")
    parser.add_argument(
        "--connection",
        "-c",
        default=None,
        help="Use a named LLM connection from `deepcode provider list`.",
    )
    add_reasoning_effort_argument(parser)
    add_access_preset_argument(parser)
    add_workspace_trust_argument(parser)
    parser.add_argument(
        "--preset",
        default=None,
        metavar="ID",
        help=(
            "Agent preset for a new Session (persona + tool face); "
            "see `deepcode exec` docs or the TUI /preset list."
        ),
    )
    parser.add_argument(
        "--skill",
        action="append",
        default=[],
        metavar="ID_OR_NAME",
        help="Select a Skill for this Turn (repeatable, maximum 8).",
    )
    parser.add_argument(
        "--max-iterations",
        type=int,
        default=None,
        help="Optional model-sampling limit for diagnostics (unlimited by default).",
    )
    args = parser.parse_args(argv)
    if len(args.skill) > MAX_SELECTED_SKILLS:
        parser.error(f"--skill may be specified at most {MAX_SELECTED_SKILLS} times")
    return _run(args)


if __name__ == "__main__":
    raise SystemExit(main())
