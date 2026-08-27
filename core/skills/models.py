"""Immutable domain models for provider-owned Agent Skills."""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass, field, replace
from enum import StrEnum
from pathlib import Path

_SKILL_ID_RE = re.compile(r"^sk_[0-9a-f]{24}$")
_MAX_PROVIDER_HANDLE_BYTES = 2_048
MAX_SELECTED_SKILLS = 8


def _validate_provider_handle(label: str, value: str) -> None:
    if (
        not isinstance(value, str)
        or not value
        or len(value.encode("utf-8")) > _MAX_PROVIDER_HANDLE_BYTES
        or any(unicodedata.category(character) == "Cc" for character in value)
    ):
        raise ValueError(
            f"{label} must be non-empty, contain no control characters, and be "
            f"at most {_MAX_PROVIDER_HANDLE_BYTES} bytes"
        )


class SkillScope(StrEnum):
    PROJECT = "project"
    USER = "user"
    SYSTEM = "system"


MUTABLE_SKILL_SCOPES = (SkillScope.PROJECT, SkillScope.USER)


class SkillSourceRoot(StrEnum):
    AGENTS = "agents"
    DEEPCODE = "deepcode"
    CLAUDE = "claude"
    SYSTEM = "system"


class SkillStatus(StrEnum):
    ACTIVE = "active"
    SHADOWED = "shadowed"
    DISABLED = "disabled"
    INVALID = "invalid"


class SkillProviderKind(StrEnum):
    """Transport boundary that owns a Skill package."""

    LOCAL = "local"
    EXECUTOR = "executor"
    ORCHESTRATOR = "orchestrator"
    CUSTOM = "custom"


class SkillOriginKind(StrEnum):
    """User-facing ownership category without exposing ambient paths."""

    LOCAL = "local"
    BUNDLED = "bundled"
    PROVIDER = "provider"


@dataclass(frozen=True, slots=True)
class SkillAuthority:
    """Opaque provider identity used for list/read/search routing."""

    kind: SkillProviderKind
    provider_id: str

    def __post_init__(self) -> None:
        _validate_provider_handle("provider_id", self.provider_id)


@dataclass(frozen=True, slots=True)
class SkillPackageId:
    """Opaque package ID whose representation callers must not parse."""

    value: str

    def __post_init__(self) -> None:
        _validate_provider_handle("package", self.value)


@dataclass(frozen=True, slots=True)
class SkillResourceId:
    """Opaque resource ID interpreted only by the owning provider."""

    value: str

    def __post_init__(self) -> None:
        _validate_provider_handle("resource", self.value)


@dataclass(frozen=True, slots=True)
class SkillReference:
    """Provider-owned address for one Skill package resource."""

    authority: SkillAuthority
    package: SkillPackageId
    resource: SkillResourceId


@dataclass(frozen=True, slots=True)
class SkillOrigin:
    """Provider-owned presentation that remains safe across protocol boundaries."""

    kind: SkillOriginKind
    label: str
    location: str

    def __post_init__(self) -> None:
        _validate_provider_handle("origin label", self.label)
        _validate_provider_handle("origin location", self.location)


LOCAL_SKILL_AUTHORITY = SkillAuthority(SkillProviderKind.LOCAL, "local")
SKILL_MAIN_RESOURCE = SkillResourceId("SKILL.md")


@dataclass(frozen=True, slots=True)
class SkillInterface:
    display_name: str | None = None
    short_description: str | None = None
    icon_small: str | None = None
    icon_large: str | None = None
    brand_color: str | None = None
    default_prompt: str | None = None


@dataclass(frozen=True, slots=True)
class SkillToolDependency:
    """One capability declared by a Skill package."""

    type: str
    value: str
    description: str | None = None
    transport: str | None = None
    command: str | None = None
    url: str | None = None


