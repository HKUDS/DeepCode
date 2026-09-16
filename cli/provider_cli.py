"""Manage the same LLM connections used by CLI and Desktop."""

from __future__ import annotations

import argparse
import getpass
import json
import sys
import time

from core.application.errors import ApplicationError
from core.application.llm_configuration_service import LLMConfigurationService


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="deepcode provider",
        description="Configure LLM connections and discover their models.",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    command_parsers: list[argparse.ArgumentParser] = []
    list_parser = commands.add_parser(
        "list", help="List configured and built-in connections."
    )
    command_parsers.append(list_parser)

    add = commands.add_parser("set", help="Create or update a connection.")
    command_parsers.append(add)
    add.add_argument("id")
    add.add_argument("--template")
    add.add_argument("--label")
    add.add_argument("--adapter", choices=("openai_compat", "anthropic"))
    add.add_argument(
        "--protocol",
        choices=("auto", "openai_chat", "openai_responses", "anthropic_messages"),
    )
    add.add_argument("--auth", choices=("api_key", "none", "oauth"))
    add.add_argument(
        "--compat",
        type=json.loads,
        help="Typed compatibility object (JSON); unknown fields are rejected.",
    )
    add.add_argument(
        "--model-declarations",
        type=json.loads,
        help="Model declaration array (JSON), including capacities and capabilities.",
    )
    add.add_argument("--api-base")
    add.add_argument("--api-key-env")
    add.add_argument(
        "--api-key",
        action="store_true",
        help="Prompt securely for an API key (input is not echoed).",
    )
    add.add_argument("--clear-api-key", action="store_true")
    add.add_argument(
        "--catalog",
        choices=("auto", "openrouter", "openai", "anthropic", "manual"),
    )
    add.add_argument("--model", action="append")
    enabled = add.add_mutually_exclusive_group()
    enabled.add_argument("--enabled", action="store_true", dest="enabled")
    enabled.add_argument("--disabled", action="store_false", dest="enabled")
    add.set_defaults(enabled=None)

    remove = commands.add_parser("remove", help="Remove a named connection.")
    command_parsers.append(remove)
    remove.add_argument("id")

    test = commands.add_parser(
        "test",
        help="Check credentials/model discovery and optionally verify one model.",
    )
    command_parsers.append(test)
    test.add_argument("id")
    test.add_argument(
        "--agent",
        action="store_true",
        help="Verify streaming and a local tool round trip: up to 3 model requests, 90 seconds; no shell or file tools.",
    )
    test.add_argument(
        "--model",
        help="Send a minimal real inference request to this model.",
    )

    login = commands.add_parser(
        "login",
        help="Sign in to a saved OpenRouter OAuth connection (up to 5 minutes).",
    )
    command_parsers.append(login)
    login.add_argument("id")
    login.add_argument("--no-browser", action="store_true")
    logout = commands.add_parser(
        "logout",
        help="Disconnect a Provider account locally; remote key revocation stays in the Provider settings.",
    )
    command_parsers.append(logout)
    logout.add_argument("id")

    models = commands.add_parser("models", help="List models for a connection.")
    command_parsers.append(models)
    models.add_argument("id")
    models.add_argument("--refresh", action="store_true")
    parser.add_argument(
        "--json", action="store_true", help="Print machine-readable JSON."
    )
    for command in command_parsers:
        command.add_argument(
            "--json",
            action="store_true",
            default=argparse.SUPPRESS,
            help="Print machine-readable JSON.",
        )
    return parser


def run(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    service = LLMConfigurationService()
    try:
        if args.command == "list":
            result = service.list_connections()
        elif args.command == "set":
            value = {"id": args.id}
            for argument, field in (
                ("template", "template"),
                ("label", "label"),
                ("adapter", "adapter"),
                ("protocol", "protocol"),
                ("auth", "auth"),
                ("compat", "compat"),
                ("api_base", "apiBase"),
                ("api_key_env", "apiKeyEnv"),
                ("catalog", "modelCatalog"),
                ("model", "manualModels"),
                ("enabled", "enabled"),
            ):
                candidate = getattr(args, argument)
                if candidate is not None:
                    value[field] = candidate
            if args.model_declarations is not None:
                if args.model:
                    raise ValueError("Use either --model or --model-declarations")
                value["manualModels"] = args.model_declarations
            if args.clear_api_key:
                value["clearApiKey"] = True
            if args.api_key:
                value["apiKey"] = getpass.getpass("API key: ")
            result = service.upsert(value)
        elif args.command == "remove":
            result = service.remove(args.id)
        elif args.command == "login":
            flow = service.login_start(args.id, open_browser=not args.no_browser)
            try:
                if flow["authorizationUrl"]:
                    print(
                        flow["authorizationUrl"],
                        file=sys.stderr if args.json else sys.stdout,
                    )
                while flow["status"] in {"starting", "pending", "exchanging"}:
                    time.sleep(0.5)
                    flow = service.login_poll(flow["flowId"])
                result = flow
            except KeyboardInterrupt:
                service.login_cancel(flow["flowId"])
                return 130
            finally:
                service.close()
        elif args.command == "logout":
            result = service.logout(args.id)
        elif args.command == "test":
            result = service.test(
                args.id,
                model_id=args.model,
                **({"mode": "agent"} if args.agent else {}),
            )
        else:
            result = service.list_models(args.id, refresh=args.refresh)
    except (ApplicationError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif args.command in {"login", "logout"}:
        if args.command == "login":
            print(
                f"{result['status']}: {result.get('accountId') or result.get('error') or args.id}"
            )
        else:
            print("Disconnected locally. Remote key settings: " + result["manageUrl"])
    elif args.command == "list" or args.command in {"set", "remove"}:
        for connection in result["connections"]:
            state = "ready" if connection["configured"] else "credential needed"
            if not connection["enabled"]:
                state = "disabled"
            print(
                f"{connection['id']:<24} {connection['label']:<22} "
                f"{connection['adapter']:<14} {state}"
            )
    elif args.command == "test":
        stream = sys.stdout if result["ok"] else sys.stderr
        print(
            f"{result['status']}: {result['connectionId']} · "
            f"{result['modelCount']} models · {result['latencyMs']} ms",
            file=stream,
        )
        for stage in result["stages"]:
            print(
                f"  {stage['id']:<10} {stage['status']:<8} {stage['detail']}",
                file=stream,
            )
        if not result["ok"]:
            return 1
    else:
        suffix = " (stale cache)" if result["stale"] else ""
        print(f"{result['connectionId']} · {result['source']}{suffix}")
        for model in result["models"]:
            print(
                f"{model['id']:<48} context={model['contextWindow']:<9} "
                f"output={model['maxOutputTokens']}"
            )
        if result.get("error"):
            print(f"warning: {result['error']}", file=sys.stderr)
    return 1 if args.command == "login" and result["status"] != "authenticated" else 0


if __name__ == "__main__":
    raise SystemExit(run())
