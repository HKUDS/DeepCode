"""Three-valued permission engine for tool calls.

Mechanism, not policy: this module *decides* allow / ask / deny for a tool
invocation. It never prompts, executes, or talks to a model — callers wire
the decision into the kernel (see ``AgentRunSpec.permission_checker``) and
turn ``ASK`` into whatever their surface needs (auto-deny in headless runs,
a real prompt in interactive ones).

Design distilled from the reference harnesses (see
DEEPCODE_V2_MASTER_PLAN.md §4.3), adapted rather than copied:

* **Non-overridable sensitive-path denylist** (OpenHarness): credential
  stores (``.ssh``, ``.aws/credentials``, ``.env`` …) can never be read or
  written, regardless of rules or mode. This is the last line of defense
  against prompt injection and cannot be turned off by config.
* **Two-dimensional wildcard rules, last-match-wins** (opencode): a rule
  matches on both the permission name (usually the tool name) and an
  argument pattern (e.g. the bash command string or the target path), so
  ``{"bash": {"git push *": "ask", "*": "allow"}}`` is expressible. Later
  rules override earlier ones.
* **Three modes**: ``default`` (mutating tools ask), ``plan`` (mutating
  tools denied — read-only exploration), ``full_auto`` (rules only, no
  implicit ask; used by non-interactive workflows).

Evaluation precedence (first decisive wins):
  1. sensitive-path denylist            → DENY   (never overridable)
  2. explicit rule match (last-wins)    → its action
  3. mode default for read-only vs mutating tools
"""

from __future__ import annotations

import fnmatch
import os
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Iterable, Mapping


class PermissionDecision(str, Enum):
    ALLOW = "allow"
    ASK = "ask"
    DENY = "deny"


class PermissionMode(str, Enum):
    DEFAULT = "default"
    PLAN = "plan"
    FULL_AUTO = "full_auto"


# Credential / secret locations that may never be read or written, no matter
# what the rules or mode say. Patterns are matched against normalized absolute
# paths with fnmatch (``*`` spans path separators here, which is what we want:
# ``*/.ssh/*`` should catch any depth). Kept deliberately small and auditable.
SENSITIVE_PATH_PATTERNS: tuple[str, ...] = (
    "*/.ssh",
    "*/.ssh/*",
    "*/.aws/credentials",
    "*/.aws/config",
    "*/.config/gcloud/*",
    "*/.azure/*",
    "*/.gnupg/*",
    "*/.docker/config.json",
    "*/.kube/config",
    "*/.netrc",
    "*/.npmrc",
    "*/.pypirc",
    "*/.git-credentials",
    "*/.deepcode/credentials*",
    "*/deepcode_config.json",
    "*/secrets.json",
    "*/.env",
    "*/.env.*",
    "*.pem",
    "*.key",
    "*id_rsa*",
    "*id_ed25519*",
)

# Argument keys inspected to find the "path" a tool touches.
_PATH_ARG_KEYS = ("file_path", "path", "root", "workspace_path", "target", "filename")

# Tool names that only read state; safe to auto-allow in default mode and
# never blocked by plan mode. Bare names + common MCP suffixes are matched.
_READ_ONLY_TOOLS = frozenset(
    {
        "read_file",
        "read_multiple_files",
        "read_code_mem",
        "get_file_structure",
        "search_code",
        "search_code_references",
        "get_indexes_overview",
        "get_operation_history",
        "grep",
        "glob",
        "ls",
        "list_dir",
        "web_fetch",
        "web_search",
    }
)


@dataclass(frozen=True)
class PermissionRule:
    """A single ``(permission, pattern) -> action`` rule.

    ``permission`` matches the tool name; ``pattern`` matches the argument
    string (command line or path). Both use fnmatch; ``*`` matches anything.
    """

    permission: str
    pattern: str
    action: PermissionDecision

    def matches(self, tool_name: str, argument: str) -> bool:
        return fnmatch.fnmatch(tool_name, self.permission) and fnmatch.fnmatch(
            argument, self.pattern
        )


def _normalize_path(raw: str, cwd: str | None) -> str:
    try:
        base = Path(cwd) if cwd else Path.cwd()
        p = Path(os.path.expanduser(raw))
        resolved = p if p.is_absolute() else (base / p)
        return os.path.normpath(str(resolved))
    except (OSError, ValueError):
        return raw


_SHELL_TOOL_NAMES = ("bash", "exec", "execute_bash", "execute_commands")


def _is_shell_tool(tool_name: str) -> bool:
    return tool_name in _SHELL_TOOL_NAMES or any(
        tool_name.endswith(f"_{name}") for name in _SHELL_TOOL_NAMES
    )


def _pattern_specificity(pattern: str) -> int:
    """Higher = more specific. Bare ``*`` is least specific (0)."""
    if pattern == "*":
        return 0
    return sum(1 for ch in pattern if ch not in "*?")


