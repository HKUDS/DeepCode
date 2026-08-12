"""Deterministic, cached discovery of project and user Skills."""

from __future__ import annotations

import hashlib
import re
import threading
from collections.abc import Callable, Iterator
from dataclasses import replace
from enum import StrEnum
from pathlib import Path
from typing import Any

import yaml

from core.skills.metadata import OPENAI_METADATA_PATH, read_skill_metadata
from core.skills.models import (
    LOCAL_SKILL_AUTHORITY,
    SkillAuthority,
    SkillKey,
    SkillPackageId,
    SkillRecord,
    SkillResolutionError,
    SkillScope,
    SkillSourceRoot,
    SkillStatus,
    SkillValidationError,
)
from core.skills.provider import (
    SkillListQuery,
    SkillReadRequest,
    SkillReadResult,
    SkillSearchRequest,
    SkillSearchMatch,
    SkillSearchResult,
)
from core.skills.roots import discover_skill_roots

SKILL_FILENAME = "SKILL.md"
MAX_SKILL_BYTES = 64 * 1024
MAX_DESCRIPTION_CHARS = 1_000
MAX_AGENT_SKILL_DESCRIPTION_CHARS = 1_024
MAX_AGENT_SKILL_COMPATIBILITY_CHARS = 500
MAX_ALLOWED_TOOLS = 128
MAX_CATALOG_WARNINGS = 100
MAX_CATALOG_ENTRIES = 256

_FRONTMATTER_RE = re.compile(r"^---[ \t]*\r?\n(.*?)\r?\n---[ \t]*\r?\n", re.DOTALL)
_TEXT_MENTION_RE = re.compile(
    r"(?<![\w$])\$([^\s$.,;:!?()\[\]{}<>\"']+)",
    re.UNICODE,
)
_AGENT_SKILL_NAME_RE = re.compile(r"^(?!.*--)[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$")


class SkillValidationProfile(StrEnum):
    """Validation contract selected by the owner of a Skill package."""

    DEEPCODE_COMPAT = "deepcode-compat"
    AGENT_SKILLS_V1 = "agent-skills-v1"


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
        self._by_id: dict[str, list[SkillRecord]] = {}
        self._by_package: dict[tuple[SkillAuthority, SkillPackageId], SkillRecord] = {}
        active: dict[str, SkillRecord] = {}
        names: dict[str, list[SkillRecord]] = {}
        all_names: set[str] = set()
        for record in records:
            self._by_id.setdefault(record.id, []).append(record)
            self._by_package.setdefault(
                (record.authority, record.package_id),
                record,
            )
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
            if record.authority != LOCAL_SKILL_AUTHORITY:
                digest.update(record.authority.kind.value.encode())
                digest.update(record.authority.provider_id.encode())
                digest.update(record.package_id.value.encode())
            digest.update(record.revision.encode())
            digest.update(record.status.value.encode())
            digest.update(b"1" if record.policy_enabled else b"0")
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

    @property
    def provider_warnings(self) -> tuple[str, ...]:
        """Warnings supplied by the owner, excluding record-derived diagnostics."""

        return self._warnings

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
        candidates = self._by_id.get(skill_id, ())
        return candidates[0] if len(candidates) == 1 else None

    def get_package(
        self,
        authority: SkillAuthority,
        package: SkillPackageId,
    ) -> SkillRecord | None:
        return self._by_package.get((authority, package))

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
        candidates = self._by_id.get(skill_id, ())
        if not candidates:
            raise SkillResolutionError(f"Skill is no longer available: {skill_id}")
        if len(candidates) > 1:
            authorities = ", ".join(
                f"{record.authority.kind.value}:{record.authority.provider_id}"
                for record in candidates
            )
            raise SkillResolutionError(
                f"Skill ID is ambiguous across providers: {skill_id} ({authorities})"
            )
        record = candidates[0]
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
            resolved.append(
                replace(
                    record,
                    status=SkillStatus.DISABLED,
                    policy_enabled=False,
                )
            )
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
    return SkillCatalog(
        tuple(record.without_instructions() for record in resolved),
        warnings=warnings,
    )


