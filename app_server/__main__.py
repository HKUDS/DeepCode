"""Run the local App Server over stdin/stdout."""

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

from app_server.server import AppServer
from core.application.application import DeepCodeApplication

_trace_startup("imports-ready")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="DeepCode stdio App Server")
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


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
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
