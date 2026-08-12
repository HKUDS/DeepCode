"""Manage generic MCP clients or expose DeepCode itself over MCP stdio."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from core.application.application import DeepCodeApplication
from core.application.errors import ApplicationError
from core.application.views import (
    mcp_inventory_view,
    mcp_oauth_flow_view,
    mcp_preset_inventory_view,
    mcp_probe_view,
)
from core.domain.project import TrustState


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="deepcode mcp",
        description="Configure MCP clients. Use 'serve' to expose DeepCode over stdio.",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    serve = commands.add_parser("serve", help="Expose DeepCode as an MCP stdio server.")
    serve.add_argument("server_args", nargs=argparse.REMAINDER)

    listing = commands.add_parser("list", help="List effective MCP client servers.")
    _workspace_arguments(listing, allow_trust=False)
    listing.add_argument("--json", action="store_true")

    presets = commands.add_parser("presets", help="List bundled MCP templates.")
    _workspace_arguments(presets, allow_trust=False)
    presets.add_argument("--json", action="store_true")

    probe = commands.add_parser("test", help="Perform a real MCP handshake.")
    probe.add_argument("name")
    _workspace_arguments(probe, allow_trust=False)
    probe.add_argument("--json", action="store_true")

    login = commands.add_parser("login", help="Authorize an OAuth MCP server.")
    login.add_argument("name")
    login.add_argument("--no-open", action="store_true")
    _workspace_arguments(login, allow_trust=False)
    login.add_argument("--json", action="store_true")

    logout = commands.add_parser("logout", help="Remove MCP OAuth credentials.")
    logout.add_argument("name")
    _workspace_arguments(logout, allow_trust=False)
    logout.add_argument("--json", action="store_true")

    for action in ("enable", "disable"):
        toggle = commands.add_parser(
            action,
            help=f"{action.title()} one configured MCP server.",
        )
        toggle.add_argument("name")
        _workspace_arguments(toggle, allow_trust=False)
        toggle.add_argument("--json", action="store_true")

    add = commands.add_parser(
        "add",
        help="Add a bundled preset, or add/replace a custom MCP server.",
    )
    add.add_argument("name")
    endpoint = add.add_mutually_exclusive_group()
    endpoint.add_argument("--url", help="Streamable HTTP endpoint.")
    endpoint.add_argument(
        "--command",
        dest="stdio_command",
        nargs=argparse.REMAINDER,
        help="stdio command followed by its arguments; keep this option last.",
    )
    add.add_argument(
        "--transport",
        choices=("streamableHttp", "sse"),
        default="streamableHttp",
        help="Transport used with --url.",
    )
    add.add_argument("--oauth", action="store_true")
    add.add_argument(
        "--enable",
        action="store_true",
        help="Enable immediately; new servers are disabled by default.",
    )
    add.add_argument("--scope", choices=("user", "project"), default="user")
    _workspace_arguments(add, allow_trust=True)
    add.add_argument("--cwd")
    add.add_argument("--env", action="append", default=[], metavar="NAME=VALUE")
    add.add_argument("--env-var", action="append", default=[])
    add.add_argument("--required-env-var", action="append", default=[])
    add.add_argument(
        "--header-env",
        action="append",
        default=[],
        metavar="HEADER=ENV",
    )
    add.add_argument(
        "--url-param-env",
        action="append",
        default=[],
        metavar="PARAM=ENV",
    )
    add.add_argument(
        "--credential-env",
        action="append",
        default=[],
        metavar="NAME=CONNECTION",
        help="Map a child environment variable to a stored provider credential.",
    )
    add.add_argument("--enabled-tool", action="append", default=None)
    add.add_argument("--disabled-tool", action="append", default=[])
    add.add_argument("--startup-timeout", type=float, default=10.0)
    add.add_argument("--tool-timeout", type=float, default=60.0)
    add.add_argument(
        "--approval",
        choices=("auto", "prompt", "writes", "approve"),
        default="auto",
    )
    add.add_argument("--required", action="store_true")
    add.add_argument("--json", action="store_true")

    remove = commands.add_parser("remove", help="Remove one MCP client server.")
    remove.add_argument("name")
    remove.add_argument("--scope", choices=("user", "project"), default="user")
    _workspace_arguments(remove, allow_trust=True)
    remove.add_argument("--json", action="store_true")
    return parser


def _workspace_arguments(parser: argparse.ArgumentParser, *, allow_trust: bool) -> None:
    parser.add_argument("--workspace", help="Project whose MCP layer is inspected.")
    if allow_trust:
        parser.add_argument(
            "--trust",
            action="store_true",
            help="Persist trust before mutating project-scoped configuration.",
        )


def run(argv: list[str] | None = None) -> int:
    values = list(argv or [])
    if not values:
        from cli.mcp_server import main as serve

        return int(serve([]) or 0)
    args = _parser().parse_args(values)
    if args.command == "serve":
        from cli.mcp_server import main as serve

        return int(serve(args.server_args) or 0)

    application = DeepCodeApplication.open(
        host_surface="mcp-cli",
        run_automation_scheduler=False,
    )
    try:
        exit_code = 0
        project_id = _project_id(
            application,
            getattr(args, "workspace", None),
            trust=bool(getattr(args, "trust", False)),
        )
        if args.command == "list":
            inventory = application.mcp.list(project_id)
            result = mcp_inventory_view(inventory)
        elif args.command == "presets":
            result = mcp_preset_inventory_view(application.mcp.list_presets(project_id))
        elif args.command in {"enable", "disable"}:
            result = mcp_inventory_view(
                application.mcp.set_enabled(
                    args.name,
                    enabled=args.command == "enable",
                    project_id=project_id,
                )
            )
        elif args.command == "test":
            result = mcp_probe_view(
                application.mcp.probe(args.name, project_id=project_id)
            )
            exit_code = 0 if result["ok"] else 1
        elif args.command == "login":
            flow = application.mcp.oauth_start(
                args.name,
                project_id=project_id,
                open_browser=not args.no_open,
            )
            if not args.json and flow.authorization_url:
                print(
                    f"Open this URL to authorize {flow.name}:\n{flow.authorization_url}"
                )
                print("Waiting for authorization...")
            flow = application.mcp.oauth_wait(flow.flow_id)
            result = mcp_oauth_flow_view(flow)
            exit_code = 0 if result["status"] == "authenticated" else 1
        elif args.command == "logout":
            result = {
                "removed": application.mcp.oauth_logout(
                    args.name,
                    project_id=project_id,
                )
            }
        elif args.command == "add":
            if args.url is None and args.stdio_command is None:
                if args.scope != "user":
                    raise ValueError("bundled presets can only be added at user scope")
                result = mcp_inventory_view(
                    application.mcp.add_preset(
                        args.name,
                        project_id=project_id,
                        enabled=args.enable,
                    )
                )
            elif args.scope == "project" and project_id is None:
                raise ValueError("--workspace is required for project scope")
            else:
                result = mcp_inventory_view(
                    application.mcp.upsert(
                        name=args.name,
                        scope=args.scope,
                        project_id=project_id,
                        patch=_server_patch(args),
                    )
                )
        else:
            if args.scope == "project" and project_id is None:
                raise ValueError("--workspace is required for project scope")
            result = mcp_inventory_view(
                application.mcp.remove(
                    name=args.name,
                    scope=args.scope,
                    project_id=project_id,
                )
            )
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        elif args.command == "presets":
            _print_presets(result)
        elif args.command == "test":
            _print_probe(result)
        elif args.command == "login":
            _print_oauth(result)
        elif args.command == "logout":
            print(
                "OAuth credentials removed."
                if result["removed"]
                else "No OAuth credentials were stored."
            )
        else:
            _print_inventory(result)
        return exit_code
    except (ApplicationError, OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    finally:
        application.close()


def _project_id(
    application: DeepCodeApplication,
    workspace: str | None,
    *,
    trust: bool,
) -> str | None:
    if workspace is None:
        return None
    project = application.projects.add(
        str(Path(workspace).expanduser().resolve(strict=True))
    )
    if trust and project.trust_state is not TrustState.TRUSTED:
        project = application.projects.update(
            project.id, trust_state=TrustState.TRUSTED
        )
    return project.id


def _server_patch(args: argparse.Namespace) -> dict[str, Any]:
    environment = _pairs(args.env, option="--env")
    credentials = {
        name: {"credentialRef": f"provider:{connection}"}
        for name, connection in _pairs(
            args.credential_env,
            option="--credential-env",
        ).items()
    }
    common: dict[str, Any] = {
        "enabled": args.enable,
        "required": args.required,
        "startupTimeoutSeconds": args.startup_timeout,
        "toolTimeoutSeconds": args.tool_timeout,
        "approvalMode": args.approval,
        "disabledTools": args.disabled_tool,
    }
    if args.enabled_tool is not None:
        common["enabledTools"] = args.enabled_tool
    if args.url:
        return {
            "type": args.transport,
            "url": args.url,
            **({"auth": "oauth"} if args.oauth else {}),
            "envHttpHeaders": _pairs(args.header_env, option="--header-env"),
            "envUrlParams": _pairs(args.url_param_env, option="--url-param-env"),
            **common,
        }
    command = list(args.stdio_command or [])
    if command and command[0] == "--":
        command.pop(0)
    if not command:
        raise ValueError("--command requires an executable")
    return {
        "type": "stdio",
        "command": command[0],
        "args": command[1:],
        "cwd": args.cwd,
        "env": environment,
        "envVars": args.env_var,
        "requiredEnvVars": args.required_env_var,
        "credentialEnv": credentials,
        **common,
    }


def _pairs(values: list[str], *, option: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for value in values:
        if "=" not in value:
            raise ValueError(f"{option} expects NAME=VALUE")
        name, item = value.split("=", 1)
        if not name.strip() or not item:
            raise ValueError(f"{option} expects non-empty NAME=VALUE")
        result[name.strip()] = item
    return result


def _print_inventory(result: dict[str, Any]) -> None:
    servers = result["servers"]
    if not servers:
        print("No MCP client servers configured for this workspace.")
        return
    print(f"{'CONFIG':<20} {'AUTH':<16} {'RUNTIME':<12} {'SCOPE':<9} NAME")
    for server in servers:
        print(
            f"{server['configurationState']:<20} {server['authState']:<16} "
            f"{server['runtimeState']:<12} {server['source']:<9} {server['name']}"
        )


def _print_presets(result: dict[str, Any]) -> None:
    print(f"{'STATUS':<14} {'CATEGORY':<14} {'TRANSPORT':<16} NAME")
    for preset in result["presets"]:
        status = "configured" if preset["configured"] else "available"
        print(
            f"{status:<14} {preset['category']:<14} "
            f"{preset['transport']:<16} {preset['id']}"
        )


def _print_probe(result: dict[str, Any]) -> None:
    if result["ok"]:
        print(
            f"Test passed for {result['name']}: {result['toolCount']} tools, "
            f"{result['resourceCount']} resources, {result['promptCount']} prompts "
            f"in {result['elapsedSeconds']:.3f}s. The test connection is closed."
        )
    else:
        print(f"Connection failed for {result['name']}: {result['error']}")


def _print_oauth(result: dict[str, Any]) -> None:
    if result["status"] == "authenticated":
        print(f"Authorized {result['name']}.")
    else:
        print(f"Authorization {result['status']}: {result['error'] or 'not completed'}")


if __name__ == "__main__":
    raise SystemExit(run(sys.argv[1:]))
