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

import re
import shlex

__all__ = ["screen_command"]

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
