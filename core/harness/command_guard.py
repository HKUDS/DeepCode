"""Destructive-command screening — cheap defense-in-depth, not the boundary.

The sandbox in :mod:`core.harness.sandbox` is what actually enforces the
execution boundary (writes fenced to the workspace on seatbelt/bwrap). This
module is the *shallow first pass* that sits in front of it: a fast, best-effort
check that catches obviously destructive commands before they ever reach the
shell.

Why this exists as its own module. The original check in ``execute_bash`` was::

    dangerous = ["rm -rf", "sudo", "chmod 777", "mkfs", "dd if="]
    if any(d in command.lower() for d in dangerous):
        block

That is a raw substring match, and it is trivially bypassable — which the
project already acknowledged (issue #128). ``rm  -rf`` (two spaces), ``rm -r -f``
(split flags), ``rm  --recursive --force``, or ``chmod  0777`` all sail straight
through, while a *benign* path like ``touch rm-rf-notes.txt`` is falsely blocked.
A blocklist can never be a security boundary; the sandbox is. But if we keep a
blocklist at all, it should honestly catch what it *claims* to, rather than
offering a false sense of coverage.

So this module splits the command on the shell control operators and then
*tokenises* each segment with :func:`shlex.split`, matching on the resulting
argv — the command name and its flags — instead of substrings of the raw
string. That closes the whitespace / flag-order / flag-spelling gaps without
pretending to be exhaustive.

:func:`screen_command` returns a human-readable reason string when a command
looks destructive, else ``None``. It never raises: a command it cannot parse is
passed through (``None``) and left to the sandbox, exactly as before — this
layer only ever *adds* friction to clearly dangerous commands, never removes the
real protection underneath.
"""

from __future__ import annotations

import os
import re
import shlex
from urllib.parse import urlparse

from core.network.hostnames import is_domain_allowed

__all__ = [
    "find_confusables",
    "screen_all",
    "screen_command",
    "screen_egress",
    "screen_install",
]

# Shell control operators that separate one simple command from the next.
# We split the raw string on these *before* tokenising, because shlex.split is
# a word splitter, not a shell parser — it would keep "x;" or "/tmp&&" as a
# single token and hide the following command.
_OPERATOR_SPLIT = re.compile(r"(?:\|\||\||&&|&|;|\n)")


def _has_flag(flag_tokens: list[str], *letters: str) -> bool:
    """Whether any short-flag cluster contains all of ``letters``.

    ``-rf``, ``-fr`` and ``-r -f`` all count as having both ``r`` and ``f``,
    because short flags may be combined in any order or split apart.
    """
    joined = "".join(t.lstrip("-") for t in flag_tokens)
    return all(letter in joined for letter in letters)


def _has_long_flag(tokens: list[str], *names: str) -> bool:
    """Whether every long flag in ``names`` (e.g. ``recursive``) is present."""
    present = {t.lstrip("-") for t in tokens if t.startswith("--")}
    return all(name in present for name in names)


def _is_recursive_force_rm(cmd: str, args: list[str]) -> bool:
    if cmd != "rm":
        return False
    flags = [a for a in args if a.startswith("-")]
    # rm treats both -r and -R as recursive.
    recursive = (
        _has_flag(flags, "r")
        or _has_flag(flags, "R")
        or _has_long_flag(args, "recursive")
    )
    force = _has_flag(flags, "f") or _has_long_flag(args, "force")
    return recursive and force


def _is_reckless_chmod(cmd: str, args: list[str]) -> bool:
    """Permissive chmod granting full rwx to everyone (the classic ``777``).

    Catches the numeric ``777``/``0777`` form and the symbolic ``a+rwx`` /
    ``a=rwx`` form; ignores harmless modes like ``755`` or ``+x``.
    """
    if cmd != "chmod":
        return False
    for a in args:
        if a.startswith("-"):
            continue
        mode = a
        if mode.isdigit() and mode[-3:] == "777":
            return True
        if mode in {"a+rwx", "a=rwx", "+rwx", "=rwx", "ugo+rwx", "ugo=rwx"}:
            return True
    return False