class LocalSkillProvider:
    """Local/system Skill provider with a filesystem-aware catalog cache."""

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

    def list(self, query: SkillListQuery) -> SkillCatalog:
        disabled = self.disabled_loader()
        signature = (*self._filesystem_signature(), tuple(sorted(disabled)))
        with self._lock:
            if (
                not query.force
                and self._catalog is not None
                and signature == self._signature
            ):
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

    def read(self, request: SkillReadRequest) -> SkillReadResult:
        from core.skills.resources import read_local_resource

        self._validate_authority(request.authority)
        record = self.list(SkillListQuery()).get_package(
            request.authority,
            request.package,
        )
        if record is None:
            raise SkillResolutionError(f"Skill not found: {request.package.value}")
        contents, resource_revision = read_local_resource(
            record,
            request.resource,
            workspace=self.workspace,
        )
        package_revision = record.revision
        if request.resource == record.main_resource:
            loaded = validate_skill_directory(
                record.directory,
                scope=record.scope,
                source_root=record.source_root,
            )
            if loaded.id != record.id:
                raise SkillResolutionError("Local Skill identity changed during read")
            contents = loaded.require_instructions()
            package_revision = loaded.revision
        return SkillReadResult(
            reference=request.reference,
            contents=contents,
            package_revision=package_revision,
            resource_revision=resource_revision,
        )

    def search(self, request: SkillSearchRequest) -> SkillSearchResult:
        self._validate_authority(request.authority)
        query = request.query.strip().casefold()
        if not query:
            return SkillSearchResult()
        matches: list[SkillSearchMatch] = []
        catalog = self.list(SkillListQuery())
        if request.package is None:
            candidates = catalog.records
        else:
            record = catalog.get_package(request.authority, request.package)
            if record is None:
                raise SkillResolutionError(f"Skill not found: {request.package.value}")
            from core.skills.resources import search_local_resources

            return SkillSearchResult(
                matches=search_local_resources(
                    record,
                    request.query,
                    workspace=self.workspace,
                    limit=request.limit,
                )
            )
        for record in candidates:
            interface = record.metadata.interface
            searchable = "\n".join(
                value
                for value in (
                    record.name,
                    record.description,
                    interface.display_name,
                    interface.short_description,
                    record.source,
                )
                if value
            ).casefold()
            if query in searchable:
                matches.append(
                    SkillSearchMatch(
                        reference=record.provider_reference,
                        title=record.name,
                        snippet=record.description,
                    )
                )
                if len(matches) >= request.limit:
                    break
        return SkillSearchResult(matches=tuple(matches))

    @staticmethod
    def _validate_authority(authority: SkillAuthority) -> None:
        if authority != LOCAL_SKILL_AUTHORITY:
            raise SkillResolutionError(
                "Local Skill provider does not own authority "
                f"{authority.kind.value}:{authority.provider_id}"
            )

    def catalog(self, *, force: bool = False) -> SkillCatalog:
        """Compatibility alias for callers predating the provider contract."""

        return self.list(SkillListQuery(force=force))

    def invalidate(self) -> None:
        with self._lock:
            self._signature = None

    def skill_change_token(self) -> object:
        """Return the catalog-affecting filesystem and policy fingerprint."""

        return (
            *self._filesystem_signature(),
            tuple(sorted(self.disabled_loader())),
        )

    def _filesystem_signature(self) -> tuple[Any, ...]:
        values: list[Any] = []
        tracked_skills = 0
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
                if tracked_skills >= MAX_CATALOG_ENTRIES:
                    return tuple(values)
                tracked_skills += 1
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


def read_skill_candidate(
    *,
    root: Path,
    directory: Path,
    scope: SkillScope,
    source_root: SkillSourceRoot,
    trust_boundary: Path | None,
    writable: bool,
    validation_profile: SkillValidationProfile = SkillValidationProfile.DEEPCODE_COMPAT,
) -> SkillRecord:
    """Read one Skill candidate while preserving invalid entries.

    Provider implementations use this parser so local, bundled, and Plugin
    Skills share one frontmatter and metadata contract. ``trust_boundary`` is
    explicit: callers that expose a filesystem-backed package must supply the
    directory that every resolved resource is required to remain inside.
    """
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
        metadata_file = directory / OPENAI_METADATA_PATH
        if trust_boundary is not None and (
            metadata_file.exists() or metadata_file.is_symlink()
        ):
            metadata_file.resolve(strict=True).relative_to(
                trust_boundary.resolve(strict=True)
            )
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
            validation_profile=validation_profile,
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


