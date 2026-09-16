"""Isolated browser acceptance host; deterministic Agent unless live is explicit."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path

from loguru import logger

logger.remove()


def prepare_live_config(destination: Path) -> None:
    from core.config import load_config
    from core.providers.profiles import ConnectionResolver

    config = load_config()
    defaults = config.agents.defaults
    connection, model = ConnectionResolver(config).resolve_selection()
    if not connection.is_usable:
        raise RuntimeError("Default live model connection is not usable")
    phase = config.resolve_phase("implementation")
    settings = defaults.model_dump(by_alias=True)
    settings.update(
        connection="live-validation",
        provider="auto",
        model=model,
        maxToolIterations=12,
        maxTokens=min(4096, phase.max_tokens),
        temperature=phase.temperature,
        reasoningEffort=phase.reasoning_effort,
    )
    protocol = os.environ.get("DEEPCODE_WEB_PROTOCOL", connection.protocol)
    with os.fdopen(
        os.open(destination, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600), "w"
    ) as stream:
        json.dump(
            {
                "agents": {"defaults": settings},
                "providers": {
                    "profiles": {
                        "live-validation": {
                            "template": connection.provider_name,
                            "protocol": protocol,
                            "auth": "api_key" if connection.api_key else "none",
                            "apiBase": connection.api_base,
                            "extraHeaders": connection.extra_headers,
                            "compat": connection.compat.model_dump(
                                by_alias=True, exclude_none=True
                            ),
                            "modelCatalog": "manual",
                            "manualModels": [
                                entry.model_dump(by_alias=True, exclude_none=True)
                                for entry in connection.manual_model_entries
                            ]
                            or [model],
                        }
                    }
                },
            },
            stream,
        )
    if connection.api_key:
        from core.providers.credentials import CredentialStore

        CredentialStore(destination.parent / "credentials.json").set(
            "live-validation", connection.api_key
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--prepare-live-config", action="store_true")
    args = parser.parse_args()
    if args.prepare_live_config:
        prepare_live_config(args.root)
    else:
        from app_server.service import serve
        from app_server.service_state import ServiceFiles
        from core.application import DeepCodeApplication

        if not args.live:
            from tests.application.test_cross_process_approval import _ApprovalFactory

            original_open = DeepCodeApplication.open

            def open_for_browser_test(*args, **kwargs):
                return original_open(
                    *args, session_factory=_ApprovalFactory(), **kwargs
                )

            DeepCodeApplication.open = staticmethod(open_for_browser_test)
        asyncio.run(serve(ServiceFiles(args.root / "state.sqlite3"), 0))
