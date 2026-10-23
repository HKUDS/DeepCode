"""Configuration loader for the Feishu approval bridge.

All secrets come from environment variables (or a .env file) so nothing
sensitive is ever committed.  Placeholders of the form ``${ENV_VAR}`` are
resolved from the environment.

Environment variables
---------------------
``FEISHU_APP_ID``            Feishu self-built app ID.
``FEISHU_APP_SECRET``        Feishu self-built app secret.
``FEISHU_OPEN_IDS``          Comma-separated ``open_id`` allowlist (the only
                             users whose card clicks count as a decision).
``FEISHU_APPROVER_TIMEOUT``  Seconds before an unanswered prompt is auto-resolved
                             (default ``600``; ``0`` disables the timeout).
``FEISHU_APPROVER_TIMEOUT_ACTION``
                             What "auto-resolve" means: ``deny`` (fail-closed,
                             the safe default), ``approve_once``, or
                             ``approve_session`` (let the run continue and stop
                             re-asking for the same tool in this session).
``FEISHU_CHAT_IDS``          Optional comma-separated ``chat_id`` targets to
                             push prompt cards to.  Leave empty to DM each
                             allowlisted user instead.
``DEEPCODE_DATABASE``        Optional path to the DeepCode SQLite database; the
                             bridge auto-discovers the app_server from it.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field


def _split_ids(raw: str | None) -> tuple[str, ...]:
    if not raw:
        return ()
    return tuple(
        part.strip() for part in raw.replace("\n", ",").split(",") if part.strip()
    )


def _coerce_timeout(raw: str | None) -> int:
    if raw is None or raw.strip() == "":
        return 600
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(
            f"FEISHU_APPROVER_TIMEOUT must be an integer, got {raw!r}"
        ) from exc
    if value < 0:
        raise ValueError("FEISHU_APPROVER_TIMEOUT must be >= 0")
    return value


# Accepted FEISHU_APPROVER_TIMEOUT_ACTION values -> canonical names.
_TIMEOUT_ACTIONS: dict[str, str] = {
    "deny": "deny",
    "approve_once": "approve_once",
    "approve_session": "approve_session",
}


def _coerce_timeout_action(raw: str | None) -> str:
    if raw is None or raw.strip() == "":
        return "deny"
    value = raw.strip().lower()
    if value not in _TIMEOUT_ACTIONS:
        raise ValueError(
            "FEISHU_APPROVER_TIMEOUT_ACTION must be one of "
            f"{sorted(_TIMEOUT_ACTIONS)}, got {raw!r}"
        )
    return _TIMEOUT_ACTIONS[value]


@dataclass(frozen=True)
class BridgeSettings:
    """Validated configuration for the Feishu approval bridge."""

    app_id: str
    app_secret: str
    open_ids: tuple[str, ...] = field(default_factory=tuple)
    timeout_seconds: int = 600
    timeout_action: str = "deny"
    chat_ids: tuple[str, ...] = field(default_factory=tuple)
    database_path: str | None = None

    def validate(self) -> None:
        """Raise ``ValueError`` when required settings are missing or empty."""
        if not self.app_id:
            raise ValueError("FEISHU_APP_ID is required")
        if not self.app_secret:
            raise ValueError("FEISHU_APP_SECRET is required")
        if not self.open_ids:
            raise ValueError(
                "FEISHU_OPEN_IDS is required (comma-separated open_id allowlist); "
                "without it no card click can produce a decision"
            )
        if self.timeout_action not in _TIMEOUT_ACTIONS.values():
            raise ValueError(
                "FEISHU_APPROVER_TIMEOUT_ACTION must be one of "
                f"{sorted(_TIMEOUT_ACTIONS.values())}, got {self.timeout_action!r}"
            )


def load_settings(environ: dict[str, str] | None = None) -> BridgeSettings:
    """Build :class:`BridgeSettings` from the environment.

    ``environ`` defaults to ``os.environ``; inject a dict in tests.
    """
    env = os.environ if environ is None else environ
    settings = BridgeSettings(
        app_id=env.get("FEISHU_APP_ID", "").strip(),
        app_secret=env.get("FEISHU_APP_SECRET", "").strip(),
        open_ids=_split_ids(env.get("FEISHU_OPEN_IDS")),
        timeout_seconds=_coerce_timeout(env.get("FEISHU_APPROVER_TIMEOUT")),
        timeout_action=_coerce_timeout_action(
            env.get("FEISHU_APPROVER_TIMEOUT_ACTION")
        ),
        chat_ids=_split_ids(env.get("FEISHU_CHAT_IDS")),
        database_path=env.get("DEEPCODE_DATABASE") or None,
    )
    settings.validate()
    return settings