def _is_disk_write(cmd: str, args: list[str]) -> bool:
    if cmd == "mkfs" or cmd.startswith("mkfs."):
        return True
    if cmd == "dd":
        return any(a.startswith("of=") for a in args)
    return False


def _classify(cmd: str, args: list[str]) -> str | None:
    if _is_recursive_force_rm(cmd, args):
        return "recursive force remove (rm -rf)"
    if _is_reckless_chmod(cmd, args):
        return "world-writable permissions (chmod 777)"
    if _is_disk_write(cmd, args):
        return "raw disk/filesystem write (dd of= / mkfs)"
    if cmd == "sudo":
        return "privilege escalation (sudo)"
    if cmd in {"shutdown", "reboot", "halt", "poweroff"}:
        return f"host power control ({cmd})"
    return None


def screen_command(command: str) -> str | None:
    """Return a reason string if ``command`` looks destructive, else ``None``.

    The raw string is first split on the shell control operators
    (``;`` ``&&`` ``||`` ``|`` ``&`` and newlines) so that a destructive stage
    hidden in a pipeline or sequence is still caught, e.g. ``echo hi && rm -rf /``
    or ``cd /tmp; rm -rf x``. Each segment is then tokenised with
    :func:`shlex.split` and classified on its argv. A segment that cannot be
    tokenised (unbalanced quotes, etc.) is skipped rather than guessed at — this
    layer never blocks what it cannot understand, and the sandbox remains the
    boundary.
    """
    if not command or not command.strip():
        return None

    for raw_segment in _OPERATOR_SPLIT.split(command):
        segment = raw_segment.strip()
        if not segment:
            continue
        try:
            tokens = shlex.split(segment, comments=False, posix=True)
        except ValueError:
            # Unbalanced quotes etc. — don't guess; defer to the sandbox.
            continue
        if not tokens:
            continue
        reason = _classify(tokens[0].lower(), tokens[1:])
        if reason is not None:
            return reason

    return None


# --------------------------------------------------------------------------
# Egress and dependency screening
# --------------------------------------------------------------------------
#
# The threat this addresses is not the operator typing a bad command. It is a
# *response-side* rewrite: an intermediary between us and the model (a relay,
# gateway, or OpenAI-compatible proxy) rewrites a tool call on its way back so
# that a benign fetch points at an attacker-controlled script, or so that a
# package name differs by one character from the one the model actually asked
# for. The rewritten call is schema-valid and looks unremarkable, so only the
# payload itself can give it away.
#
# Read the limits honestly before trusting these:
#
# * An allow-list based gate is *coarse*. An attacker who can host the payload
#   on an allow-listed domain, or who drops a stager locally and then runs it
#   through an innocuous command, walks straight through. This is a filter, not
#   a boundary; the sandbox remains the boundary.
# * ``screen_egress`` fires on the shape ``<fetcher> <url> | <interpreter>``,
#   which is the canonical one-line remote-code pattern. It is also, sadly, a
#   pattern real installers use (rustup, uv, homebrew). It therefore *asks*
#   rather than proving anything, and it is commonly waived.
# * ``screen_install`` compares names against a list. It cannot know a package
#   is malicious; it can only notice that the name is one edit away from one
#   you already depend on.

# Command separators: these end one simple command and begin another. ``|`` is
# deliberately NOT here — a pipeline is one logical action and the downstream
# stage is exactly what makes a fetch dangerous.
_COMMAND_SPLIT = re.compile(r"(?:\|\||&&|;|\n)")

# Pipeline separator, applied within one simple command.
_PIPE_SPLIT = re.compile(r"(?<!\|)\|(?!\|)")

# Programs that fetch remote content.
_FETCHERS = frozenset(
    {
        "curl",
        "wget",
        "fetch",
        "aria2c",
        "iwr",
        "invoke-webrequest",
        "invoke-restmethod",
        "irm",
    }
)

