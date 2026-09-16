"""Resolution of user Connection Profiles into executable providers."""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Any

from core.config import ConfigError, ManualModelConfig
from core.domain.execution_profile import ExecutionProfile, ExecutionSelection
from core.providers.base import GenerationSettings, LLMProvider
from core.providers.catalog import resolve_model_info
from core.providers.credentials import CredentialStore
from core.providers.protocol_config import ProviderCompat, protocol_adapter
from core.providers.reasoning import (
    ModelReasoningCapabilities,
    infer_reasoning_capabilities,
    declared_reasoning_capabilities,
    resolve_reasoning_effort,
)
from core.providers.registry import PROVIDERS, ProviderSpec, find_by_model, find_by_name
from core.providers.revisions import ProviderRevisionStore, credential_digest

if TYPE_CHECKING:
    from core.config import ConnectionProfileConfig, DeepCodeConfig


CONNECTION_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")


@dataclass(frozen=True, slots=True)
class ResolvedConnection:
    id: str
    label: str
    provider_name: str
    adapter: str
    api_key: str | None
    api_base: str | None
    extra_headers: dict[str, str]
    # Resolved catalog strategy the runtime fetches with ("openai", ...).
    model_catalog: str
    # The STORED setting ("auto" or explicit) — what surfaces display and
    # write back. Echoing the resolved kind instead rewrote a user's "auto"
    # to a concrete value on every edit round-trip.
    model_catalog_setting: str
    manual_models: tuple[str, ...]
    credential_source: str
    local: bool
    enabled: bool
    spec: ProviderSpec
    # Full per-model declarations (label/capacities/efforts). ``manual_models``
    # stays the plain id tuple existing consumers read; these carry the rest.
    manual_model_entries: tuple[ManualModelConfig, ...] = ()
    account_id: str | None = None
    protocol: str = "auto"
    auth: str = "api_key"
    compat: ProviderCompat = field(default_factory=ProviderCompat)

    def public_view(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "providerName": self.provider_name,
            "adapter": self.adapter,
            "protocol": self.protocol,
            "auth": self.auth,
            "accountId": self.account_id,
            "compat": self.compat.model_dump(by_alias=True, exclude_none=True),
            "apiBase": self.api_base,
            "apiKeyEnv": None,
            "modelCatalog": self.model_catalog_setting,
            "manualModels": list(self.manual_models),
            "manualModelEntries": [
                entry.model_dump(by_alias=True, exclude_none=True)
                for entry in self.manual_model_entries
            ],
            "configured": self.is_configured,
            "credentialSource": self.credential_source,
            "local": self.local,
            "enabled": self.enabled,
        }

    @property
    def is_configured(self) -> bool:
        credential_ready = (
            bool(self.api_key)
            or self.auth == "none"
            or (
                self.protocol == "auto"
                and (self.local or self.spec.is_direct or self.spec.is_oauth)
            )
        )
        endpoint_ready = not self.spec.requires_api_base or bool(self.api_base)
        return credential_ready and endpoint_ready

    @property
    def is_usable(self) -> bool:
        return self.enabled and self.is_configured


