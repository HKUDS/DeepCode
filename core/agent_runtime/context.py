"""Typed, transient context describing one agent execution environment."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping
from xml.sax.saxutils import escape


def _default_shell_name() -> str:
    variable = "COMSPEC" if os.name == "nt" else "SHELL"
    fallback = "cmd.exe" if os.name == "nt" else "sh"
    configured = os.environ.get(variable, "").strip()
    if not configured:
        return fallback
    return configured.replace("\\", "/").rsplit("/", maxsplit=1)[-1] or fallback


def _timezone_name(now: datetime) -> str:
    zone = getattr(now.tzinfo, "key", None)
    if isinstance(zone, str) and zone:
        return zone
    return now.tzname() or "local"


_ENV_OPEN = "<environment_context>"
_ENV_CLOSE = "</environment_context>"
# Explicit marker key on the history dict. Recognising the slot by sniffing
# its content instead would mistake a user message that merely QUOTES the
# block — a plausible thing to type when discussing the format — for the
# slot itself, and the next turn would overwrite it. Providers ignore keys
# they do not know (openai-compat whitelists, anthropic reads named fields),
# the same way the compaction checkpoint's marker travels.
ENV_CONTEXT_MARKER = "env_context"


@dataclass(frozen=True, slots=True)
class EnvironmentContext:
    """Model-visible facts that distinguish the task workspace from resources."""

    cwd: str
    shell: str
    current_date: str
    timezone: str

    @classmethod
    def for_workspace(cls, workspace: str | Path) -> "EnvironmentContext":
        now = datetime.now().astimezone()
        return cls(
            cwd=str(Path(workspace).expanduser().resolve(strict=False)),
            shell=_default_shell_name(),
            current_date=now.date().isoformat(),
            timezone=_timezone_name(now),
        )

    def render(self) -> str:
        return (
            f"{_ENV_OPEN}\n"
            f"  <cwd>{escape(self.cwd)}</cwd>\n"
            f"  <shell>{escape(self.shell)}</shell>\n"
            f"  <current_date>{escape(self.current_date)}</current_date>\n"
            f"  <timezone>{escape(self.timezone)}</timezone>\n"
            f"{_ENV_CLOSE}"
        )

    def message(self) -> dict[str, Any]:
        return {
            "role": "user",
            "content": self.render(),
            ENV_CONTEXT_MARKER: True,
        }

    @classmethod
    def is_history_message(cls, message: Mapping[str, Any]) -> bool:
        """True when ``message`` is the durable environment slot."""
        return message.get("role") == "user" and message.get(ENV_CONTEXT_MARKER) is True

    def matches_message(self, message: Mapping[str, Any]) -> bool:
        return (
            self.is_history_message(message) and message.get("content") == self.render()
        )


__all__ = ["ENV_CONTEXT_MARKER", "EnvironmentContext"]
