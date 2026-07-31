"""Resolve one immutable execution-security snapshot at Turn admission.

The product surfaces choose a small access preset.  Workers never reinterpret
that choice: the complete security profile is persisted on the Turn before it
can be scheduled.  Legacy permission-mode and sandbox settings remain a
fallback for existing installations and automation definitions.
"""

from __future__ import annotations

import os
from collections.abc import Sequence

from core.config import load_config_for_workspace
from core.domain.execution_permission import ExecutionPermissionMode
from core.domain.execution_security import (
    ExecutionAccessPreset,
    ExecutionSecurityProfile,
    normalize_permission_rules,
)
from core.domain.runtime_coordination import ExecutionClass
from core.domain.thread import Thread
from core.domain.turn import Turn

_AUTOMATION_CLASSES = frozenset(
    {
        ExecutionClass.MANUAL_AUTOMATION,
        ExecutionClass.EVENT_AUTOMATION,
        ExecutionClass.SCHEDULED_AUTOMATION,
        ExecutionClass.BACKFILL,
    }
)


class ExecutionSecurityPolicy:
    """Single admission-time policy shared by CLI, Desktop, and workers."""

    def resolve(
        self,
        thread: Thread,
        *,
        explicit_profile: ExecutionSecurityProfile | None = None,
        explicit_permission_mode: ExecutionPermissionMode | None = None,
        goal_turns: Sequence[Turn] = (),
    ) -> ExecutionSecurityProfile:
        if explicit_profile is not None:
            return explicit_profile

        # Automation owns an immutable run policy.  Its first Turn supplies an
        # explicit legacy mode; later Goal continuations inherit the frozen
        # snapshot instead of being widened by a Session UI change.
        if explicit_permission_mode is not None:
            return self._legacy_profile(
                thread.workspace_path,
                permission_mode=explicit_permission_mode,
            )
        automation_owner = next(
            (
                turn
                for turn in goal_turns
                if turn.execution_class in _AUTOMATION_CLASSES
            ),
            None,
        )
        if automation_owner is not None:
            if automation_owner.execution_security_profile is not None:
                return automation_owner.execution_security_profile
            if automation_owner.execution_permission_mode is not None:
                # A legacy mode does not contain the old sandbox or custom
                # permission rules. Treating missing rules as empty could
                # silently widen an Automation after upgrade, so continuation
                # uses a least-privilege profile until the user starts a new
                # run with an explicit complete snapshot.
                return ExecutionSecurityProfile.for_preset(
                    ExecutionAccessPreset.READ_ONLY
                )
            # Old Automation owner Turns may predate both durable fields. Their
            # exact grant cannot be reconstructed, so continuation fails safe
            # to Ask instead of consulting a possibly wider current Session.
            return ExecutionSecurityProfile.for_preset(ExecutionAccessPreset.ASK)

        if thread.access_preset_override is not None:
            return self._preset_profile(
                thread.workspace_path,
                thread.access_preset_override,
            )

        return self.resolve_default(thread.workspace_path)

    def resolve_default(
        self,
        workspace: str,
        *,
        security_config: object | None = None,
    ) -> ExecutionSecurityProfile:
        """Resolve the effective default for product surfaces and admission.

        AppServer settings projections use this same method so clients never
        reconstruct environment/legacy security policy from partial fields.
        """

        config = security_config or load_config_for_workspace(workspace).security
        preset = getattr(config, "access_preset", None)
        if preset is not None:
            return ExecutionSecurityProfile.for_preset(
                ExecutionAccessPreset(preset),
                permission_rules=normalize_permission_rules(
                    getattr(config, "permissions", None)
                ),
            )

        raw_environment_mode = (
            os.environ.get("DEEPCODE_PERMISSION_MODE", "").strip().lower()
        )
        if raw_environment_mode:
            try:
                mode = ExecutionPermissionMode(raw_environment_mode)
            except ValueError:
                mode = ExecutionPermissionMode.DEFAULT
            return self._legacy_profile(
                workspace,
                permission_mode=mode,
                security_config=config,
            )

        if _field_was_set(config, "permission_mode"):
            try:
                mode = ExecutionPermissionMode(str(config.permission_mode))
            except ValueError:
                mode = ExecutionPermissionMode.DEFAULT
            return self._legacy_profile(
                workspace,
                permission_mode=mode,
                security_config=config,
            )

        # New product Sessions use the same safe default on every surface.
        # Direct legacy harness callers keep their established client defaults.
        return ExecutionSecurityProfile.for_preset(
            ExecutionAccessPreset.ASK,
            permission_rules=normalize_permission_rules(
                getattr(config, "permissions", None)
            ),
        )

    @staticmethod
    def _preset_profile(
        workspace: str,
        preset: ExecutionAccessPreset,
    ) -> ExecutionSecurityProfile:
        config = load_config_for_workspace(workspace).security
        return ExecutionSecurityProfile.for_preset(
            preset,
            permission_rules=normalize_permission_rules(
                getattr(config, "permissions", None)
            ),
        )

    @staticmethod
    def _legacy_profile(
        workspace: str,
        *,
        permission_mode: ExecutionPermissionMode,
        security_config: object | None = None,
    ) -> ExecutionSecurityProfile:
        config = security_config or load_config_for_workspace(workspace).security
        sandbox = bool(getattr(config, "sandbox", True))
        raw_sandbox = os.environ.get("DEEPCODE_SANDBOX", "").strip().lower()
        if raw_sandbox:
            sandbox = raw_sandbox not in {"0", "false", "no", "off"}
        return ExecutionSecurityProfile.from_legacy(
            permission_mode,
            command_sandbox=sandbox,
            permission_rules=normalize_permission_rules(
                getattr(config, "permissions", None)
            ),
        )


def _field_was_set(config: object, field: str) -> bool:
    fields_set = getattr(config, "model_fields_set", None)
    if fields_set is None:
        fields_set = getattr(config, "__fields_set__", ())
    return field in fields_set


__all__ = ["ExecutionSecurityPolicy"]
