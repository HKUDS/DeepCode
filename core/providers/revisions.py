"""Private, content-addressed provider routes referenced by admitted Turns."""

from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path

from core.file_lock import exclusive_file_lock
from core.private_storage import atomic_write_private_json, open_existing_private_file


def credential_digest(value: str | None) -> str:
    return hashlib.sha256((value or "").encode()).hexdigest()


class ProviderRevisionStore:
    """Keep route/header snapshots private; API key bodies are never stored here."""

    def __init__(self, directory: Path):
        self.directory = directory

    @staticmethod
    def _identity(value: dict) -> str:
        return hashlib.sha256(
            json.dumps(
                value,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
                allow_nan=False,
            ).encode()
        ).hexdigest()

    def put(self, value: dict) -> str:
        if len(json.dumps(value).encode()) > 65536:
            raise ValueError("Resolved provider configuration exceeds 64 KiB")
        identity = self._identity(value)
        path = self.directory / f"{identity}.json"
        if path.exists():
            self.get(identity)
            return identity
        with exclusive_file_lock(self.directory / "write.lock"):
            if path.exists():
                self.get(identity)
            else:
                atomic_write_private_json(path, value)
        return identity

    def get(self, identity: str) -> dict:
        if not re.fullmatch(r"[a-f0-9]{64}", identity):
            raise ValueError("Invalid provider revision identity")
        try:
            with os.fdopen(
                open_existing_private_file(self.directory / f"{identity}.json"),
                "r",
                encoding="utf-8",
            ) as stream:
                raw = stream.read(65537)
            value = json.loads(raw)
            if (
                len(raw) > 65536
                or not isinstance(value, dict)
                or self._identity(value) != identity
            ):
                raise ValueError("Invalid provider revision")
            return value
        except (OSError, ValueError) as exc:
            raise ValueError(
                "The private provider revision is missing or invalid; resubmit with the current configuration"
            ) from exc
