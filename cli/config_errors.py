"""Turn a :class:`core.config.ConfigError` into a clean, actionable CLI message.

Shared by every entrypoint that builds an agent (TUI, headless exec, ...) so a
misconfiguration prints one friendly block — never a Python traceback — and,
when DeepCode has no config at all, points the user at ``deepcode init``.
"""

from __future__ import annotations

from core.config import ConfigError, default_config_path, home_config_path


def is_unconfigured() -> bool:
    """True when neither config layer exists on disk (a first-run situation)."""
    return not home_config_path().exists() and not default_config_path().exists()


def format_config_error(exc: ConfigError) -> str:
    lines = [f"Configuration error: {exc}"]
    if is_unconfigured():
        lines += [
            "",
            "DeepCode is not configured yet — no deepcode_config.json was found at",
            f"  {home_config_path()}  (user base)",
            "  or in the current directory / its parents (project).",
            "",
            "Run  deepcode init  to create the user base, then add a provider key",
            'under "providers" in that file. After that, deepcode works in any directory.',
        ]
    return "\n".join(lines)