@dataclass
class PermissionEngine:
    """Evaluate tool calls to allow / ask / deny.

    Parameters
    ----------
    mode:
        Governs the implicit decision when no rule matches.
    rules:
        Ordered rules; later rules override earlier ones (last-match-wins).
    read_only_tools:
        Extra tool names to treat as read-only (merged with the built-ins).
    cwd:
        Base directory used to resolve relative paths before denylist checks.
    """

    mode: PermissionMode = PermissionMode.DEFAULT
    rules: list[PermissionRule] = field(default_factory=list)
    read_only_tools: frozenset[str] = field(default_factory=frozenset)
    cwd: str | None = None

    def is_read_only(self, tool_name: str) -> bool:
        known = _READ_ONLY_TOOLS | self.read_only_tools
        if tool_name in known:
            return True
        # ``mcp_<server>_<tool>`` names embed both a server and a tool name,
        # each of which may contain underscores/dashes, so a single "bare
        # name" split is ambiguous. Match by suffix instead (the same way the
        # alias layer resolves bare names): the wrapped name ends with the
        # known read-only tool name.
        if tool_name.startswith("mcp_"):
            return any(tool_name.endswith(f"_{name}") for name in known)
        return False

    def _candidate_paths(self, arguments: Mapping[str, object]) -> list[str]:
        paths: list[str] = []
        for key in _PATH_ARG_KEYS:
            value = arguments.get(key)
            if isinstance(value, str) and value.strip():
                paths.append(value)
            elif isinstance(value, (list, tuple)):
                paths.extend(v for v in value if isinstance(v, str) and v.strip())
        return paths

    def hits_sensitive_path(self, arguments: Mapping[str, object]) -> str | None:
        """Return the offending raw path if any argument targets a secret."""
        for raw in self._candidate_paths(arguments):
            normalized = _normalize_path(raw, self.cwd)
            probe = normalized.replace(os.sep, "/")
            for pattern in SENSITIVE_PATH_PATTERNS:
                if fnmatch.fnmatch(probe, pattern) or fnmatch.fnmatch(
                    os.path.basename(probe), pattern
                ):
                    return raw
        return None

    @staticmethod
    def _argument_string(tool_name: str, arguments: Mapping[str, object]) -> str:
        """Best-effort string a pattern rule matches against.

        For shell tools it is the command; otherwise the first path-like arg,
        falling back to the whole argument repr so ``*`` rules still work.
        """
        if _is_shell_tool(tool_name):
            for key in ("command", "cmd", "commands", "script"):
                value = arguments.get(key)
                if isinstance(value, str):
                    return value
                if isinstance(value, (list, tuple)):
                    return " ".join(str(v) for v in value)
        for key in _PATH_ARG_KEYS:
            value = arguments.get(key)
            if isinstance(value, str) and value.strip():
                return value
        return " ".join(f"{k}={v}" for k, v in sorted(arguments.items()))

    def evaluate(
        self, tool_name: str, arguments: Mapping[str, object] | None = None
    ) -> tuple[PermissionDecision, str]:
        """Return ``(decision, reason)`` for a tool call.

        ``reason`` is a short human/model-readable string; when a call is
        denied the kernel surfaces it back to the model as the tool result
        (errors-as-data), so it must explain *why* and hint a legal path.
        """
        arguments = arguments or {}

        offending = self.hits_sensitive_path(arguments)
        if offending is not None:
            return (
                PermissionDecision.DENY,
                (
                    f"Access to '{offending}' is blocked: it matches the "
                    "non-overridable sensitive-path denylist (credentials / "
                    "secrets). This cannot be enabled by configuration."
                ),
            )

        argument = self._argument_string(tool_name, arguments)
        matched: PermissionRule | None = None
        for rule in self.rules:  # last match wins
            if rule.matches(tool_name, argument):
                matched = rule
        if matched is not None:
            return matched.action, (
                f"matched rule {matched.permission!r} pattern {matched.pattern!r} "
                f"→ {matched.action.value}"
            )

        read_only = self.is_read_only(tool_name)
        if self.mode is PermissionMode.PLAN:
            if read_only:
                return PermissionDecision.ALLOW, "plan mode: read-only tool allowed"
            return (
                PermissionDecision.DENY,
                "plan mode: mutating tools are denied; only read-only "
                "exploration is permitted until the plan is approved.",
            )
        if self.mode is PermissionMode.FULL_AUTO:
            return PermissionDecision.ALLOW, "full_auto mode: no implicit gate"
        # DEFAULT
        if read_only:
            return PermissionDecision.ALLOW, "default mode: read-only tool allowed"
        return (
            PermissionDecision.ASK,
            "default mode: mutating tool requires confirmation",
        )


def rules_from_config(
    config: Mapping[str, object] | None,
) -> list[PermissionRule]:
    """Build an ordered ruleset from a nested ``{perm: {pattern: action}}`` map.

    Also accepts the shorthand ``{perm: "allow"}`` (pattern ``*``). Unknown
    action strings raise ``ValueError`` so typos fail loudly.
    """
    rules: list[PermissionRule] = []
    if not config:
        return rules
    for permission, spec in config.items():
        if isinstance(spec, str):
            rules.append(
                PermissionRule(permission, "*", PermissionDecision(spec.lower()))
            )
            continue
        if isinstance(spec, Mapping):
            # Emit least-specific patterns first so that, under last-match-wins,
            # a specific pattern beats a broad one regardless of dict order.
            # This makes the natural authoring ``{"git push *": "ask", "*":
            # "allow"}`` do the intuitive thing (specific wins).
            for pattern, action in sorted(
                spec.items(), key=lambda kv: _pattern_specificity(str(kv[0]))
            ):
                rules.append(
                    PermissionRule(
                        permission,
                        str(pattern),
                        PermissionDecision(str(action).lower()),
                    )
                )
            continue
        raise ValueError(
            f"Permission spec for {permission!r} must be a string or mapping, "
            f"got {type(spec).__name__}"
        )
    return rules


def make_engine(
    mode: str | PermissionMode = PermissionMode.DEFAULT,
    *,
    rules_config: Mapping[str, object] | None = None,
    extra_read_only: Iterable[str] | None = None,
    cwd: str | None = None,
) -> PermissionEngine:
    """Convenience constructor from loosely-typed inputs (config/CLI)."""
    resolved_mode = mode if isinstance(mode, PermissionMode) else PermissionMode(mode)
    return PermissionEngine(
        mode=resolved_mode,
        rules=rules_from_config(rules_config),
        read_only_tools=frozenset(extra_read_only or ()),
        cwd=cwd,
    )
