"""Manage local Plugin registrations consumed by every DeepCode surface."""

from __future__ import annotations

import argparse
import json
import sys

from core.application.plugin_service import PluginDiscovery, PluginInfo, PluginService
from core.application.errors import ApplicationError
from core.plugins.host import LocalPluginHost
from core.skills.host import SkillWorkspaceRegistry


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="deepcode plugin",
        description=(
            "Register local Plugins whose Skills join the shared Skill runtime."
        ),
    )
    parser.add_argument("--json", action="store_true", help="Emit JSON output.")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("list", help="List registered Plugins and validation errors.")
    add = commands.add_parser("add", help="Register a local Plugin directory.")
    add.add_argument("path")
    for action in ("enable", "disable"):
        command = commands.add_parser(action, help=f"{action.title()} a Plugin.")
        command.add_argument("plugin_id")
    remove = commands.add_parser(
        "remove",
        help="Unregister a Plugin without deleting its source directory.",
    )
    remove.add_argument("plugin_id")
    remove.add_argument("--yes", action="store_true", help="Skip confirmation.")
    return parser


def run(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    skill_hosts = SkillWorkspaceRegistry()
    host = LocalPluginHost(skill_hosts, monitor=False)
    service = PluginService(host)
    try:
        if args.command == "list":
            return _print_discovery(service.list(), as_json=args.json)
        if args.command == "add":
            return _print_discovery(service.add(args.path), as_json=args.json)
        if args.command in {"enable", "disable"}:
            return _print_discovery(
                service.set_enabled(
                    args.plugin_id,
                    enabled=args.command == "enable",
                ),
                as_json=args.json,
            )
        if args.command == "remove":
            current = next(
                (
                    plugin
                    for plugin in service.list().plugins
                    if plugin.id == args.plugin_id or plugin.name == args.plugin_id
                ),
                None,
            )
            if current is None:
                raise ValueError(f"Plugin is not registered: {args.plugin_id}")
            if not args.yes:
                if not sys.stdin.isatty():
                    raise ValueError(
                        "remove requires --yes when stdin is not interactive"
                    )
                answer = input(
                    f"Unregister Plugin {current.name!r}? Source files stay in place. [y/N] "
                )
                if answer.strip().lower() not in {"y", "yes"}:
                    print("cancelled")
                    return 1
            removed = service.remove(current.id)
            if args.json:
                print(
                    json.dumps(
                        {"removed": True, "plugin": _plugin_view(removed)},
                        ensure_ascii=False,
                    )
                )
            else:
                print(f"unregistered {removed.name} ({removed.id}); source files kept")
            return 0
    except (ApplicationError, OSError, ValueError) as exc:
        if args.json:
            print(json.dumps({"error": str(exc)}, ensure_ascii=False))
        else:
            print(f"error: {exc}", file=sys.stderr)
        return 2
    finally:
        service.close()
        skill_hosts.close()
    return 2


def _print_discovery(discovery: PluginDiscovery, *, as_json: bool) -> int:
    if as_json:
        print(
            json.dumps(
                {
                    "revision": discovery.revision,
                    "plugins": [_plugin_view(plugin) for plugin in discovery.plugins],
                    "diagnostics": [
                        _diagnostic_view(item) for item in discovery.diagnostics
                    ],
                },
                ensure_ascii=False,
            )
        )
        return 0
    for diagnostic in discovery.diagnostics:
        print(
            f"{diagnostic.severity}: {diagnostic.message}",
            file=sys.stderr,
        )
    if not discovery.plugins:
        print("No Plugins registered.")
        return 0
    print(f"{'STATUS':<10} {'VERSION':<14} {'NAME':<28} PATH")
    for plugin in discovery.plugins:
        print(
            f"{plugin.status:<10} {(plugin.version or '-'):<14} "
            f"{plugin.name[:28]:<28} {plugin.path}"
        )
        if plugin.error:
            print(f"  error: {plugin.error}")
    return 0


def _plugin_view(plugin: PluginInfo) -> dict:
    return {
        "id": plugin.id,
        "name": plugin.name,
        "version": plugin.version,
        "description": plugin.description,
        "status": plugin.status,
        "enabled": plugin.enabled,
        "source": plugin.source,
        "path": plugin.path,
        "schema": plugin.schema,
        "manifestPath": plugin.manifest_path,
        "manifestRevision": plugin.manifest_revision,
        "components": [
            {
                "kind": component.kind,
                "status": component.status,
                "resource": component.resource,
                "itemCount": component.item_count,
                "diagnostics": [
                    _diagnostic_view(item) for item in component.diagnostics
                ],
            }
            for component in plugin.components
        ],
        "diagnostics": [_diagnostic_view(item) for item in plugin.diagnostics],
        "error": plugin.error,
    }


def _diagnostic_view(diagnostic) -> dict:
    return {
        "code": diagnostic.code,
        "severity": diagnostic.severity,
        "message": diagnostic.message,
        "component": diagnostic.component,
        "resource": diagnostic.resource,
    }


if __name__ == "__main__":
    raise SystemExit(run())
