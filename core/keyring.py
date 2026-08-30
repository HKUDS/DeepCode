"""P0-3: system keyring integration for API keys (Codex lesson).

Codex stores credentials in the system keyring (``keyring-store/``) instead
of plaintext config. DeepCode's config resolves ``${VAR}`` from env → .env;
this module adds a third fallback: the OS credential store (Windows
Credential Manager / macOS Keychain / Linux Secret Service) via the `keyring`
package, plus a portable JSON fallback under ``~/.deepcode/keyring.json``.

Design rules:

* **Fallback, never primary.** Env and .env keep priority — keyring is the
  last resort before failing. No behavior change for existing configs.
* **Opt-out env.** ``DEEPCODE_KEYRING=0`` disables (both backends).
* **Fail-soft.** Missing `keyring` package, unavailable OS backend, or any
  error → returns None (resolution falls through to the existing error).
* **Namespaced.** Keys are stored as ``deepcode:<NAME>`` so they never
  collide with other apps' entries in a shared keyring.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from loguru import logger

# Service name used for all keyring entries.
_KEYRING_SERVICE = "deepcode"
# Portable fallback file: {"NAME": "value"} JSON (not encrypted — same
# trust level as .env; still better than committing secrets).
_KEYRING_FILE = Path.home() / ".deepcode" / "keyring.json"


def keyring_enabled() -> bool:
    """Whether keyring lookup is on (env: ``DEEPCODE_KEYRING``; default on)."""
    value = os.environ.get("DEEPCODE_KEYRING", "").strip().lower()
    if not value:
        return True
    return value not in {"0", "false", "off", "no"}


def _keyring_pkg_get(service: str, username: str) -> str | None:
    """System-keyring lookup via the ``keyring`` package (best-effort)."""
    try:
        import keyring as _kr  # type: ignore[import-not-found]

        value = _kr.get_password(service, username)
        return value if isinstance(value, str) and value else None
    except Exception:  # noqa: BLE001 - missing package / no backend / errors
        return None


def _keyring_file_get(username: str) -> str | None:
    """Portable JSON-file fallback (best-effort, cached per call)."""
    try:
        if not _KEYRING_FILE.is_file():
            return None
        data = json.loads(_KEYRING_FILE.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return None
        value = data.get(username)
        return value if isinstance(value, str) and value else None
    except Exception:  # noqa: BLE001
        return None


def keyring_get(name: str) -> str | None:
    """Look up a secret by name (env var name) in the keyring.

    Tries the system keyring first, then the portable JSON file. Returns
    None when disabled, unsupported, or not found — never raises.
    """
    if not keyring_enabled():
        return None
    if not name or not name.strip():
        return None
    # System keyring (Windows Credential Manager / Keychain / Secret Service).
    try:
        value = _keyring_pkg_get(_KEYRING_SERVICE, name)
    except Exception:  # noqa: BLE001 - fail-soft, never raises
        value = None
    if value is not None:
        return value
    # Portable fallback file.
    try:
        return _keyring_file_get(name)
    except Exception:  # noqa: BLE001 - fail-soft, never raises
        return None


def _keyring_pkg_set(service: str, username: str, value: str) -> bool:
    """System-keyring store via the ``keyring`` package (best-effort)."""
    try:
        import keyring as _kr  # type: ignore[import-not-found]

        _kr.set_password(service, username, value)
        return True
    except Exception:  # noqa: BLE001
        return False


def keyring_set(name: str, value: str) -> bool:
    """Store a secret in the keyring (system first, JSON file fallback).

    Returns True on success. Used by `deepcode keyring set`-style tooling;
    resolution itself only ever reads.
    """
    if not keyring_enabled() or not name or value is None:
        return False
    if _keyring_pkg_set(_KEYRING_SERVICE, name, value):
        return True
    try:
        _KEYRING_FILE.parent.mkdir(parents=True, exist_ok=True)
        data: dict[str, Any] = {}
        if _KEYRING_FILE.is_file():
            try:
                data = json.loads(_KEYRING_FILE.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                data = {}
            if not isinstance(data, dict):
                data = {}
        data[name] = value
        _KEYRING_FILE.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return True
    except Exception:  # noqa: BLE001
        logger.debug("keyring_set: JSON fallback write failed for {}", name)
        return False


__all__ = ["keyring_enabled", "keyring_get", "keyring_set"]
