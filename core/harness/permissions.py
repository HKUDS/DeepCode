"""Three-valued permission engine for tool calls.

Mechanism, not policy: this module *decides* allow / ask / deny for a tool
invocation. It never prompts, executes, or talks to a model — callers wire
the decision into the kernel (see ``AgentRunSpec.permission_checker``) and
turn ``ASK`` into whatever their surface needs (auto-deny in headless runs,
a real prompt in interactive ones).

Design distilled from the reference harnesses (see
DEEPCODE_V2_MASTER_PLAN.md §4.3), adapted rather than copied:

* **Central sensitive-path protection** (OpenHarness): credential stores
  (``.ssh``, ``.aws/credentials``, ``.env`` …) are blocked ahead of rules in
  legacy, Ask, and Read-only profiles. The explicit Full Access preset disables
  this boundary together with the command sandbox, matching its honest
  unrestricted-filesystem promise.
* **Two-dimensional wildcard rules, last-match-wins** (opencode): a rule
  matches on both the permission name (usually the tool name) and an
  argument pattern (e.g. the bash command string or the target path), so
  ``{"bash": {"git push *": "ask", "*": "allow"}}`` is expressible. Later
  rules override earlier ones.
* **Three modes**: ``default`` (mutating tools ask), ``plan`` (mutating
  tools denied — read-only exploration), ``full_auto`` (rules only, no
  implicit ask; used by non-interactive workflows).

Evaluation precedence (first decisive wins):
  1. sensitive-path protection          → DENY   (when enabled)
  2. canonical Read only upper bound    → DENY   (mutating tools)
  3. explicit rule match (last-wins)    → its action
  4. mode default for read-only vs mutating tools
"""

from __future__ import annotations

import fnmatch
import os
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from core.domain.execution_security import (
    ApprovalPolicy,
    ExecutionPermissionRuleSnapshot,
    normalize_permission_rules,
)


class PermissionDecision(str, Enum):
    ALLOW = "allow"
    ASK = "ask"
    DENY = "deny"


class PermissionMode(str, Enum):
    DEFAULT = "default"
    PLAN = "plan"
    FULL_AUTO = "full_auto"


# Credential / secret locations protected by every profile except the explicit
# Full Access preset. Rules cannot override this boundary while it is enabled.
# Patterns are matched against normalized absolute
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
_PATCH_PATH_PREFIXES = (
    "*** Add File: ",
    "*** Update File: ",
    "*** Delete File: ",
    "*** Move to: ",
)

# Tool names that read state, or are otherwise side-effect-free w.r.t. the
# workspace and security posture; safe to auto-allow in default mode and never
# blocked by plan mode. Bare names + common MCP suffixes are matched.
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
        "update_plan",  # self-maintained TODO plan — pure session state
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
    approval_policy:
        Controls whether an ``ask`` decision can be surfaced to an approver.
    protect_sensitive_paths:
        Enables the central credential-path boundary.
    enforce_read_only:
        Makes mutating-tool denial an upper bound that rules cannot broaden.
        This is enabled only by the canonical Read only product preset; the
        default remains false for legacy rule compatibility.
    """

    mode: PermissionMode = PermissionMode.DEFAULT
    rules: list[PermissionRule] = field(default_factory=list)
    read_only_tools: frozenset[str] = field(default_factory=frozenset)
    cwd: str | None = None
    approval_policy: ApprovalPolicy = ApprovalPolicy.ON_REQUEST
    protect_sensitive_paths: bool = True
    enforce_read_only: bool = False

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
        # apply_patch carries paths in a structured patch envelope rather than
        # separate JSON fields. Extract only its declarative path directives so
        # the same central protection applies without a tool-name special case.
        patch = arguments.get("patch")
        if isinstance(patch, str):
            for line in patch.splitlines():
                for prefix in _PATCH_PATH_PREFIXES:
                    if line.startswith(prefix):
                        candidate = line[len(prefix) :].strip()
                        if candidate:
                            paths.append(candidate)
                        break
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

        offending = (
            self.hits_sensitive_path(arguments)
            if self.protect_sensitive_paths
            else None
        )
        if offending is not None:
            return (
                PermissionDecision.DENY,
                (
                    f"Access to '{offending}' is blocked: it matches the "
                    "non-overridable sensitive-path denylist (credentials / "
                    "secrets). Permission rules cannot override this boundary."
                ),
            )

        read_only = self.is_read_only(tool_name)
        if self.enforce_read_only and not read_only:
            return (
                PermissionDecision.DENY,
                "read-only access preset: mutating tools cannot be enabled by rules",
            )

        argument = self._argument_string(tool_name, arguments)
        matched: PermissionRule | None = None
        for rule in self.rules:  # last match wins
            if rule.matches(tool_name, argument):
                matched = rule
        if matched is not None:
            return self._apply_approval_policy(
                matched.action,
                (
                    f"matched rule {matched.permission!r} pattern {matched.pattern!r} "
                    f"→ {matched.action.value}"
                ),
            )

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
        return self._apply_approval_policy(
            PermissionDecision.ASK,
            "default mode: mutating tool requires confirmation",
        )

    def _apply_approval_policy(
        self,
        decision: PermissionDecision,
        reason: str,
    ) -> tuple[PermissionDecision, str]:
        """Make ``never`` fail closed instead of silently auto-approving ASK.

        Full Access uses ``FULL_AUTO`` so ordinary calls do not ask.  An
        explicit user rule may still resolve to ASK, however, and a headless or
        otherwise non-interactive profile must not turn that into permission.
        """

        if (
            decision is PermissionDecision.ASK
            and self.approval_policy is ApprovalPolicy.NEVER
        ):
            return (
                PermissionDecision.DENY,
                f"{reason}; approval policy is never, so the request is denied",
            )
        return decision, reason


def rules_from_config(
    config: Mapping[str, object] | None,
) -> list[PermissionRule]:
    """Build an ordered ruleset from a nested ``{perm: {pattern: action}}`` map.

    Also accepts the shorthand ``{perm: "allow"}`` (pattern ``*``). Unknown
    action strings raise ``ValueError`` so typos fail loudly.
    """
    return rules_from_snapshots(normalize_permission_rules(config))


def rules_from_snapshots(
    rules: Iterable[ExecutionPermissionRuleSnapshot],
) -> list[PermissionRule]:
    """Adapt immutable domain snapshots to the harness evaluator type."""

    return [
        PermissionRule(
            permission=rule.permission,
            pattern=rule.pattern,
            action=PermissionDecision(rule.action.value),
        )
        for rule in rules
    ]


def make_engine(
    mode: str | PermissionMode = PermissionMode.DEFAULT,
    *,
    rules_config: Mapping[str, object] | None = None,
    extra_read_only: Iterable[str] | None = None,
    cwd: str | None = None,
    approval_policy: ApprovalPolicy = ApprovalPolicy.ON_REQUEST,
    protect_sensitive_paths: bool = True,
    enforce_read_only: bool = False,
) -> PermissionEngine:
    """Convenience constructor from loosely-typed inputs (config/CLI)."""
    resolved_mode = mode if isinstance(mode, PermissionMode) else PermissionMode(mode)
    return PermissionEngine(
        mode=resolved_mode,
        rules=rules_from_config(rules_config),
        read_only_tools=frozenset(extra_read_only or ()),
        cwd=cwd,
        approval_policy=approval_policy,
        protect_sensitive_paths=protect_sensitive_paths,
        enforce_read_only=enforce_read_only,
    )
