"""Skill discovery roots and their trust boundaries."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from core.config import deepcode_home
from core.skills.models import SkillScope, SkillSourceRoot


@dataclass(frozen=True, slots=True)
class SkillRootSpec:
    path: Path
    scope: SkillScope
    source_root: SkillSourceRoot
    trust_boundary: Path | None = None
    writable: bool = False


def discover_skill_roots(
    workspace: str | Path,
    *,
    working_directory: str | Path | None = None,
    home: str | Path | None = None,
    include_user: bool = True,
    include_system: bool = True,
) -> tuple[SkillRootSpec, ...]:
    """Return canonical roots followed by fixed compatibility roots."""

    workspace_path = Path(workspace).expanduser().resolve(strict=False)
    cwd = (
        Path(working_directory).expanduser().resolve(strict=False)
        if working_directory is not None
        else workspace_path
    )
    try:
        cwd.relative_to(workspace_path)
    except ValueError as exc:
        raise ValueError("working_directory must be inside workspace") from exc

    roots: list[SkillRootSpec] = []
    for directory in _ancestors_to_boundary(cwd, workspace_path):
        roots.append(
            SkillRootSpec(
                path=directory / ".agents" / "skills",
                scope=SkillScope.PROJECT,
                source_root=SkillSourceRoot.AGENTS,
                trust_boundary=workspace_path,
                writable=directory == workspace_path,
            )
        )

    roots.extend(
        (
            SkillRootSpec(
                path=workspace_path / ".deepcode" / "skills",
                scope=SkillScope.PROJECT,
                source_root=SkillSourceRoot.DEEPCODE,
                trust_boundary=workspace_path,
            ),
            SkillRootSpec(
                path=workspace_path / ".claude" / "skills",
                scope=SkillScope.PROJECT,
                source_root=SkillSourceRoot.CLAUDE,
                trust_boundary=workspace_path,
            ),
        )
    )

    if include_user:
        if home is not None:
            home_path = Path(home).expanduser().resolve(strict=False)
            legacy_deepcode = home_path / ".deepcode" / "skills"
        else:
            home_path = Path.home().resolve(strict=False)
            legacy_deepcode = deepcode_home() / "skills"
        roots.extend(
            (
                SkillRootSpec(
                    path=home_path / ".agents" / "skills",
                    scope=SkillScope.USER,
                    source_root=SkillSourceRoot.AGENTS,
                    writable=True,
                ),
                SkillRootSpec(
                    path=legacy_deepcode,
                    scope=SkillScope.USER,
                    source_root=SkillSourceRoot.DEEPCODE,
                ),
                SkillRootSpec(
                    path=home_path / ".claude" / "skills",
                    scope=SkillScope.USER,
                    source_root=SkillSourceRoot.CLAUDE,
                ),
            )
        )

    if include_system:
        roots.append(
            SkillRootSpec(
                path=Path(__file__).resolve().parent / "builtin",
                scope=SkillScope.SYSTEM,
                source_root=SkillSourceRoot.SYSTEM,
            )
        )
    return tuple(roots)


def managed_skill_root(
    workspace: str | Path,
    scope: SkillScope,
    *,
    home: str | Path | None = None,
) -> Path:
    if scope is SkillScope.PROJECT:
        return Path(workspace).expanduser().resolve(strict=False) / ".agents" / "skills"
    if scope is SkillScope.USER:
        base = (
            Path(home).expanduser().resolve(strict=False)
            if home is not None
            else Path.home().resolve(strict=False)
        )
        return base / ".agents" / "skills"
    raise ValueError("system Skills are read-only")


def _ancestors_to_boundary(start: Path, boundary: Path) -> tuple[Path, ...]:
    result: list[Path] = []
    current = start
    while True:
        result.append(current)
        if current == boundary:
            return tuple(result)
        current = current.parent


__all__ = ["SkillRootSpec", "discover_skill_roots", "managed_skill_root"]