class ConnectionResolver:
    """Single selection algorithm shared by CLI, App Server, and Desktop."""

    def __init__(
        self,
        config: "DeepCodeConfig",
        credentials: CredentialStore | None = None,
        *,
        config_loader: Callable[[], "DeepCodeConfig"] | None = None,
        credential_overrides: dict[str, str | None] | None = None,
    ) -> None:
        self.config = config
        self.config_loader = config_loader
        self._credential_overrides = dict(credential_overrides or {})
        self.credentials = credentials or CredentialStore()
        self.revisions = ProviderRevisionStore(
            self.credentials.path.parent / "provider_revisions"
        )

    def list_connections(
        self, *, include_unconfigured: bool = True
    ) -> list[ResolvedConnection]:
        resolved: list[ResolvedConnection] = []
        explicit_ids = set(self.config.providers.profiles)
        for connection_id in sorted(explicit_ids):
            try:
                connection = self.resolve_connection(connection_id)
            except ConfigError:
                continue
            if include_unconfigured or connection.is_usable:
                resolved.append(connection)
        for spec in PROVIDERS:
            if spec.name in explicit_ids:
                continue
            connection = self._legacy_connection(spec)
            if include_unconfigured or connection.is_usable:
                resolved.append(connection)
        return resolved

    def resolve_connection(
        self,
        connection_id: str,
        *,
        model: str | None = None,
    ) -> ResolvedConnection:
        clean_id = validate_connection_id(connection_id)
        profile = self.config.providers.profiles.get(clean_id)
        if profile is not None:
            return self._profile_connection(clean_id, profile)
        spec = find_by_name(clean_id)
        if spec is not None:
            return self._legacy_connection(spec)
        raise ConfigError(f"Unknown LLM connection '{clean_id}'")

    def resolve_selection(
        self,
        selection: ExecutionSelection | None = None,
        *,
        phase: str = "implementation",
    ) -> tuple[ResolvedConnection, str]:
        normalized = (selection or ExecutionSelection()).normalized()
        settings = self.config.resolve_phase(phase)
        model = normalized.model_id or settings.model
        if not model or not model.strip():
            raise ConfigError(f"No model configured for phase '{phase}'")
        model = model.strip()

        connection_id = normalized.connection_id or settings.connection
        if connection_id:
            connection = self.resolve_connection(connection_id, model=model)
        else:
            forced = (settings.provider or "auto").lower()
            if forced != "auto":
                connection = self.resolve_connection(forced, model=model)
            else:
                _provider, provider_name = self.config._match_provider(model)
                if provider_name is None:
                    inferred = find_by_model(model)
                    connection = (
                        self.resolve_connection(inferred.name, model=model)
                        if inferred is not None
                        else self._first_usable_connection(model)
                    )
                else:
                    connection = self.resolve_connection(provider_name, model=model)
        if not connection.enabled:
            raise ConfigError(f"LLM connection '{connection.id}' is disabled")
        return connection, model

    def execution_profile(
        self,
        selection: ExecutionSelection | None = None,
        *,
        phase: str = "implementation",
        model_limits: tuple[int, int] | None = None,
        reasoning_capabilities: ModelReasoningCapabilities | None = None,
        persist_revision: bool = True,
    ) -> ExecutionProfile:
        normalized = (selection or ExecutionSelection()).normalized()
        connection, model = self.resolve_selection(normalized, phase=phase)
        settings = self.config.resolve_phase(phase)
        entry = next(
            (item for item in connection.manual_model_entries if item.id == model), None
        )
        info = resolve_model_info(model)
        published_context_window, max_output_tokens = model_limits or (
            info.context_window,
            info.max_output_tokens,
        )
        if entry is not None:
            published_context_window = entry.context_window or published_context_window
            max_output_tokens = entry.max_output_tokens or max_output_tokens
        if published_context_window < 1 or max_output_tokens < 1:
            raise ConfigError(f"Invalid model limits for '{model}'")
        max_tokens = min(settings.max_tokens, max_output_tokens)
        context_window = published_context_window
        if normalized.context_window is not None:
            if normalized.context_window > published_context_window:
                raise ConfigError(
                    f"Context window cap {normalized.context_window} exceeds "
                    f"the published {published_context_window} token window for '{model}'"
                )
            if normalized.context_window <= max_tokens:
                raise ConfigError(
                    f"Context window cap must exceed the {max_tokens} token "
                    "generation limit"
                )
            context_window = normalized.context_window
        capabilities = reasoning_capabilities or infer_reasoning_capabilities(
            model,
            provider_name=connection.provider_name,
        )
        if entry is not None:
            capabilities = declared_reasoning_capabilities(
                entry.reasoning_efforts, capabilities
            )
        reasoning_supported = (
            None
            if entry is None or entry.reasoning_efforts is None
            else entry.reasoning_efforts is not False
        )
        if reasoning_supported is False and normalized.reasoning_effort not in {
            None,
            "auto",
            "none",
            "off",
        }:
            raise ConfigError("This model is explicitly declared non-reasoning")
        try:
            reasoning_effort = resolve_reasoning_effort(
                requested=normalized.reasoning_effort,
                configured=settings.reasoning_effort
                if reasoning_supported is not False
                else None,
                capabilities=capabilities,
            )
        except ValueError as exc:
            raise ConfigError(str(exc)) from exc
        compat = ProviderCompat.model_validate(
            {
                **connection.compat.model_dump(exclude_none=True),
                **(
                    entry.compat.model_dump(exclude_none=True)
                    if entry is not None
                    else {}
                ),
            }
        )
        revision = (
            self.revisions.put(
                {
                    "schemaVersion": 1,
                    "connectionId": connection.id,
                    "providerName": connection.provider_name,
                    "adapter": connection.adapter,
                    "protocol": connection.protocol,
                    "auth": connection.auth,
                    "apiBase": connection.api_base,
                    "extraHeaders": connection.extra_headers,
                    "compat": compat.model_dump(by_alias=True, exclude_none=True),
                    "credentialDigest": credential_digest(connection.api_key),
                    "credentialAccount": connection.account_id,
                }
            )
            if persist_revision
            else None
        )
        return ExecutionProfile(
            connection_id=connection.id,
            provider_name=connection.provider_name,
            adapter=connection.adapter,
            model_id=model,
            context_window=context_window,
            max_output_tokens=max_output_tokens,
            max_tokens=max_tokens,
            temperature=settings.temperature,
            reasoning_effort=reasoning_effort,
            config_revision=self.connection_revision(connection),
            protocol=connection.protocol,
            provider_revision=revision,
            input_modalities=tuple(entry.input_modalities)
            if entry is not None and entry.input_modalities is not None
            else None,
            tool_calling=entry.tool_calling if entry is not None else None,
            reasoning_supported=reasoning_supported,
        )

    def build_provider(self, profile: ExecutionProfile) -> LLMProvider:
        connection = self.connection_for_profile(profile)
        if connection.adapter == "anthropic":
            from core.providers.anthropic import AnthropicProvider

            provider: LLMProvider = AnthropicProvider(
                api_key=connection.api_key,
                api_base=connection.api_base,
                default_model=profile.model_id,
                extra_headers=connection.extra_headers,
                compat=connection.compat,
            )
        elif connection.adapter == "openai_compat":
            from core.providers.openai_compat import OpenAICompatProvider

            provider = OpenAICompatProvider(
                api_key=connection.api_key,
                api_base=connection.api_base,
                default_model=profile.model_id,
                extra_headers=connection.extra_headers,
                spec=connection.spec,
                protocol=connection.protocol,
                compat=connection.compat,
                auth_mode=connection.auth,
            )
        else:
            raise ConfigError(
                f"Unsupported adapter '{connection.adapter}' "
                f"for connection '{connection.id}'"
            )

        def current_credential():
            resolver = ConnectionResolver(
                self.config_loader() if self.config_loader else self.config,
                self.credentials,
                credential_overrides=self._credential_overrides,
            )
            return resolver.connection_for_profile(profile).api_key

        provider.request_guard = current_credential
        provider.input_modalities = profile.input_modalities
        provider.tool_calling = profile.tool_calling
        provider.reasoning_supported = profile.reasoning_supported
        provider.generation = GenerationSettings(
            temperature=profile.temperature,
            max_tokens=profile.max_tokens,
            reasoning_effort=profile.reasoning_effort,
        )
        return provider

    def connection_for_profile(self, profile: ExecutionProfile) -> ResolvedConnection:
        """Resolve live credentials against a frozen private route, or legacy revision."""
        connection = self.resolve_connection(
            profile.connection_id, model=profile.model_id
        )
        if not connection.is_usable:
            raise ConfigError(
                f"LLM connection '{connection.id}' is disabled or has no credential"
            )
        if profile.provider_revision is None:
            if self.connection_revision(connection) != profile.config_revision:
                raise ConfigError(
                    f"LLM connection '{connection.id}' changed after this Turn was accepted. Retry with the current Session selection."
                )
            entry = next(
                (
                    item
                    for item in connection.manual_model_entries
                    if item.id == profile.model_id
                ),
                None,
            )
            if entry is not None:
                connection = replace(
                    connection,
                    compat=ProviderCompat.model_validate(
                        {
                            **connection.compat.model_dump(exclude_none=True),
                            **entry.compat.model_dump(exclude_none=True),
                        }
                    ),
                )
            return connection
        try:
            snapshot = self.revisions.get(profile.provider_revision)
            if (
                snapshot.get("schemaVersion") != 1
                or snapshot.get("connectionId") != connection.id
                or snapshot.get("providerName") != connection.provider_name
                or snapshot.get("auth") != connection.auth
                or snapshot.get("protocol") != profile.protocol
                or snapshot.get("adapter") != profile.adapter
                or snapshot.get("credentialAccount") != connection.account_id
            ):
                raise ValueError("Provider identity changed after admission")
            if connection.extra_headers != snapshot["extraHeaders"]:
                raise ValueError(
                    "Header credentials or configuration changed after this Turn was accepted; resubmit with current settings"
                )
            if connection.api_key is None and snapshot[
                "credentialDigest"
            ] != credential_digest(None):
                raise ValueError("The credential used by this Turn has been removed")
            if (
                credential_digest(connection.api_key) != snapshot["credentialDigest"]
                and connection.api_base != snapshot["apiBase"]
            ):
                raise ValueError(
                    "Credential and endpoint changed after this Turn was accepted"
                )
            return replace(
                connection,
                adapter=snapshot["adapter"],
                protocol=snapshot["protocol"],
                api_base=snapshot["apiBase"],
                extra_headers=dict(snapshot["extraHeaders"]),
                compat=ProviderCompat.model_validate(snapshot["compat"]),
                manual_model_entries=(),
            )
        except (ValueError, KeyError, TypeError) as exc:
            raise ConfigError(
                str(exc)
                if isinstance(exc, ValueError)
                else "Invalid private provider revision; resubmit with current settings"
            ) from exc

    @staticmethod
    def connection_revision(connection: ResolvedConnection) -> str:
        """Fingerprint executable, non-credential connection settings."""

        payload = json.dumps(
            {
                "id": connection.id,
                "providerName": connection.provider_name,
                "adapter": connection.adapter,
                "apiBase": connection.api_base,
                "extraHeaders": connection.extra_headers,
                "enabled": connection.enabled,
                **(
                    {"protocol": connection.protocol}
                    if connection.protocol != "auto"
                    else {}
                ),
                **({"auth": connection.auth} if connection.auth != "api_key" else {}),
                **(
                    {"compat": connection.compat.model_dump(exclude_none=True)}
                    if connection.compat.model_dump(exclude_none=True)
                    else {}
                ),
            },
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode()
        return hashlib.sha256(payload).hexdigest()[:16]

    def _first_usable_connection(self, model: str) -> ResolvedConnection:
        candidates = self.list_connections(include_unconfigured=False)
        prefix = model.split("/", 1)[0].lower() if "/" in model else ""
        for candidate in candidates:
            if candidate.id == prefix or candidate.provider_name == prefix:
                return candidate
        if candidates:
            return candidates[0]
        raise ConfigError(
            f"Could not match a configured provider for model '{model}'. "
            "Configure a connection in Desktop Settings or with `deepcode provider set`."
        )

    def _profile_connection(
        self,
        connection_id: str,
        profile: "ConnectionProfileConfig",
    ) -> ResolvedConnection:
        spec = find_by_name(profile.template)
        if spec is None:
            raise ConfigError(
                f"Connection '{connection_id}' uses unknown template "
                f"'{profile.template}'"
            )
        env_name = (profile.api_key_env or spec.env_key or "").strip()
        legacy_key = None
        if connection_id == spec.name:
            legacy_key = getattr(self.config.providers, spec.name).api_key
        api_key, source = self._credential(
            connection_id,
            env_name=env_name,
            legacy_key=legacy_key,
            key_optional=spec.is_local or spec.is_direct or spec.is_oauth,
        )
        account_id = None
        if profile.auth == "oauth":
            api_key, account_id = self.credentials.oauth_credential(connection_id)
            source = "oauth" if api_key else "missing"
        model_entries = _clean_model_entries(profile.manual_models)
        return ResolvedConnection(
            id=connection_id,
            label=profile.label.strip() or connection_id,
            provider_name=spec.name,
            adapter=protocol_adapter(profile.protocol, profile.adapter or spec.backend),
            protocol=profile.protocol,
            account_id=account_id,
            auth=profile.auth,
            compat=profile.compat,
            api_key=api_key if profile.auth != "none" else None,
            api_base=profile.api_base or spec.default_api_base or None,
            extra_headers=dict(profile.extra_headers),
            model_catalog=_catalog_kind(profile.model_catalog, spec, profile.protocol),
            model_catalog_setting=profile.model_catalog,
            manual_models=tuple(entry.id for entry in model_entries),
            manual_model_entries=model_entries,
            credential_source=source if profile.auth != "none" else "not_required",
            local=spec.is_local,
            enabled=profile.enabled,
            spec=spec,
        )

    def template_connection(self, template: str) -> ResolvedConnection:
        """A registry template as a connection value (for pre-save probing).

        Nothing is read from ``providers.profiles`` — this is the shape a
        connection WOULD have if the template were adopted as-is, so an
        editor can discover models before its first save.
        """
        spec = find_by_name(validate_connection_id(template))
        if spec is None:
            raise ConfigError(f"Unknown provider template '{template}'")
        return self._legacy_connection(spec)

    def _legacy_connection(self, spec: ProviderSpec) -> ResolvedConnection:
        provider = getattr(self.config.providers, spec.name)
        if (
            provider.auth == "none"
            and protocol_adapter(provider.protocol, spec.backend) == "anthropic"
        ):
            raise ConfigError("Anthropic Messages requires an API key")
        api_key, source = self._credential(
            spec.name,
            env_name=spec.env_key,
            legacy_key=provider.api_key,
            key_optional=spec.is_local or spec.is_direct or spec.is_oauth,
        )
        return ResolvedConnection(
            id=spec.name,
            label=spec.label,
            provider_name=spec.name,
            adapter=protocol_adapter(provider.protocol, spec.backend),
            protocol=provider.protocol,
            auth=provider.auth,
            compat=provider.compat,
            api_key=api_key if provider.auth != "none" else None,
            api_base=provider.api_base or spec.default_api_base or None,
            extra_headers=dict(provider.extra_headers or {}),
            model_catalog=_catalog_kind("auto", spec, provider.protocol),
            model_catalog_setting="auto",
            manual_models=(),
            credential_source=source if provider.auth != "none" else "not_required",
            local=spec.is_local,
            enabled=True,
            spec=spec,
        )

    def _credential(
        self,
        connection_id: str,
        *,
        env_name: str,
        legacy_key: str | None,
        key_optional: bool,
    ) -> tuple[str | None, str]:
        if connection_id in self._credential_overrides:
            value = self._credential_overrides[connection_id]
            return value, "request" if value else "missing"
        if env_name and os.environ.get(env_name):
            return os.environ[env_name], "environment"
        stored = self.credentials.get(connection_id)
        if stored:
            return stored, "credential_store"
        if legacy_key:
            return legacy_key, "legacy_config"
        return None, "not_required" if key_optional else "missing"


def validate_connection_id(value: str) -> str:
    clean = value.strip().lower()
    if not CONNECTION_ID_PATTERN.fullmatch(clean):
        raise ConfigError(
            "connection id must use 1-64 lowercase letters, numbers, '.', '_' or '-'"
        )
    return clean


def _clean_model_entries(
    values: list[str | ManualModelConfig],
) -> tuple[ManualModelConfig, ...]:
    """Normalize the mixed manualModels list to declarations, first id wins."""
    entries: dict[str, ManualModelConfig] = {}
    for value in values:
        entry = (
            ManualModelConfig(id=value.strip())
            if isinstance(value, str)
            else value.model_copy(update={"id": value.id.strip()})
        )
        if entry.id and entry.id not in entries:
            entries[entry.id] = entry
    return tuple(entries.values())


def _catalog_kind(configured: str, spec: ProviderSpec, protocol: str = "auto") -> str:
    if configured != "auto":
        return configured
    if protocol == "anthropic_messages":
        return "anthropic"
    if protocol in {"openai_chat", "openai_responses"} and spec.backend == "anthropic":
        return "openai"
    if spec.name == "openrouter":
        return "openrouter"
    if spec.name == "anthropic":
        return "anthropic"
    if spec.backend == "openai_compat":
        return "openai"
    return "manual"


__all__ = [
    "CONNECTION_ID_PATTERN",
    "ConnectionResolver",
    "ResolvedConnection",
    "validate_connection_id",
]
