"""Bounded local access to provider-owned Skill package resources."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path, PurePosixPath

from core.skills.models import (
    SkillRecord,
    SkillReference,
    SkillResolutionError,
    SkillResourceId,
    SkillScope,
)
from core.skills.provider import SkillSearchMatch

MAX_SKILL_RESOURCE_BYTES = 1024 * 1024
MAX_SKILL_SEARCH_FILES = 256
MAX_SKILL_SEARCH_DIRECTORIES = 256
MAX_SKILL_SEARCH_FILE_BYTES = 256 * 1024
MAX_SKILL_SEARCH_SNIPPET_CHARS = 400


def read_local_resource(
    entry: SkillRecord,
    resource: SkillResourceId,
    *,
    workspace: Path,
) -> tuple[str, str]:
    """Read one UTF-8 package resource without crossing its trust boundary."""

    path = _resource_path(entry, resource, workspace=workspace)
    try:
        with path.open("rb") as stream:
            payload = stream.read(MAX_SKILL_RESOURCE_BYTES + 1)
        if len(payload) > MAX_SKILL_RESOURCE_BYTES:
            raise SkillResolutionError(
                f"Skill resource exceeds {MAX_SKILL_RESOURCE_BYTES} bytes: "
                f"{resource.value}"
            )
        contents = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SkillResolutionError(
            f"Skill resource is not UTF-8 text: {resource.value}"
        ) from exc
    except OSError as exc:
        raise SkillResolutionError(
            f"Unable to read Skill resource {resource.value!r}: {exc}"
        ) from exc
    return contents, f"sha256:{hashlib.sha256(payload).hexdigest()}"


def search_local_resources(
    entry: SkillRecord,
    query: str,
    *,
    workspace: Path,
    limit: int,
) -> tuple[SkillSearchMatch, ...]:
    """Search bounded UTF-8 package files in deterministic path order."""

    needle = query.strip().casefold()
    if not needle:
        return ()
    root = _package_root(entry, workspace=workspace)
    matches: list[SkillSearchMatch] = []
    visited = 0
    directories = 0
    for current, directory_names, file_names in os.walk(root, followlinks=False):
        directories += 1
        if directories > MAX_SKILL_SEARCH_DIRECTORIES:
            break
        current_path = Path(current)
        directory_names[:] = sorted(
            name for name in directory_names if not (current_path / name).is_symlink()
        )
        for file_name in sorted(file_names):
            if visited >= MAX_SKILL_SEARCH_FILES or len(matches) >= limit:
                return tuple(matches)
            path = current_path / file_name
            try:
                if path.is_symlink() or not path.is_file():
                    continue
                resolved = path.resolve(strict=True)
                resolved.relative_to(root)
                _enforce_workspace_boundary(entry, resolved, workspace)
                visited += 1
                with resolved.open("rb") as stream:
                    payload = stream.read(MAX_SKILL_SEARCH_FILE_BYTES + 1)
                if len(payload) > MAX_SKILL_SEARCH_FILE_BYTES:
                    continue
                text = payload.decode("utf-8")
            except (OSError, UnicodeDecodeError, ValueError):
                continue
            folded = text.casefold()
            offset = folded.find(needle)
            if offset < 0:
                continue
            relative = resolved.relative_to(root).as_posix()
            snippet = _snippet(text, offset, len(query.strip()))
            matches.append(
                SkillSearchMatch(
                    reference=SkillReference(
                        entry.authority,
                        entry.package_id,
                        SkillResourceId(relative),
                    ),
                    title=relative,
                    snippet=snippet,
                )
            )
    return tuple(matches)


def _resource_path(
    entry: SkillRecord,
    resource: SkillResourceId,
    *,
    workspace: Path,
) -> Path:
    relative = _normalize_resource(resource)
    root = _package_root(entry, workspace=workspace)
    try:
        path = root.joinpath(*relative.parts).resolve(strict=True)
        path.relative_to(root)
        _enforce_workspace_boundary(entry, path, workspace)
    except (OSError, ValueError) as exc:
        raise SkillResolutionError(
            f"Skill resource is outside its package or unavailable: {resource.value}"
        ) from exc
    if not path.is_file():
        raise SkillResolutionError(f"Skill resource is not a file: {resource.value}")
    return path


def _package_root(entry: SkillRecord, *, workspace: Path) -> Path:
    try:
        root = entry.key.skill_file.parent.resolve(strict=True)
        _enforce_workspace_boundary(entry, root, workspace)
    except (OSError, ValueError) as exc:
        raise SkillResolutionError(
            f"Skill package is unavailable: {entry.name}"
        ) from exc
    if not root.is_dir():
        raise SkillResolutionError(f"Skill package is not a directory: {entry.name}")
    return root


def _enforce_workspace_boundary(
    entry: SkillRecord,
    path: Path,
    workspace: Path,
) -> None:
    if entry.scope is SkillScope.PROJECT:
        path.relative_to(workspace.resolve(strict=True))


def _normalize_resource(resource: SkillResourceId) -> PurePosixPath:
    value = resource.value
    if "\\" in value:
        raise SkillResolutionError("Local Skill resource IDs must use '/' separators")
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or not path.parts
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise SkillResolutionError(f"Invalid local Skill resource ID: {value!r}")
    return path


def _snippet(text: str, offset: int, needle_length: int) -> str:
    start = max(0, offset - MAX_SKILL_SEARCH_SNIPPET_CHARS // 3)
    end = min(
        len(text),
        offset + max(needle_length, 1) + 2 * MAX_SKILL_SEARCH_SNIPPET_CHARS // 3,
    )
    snippet = " ".join(text[start:end].split())
    if start:
        snippet = "…" + snippet
    if end < len(text):
        snippet += "…"
    return snippet[: MAX_SKILL_SEARCH_SNIPPET_CHARS + 1]


__all__ = [
    "MAX_SKILL_RESOURCE_BYTES",
    "MAX_SKILL_SEARCH_DIRECTORIES",
    "MAX_SKILL_SEARCH_FILES",
    "read_local_resource",
    "search_local_resources",
]