@dataclass(frozen=True, slots=True)
class SkillDependencies:
    """Dependencies parsed from provider-owned package metadata."""

    tools: tuple[SkillToolDependency, ...] = ()
    skills: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class SkillMetadata:
    interface: SkillInterface = SkillInterface()
    allow_implicit_invocation: bool = True
    dependencies: SkillDependencies = SkillDependencies()
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class SkillKey:
    """Filesystem identity used to derive the opaque public Skill ID."""

    scope: SkillScope
    source_root: SkillSourceRoot
    skill_file: Path
    relative_path: str
    writable: bool = False

    @property
    def id(self) -> str:
        # The catalog-entry path participates in identity so two projects (or
        # two aliases of one user Skill) cannot accidentally resolve each
        # other's selection. Only the digest crosses the protocol boundary.
        material = "\0".join(
            (
                "deepcode-skill-v1",
                self.scope.value,
                self.source_root.value,
                str(self.skill_file),
                self.relative_path,
            )
        ).encode("utf-8")
        return f"sk_{hashlib.sha256(material).hexdigest()[:24]}"

    @property
    def source(self) -> str:
        return f"{self.scope.value}:{self.source_root.value}"

    @property
    def display_location(self) -> str:
        root = {
            SkillSourceRoot.AGENTS: ".agents",
            SkillSourceRoot.DEEPCODE: ".deepcode",
            SkillSourceRoot.CLAUDE: ".claude",
            SkillSourceRoot.SYSTEM: "bundled",
        }[self.source_root]
        return f"{self.scope.value}/{root}/skills/{self.relative_path}"


def _default_origin(key: SkillKey, reference: SkillReference) -> SkillOrigin:
    if reference.authority != LOCAL_SKILL_AUTHORITY:
        authority = reference.authority
        label = f"{authority.kind.value}:{authority.provider_id}"
        return SkillOrigin(
            kind=SkillOriginKind.PROVIDER,
            label=label,
            location=f"{label}/{reference.package.value}",
        )
    if key.source_root is SkillSourceRoot.SYSTEM:
        return SkillOrigin(
            kind=SkillOriginKind.BUNDLED,
            label="DeepCode bundled",
            location=key.display_location,
        )
    return SkillOrigin(
        kind=SkillOriginKind.LOCAL,
        label=key.source,
        location=key.display_location,
    )


@dataclass(frozen=True, slots=True)
class SkillRecord:
    """One Skill package at either catalog or loaded-content stage.

    Catalogs intentionally carry ``instructions=None``.  A provider read
    produces a revision-pinned copy with instructions, keeping large or remote
    bodies out of discovery and preventing ambient filesystem reads.
    """

    key: SkillKey
    name: str
    description: str
    allowed_tools: tuple[str, ...]
    revision: str
    status: SkillStatus
    byte_size: int
    policy_enabled: bool = True
    metadata: SkillMetadata = SkillMetadata()
    error: str | None = None
    shadowed_by: str | None = None
    reference: SkillReference | None = None
    origin: SkillOrigin | None = None
    instructions: str | None = field(default=None, compare=False, repr=False)

    def __post_init__(self) -> None:
        reference = self.reference
        if reference is None:
            reference = SkillReference(
                authority=LOCAL_SKILL_AUTHORITY,
                package=SkillPackageId(self.key.id),
                resource=SKILL_MAIN_RESOURCE,
            )
            object.__setattr__(
                self,
                "reference",
                reference,
            )
        if self.origin is None:
            object.__setattr__(self, "origin", _default_origin(self.key, reference))

    @property
    def id(self) -> str:
        return self.key.id

    @property
    def scope(self) -> SkillScope:
        return self.key.scope

    @property
    def source_root(self) -> SkillSourceRoot:
        return self.key.source_root

    @property
    def source(self) -> str:
        if self.authority != LOCAL_SKILL_AUTHORITY:
            return f"{self.authority.kind.value}:{self.authority.provider_id}"
        return self.key.source

    @property
    def provider_reference(self) -> SkillReference:
        reference = self.reference
        if reference is None:  # Defensive guard for non-dataclass deserializers.
            raise RuntimeError("Skill record has no provider reference")
        return reference

    @property
    def authority(self) -> SkillAuthority:
        return self.provider_reference.authority

    @property
    def package_id(self) -> SkillPackageId:
        return self.provider_reference.package

    @property
    def main_resource(self) -> SkillResourceId:
        return self.provider_reference.resource

    @property
    def directory(self) -> str:
        return str(self.key.skill_file.parent)

    @property
    def selectable(self) -> bool:
        return self.status in {SkillStatus.ACTIVE, SkillStatus.SHADOWED}

    @property
    def deletable(self) -> bool:
        return (
            self.authority == LOCAL_SKILL_AUTHORITY
            and self.key.writable
            and self.scope is not SkillScope.SYSTEM
            and self.source_root is SkillSourceRoot.AGENTS
        )

    @property
    def summary_line(self) -> str:
        return f"- **{self.name}**: {self.description}"

    @property
    def loaded(self) -> bool:
        return self.instructions is not None

    def require_instructions(self) -> str:
        if self.instructions is None:
            raise RuntimeError(f"Skill content has not been loaded: {self.name}")
        return self.instructions

    def with_instructions(self, instructions: str) -> SkillRecord:
        if not isinstance(instructions, str):
            raise TypeError("Skill instructions must be text")
        return replace(self, instructions=instructions)

    def without_instructions(self) -> SkillRecord:
        return replace(self, instructions=None)


