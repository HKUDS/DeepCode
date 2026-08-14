"""Per-Turn Skill resolution and progressive-disclosure runtime."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from pathlib import Path

from core.skills.capabilities import SkillCapabilityResolver
from core.skills.catalog import LocalSkillProvider, SkillCatalog
from core.skills.config import SkillPolicyStore
from core.skills.models import (
    SkillAuthority,
    SkillInvocation,
    SkillInvocationKind,
    SkillPackageId,
    SkillRecord,
    SkillReference,
    SkillResolutionError,
    SkillResourceId,
    SkillSelection,
    SkillTurnSnapshot,
)
from core.skills.prompting import (
    SkillPromptBundle,
    build_skill_prompt_bundle,
    render_skill_catalog,
)
from core.skills.provider import (
    SkillListQuery,
    SkillProvider,
    SkillProviders,
    SkillProviderSource,
    SkillReadRequest,
    SkillReadResult,
    SkillSearchMatch,
    SkillSearchRequest,
    SkillSearchResult,
)

MAX_SKILLS_PER_TURN = 8
MAX_INJECTED_SKILL_CHARS = 48_000


@dataclass(slots=True)
class SkillTurnContext:
    catalog: SkillCatalog
    providers: SkillProviders
    snapshot: SkillTurnSnapshot
    loaded: dict[tuple[SkillAuthority, SkillPackageId], SkillInvocation] = field(
        default_factory=dict
    )
    loaded_records: dict[tuple[SkillAuthority, SkillPackageId], SkillRecord] = field(
        default_factory=dict
    )
    capability_tools: frozenset[str] | None = None
    capability_mcp_servers: Mapping[str, frozenset[str]] | None = None
    allowed_tools: set[str] = field(default_factory=set)
    has_tool_restriction: bool = False
    on_invocation: Callable[[SkillInvocation], None] | None = None
    on_failure: Callable[[str, str | None], None] | None = None

    def register(
        self,
        record: SkillRecord,
        kind: SkillInvocationKind,
    ) -> tuple[SkillInvocation, bool]:
        identity = (record.authority, record.package_id)
        existing = self.loaded.get(identity)
        if existing is not None:
            return existing, False
        invocation = SkillInvocation(
            skill_id=record.id,
            name=record.name,
            revision=record.revision,
            source=record.source,
            kind=kind,
        )
        self.loaded[identity] = invocation
        self.loaded_records[identity] = record
        if record.allowed_tools:
            self.has_tool_restriction = True
            self.allowed_tools.update(tool.casefold() for tool in record.allowed_tools)
        if self.on_invocation is not None:
            self.on_invocation(invocation)
        return invocation, True


class SkillRuntime:
    """One shared Skill backend used by AgentSession and every frontend."""

    def __init__(
        self,
        workspace: str | Path,
        *,
        home: str | Path | None = None,
        working_directory: str | Path | None = None,
        include_user: bool = True,
        include_system: bool = True,
        policy: SkillPolicyStore | None = None,
        skill_provider: SkillProvider | None = None,
        skill_source: SkillProviderSource | None = None,
        skill_sources: tuple[SkillProviderSource, ...] | None = None,
        skill_providers: SkillProviders | None = None,
    ) -> None:
        self.workspace = Path(workspace).expanduser().resolve(strict=False)
        self.policy = policy or SkillPolicyStore(self.workspace)
        configured = sum(
            value is not None
            for value in (
                skill_provider,
                skill_source,
                skill_sources,
                skill_providers,
            )
        )
        if configured > 1:
            raise ValueError(
                "pass only one of skill_provider, skill_source, skill_sources, "
                "or skill_providers"
            )
        if skill_providers is not None:
            skill_sources = skill_providers.sources
        if skill_sources is None and skill_source is None:
            provider = skill_provider or LocalSkillProvider(
                self.workspace,
                home=home,
                working_directory=working_directory,
                include_user=include_user,
                include_system=include_system,
                disabled_loader=self.policy.effective_disabled,
            )
            skill_source = SkillProviderSource.local(provider)
        if skill_sources is None:
            assert skill_source is not None
            skill_sources = (skill_source,)
        if not skill_sources:
            raise ValueError("SkillRuntime requires at least one Skill provider source")
        self.skill_sources = skill_sources
        self.skill_source = skill_sources[0]
        self.skill_provider = self.skill_source.provider
        self.skill_providers = skill_providers or SkillProviders(skill_sources)
        # Compatibility for integrations that inspected the original concrete
        # cache. New code should depend on ``skill_provider``.
        self.catalog_provider = self.skill_provider
        self._turn: ContextVar[SkillTurnContext | None] = ContextVar(
            f"deepcode_skill_turn_{id(self)}",
            default=None,
        )

    def catalog(self, *, force: bool = False) -> SkillCatalog:
        return self.skill_providers.list(SkillListQuery(force=force))

    def read(self, skill_id: str) -> SkillRecord:
        entry = self.catalog().get(skill_id)
        if entry is None:
            raise SkillResolutionError(f"Skill not found: {skill_id}")
        return self._load_entry(entry)

    def read_resource(
        self,
        skill_id: str,
        resource: str,
    ) -> SkillReadResult:
        entry = self.catalog().get(skill_id)
        if entry is None:
            raise SkillResolutionError(f"Skill not found: {skill_id}")
        return self.read_package_resource(entry, resource)

    def read_package_resource(
        self,
        entry: SkillRecord,
        resource: str,
    ) -> SkillReadResult:
        entry = self._current_entry(entry)
        reference = SkillReference(
            entry.authority,
            entry.package_id,
            SkillResourceId(resource),
        )
        result = self._active_providers().read(
            SkillReadRequest.from_reference(reference)
        )
        if result.package_revision != entry.revision:
            raise SkillResolutionError(
                f"Skill changed after catalog discovery: {entry.name}"
            )
        return result

    def search_package_resources(
        self,
        entry: SkillRecord,
        query: str,
        *,
        limit: int = 20,
    ) -> SkillSearchResult:
        entry = self._current_entry(entry)
        if not 1 <= limit <= 100:
            raise ValueError("Skill search limit must be between 1 and 100")
        return self._active_providers().search(
            SkillSearchRequest(
                authority=entry.authority,
                package=entry.package_id,
                query=query,
                limit=limit,
            )
        )

    def search(
        self,
        query: str,
        *,
        skill_id: str | None = None,
        limit: int = 20,
    ) -> SkillSearchResult:
        if not 1 <= limit <= 100:
            raise ValueError("Skill search limit must be between 1 and 100")
        context = self.current_context()
        catalog = context.catalog if context is not None else self.catalog()
        providers = context.providers if context is not None else self.skill_providers
        if skill_id is not None:
            record = catalog.get(skill_id)
            if record is None:
                raise SkillResolutionError(f"Skill not found: {skill_id}")
            references = (record.provider_reference,)
        else:
            references = tuple(
                dict.fromkeys(record.provider_reference for record in catalog.records)
            )

        matches: list[SkillSearchMatch] = []
        seen: set[SkillReference] = set()
        searched_authorities: set[SkillAuthority] = set()
        for reference in references:
            if skill_id is None and reference.authority in searched_authorities:
                continue
            searched_authorities.add(reference.authority)
            result = providers.search(
                SkillSearchRequest(
                    authority=reference.authority,
                    package=reference.package if skill_id is not None else None,
                    query=query,
                    limit=limit - len(matches),
                )
            )
            for match in result.matches:
                if match.reference not in seen:
                    matches.append(match)
                    seen.add(match.reference)
                if len(matches) >= limit:
                    return SkillSearchResult(tuple(matches))
        return SkillSearchResult(tuple(matches))

    def begin_turn(
        self,
        text: str,
        selections: tuple[SkillSelection, ...] = (),
        *,
        available_tools: tuple[str, ...] | None = None,
        available_mcp_servers: (
            Mapping[str, Iterable[str]] | Iterable[str] | None
        ) = None,
        on_invocation: Callable[[SkillInvocation], None] | None = None,
    ) -> tuple[SkillTurnContext, Token]:
        turn_providers = SkillProviders(self.skill_providers.sources)
        catalog = turn_providers.list(SkillListQuery())
        roots: list[tuple[SkillRecord, SkillInvocationKind]] = []
        seen: set[tuple[SkillAuthority, SkillPackageId]] = set()
        for selection in selections:
            record = catalog.select_id(selection.skill_id)
            if selection.name and selection.name.casefold() != record.name.casefold():
                raise SkillResolutionError(
                    f"Stale Skill selection: expected {selection.name}, "
                    f"resolved {record.name}"
                )
            identity = (record.authority, record.package_id)
            if identity not in seen:
                roots.append((record, SkillInvocationKind.EXPLICIT))
                seen.add(identity)
        for record in catalog.text_mentions(text):
            identity = (record.authority, record.package_id)
            if identity not in seen:
                roots.append((record, SkillInvocationKind.TEXT_MENTION))
                seen.add(identity)
        ordered = SkillCapabilityResolver(
            catalog,
            available_tools=available_tools,
            available_mcp_servers=available_mcp_servers,
        ).expand(roots)
        if len(ordered) > MAX_SKILLS_PER_TURN:
            raise SkillResolutionError(
                f"A Turn may select at most {MAX_SKILLS_PER_TURN} Skills"
            )
        loaded = tuple(
            (self._load_entry(record, providers=turn_providers), kind)
            for record, kind in ordered
        )
        instruction_chars = sum(
            len(record.require_instructions()) for record, _kind in loaded
        )
        if instruction_chars > MAX_INJECTED_SKILL_CHARS:
            raise SkillResolutionError(
                "Selected Skill instructions exceed the per-Turn budget "
                f"({instruction_chars} > {MAX_INJECTED_SKILL_CHARS} characters)"
            )

        invocations = tuple(
            SkillInvocation(
                skill_id=record.id,
                name=record.name,
                revision=record.revision,
                source=record.source,
                kind=kind,
            )
            for record, kind in loaded
        )
        declared_tools = {
            tool.casefold() for record, _kind in loaded for tool in record.allowed_tools
        }
        snapshot = SkillTurnSnapshot(
            records=tuple(record for record, _kind in loaded),
            invocations=invocations,
            catalog_revision=catalog.revision,
            allowed_tools=frozenset(declared_tools) if declared_tools else None,
        )
        context = SkillTurnContext(
            catalog=catalog,
            providers=turn_providers,
            snapshot=snapshot,
            capability_tools=(
                frozenset(tool.casefold() for tool in available_tools)
                if available_tools is not None
                else None
            ),
            capability_mcp_servers=(
                _freeze_mcp_capabilities(available_mcp_servers)
                if available_mcp_servers is not None
                else None
            ),
            on_invocation=on_invocation,
        )
        for record, kind in loaded:
            context.register(record, kind)
        return context, self._turn.set(context)

    def end_turn(self, token: Token) -> None:
        self._turn.reset(token)

    def current_context(self) -> SkillTurnContext | None:
        return self._turn.get()

    def load_implicit(self, name: str) -> tuple[SkillRecord, bool]:
        context = self.current_context()
        if context is None:
            # Compatibility for direct Tool execution outside AgentSession.
            record = self.catalog().get_implicit(name)
            if record is None:
                raise SkillResolutionError(f"Skill is not available: {name}")
            expanded = SkillCapabilityResolver(self.catalog()).expand(
                ((record, SkillInvocationKind.IMPLICIT),)
            )
            if len(expanded) != 1:
                raise SkillResolutionError(
                    "Skill dependencies require an active Agent Turn"
                )
            return self._load_entry(record), True
        record = context.catalog.get_active(name)
        if record is None:
            message = f"Skill is not available: {name}"
            if context.on_failure is not None:
                context.on_failure(message, None)
            raise SkillResolutionError(message)
        identity = (record.authority, record.package_id)
        existing = context.loaded_records.get(identity)
        if existing is not None:
            return existing, False
        if not record.metadata.allow_implicit_invocation:
            message = f"Skill requires explicit selection: {name}"
            if context.on_failure is not None:
                context.on_failure(message, record.id)
            raise SkillResolutionError(message)
        expanded = SkillCapabilityResolver(
            context.catalog,
            available_tools=context.capability_tools,
            available_mcp_servers=context.capability_mcp_servers,
        ).expand(((record, SkillInvocationKind.IMPLICIT),))
        new_entries = tuple(
            (entry, kind)
            for entry, kind in expanded
            if (entry.authority, entry.package_id) not in context.loaded
        )
        if len(context.loaded) + len(new_entries) > MAX_SKILLS_PER_TURN:
            message = f"A Turn may load at most {MAX_SKILLS_PER_TURN} Skills"
            if context.on_failure is not None:
                context.on_failure(message, record.id)
            raise SkillResolutionError(message)
        loaded_entries = tuple(
            (self._load_entry(entry), kind) for entry, kind in new_entries
        )
        instruction_chars = sum(
            len(candidate.require_instructions())
            for candidate in context.loaded_records.values()
        ) + sum(
            len(candidate.require_instructions()) for candidate, _kind in loaded_entries
        )
        if instruction_chars > MAX_INJECTED_SKILL_CHARS:
            message = (
                "Loaded Skill instructions exceed the per-Turn budget "
                f"({instruction_chars} > {MAX_INJECTED_SKILL_CHARS} characters)"
            )
            if context.on_failure is not None:
                context.on_failure(message, record.id)
            raise SkillResolutionError(message)
        added_target = False
        loaded_target: SkillRecord | None = None
        for candidate, kind in loaded_entries:
            _invocation, added = context.register(candidate, kind)
            if (candidate.authority, candidate.package_id) == identity:
                loaded_target = candidate
                added_target = added
        if loaded_target is None:
            raise RuntimeError("resolved Skill was not loaded")
        return loaded_target, added_target

    def allowed_tool_names(self) -> frozenset[str] | None:
        context = self.current_context()
        if context is None or not context.has_tool_restriction:
            return None
        # The progressive-disclosure tool must remain available so a model can
        # compose another Skill. It cannot grant any external permission.
        return frozenset((*context.allowed_tools, "skill"))

    def visible_tool_names(
        self,
        registered_names: tuple[str, ...],
    ) -> frozenset[str] | None:
        """Return a dynamic per-Turn tool filter.

        An empty catalog hides the internally registered progressive-disclosure
        tool so a workspace with no Skills retains the original CLI tool
        surface. The tool becomes visible on the next Turn after a Skill is
        added, without rebuilding the Session.
        """

        context = self.current_context()
        allowed = self.allowed_tool_names()
        if allowed is not None:
            return frozenset(
                name for name in registered_names if name.casefold() in allowed
            )
        if context is None or context.catalog.implicit():
            return None
        return frozenset(name for name in registered_names if name != "skill")

    def preamble(
        self,
        catalog: SkillCatalog | None = None,
        *,
        context_window_tokens: int | None = None,
    ) -> str:
        active = (catalog or self.catalog()).implicit()
        return render_skill_catalog(
            active,
            context_window_tokens=context_window_tokens,
        )

    def prompt_bundle(
        self,
        context: SkillTurnContext,
        *,
        context_window_tokens: int | None = None,
    ) -> SkillPromptBundle:
        return build_skill_prompt_bundle(
            context.catalog.implicit(),
            context.snapshot,
            context_window_tokens=context_window_tokens,
        )

    def invalidate(self) -> None:
        self.skill_providers.invalidate()

    def _load_entry(
        self,
        entry: SkillRecord,
        *,
        providers: SkillProviders | None = None,
    ) -> SkillRecord:
        result = (providers or self._active_providers()).read(
            SkillReadRequest.from_reference(entry.provider_reference)
        )
        if result.package_revision != entry.revision:
            raise SkillResolutionError(
                f"Skill changed after catalog discovery: {entry.name}"
            )
        return entry.with_instructions(result.contents)

    def _current_entry(self, entry: SkillRecord) -> SkillRecord:
        context = self.current_context()
        catalog = context.catalog if context is not None else self.catalog()
        current = catalog.get_package(entry.authority, entry.package_id)
        if current is None:
            raise SkillResolutionError(f"Skill is no longer available: {entry.name}")
        if current.revision != entry.revision:
            raise SkillResolutionError(
                f"Skill changed after catalog discovery: {entry.name}"
            )
        return current

    def _active_providers(self) -> SkillProviders:
        context = self.current_context()
        return context.providers if context is not None else self.skill_providers


def _freeze_mcp_capabilities(
    value: Mapping[str, Iterable[str]] | Iterable[str],
) -> Mapping[str, frozenset[str]]:
    if isinstance(value, Mapping):
        return {
            name.casefold(): frozenset(tool.casefold() for tool in tools)
            for name, tools in value.items()
        }
    return {name.casefold(): frozenset() for name in value}


def render_skill(record: SkillRecord) -> str:
    header = f"# Skill: {record.name}\n{record.description}\n"
    if record.allowed_tools:
        header += (
            "\nThis Skill declares a restricted tool set: "
            + ", ".join(record.allowed_tools)
            + "\n"
        )
    if record.authority.kind.value == "local":
        header += (
            f"\nSkill resources are in `{record.directory}`. This is not the task "
            "workspace: keep repository operations in `<environment_context><cwd>`. "
            "Resolve Skill-relative files against this directory and access bundled "
            "resources only through the normal permission and sandbox policy.\n"
        )
    else:
        header += (
            "\nUse the `skill` tool to read or search resources owned by this "
            "Skill provider; do not reinterpret its opaque resource IDs as local "
            "filesystem paths.\n"
        )
    return f"{header}\n{record.require_instructions()}"


__all__ = [
    "MAX_INJECTED_SKILL_CHARS",
    "MAX_SKILLS_PER_TURN",
    "SkillReadResult",
    "SkillRuntime",
    "SkillSearchResult",
    "SkillTurnContext",
    "render_skill",
]
