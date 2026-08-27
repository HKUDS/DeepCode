from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from core.skills.catalog import (
    LocalSkillProvider,
    SkillCatalog,
    SkillCatalogProvider,
    discover_skill_catalog,
    validate_skill_directory,
)
from core.skills.models import (
    LOCAL_SKILL_AUTHORITY,
    SKILL_MAIN_RESOURCE,
    SkillAuthority,
    SkillInvocationKind,
    SkillPackageId,
    SkillProviderKind,
    SkillRecord,
    SkillReference,
    SkillResolutionError,
    SkillSelection,
)
from core.skills.provider import (
    SkillListQuery,
    SkillProvider,
    SkillProviders,
    SkillProviderSource,
    SkillProviderUnavailableError,
    SkillReadRequest,
    SkillReadResult,
    SkillSearchMatch,
    SkillSearchRequest,
    SkillSearchResult,
)
from core.skills.runtime import SkillRuntime


def _write_skill(
    root: Path,
    name: str,
    *,
    description: str = "A reusable workflow",
    body: str = "Follow the workflow.",
    allowed_tools: str = "",
) -> Path:
    directory = root / name
    directory.mkdir(parents=True, exist_ok=True)
    allowed = f"allowed-tools: {allowed_tools}\n" if allowed_tools else ""
    (directory / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n{allowed}---\n{body}\n",
        encoding="utf-8",
    )
    return directory