# Programs that execute what they are handed. Feeding a fetch into one of these
# turns retrieved bytes into executed code.
_INTERPRETERS = frozenset(
    {
        "bash",
        "sh",
        "zsh",
        "dash",
        "ksh",
        "ash",
        "fish",
        "python",
        "python3",
        "py",
        "node",
        "nodejs",
        "deno",
        "bun",
        "perl",
        "ruby",
        "php",
        "iex",
        "powershell",
        "pwsh",
        "cmd",
    }
)

# Environment variable that waives the egress screen for one deliberate run.
_ALLOW_REMOTE_SCRIPT_ENV = "DEEPCODE_ALLOW_REMOTE_SCRIPT"

# Environment variable that turns the new screens off wholesale.
_SCREEN_DISABLE_ENV = "DEEPCODE_COMMAND_SCREEN"

# Package managers, mapped to the sub-commands that install something.
_INSTALL_SUBCOMMANDS: dict[str, frozenset[str]] = {
    "pip": frozenset({"install"}),
    "pip3": frozenset({"install"}),
    "pipx": frozenset({"install"}),
    "uv": frozenset({"add", "pip", "sync"}),
    "poetry": frozenset({"add", "install"}),
    "npm": frozenset({"install", "i", "add"}),
    "pnpm": frozenset({"install", "i", "add"}),
    "yarn": frozenset({"add", "install"}),
    "cargo": frozenset({"add", "install"}),
    "go": frozenset({"get", "install"}),
    "gem": frozenset({"install"}),
    "choco": frozenset({"install"}),
    "winget": frozenset({"install"}),
    "scoop": frozenset({"install"}),
    "brew": frozenset({"install"}),
    "apt": frozenset({"install"}),
    "apt-get": frozenset({"install"}),
}

# Options that consume the following token, so it is a value and not a package.
_VALUE_TAKING_FLAGS = frozenset(
    {
        "-r",
        "--requirement",
        "-i",
        "--index-url",
        "--extra-index-url",
        "-t",
        "--target",
        "--prefix",
        "--cache-dir",
        "--trusted-host",
        "-p",
        "--python",
        "-e",
        "--editable",
        "--registry",
        "--source",
        "--from",
        "--tag",
        "--version",
        "--features",
        "--path",
        "--root",
    }
)

# Flags whose value names a package index rather than a package.
_INDEX_FLAGS = frozenset(
    {
        "-i",
        "--index-url",
        "--extra-index-url",
        "--registry",
        "--source",
        "--from-index",
    }
)

# Registries we treat as canonical unless the caller says otherwise.
_DEFAULT_TRUSTED_INDEXES = frozenset(
    {
        "pypi.org",
        "files.pythonhosted.org",
        "registry.npmjs.org",
        "registry.yarnpkg.com",
        "crates.io",
        "static.crates.io",
        "proxy.golang.org",
        "rubygems.org",
        "repo.maven.apache.org",
    }
)

# High-traffic names that typosquatters target. Deliberately short: it exists to
# make the *shape* of a one-edit substitution visible on a fresh checkout that
# declares no dependencies yet. Callers with a real manifest should pass
# ``known_packages`` and let that list do the work.
_POPULAR_PACKAGES = frozenset(
    {
        "requests",
        "urllib3",
        "numpy",
        "pandas",
        "flask",
        "django",
        "pyyaml",
        "cryptography",
        "openai",
        "anthropic",
        "boto3",
        "pydantic",
        "pytest",
        "setuptools",
        "react",
        "lodash",
        "express",
        "axios",
        "typescript",
        "electron",
    }
)


def _screens_disabled() -> bool:
    return os.environ.get(_SCREEN_DISABLE_ENV, "").strip().lower() in {
        "0",
        "false",
        "off",
        "no",
    }


