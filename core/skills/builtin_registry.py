"""Stable product roles for bundled Skills.

Bundled roles live in one backend registry so frontends never infer product
behavior from a display name.  The mapping is intentionally limited to
DeepCode-owned system packages; user and provider Skills cannot claim a role.
"""

from __future__ import annotations

from enum import StrEnum

from core.skills.models import (
    LOCAL_SKILL_AUTHORITY,
    SkillRecord,
    SkillScope,
    SkillSourceRoot,
)


class BuiltinSkillRole(StrEnum):
    AUTHORING = "authoring"


_ROLES_BY_PACKAGE = {
    "skill-creator": BuiltinSkillRole.AUTHORING,
}


def builtin_role(record: SkillRecord) -> BuiltinSkillRole | None:
    if (
        record.authority != LOCAL_SKILL_AUTHORITY
        or record.scope is not SkillScope.SYSTEM
        or record.source_root is not SkillSourceRoot.SYSTEM
    ):
        return None
    return _ROLES_BY_PACKAGE.get(record.key.relative_path)


def find_builtin_skill(
    records: tuple[SkillRecord, ...],
    role: BuiltinSkillRole,
) -> SkillRecord | None:
    return next(
        (
            record
            for record in records
            if builtin_role(record) is role
            and record.selectable
            and record.policy_enabled
        ),
        None,
    )


__all__ = ["BuiltinSkillRole", "builtin_role", "find_builtin_skill"]