# Private compatibility name for the local discovery implementation. Keeping
# the call sites below small also makes the public provider seam explicit.
_read_candidate = read_skill_candidate


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
    validation_profile: SkillValidationProfile,
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

    if validation_profile is SkillValidationProfile.AGENT_SKILLS_V1:
        name, description, allowed_tools = _parse_agent_skills_metadata(
            metadata,
            directory_name=fallback_name,
            path=path,
        )
    else:
        name = str(metadata.get("name") or fallback_name).strip()
        _validate_name(name, path)
        description = str(metadata.get("description") or "").strip()
        if not description:
            raise SkillValidationError(f"{path}: Skill {name!r} needs a description")
        if len(description) > MAX_DESCRIPTION_CHARS:
            raise SkillValidationError(
                f"{path}: description exceeds {MAX_DESCRIPTION_CHARS} characters"
            )
        allowed_tools = _coerce_tools(
            metadata.get("allowed-tools", metadata.get("allowed_tools")),
            path=path,
        )
    instructions = text[match.end() :].strip()
    if not instructions:
        raise SkillValidationError(f"{path}: Skill {name!r} has no instructions")
    return name, description, instructions, allowed_tools


def _parse_agent_skills_metadata(
    metadata: dict[Any, Any],
    *,
    directory_name: str,
    path: Path,
) -> tuple[str, str, tuple[str, ...]]:
    """Validate the portable Agent Skills frontmatter contract."""

    name = metadata.get("name")
    if not isinstance(name, str):
        raise SkillValidationError(f"{path}: Agent Skill name must be a string")
    if len(name) > 64 or not _AGENT_SKILL_NAME_RE.fullmatch(name):
        raise SkillValidationError(
            f"{path}: Agent Skill name must be 1-64 lowercase letters, numbers, "
            "or hyphens without leading, trailing, or consecutive hyphens"
        )
    if name != directory_name:
        raise SkillValidationError(
            f"{path}: Agent Skill name {name!r} must match directory {directory_name!r}"
        )

    description = metadata.get("description")
    if not isinstance(description, str) or not description:
        raise SkillValidationError(
            f"{path}: Agent Skill description must be a non-empty string"
        )
    if len(description) > MAX_AGENT_SKILL_DESCRIPTION_CHARS:
        raise SkillValidationError(
            f"{path}: Agent Skill description exceeds "
            f"{MAX_AGENT_SKILL_DESCRIPTION_CHARS} characters"
        )

    compatibility = metadata.get("compatibility")
    if compatibility is not None and (
        not isinstance(compatibility, str)
        or not compatibility
        or len(compatibility) > MAX_AGENT_SKILL_COMPATIBILITY_CHARS
    ):
        raise SkillValidationError(
            f"{path}: Agent Skill compatibility must be a 1-"
            f"{MAX_AGENT_SKILL_COMPATIBILITY_CHARS} character string"
        )

    license_name = metadata.get("license")
    if license_name is not None and not isinstance(license_name, str):
        raise SkillValidationError(f"{path}: Agent Skill license must be a string")

    custom_metadata = metadata.get("metadata")
    if custom_metadata is not None and (
        not isinstance(custom_metadata, dict)
        or any(
            not isinstance(key, str) or not isinstance(value, str)
            for key, value in custom_metadata.items()
        )
    ):
        raise SkillValidationError(
            f"{path}: Agent Skill metadata must map strings to strings"
        )

    return (
        name,
        description,
        _coerce_agent_tools(
            metadata.get("allowed-tools"),
            path=path,
        ),
    )


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


def _coerce_agent_tools(value: Any, *, path: Path) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, str):
        raise SkillValidationError(
            f"{path}: Agent Skill allowed-tools must be a space-delimited string"
        )
    tools = tuple(dict.fromkeys(value.split()))
    if not tools:
        raise SkillValidationError(
            f"{path}: Agent Skill allowed-tools cannot be empty when declared"
        )
    if len(tools) > MAX_ALLOWED_TOOLS:
        raise SkillValidationError(
            f"{path}: allowed-tools exceeds {MAX_ALLOWED_TOOLS} entries"
        )
    return tools


# Compatibility for code that imported the original cache-oriented name.
SkillCatalogProvider = LocalSkillProvider


__all__ = [
    "MAX_CATALOG_ENTRIES",
    "MAX_SKILL_BYTES",
    "SKILL_FILENAME",
    "LocalSkillProvider",
    "SkillCatalog",
    "SkillCatalogProvider",
    "SkillValidationProfile",
    "discover_skill_catalog",
    "read_skill_candidate",
    "validate_skill_directory",
]