def _remote_script_allowed() -> bool:
    return os.environ.get(_ALLOW_REMOTE_SCRIPT_ENV, "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _simple_commands(command: str) -> list[str]:
    """Split on shell separators, leaving pipelines intact."""

    return [
        segment.strip() for segment in _COMMAND_SPLIT.split(command) if segment.strip()
    ]


def _pipeline_stages(simple_command: str) -> list[list[str]]:
    """Tokenise each ``|``-separated stage; unparseable stages become empty."""

    stages: list[list[str]] = []
    for raw in _PIPE_SPLIT.split(simple_command):
        raw = raw.strip()
        if not raw:
            continue
        try:
            stages.append(shlex.split(raw, comments=False, posix=True))
        except ValueError:
            stages.append([])
    return stages


def _program(token: str) -> str:
    """The bare program name, without directory or Windows extension."""

    name = token.replace("\\", "/").rsplit("/", 1)[-1].lower()
    for suffix in (".exe", ".cmd", ".bat", ".ps1"):
        if name.endswith(suffix):
            name = name.removesuffix(suffix)
    return name


def _urls(tokens: list[str]) -> list[str]:
    return [
        t
        for t in tokens
        if t.startswith(("http://", "https://", "HTTP://", "HTTPS://"))
    ]


def _url_host(url: str) -> str | None:
    try:
        parsed = urlparse(url)
    except ValueError:
        return None
    return parsed.hostname or None


def screen_egress(
    command: str,
    *,
    allowed_domains: tuple[str, ...] = (),
    blocked_domains: tuple[str, ...] = (),
) -> str | None:
    """Flag remote-code pipelines and fetches from untrusted hosts.

    Returns a human-readable reason, or ``None``. With no ``allowed_domains``
    configured the domain rule is inert and only the pipeline shape and the
    explicit ``blocked_domains`` are enforced — an empty allow-list must never
    be mistaken for "nothing is allowed", or every benign fetch would break.
    """

    if not command or not command.strip() or _screens_disabled():
        return None

    for simple in _simple_commands(command):
        stages = _pipeline_stages(simple)
        for index, tokens in enumerate(stages):
            if not tokens:
                continue
            if _program(tokens[0]) not in _FETCHERS:
                continue
            urls = _urls(tokens)
            if not urls:
                continue

            downstream = stages[index + 1 :]
            interpreter = next(
                (
                    _program(stage[0])
                    for stage in downstream
                    if stage and _program(stage[0]) in _INTERPRETERS
                ),
                None,
            )
            if interpreter is not None and not _remote_script_allowed():
                return (
                    f"remote script piped into an interpreter "
                    f"({_program(tokens[0])} ... | {interpreter}); "
                    f"set {_ALLOW_REMOTE_SCRIPT_ENV}=1 to permit this deliberately"
                )

            for url in urls:
                host = _url_host(url)
                if host is None:
                    continue
                if not is_domain_allowed(
                    host,
                    allowed_domains=allowed_domains,
                    blocked_domains=blocked_domains,
                ):
                    return f"fetch from a host outside the allow-list ({host})"

    return None


def _is_index_host(host: str, allowed_indexes: frozenset[str]) -> bool:
    return any(host == known or host.endswith(f".{known}") for known in allowed_indexes)


def _edit_distance(left: str, right: str) -> int:
    """Damerau-Levenshtein distance (optimal string alignment).

    Plain Levenshtein is the wrong metric here: the classic typosquat is a
    *transposition* (``requests`` → ``reqeusts``, ``lodash`` → ``lodahs``), and
    a transposition costs two edits under Levenshtein while being a single
    keystroke in practice. Counting it as one is what makes the check fire.
    """

    if left == right:
        return 0
    if not left:
        return len(right)
    if not right:
        return len(left)
    rows, cols = len(left) + 1, len(right) + 1
    distance = [[0] * cols for _ in range(rows)]
    for i in range(rows):
        distance[i][0] = i
    for j in range(cols):
        distance[0][j] = j
    for i in range(1, rows):
        for j in range(1, cols):
            cost = 0 if left[i - 1] == right[j - 1] else 1
            distance[i][j] = min(
                distance[i - 1][j] + 1,
                distance[i][j - 1] + 1,
                distance[i - 1][j - 1] + cost,
            )
            if (
                i > 1
                and j > 1
                and left[i - 1] == right[j - 2]
                and left[i - 2] == right[j - 1]
            ):
                distance[i][j] = min(distance[i][j], distance[i - 2][j - 2] + 1)
    return distance[rows - 1][cols - 1]


def find_confusables(
    package: str,
    known: frozenset[str] | tuple[str, ...] | set[str],
) -> list[str]:
    """Known names within one edit (or two, for long names) of ``package``.

    One edit catches the classic substitution (``requests`` → ``reqeusts``).
    Long names get a budget of two because a single character change in a
    20-character name is nearly invisible and a two-edit reordering of a short
    name would flag far too much.
    """

    normalised = package.strip().lower()
    if not normalised:
        return []
    # Compare on the bare distribution name: pip accepts ``pkg[extra]==1.2``.
    normalised = re.split(r"[\[=<>!~;@]", normalised, maxsplit=1)[0].strip()
    if not normalised:
        return []
    budget = 2 if len(normalised) >= 10 else 1
    hits: list[str] = []
    for candidate in known:
        other = candidate.lower()
        if other == normalised:
            continue
        if abs(len(other) - len(normalised)) > budget:
            continue
        if _edit_distance(normalised, other) <= budget:
            hits.append(candidate)
    return sorted(hits)


def screen_install(
    command: str,
    *,
    known_packages: frozenset[str] | tuple[str, ...] | set[str] = (),
    allowed_indexes: frozenset[str] = _DEFAULT_TRUSTED_INDEXES,
) -> str | None:
    """Flag installs from a non-canonical index or a confusable package name.

    ``known_packages`` should be the project's declared dependencies; the
    module's short built-in list is unioned in so a fresh checkout still gets
    the obvious cases.
    """

    if not command or not command.strip() or _screens_disabled():
        return None

    universe = frozenset(known_packages) | _POPULAR_PACKAGES

    for simple in _simple_commands(command):
        for tokens in _pipeline_stages(simple):
            if not tokens:
                continue
            program = _program(tokens[0])
            args = tokens[1:]

            # ``python -m pip install ...`` / ``py -m pip``
            if (
                program in {"python", "python3", "py"}
                and len(args) >= 3
                and args[0] == "-m"
            ):
                program = _program(args[1])
                args = args[2:]

            subcommands = _INSTALL_SUBCOMMANDS.get(program)
            if not subcommands:
                continue
            if not args or args[0].lower() not in subcommands:
                continue

            packages: list[str] = []
            index_urls: list[str] = []
            i = 1
            while i < len(args):
                token = args[i]
                if not token.startswith("-"):
                    packages.append(token)
                    i += 1
                    continue
                flag, _, inline = token.partition("=")
                takes_value = flag in _VALUE_TAKING_FLAGS
                if flag in _INDEX_FLAGS:
                    if inline:
                        index_urls.append(inline)
                    elif takes_value and i + 1 < len(args):
                        index_urls.append(args[i + 1])
                        i += 1
                    i += 1
                    continue
                if takes_value and not inline:
                    i += 1  # the following token is this flag's value
                i += 1

            for candidate in index_urls:
                host = _url_host(candidate) or candidate.split("/")[0]
                if host and not _is_index_host(host.lower(), allowed_indexes):
                    return f"package install from a non-canonical index ({host})"

            for package in packages:
                confusables = find_confusables(package, universe)
                if confusables:
                    return (
                        f"package name {package!r} is one edit away from "
                        f"{confusables[0]!r}; possible typosquat"
                    )

    return None


def screen_all(
    command: str,
    *,
    allowed_domains: tuple[str, ...] = (),
    blocked_domains: tuple[str, ...] = (),
    known_packages: frozenset[str] | tuple[str, ...] | set[str] = (),
    allowed_indexes: frozenset[str] = _DEFAULT_TRUSTED_INDEXES,
) -> str | None:
    """Run every screen and return the first reason, or ``None``.

    Order matters only for the quality of the message: the destructive check is
    the cheapest and the most certain, so it speaks first.
    """

    return (
        screen_command(command)
        or screen_egress(
            command,
            allowed_domains=allowed_domains,
            blocked_domains=blocked_domains,
        )
        or screen_install(
            command,
            known_packages=known_packages,
            allowed_indexes=allowed_indexes,
        )
    )
