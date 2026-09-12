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

The escape hatch for whole-process opt-out is
``DEEPCODE_BASH_FULL_ENV=1``, honoured at the call sites that spawn shells
(see :mod:`core.harness.tools.shell`). It exists because some build scripts
read credentials from the environment and cannot be fixed quickly; it is
deliberately not the default.
"""

from __future__ import annotations

import os
import re

__all__ = [
    "FULL_ENV_ENV_VAR",
    "SENSITIVE_ENV_PATTERN",
    "full_env_requested",
    "scrubbed_parent_env",
]

# Credential-shaped environment names are not forwarded to children.
SENSITIVE_ENV_PATTERN = re.compile(r"KEY|PASSWORD|SECRET|TOKEN", re.IGNORECASE)

# Opt-out: give a child the untouched parent environment.
FULL_ENV_ENV_VAR = "DEEPCODE_BASH_FULL_ENV"


def full_env_requested() -> bool:
    """Whether the operator explicitly asked for the untouched environment."""

    return os.environ.get(FULL_ENV_ENV_VAR, "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


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
