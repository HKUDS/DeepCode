"""Manage the same local Skill catalog consumed by DeepCode Agent turns."""

from __future__ import annotations

import argparse
import json
import os
import sys

from core.skills.management import LocalSkillManager
from core.skills.models import MUTABLE_SKILL_SCOPES, SkillRecord, SkillScope


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="deepcode skill",
        description="List and manage local Agent Skills.",
    )
    parser.add_argument(
        "--workspace",
        "-w",
        default=os.getcwd(),
        help="Workspace whose project Skills and policy are used.",
    )
    parser.add_argument(
        "--json", action="store_true", help="Emit machine-readable JSON."
    )
    commands = parser.add_subparsers(dest="command", required=True)

    commands.add_parser(
        "list", help="List active, shadowed, disabled, and invalid Skills."
    )

    show = commands.add_parser(
        "show", help="Show one Skill by opaque ID or active name."
    )
    show.add_argument("skill")

    import_command = commands.add_parser(
        "import",
        help="Validate and atomically import a local Skill directory.",
    )
    import_command.add_argument("path")
    import_command.add_argument(
        "--scope",
        choices=[scope.value for scope in MUTABLE_SKILL_SCOPES],
        default=SkillScope.PROJECT.value,
    )

    for action in ("enable", "disable"):
        command = commands.add_parser(action, help=f"{action.title()} a Skill by ID.")
        command.add_argument("skill_id")
        command.add_argument(
            "--scope",
            choices=[scope.value for scope in MUTABLE_SKILL_SCOPES],
            default=SkillScope.PROJECT.value,
            help="Configuration layer to mutate (project is least-global).",
        )

    remove = commands.add_parser(
        "remove",
        help="Delete a managed .agents Skill (legacy and system Skills are read-only).",
    )
    remove.add_argument("skill_id")
    remove.add_argument("--yes", action="store_true", help="Skip confirmation.")

    commands.add_parser("reload", help="Force a fresh catalog scan.")
    return parser


def run(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        manager = LocalSkillManager(args.workspace)
        if args.command in {"list", "reload"}:
            catalog = manager.catalog(force=args.command == "reload")
            return _print_catalog(catalog.records, catalog.revision, as_json=args.json)
        if args.command == "show":
            return _print_detail(manager.find(args.skill), as_json=args.json)
        if args.command == "import":
            record = manager.import_directory(
                args.path,
                target_scope=SkillScope(args.scope),
            )
            return _print_detail(record, as_json=args.json)
        if args.command in {"enable", "disable"}:
            catalog = manager.set_enabled(
                args.skill_id,
                enabled=args.command == "enable",
                config_scope=SkillScope(args.scope),
            )
            record = catalog.get(args.skill_id)
            if record is None:
                raise ValueError(
                    f"Skill not found after policy update: {args.skill_id}"
                )
            return _print_record(record, as_json=args.json)
        if args.command == "remove":
            record = manager.find(args.skill_id)
            if not args.yes:
                if not sys.stdin.isatty():
                    raise ValueError(
                        "remove requires --yes when stdin is not interactive"
                    )
                answer = input(
                    f"Remove managed Skill {record.name!r} ({record.id})? [y/N] "
                )
                if answer.strip().lower() not in {"y", "yes"}:
                    print("cancelled")
                    return 1
            removed = manager.delete(record.id)
            if args.json:
                print(json.dumps({"removed": True, "skill": _record_view(removed)}))
            else:
                print(f"removed {removed.name} ({removed.id})")
            return 0
    except (OSError, PermissionError, ValueError) as exc:
        if args.json:
            print(json.dumps({"error": str(exc)}, ensure_ascii=False))
        else:
            print(f"error: {exc}", file=sys.stderr)
        return 2
    return 2


def _print_catalog(
    records: tuple[SkillRecord, ...],
    revision: str,
    *,
    as_json: bool,
) -> int:
    if as_json:
        print(
            json.dumps(
                {
                    "catalogRevision": revision,
                    "skills": [_record_view(record) for record in records],
                },
                ensure_ascii=False,
            )
        )
        return 0
    if not records:
        print("No Skills discovered.")
        return 0
    print(f"{'STATUS':<10} {'SCOPE':<8} {'NAME':<24} ID")
    for record in records:
        print(
            f"{record.status.value:<10} {record.scope.value:<8} "
            f"{record.name[:24]:<24} {record.id}"
        )
    return 0


def _print_detail(record: SkillRecord, *, as_json: bool) -> int:
    if as_json:
        value = _record_view(record)
        value["instructions"] = record.instructions
        print(json.dumps(value, ensure_ascii=False))
        return 0
    _print_record(record, as_json=False)
    if record.instructions:
        print("\n" + record.instructions)
    return 0


def _print_record(record: SkillRecord, *, as_json: bool) -> int:
    if as_json:
        print(json.dumps(_record_view(record), ensure_ascii=False))
        return 0
    print(f"{record.name} — {record.description or '(invalid)'}")
    print(f"id: {record.id}")
    print(
        f"source: {record.source}  status: {record.status.value}  "
        f"revision: {record.revision}"
    )
    if record.allowed_tools:
        print(f"allowed tools: {', '.join(record.allowed_tools)}")
    if record.error:
        print(f"error: {record.error}")
    return 0


def _record_view(record: SkillRecord) -> dict:
    return {
        "id": record.id,
        "name": record.name,
        "description": record.description,
        "scope": record.scope.value,
        "sourceRoot": record.source_root.value,
        "source": record.source,
        "status": record.status.value,
        "revision": record.revision,
        "allowedTools": list(record.allowed_tools),
        "byteSize": record.byte_size,
        "shadowedBy": record.shadowed_by,
        "error": record.error,
        "displayName": record.metadata.interface.display_name,
        "shortDescription": record.metadata.interface.short_description,
        "defaultPrompt": record.metadata.interface.default_prompt,
        "allowImplicitInvocation": record.metadata.allow_implicit_invocation,
        "deletable": record.deletable,
    }


if __name__ == "__main__":
    raise SystemExit(run())
