"""Attach stdin/stdout to the local DeepCode service."""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path

_PROCESS_STARTED = time.perf_counter()


def _trace_startup(stage: str) -> None:
    if os.environ.get("DEEPCODE_STARTUP_TRACE") == "1":
        elapsed = time.perf_counter() - _PROCESS_STARTED
        print(f"startup {elapsed:.3f}s {stage}", file=sys.stderr, flush=True)


_trace_startup("entrypoint")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="DeepCode stdio App Server")
    parser.add_argument(
        "--service",
        action="store_true",
        help="manage the background service (accepts service subcommands)",
    )
    parser.add_argument(
        "--serve",
        action="store_true",
        help="run the loopback Web service (accepts serve options)",
    )
    parser.add_argument(
        "--web",
        action="store_true",
        help="start/connect to the service and open its browser client (accepts web options)",
    )
    parser.add_argument(
        "--managed-config",
        type=Path,
        help="read a private supervisor launch configuration",
    )
    parser.add_argument(
        "--database",
        type=Path,
        default=None,
        help="override the default ~/.deepcode/state/deepcode.sqlite3",
    )
    parser.add_argument(
        "--verify-runtime",
        action="store_true",
        help="import packaged Agent, provider, MCP client, and Paper kernels, then exit",
    )
    return parser


def main(argv: list[str] | None = None, *, shared_service: bool = True) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if "--service" in arguments:
        from cli.service_cli import run as service_cli_main

        return service_cli_main([value for value in arguments if value != "--service"])
    if "--serve" in arguments:
        from app_server.service import main as serve_main

        return serve_main([value for value in arguments if value != "--serve"])
    if "--web" in arguments:
        from cli.web_cli import run as web_main

        return web_main([value for value in arguments if value != "--web"])
    args = build_parser().parse_args(arguments)
    if args.managed_config is not None:
        from app_server.managed_entry import run as managed_main

        return managed_main(args.managed_config)
    if args.verify_runtime:
        from app_server.runtime_probe import verify_runtime

        print(json.dumps(verify_runtime(), separators=(",", ":")))
        return 0
    logging.basicConfig(
        stream=sys.stderr,
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    source, protocol_sink = isolate_protocol_streams()
    if shared_service:
        from app_server.service_state import ServiceFiles
        from app_server.stdio_relay import serve_relay
        from core.persistence.database import default_database_path

        return serve_relay(
            ServiceFiles(args.database or default_database_path()),
            source,
            protocol_sink,
        )
    from app_server.server import AppServer
    from core.application.application import DeepCodeApplication

    _trace_startup("opening-application")
    application = DeepCodeApplication.open(
        args.database,
        host_surface="app_server",
        run_automation_scheduler=True,
    )
    _trace_startup("application-ready")
    return AppServer(application).serve(source, protocol_sink)


def isolate_protocol_streams():
    """Reserve the original stdout buffer for RPC and route all prints to stderr."""

    source = sys.stdin.buffer
    protocol_sink = sys.stdout.buffer
    sys.stdout = sys.stderr
    return source, protocol_sink


if __name__ == "__main__":
    raise SystemExit(main())
