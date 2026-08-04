"""Deterministic, cached discovery of project and user Skills."""

from __future__ import annotations

import hashlib
import re
import threading
from collections.abc import Callable, Iterator
from dataclasses import replace
from pathlib import Path
from typing import Any

import yaml

from core.skills.models import (
    SkillKey,
    SkillRecord,
    SkillResolutionError,
    SkillScope,
    SkillSourceRoot,
    SkillStatus,
    SkillValidationError,
)
from core.skills.metadata import OPENAI_METADATA_PATH, read_skill_metadata
from core.skills.roots import discover_skill_roots

SKILL_FILENAME = "SKILL.md"
MAX_SKILL_BYTES = 64 * 1024
MAX_DESCRIPTION_CHARS = 1_000
MAX_ALLOWED_TOOLS = 128
MAX_CATALOG_WARNINGS = 100
MAX_CATALOG_ENTRIES = 256

_FRONTMATTER_RE = re.compile(r"^---[ \t]*\r?\n(.*?)\r?\n---[ \t]*\r?\n", re.DOTALL)
_TEXT_MENTION_RE = re.compile(
    r"(?<![\w$])\$([^\s$.,;:!?()\[\]{}<>\"']+)",
    re.UNICODE,
)


class SkillCatalog:
    """Immutable view retaining active, shadowed, disabled, and invalid entries."""

    def __init__(
        self,
        records: tuple[SkillRecord, ...],
        *,
        warnings: tuple[str, ...] = (),
    ) -> None:
        self._records = records
        self._warnings = warnings
        self._by_id: dict[str, SkillRecord] = {}
        active: dict[str, SkillRecord] = {}
        names: dict[str, list[SkillRecord]] = {}
        all_names: set[str] = set()
        for record in records:
            self._by_id.setdefault(record.id, record)
            all_names.add(record.name.casefold())
            if record.status is SkillStatus.ACTIVE:
                active[record.name.casefold()] = record
            if record.selectable:
                names.setdefault(record.name.casefold(), []).append(record)
        self._active_by_name = active
        self._all_names = frozenset(all_names)
        self._selectable_by_name = {
            name: tuple(candidates) for name, candidates in names.items()
        }
        digest = hashlib.sha256()
        for record in records:
            digest.update(record.id.encode())
            digest.update(record.revision.encode())
            digest.update(record.status.value.encode())
            digest.update((record.shadowed_by or "").encode())
        self.revision = f"sha256:{digest.hexdigest()}"

    @property
    def records(self) -> tuple[SkillRecord, ...]:
        return self._records

    @property
    def warnings(self) -> tuple[str, ...]:
        invalid = tuple(
            (record.error or f"{record.name}: invalid Skill")[:2_000]
            for record in self._records
            if record.status is SkillStatus.INVALID
        )
        metadata = tuple(
            warning for record in self._records for warning in record.metadata.warnings
        )
        return (*self._warnings, *invalid, *metadata)[:MAX_CATALOG_WARNINGS]

    def active(self) -> tuple[SkillRecord, ...]:
        return tuple(
            sorted(
                (
                    record
                    for record in self._records
                    if record.status is SkillStatus.ACTIVE
                ),
                key=lambda item: item.name.casefold(),
            )
        )

    def get(self, skill_id: str) -> SkillRecord | None:
        return self._by_id.get(skill_id)

    def get_active(self, name: str) -> SkillRecord | None:
        return self._active_by_name.get(name.casefold())

    def implicit(self) -> tuple[SkillRecord, ...]:
        return tuple(
            record
            for record in self.active()
            if record.metadata.allow_implicit_invocation
        )

    def get_implicit(self, name: str) -> SkillRecord | None:
        record = self.get_active(name)
        if record is None or not record.metadata.allow_implicit_invocation:
            return None
        return record

    def select_id(self, skill_id: str) -> SkillRecord:
        record = self.get(skill_id)
        if record is None:
            raise SkillResolutionError(f"Skill is no longer available: {skill_id}")
        if record.status is SkillStatus.DISABLED:
            raise SkillResolutionError(f"Skill is disabled: {record.name}")
        if record.status is SkillStatus.INVALID:
            raise SkillResolutionError(
                f"Skill is invalid: {record.name}: {record.error or 'validation failed'}"
            )
        return record

    def select_name(self, name: str) -> SkillRecord:
        candidates = self._selectable_by_name.get(name.casefold(), ())
        if not candidates:
            disabled = [
                record
                for record in self._records
                if record.name.casefold() == name.casefold()
                and record.status is SkillStatus.DISABLED
            ]
            if disabled:
                raise SkillResolutionError(f"Skill is disabled: {name}")
            raise SkillResolutionError(f"Skill is not available: {name}")
        if len(candidates) > 1:
            sources = ", ".join(record.source for record in candidates)
            raise SkillResolutionError(
                f"Skill name is ambiguous: {name} ({sources}); select it by ID"
            )
        return candidates[0]

    def text_mentions(self, text: str) -> tuple[SkillRecord, ...]:
        selected: list[SkillRecord] = []
        seen: set[str] = set()
        for match in _TEXT_MENTION_RE.finditer(text):
            mentioned_name = match.group(1)
            # Shell variables, prices, and template syntax also use `$`.
            # Unknown tokens remain ordinary user text.
            if mentioned_name.casefold() not in self._all_names:
                continue
            record = self.select_name(mentioned_name)
            if record.id not in seen:
                selected.append(record)
                seen.add(record.id)
        return tuple(selected)


