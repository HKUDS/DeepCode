"""What an "always allow this session" answer covers.

An approval answered with ``approved_session`` used to cover the whole tool:
approving ``ls`` once meant ``rm -rf`` never asked again. A session approval
now covers the same *kind* of call only, and every client shows the same
sentence for it ("Always allow `git status …` this session"), because the
scope is computed here, once, when the approval is requested.

A scope is a set of keys. A later call is covered when every one of its keys
was granted earlier in the Session:

* **Commands** (shell tools) — one key per simple command in the line. The
  key is the program plus its subcommand where it has one (``git status``,
  ``npm run test``, ``ls``), so a key covers any arguments. A compound line
  (``&&``, ``;``, ``|``, subshells) is covered only when every part is.
  Anything a prefix cannot describe safely — a write redirect, a wrapper such
  as ``sudo`` or ``xargs``, a leading ``VAR=value``, ``python -c`` — is keyed
  by its exact text, and a line the parser cannot follow (command
  substitution, here-documents, ``for``/``if`` blocks) by the whole line.
* **File edits** (write, edit, apply_patch) — "edits in this workspace":
  one key for the workspace, whichever edit tool asks. A path outside the
  workspace is keyed by that exact path.
* **Any other tool** — no keys: the grant covers that one tool, as before.

Mechanism only: :class:`~core.application.approval_service.ApprovalService`
stores and checks the keys.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from core.harness.permissions import is_file_edit_tool, is_shell_tool

_PREFIX = "cmd:"  # program (+ subcommand): any arguments
_EXACT = "cmd="  # this exact command text
_EDITS_IN = "edits:"  # any file inside this workspace
_EDIT_PATH = "edit:"  # this one file
_EXACT_CALL = "call:"  # this exact tool call (arguments hashed)

_LABEL_TEXT_CELLS = 48

# Programs whose first argument is a subcommand that changes what they do.
_SUBCOMMAND_PROGRAMS = frozenset(
    {
        "apt",
        "apt-get",
        "brew",
        "bun",
        "bundle",
        "cargo",
        "cmake",
        "composer",
        "conda",
        "dart",
        "deno",
        "dnf",
        "docker",
        "dotnet",
        "flutter",
        "gem",
        "gh",
        "git",
        "go",
        "gradle",
        "./gradlew",
        "helm",
        "just",
        "kubectl",
        "make",
        "mamba",
        "mix",
        "mvn",
        "./mvnw",
        "npm",
        "pdm",
        "pip",
        "pip3",
        "pipenv",
        "pnpm",
        "podman",
        "poetry",
        "pre-commit",
        "rake",
        "ruff",
        "rustup",
        "swift",
        "systemctl",
        "terraform",
        "uv",
        "yarn",
    }
)
# ``<program> <subcommand> <name>``: the third word picks what runs.
_THREE_WORD_COMMANDS = frozenset(
    {
        ("bun", "run"),
        ("docker", "compose"),
        ("gh", "issue"),
        ("gh", "pr"),
        ("gh", "release"),
        ("gh", "repo"),
        ("gh", "run"),
        ("gh", "workflow"),
        ("git", "remote"),
        ("git", "stash"),
        ("git", "submodule"),
        ("git", "worktree"),
        ("go", "run"),
        ("npm", "run"),
        ("pnpm", "run"),
        ("yarn", "run"),
    }
)
# ``<runner> <command …>`` runs another command; its own prefix follows.
_RUNNER_COMMANDS = frozenset(
    {
        ("hatch", "run"),
        ("npm", "exec"),
        ("pdm", "run"),
        ("pipenv", "run"),
        ("pnpm", "dlx"),
        ("pnpm", "exec"),
        ("poetry", "run"),
        ("uv", "run"),
        ("yarn", "dlx"),
        ("bun", "x"),
    }
)
_RUNNER_PROGRAMS = frozenset({"bunx", "npx", "pnpx", "uvx"})
# ``<interpreter> <script>``: the script is what runs, so it is the subcommand.
_INTERPRETERS = frozenset(
    {
        ".",
        "bash",
        "dash",
        "fish",
        "ksh",
        "lua",
        "node",
        "perl",
        "php",
        "Rscript",
        "ruby",
        "sh",
        "source",
        "zsh",
    }
)
_PYTHON = re.compile(r"python(?:\d+(?:\.\d+)*)?")
# Run whatever their arguments say, as someone else, later or elsewhere: no
# prefix can describe that.
_WRAPPERS = frozenset(
    {
        "builtin",
        "caffeinate",
        "chroot",
        "chronic",
        "command",
        "doas",
        "env",
        "eval",
        "exec",
        "flock",
        "ionice",
        "nice",
        "nohup",
        "parallel",
        "pkexec",
        "runuser",
        "script",
        "setsid",
        "sg",
        "ssh",
        "sshpass",
        "stdbuf",
        "strace",
        "su",
        "sudo",
        "time",
        "timeout",
        "unbuffer",
        "watch",
        "xargs",
    }
)
_FIND_ACTIONS = frozenset(
    {
        "-delete",
        "-exec",
        "-execdir",
        "-fls",
        "-fprint",
        "-fprint0",
        "-fprintf",
        "-ok",
        "-okdir",
    }
)
_RESERVED = frozenset(
    {
        "case",
        "do",
        "done",
        "elif",
        "else",
        "esac",
        "fi",
        "for",
        "function",
        "if",
        "in",
        "select",
        "then",
        "until",
        "while",
        "[[",
        "]]",
    }
)
_OPERATOR_CHARS = frozenset(";&|()<>\n")
_SEPARATORS = frozenset({"{", "}", "!"})
# Text the tokenizer cannot follow: command or process substitution, and
# here-documents (their body is not a command).
_OPAQUE = ("`", "$(", "<(", ">(", "<<")
_HARMLESS_TARGETS = frozenset({"/dev/null", "/dev/stdout", "/dev/stderr"})
_ASSIGNMENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*=")
_PATH_ARGUMENT_KEYS = ("file_path", "path", "filename", "target")
_PATCH_PATH = re.compile(
    r"^\*\*\* (?:Add|Update|Delete) File: (.+)$|^\*\*\* Move to: (.+)$", re.MULTILINE
)


@dataclass(frozen=True, slots=True)
class SessionScope:
    """What answering "always allow" to one call covers for the Session.

    ``keys`` empty means the whole tool (any call to it), which is what
    every tool other than commands and file edits keeps.
    """

    tool_name: str
    keys: tuple[str, ...]
    target: str

    @property
    def label(self) -> str:
        return f"Always allow {self.target} this session"

    def to_payload(self) -> dict[str, object]:
        return {"keys": list(self.keys), "target": self.target, "label": self.label}

    @classmethod
    def from_payload(cls, tool_name: str, payload: object) -> SessionScope | None:
        """The scope an approval request recorded, or ``None`` if unusable."""

        if not isinstance(payload, Mapping):
            return None
        keys = payload.get("keys")
        target = payload.get("target")
        if not isinstance(keys, list) or not isinstance(target, str):
            return None
        if not all(isinstance(key, str) and key for key in keys):
            return None
        return cls(tool_name=tool_name, keys=tuple(keys), target=target)


def session_scope(
    tool_name: str,
    arguments: Mapping[str, object] | None,
    workspace: str | None,
) -> SessionScope:
    """The scope an "always allow" answer to this call grants."""

    arguments = arguments or {}
    if is_shell_tool(tool_name):
        keys = command_keys(_command_text(arguments))
        return SessionScope(tool_name, keys, _command_target(keys))
    if is_file_edit_tool(tool_name):
        return _edit_scope(tool_name, arguments, workspace)
    return SessionScope(tool_name, (), f"`{tool_name}`")


def requested_session_target(request: Mapping[str, object]) -> str:
    """What "always allow" covers for an approval request, for a prompt.

    A request from a service that predates scopes carries none; the answer
    then covered the whole tool, and the prompt says so.
    """

    scope = SessionScope.from_payload(
        str(request.get("toolName") or "tool"), request.get("sessionScope")
    )
    return scope.target if scope is not None else "this tool"


def is_covered(
    scope: SessionScope,
    grants: Iterable[tuple[str, tuple[str, ...]]],
) -> bool:
    """Whether earlier grants ``(tool_name, keys)`` cover ``scope``.

    A scoped call needs every one of its keys granted; a whole-tool call
    needs a whole-tool grant for the same tool. A whole-tool grant never
    covers a command or a file edit: such grants were made before scopes
    existed, and honouring them would keep the old "the whole tool" answer.
    """

    granted: set[str] = set()
    whole_tools: set[str] = set()
    for tool_name, keys in grants:
        if keys:
            granted.update(keys)
        else:
            whole_tools.add(tool_name)
    if scope.keys:
        return all(key in granted for key in scope.keys)
    return scope.tool_name in whole_tools


# -- commands ---------------------------------------------------------------


def _command_text(arguments: Mapping[str, object]) -> str:
    for key in ("command", "cmd", "commands", "script"):
        value = arguments.get(key)
        if isinstance(value, str):
            return value
        if isinstance(value, (list, tuple)):
            return "\n".join(str(part) for part in value)
    return ""


def command_keys(command: str) -> tuple[str, ...]:
    """One key per simple command in ``command``, in order, without repeats."""

    text = command.strip()
    whole = (_EXACT + text,)
    if not text or any(marker in text for marker in _OPAQUE):
        return whole
    try:
        segments = _segments(text)
    except ValueError:
        return whole
    keys: list[str] = []
    for words, raw, writes_file in segments:
        if words and words[0] in _RESERVED:
            return whole
        key = _segment_key(words, raw, writes_file)
        if key is not None and key not in keys:
            keys.append(key)
    return tuple(keys) or whole


def _tokens(text: str) -> list[str]:
    lexer = shlex.shlex(text, posix=True, punctuation_chars=";&|()<>\n")
    lexer.whitespace = " \t\r"
    lexer.whitespace_split = True
    lexer.commenters = ""
    return list(lexer)


def _segments(text: str) -> list[tuple[list[str], list[str], bool]]:
    """Split a command line into simple commands.

    Each is ``(words, tokens, writes_file)``: its words without
    redirections, all of its tokens (redirections included, for an exact
    key), and whether it redirects output into a file. Raises ``ValueError``
    when the line cannot be split (unbalanced quotes, an operator the split
    does not know).
    """

    segments: list[tuple[list[str], list[str], bool]] = []
    words: list[str] = []
    raw: list[str] = []
    writes_file = False

    def close() -> None:
        nonlocal words, raw, writes_file
        if words or raw:
            segments.append((words, raw, writes_file))
        words, raw, writes_file = [], [], False

    tokens = _tokens(text)
    index = 0
    while index < len(tokens):
        token = tokens[index]
        index += 1
        if token and set(token) <= _OPERATOR_CHARS:
            if "<" in token or ">" in token:
                if set(token) - {"<", ">", "&", "|"}:
                    raise ValueError(f"unsupported operator: {token!r}")
                if words and words[-1].isdigit():
                    words.pop()  # the file descriptor of ``2>``
                if index >= len(tokens):
                    raise ValueError("redirection without a target")
                target = tokens[index]
                index += 1
                if set(target) <= _OPERATOR_CHARS:
                    raise ValueError("redirection without a target")
                raw += [token, target]
                if token in {"<", "<&"} or (
                    token.endswith("&") and (target.isdigit() or target == "-")
                ):
                    continue  # reads a file, or duplicates a descriptor
                if target not in _HARMLESS_TARGETS:
                    writes_file = True
                continue
            close()
            continue
        if token in _SEPARATORS and not words:
            continue
        if token == "}":
            close()
            continue
        words.append(token)
        raw.append(token)
    close()
    return segments


def _segment_key(words: list[str], raw: list[str], writes_file: bool) -> str | None:
    if not words:
        # Only redirections (``> file`` truncates it): keyed by its text.
        return _EXACT + " ".join(raw) if raw else None
    exact = _EXACT + " ".join(
        token if set(token) <= _OPERATOR_CHARS else shlex.quote(token) for token in raw
    )
    if writes_file:
        return exact
    prefix = _command_prefix(words)
    return exact if prefix is None else _PREFIX + " ".join(prefix)


def _command_prefix(words: list[str]) -> list[str] | None:
    """The words that name this kind of command, or ``None`` for exact-only."""

    program, arguments = words[0], words[1:]
    if (
        _ASSIGNMENT.match(program)
        or any(character in program for character in "$*?[")
        or program in _WRAPPERS
    ):
        return None
    if program in _RUNNER_PROGRAMS:
        if not arguments or arguments[0].startswith("-"):
            return None if arguments else [program]
        inner = _command_prefix(arguments)
        return None if inner is None else [program, *inner]
    if program in _INTERPRETERS or _PYTHON.fullmatch(program):
        if not arguments:
            return [program]
        if arguments[0] == "-m" and len(arguments) > 1 and _PYTHON.fullmatch(program):
            return [program, "-m", arguments[1]]
        if arguments[0].startswith("-"):
            return None
        return [program, arguments[0]]
    if program == "find":
        return None if _FIND_ACTIONS.intersection(arguments) else [program]
    if program in _SUBCOMMAND_PROGRAMS:
        if not arguments:
            return [program]
        subcommand = arguments[0]
        if subcommand.startswith("-"):
            return None  # options before the subcommand change what it means
        rest = arguments[1:]
        if (program, subcommand) in _RUNNER_COMMANDS:
            if not rest or rest[0].startswith("-"):
                return None if rest else [program, subcommand]
            inner = _command_prefix(rest)
            return None if inner is None else [program, subcommand, *inner]
        if (program, subcommand) in _THREE_WORD_COMMANDS and rest:
            if rest[0].startswith("-"):
                return None
            return [program, subcommand, rest[0]]
        return [program, subcommand]
    return [program]


def _command_target(keys: tuple[str, ...]) -> str:
    shown = []
    for key in keys:
        if key.startswith(_PREFIX):
            shown.append(f"`{_fit(key[len(_PREFIX) :])} …`")
        else:
            shown.append(f"exactly `{_fit(key[len(_EXACT) :])}`")
    return _join(shown)


# -- file edits -------------------------------------------------------------


def _edit_scope(
    tool_name: str,
    arguments: Mapping[str, object],
    workspace: str | None,
) -> SessionScope:
    paths = _edit_paths(tool_name, arguments)
    if not paths:
        digest = hashlib.sha256(
            json.dumps(
                {"tool": tool_name, "arguments": arguments},
                sort_keys=True,
                default=str,
            ).encode()
        ).hexdigest()
        return SessionScope(tool_name, (_EXACT_CALL + digest,), "this exact change")
    root = _real(workspace) if workspace else None
    keys: list[str] = []
    inside = False
    outside: list[str] = []
    for raw in paths:
        joined = raw if os.path.isabs(raw) or root is None else os.path.join(root, raw)
        resolved = _real(joined)
        if root is not None and (
            resolved == root or resolved.startswith(root.rstrip(os.sep) + os.sep)
        ):
            inside = True
            key = _EDITS_IN + root
        else:
            key = _EDIT_PATH + resolved
            outside.append(f"`{_fit(resolved)}`")
        if key not in keys:
            keys.append(key)
    parts = (["edits in this workspace"] if inside else []) + (
        [f"edits to {_join(list(dict.fromkeys(outside)))}"] if outside else []
    )
    return SessionScope(tool_name, tuple(keys), " and ".join(parts))


def _edit_paths(tool_name: str, arguments: Mapping[str, object]) -> list[str]:
    for key in _PATH_ARGUMENT_KEYS:
        value = arguments.get(key)
        if isinstance(value, str) and value.strip():
            return [value.strip()]
    patch = arguments.get("patch") or arguments.get("input")
    if isinstance(patch, str):
        found = [
            (match.group(1) or match.group(2)).strip()
            for match in _PATCH_PATH.finditer(patch)
        ]
        return [path for path in found if path]
    return []


def _real(path: str) -> str:
    try:
        return os.path.realpath(os.path.expanduser(path))
    except (OSError, ValueError):
        return os.path.normpath(path)


# -- labels -----------------------------------------------------------------


def _fit(text: str) -> str:
    text = " ".join(text.split())
    if len(text) <= _LABEL_TEXT_CELLS:
        return text
    return text[: _LABEL_TEXT_CELLS - 1] + "…"


def _join(parts: list[str]) -> str:
    if len(parts) <= 1:
        return "".join(parts)
    return ", ".join(parts[:-1]) + " and " + parts[-1]
