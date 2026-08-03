"""Resolution of user Connection Profiles into executable providers."""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from core.config import ConfigError
from core.domain.execution_profile import ExecutionProfile, ExecutionSelection
from core.providers.base import GenerationSettings, LLMProvider
from core.providers.catalog import resolve_model_info
from core.providers.credentials import CredentialStore
from core.providers.registry import PROVIDERS, ProviderSpec, find_by_model, find_by_name
from core.providers.reasoning import (
    ModelReasoningCapabilities,
    infer_reasoning_capabilities,
    resolve_reasoning_effort,
)

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
    model_catalog: str
    manual_models: tuple[str, ...]
    credential_source: str
    local: bool
    enabled: bool
    spec: ProviderSpec

    def public_view(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "providerName": self.provider_name,
            "adapter": self.adapter,
            "apiBase": self.api_base,
            "apiKeyEnv": None,
            "modelCatalog": self.model_catalog,
            "manualModels": list(self.manual_models),
            "configured": self.is_configured,
            "credentialSource": self.credential_source,
            "local": self.local,
            "enabled": self.enabled,
        }

    @property
    def is_configured(self) -> bool:
        credential_ready = (
            bool(self.api_key)
            or self.local
            or self.spec.is_direct
            or self.spec.is_oauth
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
    ) -> None:
        self.config = config
        self.credentials = credentials or CredentialStore()

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
    ) -> ExecutionProfile:
        normalized = (selection or ExecutionSelection()).normalized()
        connection, model = self.resolve_selection(normalized, phase=phase)
        settings = self.config.resolve_phase(phase)
        info = resolve_model_info(model)
        context_window, max_output_tokens = model_limits or (
            info.context_window,
            info.max_output_tokens,
        )
        if context_window < 1 or max_output_tokens < 1:
            raise ConfigError(f"Invalid model limits for '{model}'")
        capabilities = reasoning_capabilities or infer_reasoning_capabilities(
            model,
            provider_name=connection.provider_name,
        )
        try:
            reasoning_effort = resolve_reasoning_effort(
                requested=normalized.reasoning_effort,
                configured=settings.reasoning_effort,
                capabilities=capabilities,
            )
        except ValueError as exc:
            raise ConfigError(str(exc)) from exc
        return ExecutionProfile(
            connection_id=connection.id,
            provider_name=connection.provider_name,
            adapter=connection.adapter,
            model_id=model,
            context_window=context_window,
            max_output_tokens=max_output_tokens,
            max_tokens=min(settings.max_tokens, max_output_tokens),
            temperature=settings.temperature,
            reasoning_effort=reasoning_effort,
            config_revision=self.connection_revision(connection),
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
            )
        elif connection.adapter == "openai_compat":
            from core.providers.openai_compat import OpenAICompatProvider

            provider = OpenAICompatProvider(
                api_key=connection.api_key,
                api_base=connection.api_base,
                default_model=profile.model_id,
                extra_headers=connection.extra_headers,
                spec=connection.spec,
            )
        else:
            raise ConfigError(
                f"Unsupported adapter '{connection.adapter}' "
                f"for connection '{connection.id}'"
            )
        provider.generation = GenerationSettings(
            temperature=profile.temperature,
            max_tokens=profile.max_tokens,
            reasoning_effort=profile.reasoning_effort,
        )
        return provider

    def connection_for_profile(
        self,
        profile: ExecutionProfile,
    ) -> ResolvedConnection:
        """Resolve credentials while rejecting non-secret config drift."""

        connection = self.resolve_connection(
            profile.connection_id,
            model=profile.model_id,
        )
        if self.connection_revision(connection) != profile.config_revision:
            raise ConfigError(
                f"LLM connection '{connection.id}' changed after this Turn was "
                "accepted. Retry with the current Session selection."
            )
        if not connection.is_usable:
            raise ConfigError(
                f"LLM connection '{connection.id}' has no credential. "
                "Configure it in Desktop Settings or with `deepcode provider set`."
            )
        return connection

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
        return ResolvedConnection(
            id=connection_id,
            label=profile.label.strip() or connection_id,
            provider_name=spec.name,
            adapter=profile.adapter or spec.backend,
            api_key=api_key,
            api_base=profile.api_base or spec.default_api_base or None,
            extra_headers=dict(profile.extra_headers),
            model_catalog=_catalog_kind(profile.model_catalog, spec),
            manual_models=_clean_models(profile.manual_models),
            credential_source=source,
            local=spec.is_local,
            enabled=profile.enabled,
            spec=spec,
        )

    def _legacy_connection(self, spec: ProviderSpec) -> ResolvedConnection:
        provider = getattr(self.config.providers, spec.name)
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
            adapter=spec.backend,
            api_key=api_key,
            api_base=provider.api_base or spec.default_api_base or None,
            extra_headers=dict(provider.extra_headers or {}),
            model_catalog=_catalog_kind("auto", spec),
            manual_models=(),
            credential_source=source,
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


def _clean_models(values: list[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(value.strip() for value in values if value.strip()))


def _catalog_kind(configured: str, spec: ProviderSpec) -> str:
    if configured != "auto":
        return configured
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