def discover_skill_catalog(
    workspace: str | Path,
    *,
    home: str | Path | None = None,
    working_directory: str | Path | None = None,
    include_user: bool = True,
    include_system: bool = True,
    disabled_ids: frozenset[str] = frozenset(),
) -> SkillCatalog:
    """Discover every direct child Skill in deterministic precedence order."""

    records: list[SkillRecord] = []
    truncated = False
    for root_spec in discover_skill_roots(
        workspace,
        working_directory=working_directory,
        home=home,
        include_user=include_user,
        include_system=include_system,
    ):
        for directory in _iter_skill_directories(root_spec.path):
            if len(records) >= MAX_CATALOG_ENTRIES:
                truncated = True
                break
            records.append(
                _read_candidate(
                    root=root_spec.path,
                    directory=directory,
                    scope=root_spec.scope,
                    source_root=root_spec.source_root,
                    trust_boundary=root_spec.trust_boundary,
                    writable=root_spec.writable,
                )
            )
        if truncated:
            break

    winner_by_name: dict[str, SkillRecord] = {}
    resolved: list[SkillRecord] = []
    for record in records:
        if record.id in disabled_ids:
            resolved.append(replace(record, status=SkillStatus.DISABLED))
            continue
        if record.status is SkillStatus.INVALID:
            resolved.append(record)
            continue
        folded = record.name.casefold()
        winner = winner_by_name.get(folded)
        if winner is None:
            active = replace(record, status=SkillStatus.ACTIVE)
            winner_by_name[folded] = active
            resolved.append(active)
        else:
            resolved.append(
                replace(
                    record,
                    status=SkillStatus.SHADOWED,
                    shadowed_by=winner.id,
                )
            )
    warnings = (
        (
            (
                f"Skill catalog is limited to {MAX_CATALOG_ENTRIES} entries; "
                "remaining directories were not loaded"
            ),
        )
        if truncated
        else ()
    )
    return SkillCatalog(tuple(resolved), warnings=warnings)


