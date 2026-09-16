"""Credential-shaped environment scrubbing for spawned child processes.

Why this is its own module. The harness holds live provider credentials in its
own process environment (``.env`` is loaded by several MCP servers and written
back with ``os.environ.setdefault`` / ``os.environ[k] = v``). Every child we
spawn inherits that environment by default, and any command whose *stdout*
becomes a tool result therefore forwards the value into the next outbound
prompt — where a third-party model relay can read it in plaintext.

That is a passive-collection path, not an exotic one: a single ``env`` call is
enough to close the loop. The cheap, honest mitigation is to not hand the
credentials to children in the first place.

Two rules keep this usable:

* Only credential-*shaped* names are dropped (``KEY`` / ``PASSWORD`` /
  ``SECRET`` / ``TOKEN``). ``PATH``, ``HOME``, locale, and proxy variables
  survive, so children run normally.
* A caller that genuinely needs a variable forwards it explicitly through
  ``extra_env``, which merges *after* the scrub. The scrub is a default, not a
  cage — but forwarding a secret becomes a deliberate act.

Where the scrub applies by default. External agent CLIs
(:mod:`core.harness.agents.external_backend`) have always received the
scrubbed environment and still do. The agent's own shell, hook commands and
the code-mode runtime keep the full environment unless the operator opts in
with ``DEEPCODE_BASH_SCRUB_ENV=1``: everyday work such as ``gh``, ``aws``,
``huggingface-cli`` or a private package index reads its token from the
environment, and silently dropping it would break those workflows for every
user to defend against a relay that rewrites tool calls.
``DEEPCODE_BASH_FULL_ENV=1`` remains the explicit "give children everything"
override and wins over the opt-in.
"""

from __future__ import annotations

import os
import re

__all__ = [
    "FULL_ENV_ENV_VAR",
    "SCRUB_ENV_VAR",
    "SENSITIVE_ENV_PATTERN",
    "child_env",
    "env_scrub_requested",
    "full_env_requested",
    "scrubbed_parent_env",
]

# Credential-shaped environment names are not forwarded to children.
SENSITIVE_ENV_PATTERN = re.compile(r"KEY|PASSWORD|SECRET|TOKEN", re.IGNORECASE)

# Opt-out: give a child the untouched parent environment.
FULL_ENV_ENV_VAR = "DEEPCODE_BASH_FULL_ENV"

# Opt-in: scrub the environment handed to the agent's shell, hook commands and
# the code-mode runtime. Off by default; see the module docstring.
SCRUB_ENV_VAR = "DEEPCODE_BASH_SCRUB_ENV"

_TRUTHY = frozenset({"1", "true", "yes", "on"})


def full_env_requested() -> bool:
    """Whether the operator explicitly asked for the untouched environment."""

    return os.environ.get(FULL_ENV_ENV_VAR, "").strip().lower() in _TRUTHY


def env_scrub_requested() -> bool:
    """Whether the operator opted the shell, hooks and code mode into the scrub."""

    return os.environ.get(SCRUB_ENV_VAR, "").strip().lower() in _TRUTHY


def child_env(extra_env: dict[str, str] | None = None) -> dict[str, str]:
    """Environment for the agent's shell, a hook command or the code-mode runtime.

    The full parent environment by default; the credential scrub applies only
    when :data:`SCRUB_ENV_VAR` is set, and :data:`FULL_ENV_ENV_VAR` still wins.
    ``extra_env`` merges last either way.
    """

    return scrubbed_parent_env(
        extra_env, force_full=not env_scrub_requested() or full_env_requested()
    )


def scrubbed_parent_env(
    extra_env: dict[str, str] | None = None,
    *,
    force_full: bool = False,
) -> dict[str, str]:
    """The ambient environment minus credential-shaped names.

    ``extra_env`` is merged *after* the scrub, so a caller can deliberately
    forward one credential without opening the whole environment. Passing
    ``force_full=True`` (or setting :data:`FULL_ENV_ENV_VAR`) returns the
    ambient environment unchanged and merges ``extra_env`` on top.
    """

    if force_full or full_env_requested():
        env: dict[str, str] = dict(os.environ)
    else:
        env = {
            key: value
            for key, value in os.environ.items()
            if not SENSITIVE_ENV_PATTERN.search(key)
        }
    if extra_env:
        env.update(extra_env)
    return env
