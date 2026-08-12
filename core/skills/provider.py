"""Provider contract for Skill discovery and provider-owned access.

The runtime consumes this boundary instead of assuming every Skill comes from
the ambient filesystem.  Local Skills still use :class:`LocalSkillProvider`;
future providers can keep their own resource identity and access rules without
changing Turn resolution.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from core.skills.models import (
    LOCAL_SKILL_AUTHORITY,
    SkillAuthority,
    SkillPackageId,
    SkillProviderKind,
    SkillReference,
    SkillResolutionError,
    SkillResourceId,
)

if TYPE_CHECKING:
    from core.skills.catalog import SkillCatalog

MAX_PROVIDER_RESOURCE_BYTES = 1024 * 1024
MAX_PROVIDER_SEARCH_TEXT_CHARS = 2_000
MAX_PROVIDER_SEARCH_QUERY_CHARS = 1_000


class SkillProviderUnavailableError(SkillResolutionError):
    """One optional Provider could not produce its catalog."""


@dataclass(frozen=True, slots=True)
class SkillListQuery:
    """Options for producing one catalog snapshot."""

    force: bool = False


@dataclass(frozen=True, slots=True)
class SkillReadRequest:
    """Read one resource through the authority that listed its package."""

    authority: SkillAuthority
    package: SkillPackageId
    resource: SkillResourceId

    @classmethod
    def from_reference(cls, reference: SkillReference) -> SkillReadRequest:
        return cls(
            authority=reference.authority,
            package=reference.package,
            resource=reference.resource,
        )

    @property
    def reference(self) -> SkillReference:
        return SkillReference(self.authority, self.package, self.resource)


@dataclass(frozen=True, slots=True)
class SkillReadResult:
    """Revision-pinned text returned by the package-owning Provider."""

    reference: SkillReference
    contents: str
    package_revision: str
    resource_revision: str

    def __post_init__(self) -> None:
        if len(self.contents.encode("utf-8")) > MAX_PROVIDER_RESOURCE_BYTES:
            raise ValueError(
                f"Skill resource exceeds {MAX_PROVIDER_RESOURCE_BYTES} bytes"
            )
        if not self.package_revision or not self.resource_revision:
            raise ValueError("Skill read revisions must not be empty")


@dataclass(frozen=True, slots=True)
class SkillSearchMatch:
    """One bounded provider-owned metadata or resource search match."""

    reference: SkillReference
    title: str
    snippet: str

    def __post_init__(self) -> None:
        if not self.title or len(self.title) > MAX_PROVIDER_SEARCH_TEXT_CHARS:
            raise ValueError("Skill search title is empty or too long")
        if len(self.snippet) > MAX_PROVIDER_SEARCH_TEXT_CHARS:
            raise ValueError("Skill search snippet is too long")


@dataclass(frozen=True, slots=True)
class SkillSearchRequest:
    """Bounded metadata search over one provider's catalog."""

    authority: SkillAuthority
    query: str
    package: SkillPackageId | None = None
    limit: int = 20

    def __post_init__(self) -> None:
        if not 1 <= self.limit <= 100:
            raise ValueError("Skill search limit must be between 1 and 100")
        if len(self.query) > MAX_PROVIDER_SEARCH_QUERY_CHARS:
            raise ValueError(
                f"Skill search query exceeds {MAX_PROVIDER_SEARCH_QUERY_CHARS} "
                "characters"
            )


@dataclass(frozen=True, slots=True)
class SkillSearchResult:
    """Deterministically ordered matches owned by one provider."""

    matches: tuple[SkillSearchMatch, ...] = ()


@runtime_checkable
class SkillProvider(Protocol):
    """Source-specific Skill catalog, read, and search operations.

    Implementations own the identity boundary for every record they list.  A
    caller must read or search through that same provider instead of converting
    a provider record into an ambient filesystem path.
    """

    def list(self, query: SkillListQuery) -> SkillCatalog:
        """Return an immutable catalog snapshot."""

    def read(self, request: SkillReadRequest) -> SkillReadResult:
        """Read one provider-owned package resource."""

    def search(self, request: SkillSearchRequest) -> SkillSearchResult:
        """Search provider-owned catalog metadata or package resources."""


@runtime_checkable
class InvalidatableSkillProvider(Protocol):
    """Optional cache lifecycle implemented by mutable providers."""

    def invalidate(self) -> None:
        """Invalidate cached state before the next list operation."""


@runtime_checkable
class SkillChangeTokenProvider(Protocol):
    """Optional cheap change token for locally mutable provider state.

    The application-lifetime host polls this token outside Turn execution. A
    provider that has its own push subscription can omit this protocol and
    invalidate the host through its contribution owner instead.
    """

    def skill_change_token(self) -> object:
        """Return an equality-comparable token for catalog-affecting state."""


