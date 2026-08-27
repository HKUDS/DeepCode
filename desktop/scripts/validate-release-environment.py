"""Fail closed when a production release is missing signing credentials."""

from __future__ import annotations

import argparse
import os

COMMON = ("TAURI_SIGNING_PRIVATE_KEY", "TAURI_UPDATER_PUBLIC_KEY")
PLATFORM_REQUIREMENTS = {
    "macos": (
        "APPLE_CERTIFICATE",
        "APPLE_CERTIFICATE_PASSWORD",
        "APPLE_ID",
        "APPLE_PASSWORD",
        "APPLE_TEAM_ID",
    ),
    "windows": (
        "WINDOWS_CERTIFICATE",
        "WINDOWS_CERTIFICATE_PASSWORD",
    ),
    "linux": (
        "LINUX_GPG_PRIVATE_KEY",
        "LINUX_GPG_KEY_ID",
        "APPIMAGETOOL_SIGN_PASSPHRASE",
    ),
}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--platform", choices=tuple(PLATFORM_REQUIREMENTS), required=True
    )
    args = parser.parse_args()
    required = COMMON + PLATFORM_REQUIREMENTS[args.platform]
    missing = [name for name in required if not os.environ.get(name, "").strip()]
    if missing:
        raise RuntimeError(
            f"{args.platform} release credentials are incomplete: " + ", ".join(missing)
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
