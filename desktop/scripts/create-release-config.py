"""Generate the non-secret Tauri configuration used by signed release builds."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from urllib.parse import urlsplit


def _endpoint() -> str:
    configured = os.environ.get("TAURI_UPDATER_ENDPOINT", "").strip()
    if configured:
        endpoint = configured
    else:
        repository = os.environ.get("GITHUB_REPOSITORY", "").strip()
        if not repository or "/" not in repository:
            raise RuntimeError(
                "GITHUB_REPOSITORY or TAURI_UPDATER_ENDPOINT is required"
            )
        endpoint = (
            f"https://github.com/{repository}/releases/latest/download/latest.json"
        )
    parsed = urlsplit(endpoint)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.fragment
    ):
        raise RuntimeError(
            "the updater endpoint must be an absolute HTTPS URL without "
            "credentials or a fragment"
        )
    return endpoint


def _public_key() -> str:
    public_key = os.environ.get("TAURI_UPDATER_PUBLIC_KEY", "").strip()
    if len(public_key) < 32:
        raise RuntimeError("TAURI_UPDATER_PUBLIC_KEY is missing or invalid")
    return public_key


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    config = {
        "bundle": {
            "createUpdaterArtifacts": True,
            "macOS": {
                # Release builds infer the Developer ID identity from the
                # imported APPLE_CERTIFICATE instead of using local ad-hoc signing.
                "signingIdentity": None,
                "hardenedRuntime": True,
            },
        },
        "plugins": {
            "updater": {
                "pubkey": _public_key(),
                "endpoints": [_endpoint()],
                "windows": {"installMode": "passive"},
            }
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_suffix(args.output.suffix + ".tmp")
    temporary.write_text(
        json.dumps(config, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