class SkillCatalogProvider:
    """Thread-safe catalog cache invalidated by filesystem or policy changes."""

    def __init__(
        self,
        workspace: str | Path,
        *,
        home: str | Path | None = None,
        working_directory: str | Path | None = None,
        include_user: bool = True,
        include_system: bool = True,
        disabled_loader: Callable[[], frozenset[str]] | None = None,
    ) -> None:
        self.workspace = Path(workspace).expanduser().resolve(strict=False)
        self.home = (
            Path(home).expanduser().resolve(strict=False) if home is not None else None
        )
        self.working_directory = (
            Path(working_directory).expanduser().resolve(strict=False)
            if working_directory is not None
            else self.workspace
        )
        self.include_user = include_user
        self.include_system = include_system
        self.disabled_loader = disabled_loader or (lambda: frozenset())
        self._lock = threading.RLock()
        self._signature: tuple[Any, ...] | None = None
        self._catalog: SkillCatalog | None = None

    def catalog(self, *, force: bool = False) -> SkillCatalog:
        disabled = self.disabled_loader()
        signature = (*self._filesystem_signature(), tuple(sorted(disabled)))
        with self._lock:
            if not force and self._catalog is not None and signature == self._signature:
                return self._catalog
            catalog = discover_skill_catalog(
                self.workspace,
                working_directory=self.working_directory,
                home=self.home,
                include_user=self.include_user,
                include_system=self.include_system,
                disabled_ids=disabled,
            )
            self._catalog = catalog
            self._signature = signature
            return catalog

    def invalidate(self) -> None:
        with self._lock:
            self._signature = None

    def _filesystem_signature(self) -> tuple[Any, ...]:
        values: list[Any] = []
        for root_spec in discover_skill_roots(
            self.workspace,
            working_directory=self.working_directory,
            home=self.home,
            include_user=self.include_user,
            include_system=self.include_system,
        ):
            root = root_spec.path
            try:
                root_stat = root.stat()
                values.append((str(root), root_stat.st_mtime_ns, root_stat.st_size))
            except OSError:
                values.append((str(root), None, None))
                continue
            for directory in _iter_skill_directories(root):
                skill_file = directory / SKILL_FILENAME
                for tracked_file in (skill_file, directory / OPENAI_METADATA_PATH):
                    try:
                        stat = tracked_file.stat()
                        values.append(
                            (str(tracked_file), stat.st_mtime_ns, stat.st_size)
                        )
                    except OSError:
                        values.append((str(tracked_file), None, None))
        return tuple(values)


def validate_skill_directory(
    directory: str | Path,
    *,
    scope: SkillScope = SkillScope.USER,
    source_root: SkillSourceRoot = SkillSourceRoot.DEEPCODE,
) -> SkillRecord:
    unresolved = Path(directory).expanduser()
    path = unresolved.resolve(strict=True)
    if not path.is_dir():
        raise SkillValidationError("Skill source must be a directory")
    record = _read_candidate(
        root=path.parent,
        directory=path,
        scope=scope,
        source_root=source_root,
        trust_boundary=None,
        writable=False,
    )
    if record.status is SkillStatus.INVALID:
        raise SkillValidationError(record.error or "Skill validation failed")
    return replace(record, status=SkillStatus.ACTIVE)


def _iter_skill_directories(root: Path) -> Iterator[Path]:
    try:
        children = sorted(root.iterdir(), key=lambda item: item.name.casefold())
    except OSError:
        return
    for child in children:
        try:
            if child.is_dir() and (child / SKILL_FILENAME).is_file():
                yield child
        except OSError:
            continue


def _read_candidate(
    *,
    root: Path,
    directory: Path,
    scope: SkillScope,
    source_root: SkillSourceRoot,
    trust_boundary: Path | None,
    writable: bool,
) -> SkillRecord:
    unresolved_file = directory / SKILL_FILENAME
    fallback_name = directory.name
    byte_size = 0
    revision = "sha256:unavailable"
    try:
        root.resolve(strict=True)
        skill_file = unresolved_file.resolve(strict=True)
        if trust_boundary is not None:
            # Repository aliases may organize Skills, but must not load
            # instructions from outside the trusted workspace. User-owned
            # aliases may point at plugin or cache locations.
            # The discovery layer supplies the workspace boundary explicitly.
            # Following a symlinked `skills/` directory must not move that
            # boundary outside the trusted workspace.
            skill_file.relative_to(trust_boundary.resolve(strict=True))
        stat = skill_file.stat()
        byte_size = stat.st_size
        if byte_size > MAX_SKILL_BYTES:
            raise SkillValidationError(
                f"{skill_file}: SKILL.md exceeds {MAX_SKILL_BYTES} bytes"
            )
        payload = skill_file.read_bytes()
        metadata, metadata_payload = read_skill_metadata(skill_file.parent)
        revision_payload = payload
        if metadata_payload is not None:
            revision_payload += b"\0agents/openai.yaml\0" + metadata_payload
        revision = f"sha256:{hashlib.sha256(revision_payload).hexdigest()}"
        text = payload.decode("utf-8")
        name, description, instructions, allowed_tools = _parse_text(
            text,
            fallback_name=fallback_name,
            path=skill_file,
        )
        key = _key(
            root=root.absolute(),
            skill_file=unresolved_file.absolute(),
            scope=scope,
            source_root=source_root,
            writable=writable,
        )
        return SkillRecord(
            key=key,
            name=name,
            description=description,
            instructions=instructions,
            allowed_tools=allowed_tools,
            revision=revision,
            status=SkillStatus.ACTIVE,
            byte_size=byte_size,
            metadata=metadata,
        )
    except (OSError, ValueError) as exc:
        skill_file = unresolved_file.absolute()
        key = _key(
            root=root.absolute(),
            skill_file=skill_file,
            scope=scope,
            source_root=source_root,
            writable=writable,
        )
        return SkillRecord(
            key=key,
            name=fallback_name,
            description="",
            instructions="",
            allowed_tools=(),
            revision=revision,
            status=SkillStatus.INVALID,
            byte_size=byte_size,
            error=str(exc)[:2_000],
        )


