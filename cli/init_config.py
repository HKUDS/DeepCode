"""Create DeepCode's user-level configuration directory.

The default path is ``$DEEPCODE_HOME`` or ``~/.deepcode``.  Initialization is
safe to run from any workspace: it never copies a repository configuration or
credentials implicitly.  Users can explicitly import a trusted JSON file with
``--from`` and configure secrets separately through Desktop Settings or
``deepcode provider set``.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import uuid
from pathlib import Path
from typing import Any

from core.config import (
    DEEPCODE_HOME_ENV,
    DeepCodeConfig,
    deepcode_home,
    home_config_path,
    load_config,
)
from core.private_storage import (
    ensure_private_directory,
    ensure_private_file,
    open_private_file,
)
from core.providers.credentials import CredentialStore
from core.providers.profiles import ConnectionResolver

_DEFAULT_CONFIG: dict[str, Any] = {
    "security": {
        "accessPreset": "ask",
    }
}


def _seed(from_path: str | None) -> tuple[dict[str, Any], str]:
    if from_path is None:
        return _DEFAULT_CONFIG, "safe defaults"

    source = Path(from_path).expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(f"--from path does not exist: {source}")
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON in {source}: {exc}") from exc
    if not isinstance(payload, dict):
        raise TypeError(f"configuration in {source} must be a JSON object")
    DeepCodeConfig.model_validate(payload)
    return payload, str(source)


def _has_cloud_credential() -> bool:
    try:
        config = load_config(config_path=home_config_path())
        resolver = ConnectionResolver(config, CredentialStore())
        return any(
            connection.enabled and bool(connection.api_key)
            for connection in resolver.list_connections()
        )
    except Exception:  # noqa: BLE001 - status hint must not break initialization
        return False


def _launch_hint() -> str:
    system = platform.system()
    home = deepcode_home()
    custom = os.environ.get(DEEPCODE_HOME_ENV)
    if custom:
        return f"Configuration directory: {home} (from {DEEPCODE_HOME_ENV})."
    if system == "Windows":
        return (
            f"Configuration directory: {home}. To relocate it, set "
            f"{DEEPCODE_HOME_ENV}, for example: "
            f"setx {DEEPCODE_HOME_ENV} D:\\deepcode"
        )
    return (
        f"Configuration directory: {home}. To relocate it, export "
        f'{DEEPCODE_HOME_ENV}, for example: export {DEEPCODE_HOME_ENV}="'
        '$HOME/.config/deepcode"'
    )


def _replace_private_json(path: Path, payload: dict[str, Any]) -> None:
    ensure_private_directory(path.parent)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    descriptor = open_private_file(
        temporary,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL,
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        ensure_private_file(path)
    finally:
        temporary.unlink(missing_ok=True)


def _ready_message() -> str:
    if _has_cloud_credential():
        return "An LLM credential is configured; DeepCode is ready to run."
    return (
        "Next: add an LLM connection in Desktop Settings, or run "
        "`deepcode provider set openai --api-key`. Local providers do not "
        "require a cloud API key."
    )


def run(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="deepcode init",
        description="Initialize DeepCode's private user configuration.",
    )
    parser.add_argument(
        "--from",
        dest="from_path",
        metavar="PATH",
        default=None,
        help="Explicitly import a trusted DeepCode JSON configuration.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Replace an existing config after keeping a private .bak copy.",
    )
    args = parser.parse_args(argv)

    ensure_private_directory(deepcode_home())
    destination = home_config_path()
    if destination.is_file() and not args.force:
        ensure_private_file(destination)
        print(f"Already initialized: {destination}")
        print(_ready_message())
        print(_launch_hint())
        print("(Use --force only when you intend to replace this configuration.)")
        return 0

    try:
        payload, source_label = _seed(args.from_path)
    except (FileNotFoundError, TypeError, ValueError) as exc:
        print(f"Error: {exc}")
        return 1

    if destination.is_file():
        backup = destination.with_suffix(destination.suffix + ".bak")
        try:
            current = json.loads(destination.read_text(encoding="utf-8"))
            if not isinstance(current, dict):
                raise TypeError("existing configuration must be a JSON object")
            _replace_private_json(backup, current)
        except (OSError, TypeError, json.JSONDecodeError) as exc:
            print(f"Error: could not back up existing configuration: {exc}")
            return 1
        print(f"Backed up existing configuration to {backup}")

    _replace_private_json(destination, payload)
    print(f"Initialized {destination}")
    print(f"  source: {source_label}")
    print(_ready_message())
    print(_launch_hint())
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
