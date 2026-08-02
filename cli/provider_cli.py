"""Manage the same LLM connections used by CLI and Desktop."""

from __future__ import annotations

import argparse
import getpass
import json
import sys

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

    test = commands.add_parser("test", help="Test authentication/model discovery.")
    command_parsers.append(test)
    test.add_argument("id")

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
                ("api_base", "apiBase"),
                ("api_key_env", "apiKeyEnv"),
                ("catalog", "modelCatalog"),
                ("model", "manualModels"),
                ("enabled", "enabled"),
            ):
                candidate = getattr(args, argument)
                if candidate is not None:
                    value[field] = candidate
            if args.clear_api_key:
                value["clearApiKey"] = True
            if args.api_key:
                value["apiKey"] = getpass.getpass("API key: ")
            result = service.upsert(value)
        elif args.command == "remove":
            result = service.remove(args.id)
        elif args.command == "test":
            result = service.test(args.id)
        else:
            result = service.list_models(args.id, refresh=args.refresh)
    except (ApplicationError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
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
        if result["ok"]:
            print(
                f"ok: {result['connectionId']} · {result['modelCount']} models · "
                f"{result['latencyMs']} ms"
            )
        else:
            print(f"failed: {result['error']}", file=sys.stderr)
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
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