@dataclass(frozen=True, slots=True)
class SkillProviderSource:
    """Bind a Provider to the authorities it is permitted to own."""

    kind: SkillProviderKind
    label: str
    provider: SkillProvider
    provider_ids: frozenset[str] | None = None

    @classmethod
    def for_authority(
        cls,
        authority: SkillAuthority,
        provider: SkillProvider,
        *,
        label: str | None = None,
    ) -> SkillProviderSource:
        return cls(
            kind=authority.kind,
            label=label or authority.provider_id,
            provider=provider,
            provider_ids=frozenset((authority.provider_id,)),
        )

    @classmethod
    def local(cls, provider: SkillProvider) -> SkillProviderSource:
        return cls.for_authority(
            LOCAL_SKILL_AUTHORITY,
            provider,
            label="local",
        )

    def owns(self, authority: SkillAuthority) -> bool:
        return self.kind is authority.kind and (
            self.provider_ids is None or authority.provider_id in self.provider_ids
        )

    def list(self, query: SkillListQuery) -> SkillCatalog:
        catalog = self.provider.list(query)
        identities: set[tuple[SkillAuthority, SkillPackageId]] = set()
        duplicate: tuple[SkillAuthority, SkillPackageId] | None = None
        for record in catalog.records:
            identity = (record.authority, record.package_id)
            if identity in identities:
                duplicate = identity
                break
            identities.add(identity)
        mismatched = next(
            (record for record in catalog.records if not self.owns(record.authority)),
            None,
        )
        if mismatched is not None:
            authority = mismatched.authority
            raise SkillResolutionError(
                f"{self.label} Skill provider listed package "
                f"{mismatched.package_id.value!r} for unowned authority "
                f"{authority.kind.value}:{authority.provider_id}"
            )
        if duplicate is not None:
            authority, package = duplicate
            raise SkillResolutionError(
                f"{self.label} Skill provider listed duplicate package "
                f"{authority.kind.value}:{authority.provider_id}/{package.value}"
            )
        if any(record.loaded for record in catalog.records):
            from core.skills.catalog import SkillCatalog

            catalog = SkillCatalog(
                tuple(record.without_instructions() for record in catalog.records),
                warnings=catalog.provider_warnings,
            )
        return catalog


class SkillProviders:
    """Compose catalogs and route access without weakening ownership."""

    def __init__(self, sources: tuple[SkillProviderSource, ...]) -> None:
        if not sources:
            raise ValueError("SkillProviders requires at least one source")
        self._sources = tuple(sources)
        self._lock = threading.RLock()

    @property
    def sources(self) -> tuple[SkillProviderSource, ...]:
        with self._lock:
            return self._sources

    def replace_sources(self, sources: tuple[SkillProviderSource, ...]) -> None:
        """Replace routing sources without changing active Turn snapshots."""

        if not sources:
            raise ValueError("SkillProviders requires at least one source")
        with self._lock:
            previous = self._sources
            self._sources = tuple(sources)
        self._invalidate_sources(previous)
        self.invalidate()

    def list(self, query: SkillListQuery) -> SkillCatalog:
        from core.skills.aggregation import merge_skill_catalogs

        catalogs: list[SkillCatalog] = []
        warnings: list[str] = []
        for source in self.sources:
            try:
                catalogs.append(source.list(query))
            except SkillProviderUnavailableError as exc:
                warnings.append(f"{source.label} Skills unavailable: {exc}")
        return merge_skill_catalogs(tuple(catalogs), warnings=tuple(warnings))

    def read(self, request: SkillReadRequest) -> SkillReadResult:
        sources = tuple(
            source for source in self.sources if source.owns(request.authority)
        )
        if not sources:
            raise self._not_configured(request.authority)

        last_error: SkillResolutionError | None = None
        for source in sources:
            try:
                result = source.provider.read(request)
            except SkillResolutionError as exc:
                last_error = exc
                continue
            except (TypeError, ValueError) as exc:
                raise SkillResolutionError(
                    f"{source.label} Skill provider returned an invalid resource: {exc}"
                ) from exc
            if result.reference != request.reference:
                raise SkillResolutionError(
                    f"{source.label} Skill provider returned a different resource"
                )
            return result

        if last_error is not None:
            raise last_error
        raise self._not_configured(request.authority)

    def search(self, request: SkillSearchRequest) -> SkillSearchResult:
        sources = tuple(
            source for source in self.sources if source.owns(request.authority)
        )
        if not sources:
            raise self._not_configured(request.authority)

        last_error: SkillResolutionError | None = None
        for source in sources:
            try:
                result = source.provider.search(request)
            except SkillResolutionError as exc:
                last_error = exc
                continue
            except (TypeError, ValueError) as exc:
                raise SkillResolutionError(
                    f"{source.label} Skill provider returned invalid search data: {exc}"
                ) from exc
            if len(result.matches) > request.limit:
                raise SkillResolutionError(
                    f"{source.label} Skill provider exceeded the search limit"
                )
            for match in result.matches:
                reference = match.reference
                if reference.authority != request.authority or (
                    request.package is not None and reference.package != request.package
                ):
                    raise SkillResolutionError(
                        f"{source.label} Skill provider returned a search match "
                        "owned by a different package"
                    )
            return result

        if last_error is not None:
            raise last_error
        raise self._not_configured(request.authority)

    def invalidate(self) -> None:
        self._invalidate_sources(self.sources)

    @staticmethod
    def _invalidate_sources(sources: tuple[SkillProviderSource, ...]) -> None:
        seen: set[int] = set()
        for source in sources:
            provider_identity = id(source.provider)
            if provider_identity in seen:
                continue
            seen.add(provider_identity)
            if isinstance(source.provider, InvalidatableSkillProvider):
                source.provider.invalidate()

    @staticmethod
    def _not_configured(authority: SkillAuthority) -> SkillResolutionError:
        return SkillResolutionError(
            "Skill provider is not configured for authority "
            f"{authority.kind.value}:{authority.provider_id}"
        )


__all__ = [
    "InvalidatableSkillProvider",
    "MAX_PROVIDER_RESOURCE_BYTES",
    "MAX_PROVIDER_SEARCH_QUERY_CHARS",
    "SkillListQuery",
    "SkillProvider",
    "SkillProviderUnavailableError",
    "SkillProviderSource",
    "SkillProviders",
    "SkillReadRequest",
    "SkillReadResult",
    "SkillSearchRequest",
    "SkillSearchMatch",
    "SkillSearchResult",
]
