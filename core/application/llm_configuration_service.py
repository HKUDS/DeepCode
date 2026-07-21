"""Application service for shared CLI/Desktop LLM configuration."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from core.application.config_store import ConfigStore
from core.application.errors import InvalidArgumentError
from core.application.project_service import ProjectService
from core.config import (
    ConnectionProfileConfig,
    DeepCodeConfig,
    load_config,
    load_config_for_workspace,
)
from core.domain.execution_profile import ExecutionProfile, ExecutionSelection
from core.providers.catalog_service import ModelCatalogService
from core.providers.credentials import CredentialStore
from core.providers.profiles import ConnectionResolver, validate_connection_id
from core.providers.registry import PROVIDERS, find_by_name


_PROFILE_FIELDS = {
    "id",
    "label",
    "template",
    "adapter",
    "apiBase",
    "apiKeyEnv",
    "apiKey",
    "clearApiKey",
    "extraHeaders",
    "modelCatalog",
    "manualModels",
    "enabled",
}


class LLMConfigurationService:
    """Own connection mutations and resolve secret-free Turn snapshots."""

    def __init__(
        self,
        projects: ProjectService | None = None,
        *,
        config_store: ConfigStore | None = None,
        credential_store: CredentialStore | None = None,
        catalog: ModelCatalogService | None = None,
    ) -> None:
        self.projects = projects
        self.config_store = config_store or ConfigStore()
        self.credentials = credential_store or CredentialStore()
        self.catalog = catalog or ModelCatalogService()

    def list_connections(self, project_id: str | None = None) -> dict[str, Any]:
        config = self._config(project_id=project_id)
        resolver = ConnectionResolver(config, self.credentials)
        connections = []
        for connection in resolver.list_connections(include_unconfigured=True):
            view = connection.public_view()
            explicit = config.providers.profiles.get(connection.id)
            if explicit is not None:
                view["apiKeyEnv"] = explicit.api_key_env
                view["explicit"] = True
            else:
                view["apiKeyEnv"] = connection.spec.env_key or None
                view["explicit"] = False
            connections.append(view)
        return {
            "connections": connections,
            "templates": [
                {
                    "name": spec.name,
                    "label": spec.label,
                    "adapter": spec.backend,
                    "defaultApiBase": spec.default_api_base or None,
                    "local": spec.is_local,
                }
                for spec in PROVIDERS
            ],
            "configPath": str(self.config_store.path),
            "credentialPath": str(self.credentials.path),
        }

    def upsert(self, value: dict[str, Any]) -> dict[str, Any]:
        connection_id, api_key, clear_api_key = self._parse_mutation(value)
        config_fields_supplied = bool(
            set(value) - {"id", "apiKey", "clearApiKey"}
        )

        def transform(current: dict[str, Any]) -> dict[str, Any]:
            providers = current.get("providers")
            providers = dict(providers) if isinstance(providers, dict) else {}
            profiles = providers.get("profiles")
            profiles = dict(profiles) if isinstance(profiles, dict) else {}
            existing = profiles.get(connection_id)
            existing = dict(existing) if isinstance(existing, dict) else None
            normalized = self._normalize_profile(
                value,
                connection_id=connection_id,
                existing=existing,
                providers=providers,
            )
            profiles[connection_id] = normalized
            providers["profiles"] = profiles
            return {**current, "providers": providers}

        current = self.config_store.read()
        current_profiles = current.get("providers", {})
        current_profiles = (
            current_profiles.get("profiles", {})
            if isinstance(current_profiles, dict)
            else {}
        )
        already_explicit = (
            isinstance(current_profiles, dict) and connection_id in current_profiles
        )
        credential_only_builtin = (
            not config_fields_supplied
            and not already_explicit
            and find_by_name(connection_id) is not None
        )
        if not credential_only_builtin:
            self.config_store.mutate(transform)
        if clear_api_key:
            self.credentials.clear(connection_id)
        if api_key is not None:
            self.credentials.set(connection_id, api_key)
        return self.list_connections()

    def remove(self, connection_id: str) -> dict[str, Any]:
        try:
            clean_id = validate_connection_id(connection_id)
        except ValueError as exc:
            raise InvalidArgumentError(str(exc)) from exc
        removed = False

        def transform(current: dict[str, Any]) -> dict[str, Any]:
            nonlocal removed
            providers = current.get("providers")
            providers = dict(providers) if isinstance(providers, dict) else {}
            profiles = providers.get("profiles")
            profiles = dict(profiles) if isinstance(profiles, dict) else {}
            removed = profiles.pop(clean_id, None) is not None
            if profiles:
                providers["profiles"] = profiles
            else:
                providers.pop("profiles", None)
            if providers:
                return {**current, "providers": providers}
            return {key: value for key, value in current.items() if key != "providers"}

        self.config_store.mutate(transform)
        credential_removed = self.credentials.clear(clean_id)
        return {
            "removed": removed or credential_removed,
            **self.list_connections(),
        }

    def list_models(
        self,
        connection_id: str,
        *,
        project_id: str | None = None,
        refresh: bool = False,
    ) -> dict[str, Any]:
        resolver = self._resolver(project_id=project_id)
        try:
            connection = resolver.resolve_connection(connection_id)
        except ValueError as exc:
            raise InvalidArgumentError(str(exc)) from exc
        return self.catalog.list_models(connection, refresh=refresh).to_dict()

    def test(self, connection_id: str) -> dict[str, Any]:
        try:
            connection = self._resolver().resolve_connection(connection_id)
        except ValueError as exc:
            raise InvalidArgumentError(str(exc)) from exc
        if not connection.is_usable:
            return {
                "connectionId": connection.id,
                "ok": False,
                "latencyMs": 0,
                "modelCount": 0,
                "error": "No API credential is configured",
            }
        return self.catalog.test_connection(connection)

    def resolve(
        self,
        workspace: str | Path,
        selection: ExecutionSelection | None,
        *,
        phase: str = "implementation",
    ) -> ExecutionProfile:
        try:
            config = load_config_for_workspace(workspace)
            resolver = ConnectionResolver(config, self.credentials)
            connection, model = resolver.resolve_selection(selection, phase=phase)
            cached = self.catalog.cached_model(connection, model)
            return resolver.execution_profile(
                selection,
                phase=phase,
                model_limits=(
                    (cached.context_window, cached.max_output_tokens)
                    if cached is not None
                    else None
                ),
            )
        except ValueError as exc:
            raise InvalidArgumentError(str(exc)) from exc

    def _resolver(self, project_id: str | None = None) -> ConnectionResolver:
        return ConnectionResolver(self._config(project_id=project_id), self.credentials)

    def _config(self, *, project_id: str | None = None) -> DeepCodeConfig:
        if project_id is None:
            return load_config(config_path=self.config_store.path)
        if self.projects is None:
            raise InvalidArgumentError("project-scoped LLM settings are unavailable")
        project = self.projects.read(project_id)
        workspace = Path(project.canonical_path).resolve(strict=False)
        return load_config_for_workspace(workspace)

    @staticmethod
    def _parse_mutation(
        value: dict[str, Any],
    ) -> tuple[str, str | None, bool]:
        if not isinstance(value, dict):
            raise InvalidArgumentError("connection must be an object")
        unknown = set(value) - _PROFILE_FIELDS
        if unknown:
            raise InvalidArgumentError(
                f"unsupported connection field(s): {', '.join(sorted(unknown))}"
            )
        try:
            connection_id = validate_connection_id(str(value.get("id", "")))
        except ValueError as exc:
            raise InvalidArgumentError(str(exc)) from exc
        api_key = None
        if "apiKey" in value:
            api_key_value = value["apiKey"]
            if not isinstance(api_key_value, str) or not api_key_value.strip():
                raise InvalidArgumentError("apiKey must be a non-empty string")
            api_key = api_key_value.strip()
        clear_api_key = value.get("clearApiKey", False)
        if not isinstance(clear_api_key, bool):
            raise InvalidArgumentError("clearApiKey must be a boolean")
        return connection_id, api_key, clear_api_key

    @staticmethod
    def _normalize_profile(
        value: dict[str, Any],
        *,
        connection_id: str,
        existing: dict[str, Any] | None,
        providers: dict[str, Any],
    ) -> dict[str, Any]:
        profile_data = _profile_seed(
            connection_id,
            existing=existing,
            providers=providers,
        )
        field_names = {
            "label",
            "template",
            "adapter",
            "apiBase",
            "apiKeyEnv",
            "extraHeaders",
            "modelCatalog",
            "manualModels",
            "enabled",
        }
        for field in field_names.intersection(value):
            profile_data[field] = value[field]

        profile_data["label"] = str(
            profile_data.get("label") or connection_id
        ).strip()
        template = str(profile_data.get("template") or "custom").strip().lower()
        if find_by_name(template) is None:
            raise InvalidArgumentError(f"unknown provider template: {template}")
        profile_data["template"] = template
        if "apiBase" in profile_data:
            profile_data["apiBase"] = _clean_optional(profile_data["apiBase"])
        if "apiKeyEnv" in profile_data:
            profile_data["apiKeyEnv"] = _clean_optional(
                profile_data["apiKeyEnv"]
            )
        try:
            parsed = ConnectionProfileConfig.model_validate(profile_data)
        except Exception as exc:
            raise InvalidArgumentError(f"invalid connection: {exc}") from exc
        return parsed.model_dump(by_alias=True, exclude_none=True)


def _clean_optional(value: Any) -> str | None:
    if value is None:
        return None
    clean = str(value).strip()
    return clean or None


def _profile_seed(
    connection_id: str,
    *,
    existing: dict[str, Any] | None,
    providers: dict[str, Any],
) -> dict[str, Any]:
    if existing is not None:
        return dict(existing)
    spec = find_by_name(connection_id)
    if spec is None:
        return {
            "label": connection_id,
            "template": "custom",
            "modelCatalog": "auto",
            "manualModels": [],
            "enabled": True,
        }
    legacy = providers.get(spec.name)
    legacy = legacy if isinstance(legacy, dict) else {}
    return {
        "label": spec.label,
        "template": spec.name,
        "adapter": spec.backend,
        "apiBase": legacy.get("apiBase", legacy.get("api_base"))
        or spec.default_api_base
        or None,
        "extraHeaders": legacy.get(
            "extraHeaders",
            legacy.get("extra_headers", {}),
        )
        or {},
        "modelCatalog": "auto",
        "manualModels": [],
        "enabled": True,
    }


__all__ = ["LLMConfigurationService"]
