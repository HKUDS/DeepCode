"""Atomic user/project enablement policy for Skills."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from core.config import home_config_path, project_config_path
from core.skills.models import SkillScope, SkillValidationError

_SKILL_ID_RE = re.compile(r"^sk_[0-9a-f]{24}$")
_MAX_DISABLED_SKILLS = 10_000


class SkillPolicyStore:
    """Read layered policy and mutate one explicit configuration scope."""

    def __init__(
        self,
        workspace: str | Path,
        *,
        user_config: str | Path | None = None,
        project_config: str | Path | None = None,
    ) -> None:
        # Imported lazily to keep the Skills domain independent from the
        # application package's eager public facade during module bootstrap.
        from core.application.config_store import ConfigStore

        self.workspace = Path(workspace).expanduser().resolve(strict=False)
        self.user_store = ConfigStore(user_config or home_config_path())
        self.project_store = ConfigStore(
            project_config or project_config_path(self.workspace)
        )

    def effective_disabled(self) -> frozenset[str]:
        try:
            return frozenset(
                (
                    *self._read_disabled(self.user_store),
                    *self._read_disabled(self.project_store),
                )
            )
        except (OSError, ValueError) as exc:
            raise SkillValidationError(f"invalid Skill policy: {exc}") from exc

    def disabled_for_scope(self, scope: SkillScope) -> frozenset[str]:
        try:
            return frozenset(self._read_disabled(self._store(scope)))
        except (OSError, ValueError) as exc:
            raise SkillValidationError(f"invalid Skill policy: {exc}") from exc

    def set_enabled(
        self,
        skill_id: str,
        *,
        enabled: bool,
        scope: SkillScope,
    ) -> frozenset[str]:
        _validate_skill_id(skill_id)
        store = self._store(scope)

        def mutate(current: dict) -> dict:
            skills = current.get("skills")
            skill_settings = dict(skills) if isinstance(skills, dict) else {}
            disabled = set(_disabled_from_raw(skill_settings))
            if enabled:
                disabled.discard(skill_id)
            else:
                disabled.add(skill_id)
            if len(disabled) > _MAX_DISABLED_SKILLS:
                raise ValueError(
                    f"disabled Skills exceeds {_MAX_DISABLED_SKILLS} entries"
                )
            skill_settings["disabled"] = sorted(disabled)
            updated = dict(current)
            updated["skills"] = skill_settings
            return updated

        updated = store.mutate(mutate)
        return frozenset(_disabled_from_raw(updated.get("skills")))

    def _store(self, scope: SkillScope) -> Any:
        return self.user_store if scope is SkillScope.USER else self.project_store

    @staticmethod
    def _read_disabled(store: Any) -> tuple[str, ...]:
        return _disabled_from_raw(store.read().get("skills"))


def _disabled_from_raw(value) -> tuple[str, ...]:
    if not isinstance(value, dict):
        return ()
    disabled = value.get("disabled", [])
    if not isinstance(disabled, list):
        raise ValueError("skills.disabled must be an array")
    result: list[str] = []
    seen: set[str] = set()
    for raw in disabled:
        if not isinstance(raw, str):
            raise ValueError("skills.disabled entries must be Skill IDs")
        _validate_skill_id(raw)
        if raw not in seen:
            result.append(raw)
            seen.add(raw)
    if len(result) > _MAX_DISABLED_SKILLS:
        raise ValueError(f"skills.disabled exceeds {_MAX_DISABLED_SKILLS} entries")
    return tuple(result)


def _validate_skill_id(skill_id: str) -> None:
    if not _SKILL_ID_RE.fullmatch(skill_id):
        raise ValueError("invalid Skill ID")


__all__ = ["SkillPolicyStore"]
