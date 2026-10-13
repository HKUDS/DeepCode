"""Open the packaged browser client using an instance-local one-time link."""

from __future__ import annotations

import argparse
import json
import sys
import webbrowser
from pathlib import Path

from app_server.service_client import (
    ServiceClient,
    ServiceOperationError,
    ServiceUnavailable,
)
from app_server.service_state import ServiceFiles
from app_server.web_surface import read_web_build
from cli.service_cli import start_service
from core.persistence.database import default_database_path


def run(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="deepcode web")
    parser.add_argument("--database", type=Path, default=default_database_path())
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument(
        "--no-open",
        action="store_true",
        help="Print the short-lived link without opening a browser",
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    files = ServiceFiles(args.database)
    if not files.running() and read_web_build() is None:
        parser.error(
            "Web assets are missing or incompatible. Install a complete release, "
            "or run npm run build:web in desktop/."
        )
    try:
        status = start_service(files, port=args.port)
        ticket = ServiceClient(files).call(
            "auth/issue", instance_id=status["instanceId"]
        )
    except (ServiceUnavailable, ServiceOperationError, OSError) as exc:
        if args.json:
            print(json.dumps({"error": str(exc)}))
        else:
            print(f"error: {exc}", file=sys.stderr)
        return 1
    url = status["url"] + "/#ticket=" + ticket["ticket"]
    print(
        json.dumps(
            {
                "url": url,
                "expiresIn": ticket["expiresIn"],
                "instanceId": status["instanceId"],
            }
        )
        if args.json
        else f"Open within {ticket['expiresIn']} seconds (one use):\n{url}"
    )
    if not args.no_open:
        webbrowser.open(url)
    return 0