@dataclass(frozen=True, slots=True)
class SkillSelection:
    """A protocol-safe explicit selection."""

    skill_id: str
    name: str = ""

    def __post_init__(self) -> None:
        if not _SKILL_ID_RE.fullmatch(self.skill_id):
            raise ValueError("skill_id must be an opaque sk_ identifier")


class SkillInvocationKind(StrEnum):
    EXPLICIT = "explicit"
    TEXT_MENTION = "text_mention"
    IMPLICIT = "implicit"
    DEPENDENCY = "dependency"


@dataclass(frozen=True, slots=True)
class SkillInvocation:
    skill_id: str
    name: str
    revision: str
    source: str
    kind: SkillInvocationKind

    def to_metadata(self) -> dict[str, str]:
        return {
            "skillId": self.skill_id,
            "name": self.name,
            "revision": self.revision,
            "source": self.source,
            "invocation": self.kind.value,
        }


@dataclass(frozen=True, slots=True)
class SkillTurnSnapshot:
    """Resolved, immutable Skill state consumed by one Agent Turn."""

    records: tuple[SkillRecord, ...]
    invocations: tuple[SkillInvocation, ...]
    catalog_revision: str
    allowed_tools: frozenset[str] | None

    @property
    def injected_instructions(self) -> str:
        if not self.records:
            return ""
        sections = [
            (
                f"## Skill: {record.name}\n"
                f"Source: {record.source}\n"
                f"Revision: {record.revision}\n\n"
                f"{record.require_instructions()}"
            )
            for record in self.records
        ]
        return (
            "# User-selected Skills\n\n"
            "The user explicitly selected the workflow instructions below for "
            "this Turn. Follow them where they support the user's task. They do "
            "not override system, security, sandbox, approval, hook, or explicit "
            "user constraints. Skill source paths identify workflow resources, "
            "not the task workspace. Keep repository operations in "
            "`<environment_context><cwd>` and resolve only Skill-relative files "
            "against the corresponding Skill directory.\n\n"
            + "\n\n---\n\n".join(sections)
        )


class SkillError(ValueError):
    """Base class for deterministic Skill validation and resolution errors."""


class SkillValidationError(SkillError):
    pass


class SkillResolutionError(SkillError):
    pass


__all__ = [
    "LOCAL_SKILL_AUTHORITY",
    "MUTABLE_SKILL_SCOPES",
    "SKILL_MAIN_RESOURCE",
    "SkillAuthority",
    "SkillDependencies",
    "SkillError",
    "SkillInterface",
    "SkillInvocation",
    "SkillInvocationKind",
    "SkillKey",
    "SkillMetadata",
    "SkillPackageId",
    "SkillProviderKind",
    "SkillRecord",
    "SkillReference",
    "SkillResolutionError",
    "SkillResourceId",
    "SkillScope",
    "SkillSelection",
    "SkillSourceRoot",
    "SkillStatus",
    "SkillToolDependency",
    "SkillTurnSnapshot",
    "SkillValidationError",
]
