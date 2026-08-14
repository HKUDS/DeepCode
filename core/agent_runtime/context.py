"""Typed, transient context describing one agent execution environment."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
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
            "<environment_context>\n"
            f"  <cwd>{escape(self.cwd)}</cwd>\n"
            f"  <shell>{escape(self.shell)}</shell>\n"
            f"  <current_date>{escape(self.current_date)}</current_date>\n"
            f"  <timezone>{escape(self.timezone)}</timezone>\n"
            "</environment_context>"
        )

    def message(self) -> dict[str, str]:
        return {"role": "user", "content": self.render()}


__all__ = ["EnvironmentContext"]
