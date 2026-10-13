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
  ``SECRET`` / ``TOKEN`` / ``CREDENTIAL`` / ``AUTH``). ``PATH``, ``HOME``,
  locale, and proxy variables survive, so children run normally.
* A caller that genuinely needs a variable forwards it explicitly through
  ``extra_env``, which merges *after* the scrub. The scrub is a default, not a
  cage — but forwarding a secret becomes a deliberate act.

Where the scrub applies by default. External agent CLIs
(:mod:`core.harness.agents.external_backend`) have always received the
scrubbed environment and still do. The agent's own shell, hook commands and
the code-mode runtime keep the user's environment unless the operator opts in
with ``DEEPCODE_BASH_SCRUB_ENV=1``: everyday work such as ``gh``, ``aws``,
``huggingface-cli`` or a private package index reads its token from the
environment, and silently dropping it would break those workflows for every
user to defend against a relay that rewrites tool calls.

What never reaches those children is what belongs to DeepCode itself rather
than to the user's work: the variables DeepCode reads its own model-connection
keys from (``OPENAI_API_KEY``, a connection's ``apiKeyEnv``, a ``${NAME}`` in
the provider settings, ...), and DeepCode's own checkout and Python
environment (its ``PYTHONPATH`` entry, an activated venv or conda env and that
env's ``PATH`` entries), which a client started from there passes on to the
service and which would otherwise point the project's Python at DeepCode's.

``DEEPCODE_BASH_FULL_ENV=1`` remains the explicit "give children everything"
override and wins over all of the above.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

__all__ = [
    "FULL_ENV_ENV_VAR",
    "SCRUB_ENV_VAR",
    "SENSITIVE_ENV_PATTERN",
    "child_env",
    "env_scrub_requested",
    "full_env_requested",
    "scrubbed_parent_env",
]

# Credential-shaped environment names are not forwarded to children. ``AUTH``
# spares the GIT_AUTHOR_* identity and SSH_AUTH_SOCK, the agent socket path
# git-over-ssh needs; neither carries a secret.
SENSITIVE_ENV_PATTERN = re.compile(
    r"KEY|PASSWORD|SECRET|TOKEN|CREDENTIAL|(?<!SSH_)AUTH(?!OR(?:_|$))", re.IGNORECASE
)

# Opt-out: give a child the untouched parent environment.
FULL_ENV_ENV_VAR = "DEEPCODE_BASH_FULL_ENV"

# Opt-in: scrub the environment handed to the agent's shell, hook commands and
# the code-mode runtime. Off by default; see the module docstring.
SCRUB_ENV_VAR = "DEEPCODE_BASH_SCRUB_ENV"

_TRUTHY = frozenset({"1", "true", "yes", "on"})

# The directory DeepCode's own top-level packages (``core``, ``tools``, ...)
# are imported from.
_DEEPCODE_ROOT = os.path.normcase(Path(__file__).resolve().parents[2])


def full_env_requested() -> bool:
    """Whether the operator explicitly asked for the untouched environment."""

    return os.environ.get(FULL_ENV_ENV_VAR, "").strip().lower() in _TRUTHY


def env_scrub_requested() -> bool:
    """Whether the operator opted the shell, hooks and code mode into the scrub."""

    return os.environ.get(SCRUB_ENV_VAR, "").strip().lower() in _TRUTHY


def child_env(extra_env: dict[str, str] | None = None) -> dict[str, str]:
    """Environment for the agent's shell, a hook command or the code-mode runtime.

    The parent environment without DeepCode's own model keys and Python
    environment by default; the credential scrub applies only when
    :data:`SCRUB_ENV_VAR` is set, and :data:`FULL_ENV_ENV_VAR` still wins.
    ``extra_env`` merges last either way.
    """

    if env_scrub_requested():
        return scrubbed_parent_env(extra_env)
    env = dict(os.environ)
    if not full_env_requested():
        _drop_deepcode_own(env)
    if extra_env:
        env.update(extra_env)
    return env


def scrubbed_parent_env(
    extra_env: dict[str, str] | None = None,
    *,
    force_full: bool = False,
) -> dict[str, str]:
    """The ambient environment minus credential-shaped names and DeepCode's own.

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
        _drop_deepcode_own(env)
    if extra_env:
        env.update(extra_env)
    return env


def _drop_deepcode_own(env: dict[str, str]) -> None:
    """Remove what belongs to DeepCode itself rather than to the user's work."""

    from core.config import credential_variables

    for name in credential_variables():
        env.pop(name, None)
    pythonpath = env.pop("PYTHONPATH", None)
    if pythonpath:
        # An empty entry means the child's own working directory; keep it.
        kept = [
            entry
            for entry in pythonpath.split(os.pathsep)
            if not entry or _resolved(entry) != _DEEPCODE_ROOT
        ]
        if any(kept):
            env["PYTHONPATH"] = os.pathsep.join(kept)
    # A client that had DeepCode's own environment activated passes it on:
    # uv, poetry and conda would install into it, and ``python``/``pip`` on
    # PATH would be DeepCode's interpreter rather than the project's. The
    # conda base is many users' everyday Python, so it is left alone.
    prefix = _resolved(sys.prefix)
    activated = False
    if _resolved(env.get("VIRTUAL_ENV")) == prefix:
        del env["VIRTUAL_ENV"]
        activated = True
    if (
        _resolved(env.get("CONDA_PREFIX")) == prefix
        and env.get("CONDA_DEFAULT_ENV") != "base"
    ):
        del env["CONDA_PREFIX"]
        env.pop("CONDA_DEFAULT_ENV", None)
        activated = True
    if activated and "PATH" in env:
        env["PATH"] = os.pathsep.join(
            entry
            for entry in env["PATH"].split(os.pathsep)
            if not _inside(entry, prefix)
        )


def _resolved(path: str | None) -> str | None:
    """``path`` with links resolved, comparable as text; ``None`` when empty."""

    return os.path.normcase(os.path.realpath(path)) if path else None


def _inside(path: str, root: str) -> bool:
    resolved = _resolved(path)
    return resolved is not None and (
        resolved == root or resolved.startswith(root + os.sep)
    )