def _key(
    *,
    root: Path,
    skill_file: Path,
    scope: SkillScope,
    source_root: SkillSourceRoot,
    writable: bool,
) -> SkillKey:
    try:
        relative = str(skill_file.parent.relative_to(root))
    except ValueError:
        relative = skill_file.parent.name
    return SkillKey(
        scope=scope,
        source_root=source_root,
        skill_file=skill_file,
        relative_path=relative,
        writable=writable,
    )


def _parse_text(
    text: str,
    *,
    fallback_name: str,
    path: Path,
) -> tuple[str, str, str, tuple[str, ...]]:
    match = _FRONTMATTER_RE.match(text)
    if not match:
        raise SkillValidationError(
            f"{path}: SKILL.md must open with a YAML frontmatter block"
        )
    try:
        metadata = yaml.safe_load(match.group(1)) or {}
    except yaml.YAMLError as exc:
        raise SkillValidationError(f"{path}: invalid YAML frontmatter: {exc}") from exc
    if not isinstance(metadata, dict):
        raise SkillValidationError(f"{path}: frontmatter must be a mapping")

    name = str(metadata.get("name") or fallback_name).strip()
    _validate_name(name, path)
    description = str(metadata.get("description") or "").strip()
    if not description:
        raise SkillValidationError(f"{path}: Skill {name!r} needs a description")
    if len(description) > MAX_DESCRIPTION_CHARS:
        raise SkillValidationError(
            f"{path}: description exceeds {MAX_DESCRIPTION_CHARS} characters"
        )
    instructions = text[match.end() :].strip()
    if not instructions:
        raise SkillValidationError(f"{path}: Skill {name!r} has no instructions")
    allowed_tools = _coerce_tools(
        metadata.get("allowed-tools", metadata.get("allowed_tools")),
        path=path,
    )
    return name, description, instructions, allowed_tools


def _validate_name(name: str, path: Path) -> None:
    if not name or len(name) > 80:
        raise SkillValidationError(f"{path}: Skill name must be 1-80 characters")
    if not name[0].isalnum() or any(
        not (character.isalnum() or character in "._-") for character in name
    ):
        raise SkillValidationError(
            f"{path}: Skill name may contain letters, numbers, '.', '_' and '-'"
        )


def _coerce_tools(value: Any, *, path: Path) -> tuple[str, ...]:
    if not value:
        return ()
    if isinstance(value, str):
        raw = value.split(",")
    elif isinstance(value, (list, tuple)):
        raw = value
    else:
        raise SkillValidationError(f"{path}: allowed-tools must be a list or CSV")
    tools: list[str] = []
    seen: set[str] = set()
    for item in raw:
        tool = str(item).strip()
        if not tool or any(character.isspace() for character in tool):
            raise SkillValidationError(f"{path}: invalid allowed-tools entry")
        if tool not in seen:
            tools.append(tool)
            seen.add(tool)
    if len(tools) > MAX_ALLOWED_TOOLS:
        raise SkillValidationError(
            f"{path}: allowed-tools exceeds {MAX_ALLOWED_TOOLS} entries"
        )
    return tuple(tools)


__all__ = [
    "MAX_SKILL_BYTES",
    "MAX_CATALOG_ENTRIES",
    "SKILL_FILENAME",
    "SkillCatalog",
    "SkillCatalogProvider",
    "discover_skill_catalog",
    "validate_skill_directory",
]
