"""Immutable, transport-neutral execution security snapshots."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from core.domain.execution_permission import ExecutionPermissionMode


class ExecutionAccessPreset(StrEnum):
    """User-facing access choices shared by every DeepCode client."""

    ASK = "ask"
    READ_ONLY = "read_only"
    FULL_ACCESS = "full_access"


class FilesystemScope(StrEnum):
    """Filesystem boundary enforced for one execution."""

    WORKSPACE = "workspace"
    UNRESTRICTED = "unrestricted"


class ApprovalPolicy(StrEnum):
    """Whether an execution may suspend for an explicit user decision."""

    ON_REQUEST = "on_request"
    NEVER = "never"


class ExecutionPermissionRuleAction(StrEnum):
    """Frozen action for one user-authored permission rule."""

    ALLOW = "allow"
    ASK = "ask"
    DENY = "deny"


def parse_access_preset_override(
    metadata: Mapping[str, object],
) -> ExecutionAccessPreset | None:
    """Decode the canonical Session override without fail-open fallback.

    The snake-case key is authoritative even when explicitly null.  A legacy
    camel-case alias is read only when the canonical key is absent.  Invalid
    externally edited metadata is an error: ``None`` means inheritance and
    could otherwise widen a damaged Session to a Full access default.
    """

    raw = (
        metadata["access_preset_override"]
        if "access_preset_override" in metadata
        else metadata.get("accessPresetOverride")
    )
    if raw is None:
        return None
    try:
        return ExecutionAccessPreset(str(raw).strip().lower())
    except ValueError as exc:
        raise ValueError("invalid Session access preset override") from exc


@dataclass(frozen=True, slots=True)
class ExecutionPermissionRuleSnapshot:
    """Transport-neutral permission rule captured with a Turn."""

    permission: str
    pattern: str
    action: ExecutionPermissionRuleAction

    def __post_init__(self) -> None:
        if not isinstance(self.permission, str) or not self.permission.strip():
            raise ValueError("permission rule name must be a non-empty string")
        if not isinstance(self.pattern, str):
            raise TypeError("permission rule pattern must be a string")
        if not isinstance(self.action, ExecutionPermissionRuleAction):
            raise TypeError("permission rule action must be typed")

    def to_dict(self) -> dict[str, str]:
        return {
            "permission": self.permission,
            "pattern": self.pattern,
            "action": self.action.value,
        }

    @classmethod
    def from_dict(cls, value: Any) -> ExecutionPermissionRuleSnapshot | None:
        if not isinstance(value, dict):
            return None
        try:
            return cls(
                permission=value["permission"],
                pattern=value["pattern"],
                action=ExecutionPermissionRuleAction(value["action"]),
            )
        except (KeyError, TypeError, ValueError):
            return None


def normalize_permission_rules(
    config: Mapping[str, object] | None,
) -> tuple[ExecutionPermissionRuleSnapshot, ...]:
    """Normalize the public nested rules config into a stable ordered tuple.

    The accepted shape remains ``{tool: action}`` or
    ``{tool: {argumentPattern: action}}``. Less-specific patterns are emitted
    first so the existing last-match-wins evaluator gives specific patterns
    precedence independent of JSON object ordering.
    """

    if config is None:
        return ()
    if not isinstance(config, Mapping):
        raise ValueError("permissions config must be an object")
    if not config:
        return ()
    rules: list[ExecutionPermissionRuleSnapshot] = []
    for raw_permission, spec in config.items():
        permission = str(raw_permission)
        if isinstance(spec, str):
            rules.append(
                ExecutionPermissionRuleSnapshot(
                    permission=permission,
                    pattern="*",
                    action=ExecutionPermissionRuleAction(spec.lower()),
                )
            )
            continue
        if isinstance(spec, Mapping):
            for raw_pattern, raw_action in sorted(
                spec.items(),
                key=lambda pair: _pattern_specificity(str(pair[0])),
            ):
                rules.append(
                    ExecutionPermissionRuleSnapshot(
                        permission=permission,
                        pattern=str(raw_pattern),
                        action=ExecutionPermissionRuleAction(str(raw_action).lower()),
                    )
                )
            continue
        raise ValueError(
            f"Permission spec for {permission!r} must be a string or mapping, "
            f"got {type(spec).__name__}"
        )
    return tuple(rules)


def _pattern_specificity(pattern: str) -> int:
    if pattern == "*":
        return 0
    return sum(1 for character in pattern if character not in "*?")


_PRESET_PROFILES: dict[
    ExecutionAccessPreset,
    tuple[ExecutionPermissionMode, bool, FilesystemScope, ApprovalPolicy],
] = {
    ExecutionAccessPreset.ASK: (
        ExecutionPermissionMode.DEFAULT,
        True,
        FilesystemScope.WORKSPACE,
        ApprovalPolicy.ON_REQUEST,
    ),
    ExecutionAccessPreset.READ_ONLY: (
        ExecutionPermissionMode.PLAN,
        True,
        FilesystemScope.WORKSPACE,
        ApprovalPolicy.NEVER,
    ),
    ExecutionAccessPreset.FULL_ACCESS: (
        ExecutionPermissionMode.FULL_AUTO,
        False,
        FilesystemScope.UNRESTRICTED,
        ApprovalPolicy.NEVER,
    ),
}


@dataclass(frozen=True, slots=True)
class ExecutionSecurityProfile:
    """Complete, immutable security facts captured for one Turn.

    ``access_preset`` is nullable only for legacy compatibility.  A legacy
    ``full_auto`` permission mode may still have command sandboxing enabled,
    so it must not be relabelled as the stronger ``full_access`` product
    promise.  New user selections are created through :meth:`for_preset` and
    therefore always carry a non-null preset and its canonical policy tuple.
    """

    access_preset: ExecutionAccessPreset | None
    permission_mode: ExecutionPermissionMode
    command_sandbox: bool
    filesystem_scope: FilesystemScope
    approval_policy: ApprovalPolicy
    permission_rules: tuple[ExecutionPermissionRuleSnapshot, ...] = ()

    def __post_init__(self) -> None:
        if self.access_preset is not None and not isinstance(
            self.access_preset, ExecutionAccessPreset
        ):
            raise TypeError("access_preset must be an ExecutionAccessPreset or None")
        if not isinstance(self.permission_mode, ExecutionPermissionMode):
            raise TypeError("permission_mode must be an ExecutionPermissionMode")
        if not isinstance(self.command_sandbox, bool):
            raise TypeError("command_sandbox must be a bool")
        if not isinstance(self.filesystem_scope, FilesystemScope):
            raise TypeError("filesystem_scope must be a FilesystemScope")
        if not isinstance(self.approval_policy, ApprovalPolicy):
            raise TypeError("approval_policy must be an ApprovalPolicy")
        if not isinstance(self.permission_rules, tuple) or any(
            not isinstance(rule, ExecutionPermissionRuleSnapshot)
            for rule in self.permission_rules
        ):
            raise TypeError("permission_rules must be a tuple of rule snapshots")
        if self.access_preset is None:
            return
        expected = _PRESET_PROFILES[self.access_preset]
        actual = (
            self.permission_mode,
            self.command_sandbox,
            self.filesystem_scope,
            self.approval_policy,
        )
        if actual != expected:
            raise ValueError(
                f"security facts do not match the {self.access_preset.value!r} "
                "access preset"
            )

    @classmethod
    def for_preset(
        cls,
        preset: ExecutionAccessPreset | str,
        *,
        permission_rules: tuple[ExecutionPermissionRuleSnapshot, ...] = (),
    ) -> ExecutionSecurityProfile:
        """Resolve one user-facing preset into canonical execution facts."""

        selected = (
            preset
            if isinstance(preset, ExecutionAccessPreset)
            else ExecutionAccessPreset(preset)
        )
        permission_mode, sandbox, scope, approvals = _PRESET_PROFILES[selected]
        return cls(
            access_preset=selected,
            permission_mode=permission_mode,
            command_sandbox=sandbox,
            filesystem_scope=scope,
            approval_policy=approvals,
            permission_rules=permission_rules,
        )

    @classmethod
    def from_legacy(
        cls,
        permission_mode: ExecutionPermissionMode | str,
        *,
        command_sandbox: bool,
        filesystem_scope: FilesystemScope | str = FilesystemScope.WORKSPACE,
        approval_policy: ApprovalPolicy | str = ApprovalPolicy.ON_REQUEST,
        permission_rules: tuple[ExecutionPermissionRuleSnapshot, ...] = (),
    ) -> ExecutionSecurityProfile:
        """Capture old mode/config facts without claiming a product preset."""

        return cls(
            access_preset=None,
            permission_mode=(
                permission_mode
                if isinstance(permission_mode, ExecutionPermissionMode)
                else ExecutionPermissionMode(permission_mode)
            ),
            command_sandbox=command_sandbox,
            filesystem_scope=(
                filesystem_scope
                if isinstance(filesystem_scope, FilesystemScope)
                else FilesystemScope(filesystem_scope)
            ),
            approval_policy=(
                approval_policy
                if isinstance(approval_policy, ApprovalPolicy)
                else ApprovalPolicy(approval_policy)
            ),
            permission_rules=permission_rules,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "accessPreset": (
                self.access_preset.value if self.access_preset is not None else None
            ),
            "permissionMode": self.permission_mode.value,
            "commandSandbox": self.command_sandbox,
            "filesystemScope": self.filesystem_scope.value,
            "approvalPolicy": self.approval_policy.value,
            "permissionRules": [rule.to_dict() for rule in self.permission_rules],
        }

    @classmethod
    def from_dict(cls, value: Any) -> ExecutionSecurityProfile | None:
        """Decode persisted data, returning ``None`` for invalid snapshots."""

        if not isinstance(value, dict):
            return None
        try:
            raw_preset = value.get("accessPreset")
            preset = (
                ExecutionAccessPreset(raw_preset) if raw_preset is not None else None
            )
            command_sandbox = value["commandSandbox"]
            if not isinstance(command_sandbox, bool):
                return None
            raw_rules = value.get("permissionRules", [])
            if not isinstance(raw_rules, list):
                return None
            permission_rules: list[ExecutionPermissionRuleSnapshot] = []
            for raw_rule in raw_rules:
                rule = ExecutionPermissionRuleSnapshot.from_dict(raw_rule)
                if rule is None:
                    return None
                permission_rules.append(rule)
            return cls(
                access_preset=preset,
                permission_mode=ExecutionPermissionMode(value["permissionMode"]),
                command_sandbox=command_sandbox,
                filesystem_scope=FilesystemScope(value["filesystemScope"]),
                approval_policy=ApprovalPolicy(value["approvalPolicy"]),
                permission_rules=tuple(permission_rules),
            )
        except (KeyError, TypeError, ValueError):
            return None


__all__ = [
    "ApprovalPolicy",
    "ExecutionAccessPreset",
    "ExecutionPermissionRuleAction",
    "ExecutionPermissionRuleSnapshot",
    "ExecutionSecurityProfile",
    "FilesystemScope",
    "normalize_permission_rules",
    "parse_access_preset_override",
]