def test_provider_refactor_preserves_catalog_and_turn_semantics(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    home = tmp_path / "home"
    workspace.mkdir()
    home.mkdir()
    _write_skill(
        workspace / ".agents" / "skills",
        "review",
        body="Inspect the implementation and evidence.",
        allowed_tools="Read, grep",
    )
    _write_skill(workspace / ".deepcode" / "skills", "review")
    _write_skill(home / ".agents" / "skills", "document")
    invalid = workspace / ".claude" / "skills" / "broken"
    invalid.mkdir(parents=True)
    (invalid / "SKILL.md").write_text("missing frontmatter", encoding="utf-8")

    baseline = discover_skill_catalog(
        workspace,
        home=home,
        include_system=False,
    )
    runtime = SkillRuntime(
        workspace,
        home=home,
        include_system=False,
    )
    catalog = runtime.catalog()

    assert (catalog.records, catalog.warnings, catalog.revision) == (
        baseline.records,
        baseline.warnings,
        baseline.revision,
    )
    review = catalog.get_active("review")
    document = catalog.get_active("document")
    assert review is not None
    assert document is not None

    context, token = runtime.begin_turn(
        "Use $document after the review.",
        (SkillSelection(review.id, review.name),),
    )
    try:
        assert context.snapshot.records == (review, document)
        assert tuple(item.kind for item in context.snapshot.invocations) == (
            SkillInvocationKind.EXPLICIT,
            SkillInvocationKind.TEXT_MENTION,
        )
        assert runtime.allowed_tool_names() == frozenset({"read", "grep", "skill"})
    finally:
        runtime.end_turn(token)


def test_local_provider_implements_list_read_search_contract(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _write_skill(
        workspace / ".agents" / "skills",
        "review-api",
        description="Review public API compatibility",
    )
    _write_skill(
        workspace / ".agents" / "skills",
        "review-tests",
        description="Review test evidence",
    )
    provider = LocalSkillProvider(
        workspace,
        include_user=False,
        include_system=False,
    )

    assert isinstance(provider, SkillProvider)
    catalog = provider.list(SkillListQuery())
    review_api = catalog.get_active("review-api")
    assert review_api is not None
    assert review_api.authority == LOCAL_SKILL_AUTHORITY
    assert review_api.package_id == SkillPackageId(review_api.id)
    assert review_api.main_resource == SKILL_MAIN_RESOURCE
    reference = review_api.provider_reference
    read = provider.read(SkillReadRequest.from_reference(reference))
    assert read.reference == reference
    assert read.contents == "Follow the workflow."
    assert read.package_revision == review_api.revision
    assert provider.search(
        SkillSearchRequest(
            authority=LOCAL_SKILL_AUTHORITY,
            query="REVIEW",
            limit=1,
        )
    ).matches == (
        SkillSearchMatch(
            reference=reference,
            title="review-api",
            snippet="Review public API compatibility",
        ),
    )
    assert (
        provider.search(
            SkillSearchRequest(
                authority=LOCAL_SKILL_AUTHORITY,
                query="not present",
            )
        ).matches
        == ()
    )
    assert provider.catalog() is catalog
    assert SkillCatalogProvider is LocalSkillProvider


class _StaticProvider:
    def __init__(self, catalog: SkillCatalog) -> None:
        self.catalog = catalog
        self.queries: list[SkillListQuery] = []
        self.read_requests: list[SkillReadRequest] = []
        self.search_requests: list[SkillSearchRequest] = []

    def list(self, query: SkillListQuery) -> SkillCatalog:
        self.queries.append(query)
        return self.catalog

    def read(self, request: SkillReadRequest) -> SkillReadResult:
        self.read_requests.append(request)
        record = self.catalog.get(request.package.value)
        if record is None:
            raise AssertionError("test provider received an unknown ID")
        loaded = validate_skill_directory(
            record.directory,
            scope=record.scope,
            source_root=record.source_root,
        )
        return SkillReadResult(
            request.reference,
            loaded.require_instructions(),
            record.revision,
            record.revision,
        )

    def search(self, request: SkillSearchRequest) -> SkillSearchResult:
        self.search_requests.append(request)
        matches = tuple(
            SkillSearchMatch(
                reference=record.provider_reference,
                title=record.name,
                snippet=record.description,
            )
            for record in self.catalog.records
            if record.authority == request.authority
            and (request.package is None or record.package_id == request.package)
            and request.query.casefold() in record.name.casefold()
        )[: request.limit]
        return SkillSearchResult(matches)


class _MismatchedReadProvider(_StaticProvider):
    def __init__(self, catalog: SkillCatalog, returned: SkillRecord) -> None:
        super().__init__(catalog)
        self.returned = returned

    def read(self, request: SkillReadRequest) -> SkillReadResult:
        self.read_requests.append(request)
        return SkillReadResult(
            self.returned.provider_reference,
            "mismatched",
            self.returned.revision,
            self.returned.revision,
        )


class _UnavailableProvider(_StaticProvider):
    def list(self, query: SkillListQuery) -> SkillCatalog:
        del query
        raise SkillProviderUnavailableError("temporary outage")


def test_runtime_depends_only_on_skill_provider_contract(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _write_skill(workspace / ".agents" / "skills", "provider-owned")
    catalog = discover_skill_catalog(
        workspace,
        include_user=False,
        include_system=False,
    )
    provider = _StaticProvider(catalog)
    runtime = SkillRuntime(workspace, skill_provider=provider)

    assert runtime.catalog(force=True).records == catalog.records
    record = catalog.get_active("provider-owned")
    assert record is not None
    loaded = runtime.read(record.id)
    assert loaded == record
    assert loaded.require_instructions() == "Follow the workflow."
    assert tuple(match.title for match in runtime.search("owned").matches) == (
        "provider-owned",
    )
    runtime.invalidate()
    assert provider.queries[0] == SkillListQuery(force=True)
    assert provider.read_requests == [
        SkillReadRequest.from_reference(record.provider_reference)
    ]
    assert provider.search_requests == [
        SkillSearchRequest(
            authority=LOCAL_SKILL_AUTHORITY,
            query="owned",
            limit=20,
        )
    ]


def test_provider_router_uses_exact_authority_and_rejects_wrong_routes(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _write_skill(workspace / ".agents" / "skills", "alpha")
    _write_skill(workspace / ".agents" / "skills", "bravo")
    baseline = discover_skill_catalog(
        workspace,
        include_user=False,
        include_system=False,
    )
    alpha = baseline.get_active("alpha")
    bravo = baseline.get_active("bravo")
    assert alpha is not None
    assert bravo is not None

    authority_a = SkillAuthority(SkillProviderKind.EXECUTOR, "environment-a")
    authority_b = SkillAuthority(SkillProviderKind.EXECUTOR, "environment-b")
    alpha = replace(
        alpha,
        reference=SkillReference(
            authority_a,
            SkillPackageId(alpha.id),
            SKILL_MAIN_RESOURCE,
        ),
    )
    bravo = replace(
        bravo,
        reference=SkillReference(
            authority_b,
            SkillPackageId(bravo.id),
            SKILL_MAIN_RESOURCE,
        ),
    )
    provider_a = _StaticProvider(SkillCatalog((alpha,)))
    provider_b = _StaticProvider(SkillCatalog((bravo,)))
    providers = SkillProviders(
        (
            SkillProviderSource.for_authority(authority_a, provider_a),
            SkillProviderSource.for_authority(authority_b, provider_b),
        )
    )

    request = SkillReadRequest.from_reference(bravo.provider_reference)
    assert providers.read(request).contents == "Follow the workflow."
    assert provider_a.read_requests == []
    assert provider_b.read_requests == [request]

    search_request = SkillSearchRequest(
        authority=authority_b,
        package=bravo.package_id,
        query="bravo",
    )
    assert tuple(match.title for match in providers.search(search_request).matches) == (
        "bravo",
    )
    assert provider_a.search_requests == []
    assert provider_b.search_requests == [search_request]

    unknown = SkillAuthority(SkillProviderKind.EXECUTOR, "environment-missing")
    with pytest.raises(SkillResolutionError, match="not configured"):
        providers.read(
            SkillReadRequest(
                authority=unknown,
                package=bravo.package_id,
                resource=bravo.main_resource,
            )
        )
    with pytest.raises(SkillResolutionError, match="not configured"):
        providers.search(
            SkillSearchRequest(
                authority=unknown,
                package=bravo.package_id,
                query="bravo",
            )
        )

    mismatched_provider = _MismatchedReadProvider(
        SkillCatalog((bravo,)),
        returned=alpha,
    )
    mismatched_router = SkillProviders(
        (SkillProviderSource.for_authority(authority_b, mismatched_provider),)
    )
    with pytest.raises(SkillResolutionError, match="different resource"):
        mismatched_router.read(request)


def test_runtime_preserves_provider_identity_in_turn_snapshot(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _write_skill(workspace / ".agents" / "skills", "remote-review")
    local_catalog = discover_skill_catalog(
        workspace,
        include_user=False,
        include_system=False,
    )
    local_record = local_catalog.get_active("remote-review")
    assert local_record is not None
    authority = SkillAuthority(SkillProviderKind.CUSTOM, "research-runtime")
    remote_record = replace(
        local_record,
        reference=SkillReference(
            authority,
            SkillPackageId(local_record.id),
            SKILL_MAIN_RESOURCE,
        ),
    )
    provider = _StaticProvider(SkillCatalog((remote_record,)))
    source = SkillProviderSource.for_authority(authority, provider)
    runtime = SkillRuntime(workspace, skill_source=source)

    context, token = runtime.begin_turn(
        "Run the review",
        (SkillSelection(remote_record.id),),
    )
    try:
        assert context.snapshot.records == (remote_record,)
        assert context.snapshot.records[0].provider_reference.authority == authority
        assert runtime.read(remote_record.id) == remote_record
    finally:
        runtime.end_turn(token)


def test_provider_source_rejects_catalog_entries_from_an_unowned_authority(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _write_skill(workspace / ".agents" / "skills", "foreign")
    catalog = discover_skill_catalog(
        workspace,
        include_user=False,
        include_system=False,
    )
    provider = _StaticProvider(catalog)
    authority = SkillAuthority(SkillProviderKind.CUSTOM, "custom-source")
    source = SkillProviderSource.for_authority(authority, provider)

    with pytest.raises(SkillResolutionError, match="unowned authority"):
        source.list(SkillListQuery())


def test_provider_source_strips_bodies_from_catalog_results(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _write_skill(workspace / ".agents" / "skills", "body-leak")
    catalog = discover_skill_catalog(
        workspace,
        include_user=False,
        include_system=False,
    )
    record = catalog.get_active("body-leak")
    assert record is not None
    provider = _StaticProvider(
        SkillCatalog((record.with_instructions("MUST-NOT-STAY-IN-CATALOG"),))
    )

    sanitized = SkillProviderSource.local(provider).list(SkillListQuery())

    assert sanitized.records[0].instructions is None


def test_multi_provider_catalog_merges_in_source_order_and_isolates_outages(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _write_skill(workspace / ".agents" / "skills", "first")
    _write_skill(workspace / ".agents" / "skills", "second")
    local = discover_skill_catalog(
        workspace,
        include_user=False,
        include_system=False,
    )
    first = local.get_active("first")
    second = local.get_active("second")
    assert first is not None
    assert second is not None
    authority_a = SkillAuthority(SkillProviderKind.CUSTOM, "provider-a")
    authority_b = SkillAuthority(SkillProviderKind.CUSTOM, "provider-b")
    first = replace(
        first,
        name="shared",
        reference=SkillReference(
            authority_a,
            SkillPackageId(first.id),
            SKILL_MAIN_RESOURCE,
        ),
    )
    second = replace(
        second,
        name="shared",
        reference=SkillReference(
            authority_b,
            SkillPackageId(second.id),
            SKILL_MAIN_RESOURCE,
        ),
    )
    provider_a = _StaticProvider(SkillCatalog((first,)))
    provider_b = _StaticProvider(SkillCatalog((second,)))
    unavailable = _UnavailableProvider(SkillCatalog(()))
    runtime = SkillRuntime(
        workspace,
        skill_sources=(
            SkillProviderSource.for_authority(authority_a, provider_a),
            SkillProviderSource.for_authority(
                SkillAuthority(SkillProviderKind.CUSTOM, "offline"),
                unavailable,
            ),
            SkillProviderSource.for_authority(authority_b, provider_b),
        ),
    )

    catalog = runtime.catalog()

    assert catalog.records[0].status.value == "active"
    assert catalog.records[1].status.value == "shadowed"
    assert catalog.records[1].shadowed_by == catalog.records[0].id
    assert any("offline Skills unavailable" in warning for warning in catalog.warnings)


def test_catalog_rejects_colliding_skill_ids_across_authorities(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _write_skill(workspace / ".agents" / "skills", "collision")
    local = discover_skill_catalog(
        workspace,
        include_user=False,
        include_system=False,
    )
    record = local.get_active("collision")
    assert record is not None
    foreign = replace(
        record,
        reference=SkillReference(
            SkillAuthority(SkillProviderKind.CUSTOM, "foreign"),
            record.package_id,
            record.main_resource,
        ),
    )
    catalog = SkillCatalog((record, foreign))

    assert catalog.get(record.id) is None
    with pytest.raises(SkillResolutionError, match="ambiguous across providers"):
        catalog.select_id(record.id)
