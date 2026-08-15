"""Application service for shared CLI/Desktop LLM configuration."""

from __future__ import annotations

import asyncio
import time
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
from core.providers.reasoning import infer_reasoning_capabilities
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

_MODEL_PROBE_PROMPT = (
    "Reply with exactly OK. This is a DeepCode connection verification request."
)
_MODEL_PROBE_TIMEOUT_SECONDS = 30.0


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
                    "apiKeyEnv": spec.env_key or None,
                    "requiresApiBase": spec.requires_api_base,
                    "local": spec.is_local,
                }
                for spec in PROVIDERS
            ],
            "configPath": str(self.config_store.path),
            "credentialPath": str(self.credentials.path),
        }

    def resolve_api_credential(
        self,
        connection_id: str,
        project_id: str | None = None,
    ) -> str | None:
        """Resolve one secret for an internal tool adapter without exposing it."""

        try:
            return (
                self._resolver(project_id=project_id)
                .resolve_connection(connection_id)
                .api_key
            )
        except ValueError:
            return None

    def upsert(self, value: dict[str, Any]) -> dict[str, Any]:
        connection_id, api_key, clear_api_key = self._parse_mutation(value)
        config_fields_supplied = bool(set(value) - {"id", "apiKey", "clearApiKey"})

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
            # A built-in template may also carry a legacy literal key in its
            # fixed block. Leaving it would make `remove` a lie: the
            # connection reappears in the next listing, still configured
            # from "legacy_config". Un-configure means every key source.
            legacy_block = providers.get(clean_id)
            if isinstance(legacy_block, dict) and "apiKey" in legacy_block:
                legacy_block = {
                    key: value for key, value in legacy_block.items() if key != "apiKey"
                }
                removed = True
                if legacy_block:
                    providers[clean_id] = legacy_block
                else:
                    providers.pop(clean_id, None)
            if providers:
                return {**current, "providers": providers}
            return {key: value for key, value in current.items() if key != "providers"}

        self.config_store.mutate(transform)
        credential_removed = self.credentials.clear(clean_id)
        return {
            "removed": removed or credential_removed,
            **self.list_connections(),
        }

    def model_reasoning(
        self,
        connection_id: str,
        model_id: str,
        *,
        project_id: str | None = None,
    ):
        """Last-known reasoning capabilities for one route, without I/O.

        Catalog snapshot first (the provider's own published controls),
        offline inference second — the same precedence
        :meth:`resolve_phases` applies when building an execution profile.
        """
        try:
            resolver = self._resolver(project_id=project_id)
            connection = resolver.resolve_connection(connection_id)
        except ValueError:
            return infer_reasoning_capabilities(model_id)
        cached = self.catalog.cached_model(connection, model_id)
        if cached is not None and cached.reasoning is not None:
            return cached.reasoning
        return infer_reasoning_capabilities(
            model_id,
            provider_name=connection.provider_name,
        )

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

    def test(
        self,
        connection_id: str,
        *,
        project_id: str | None = None,
        model_id: str | None = None,
    ) -> dict[str, Any]:
        """Check one connection and optionally run a minimal real model request.

        Catalog discovery and inference are deliberately separate stages. Some
        OpenAI-compatible endpoints do not expose ``/models`` while still
        supporting inference; conversely, a public catalog does not prove that a
        credential may call a particular model.
        """

        try:
            resolver = self._resolver(project_id=project_id)
            connection = resolver.resolve_connection(connection_id)
        except ValueError as exc:
            raise InvalidArgumentError(str(exc)) from exc
        if not connection.is_usable:
            detail = "No API credential is configured"
            return _verification_result(
                connection.id,
                status="error",
                ok=False,
                started=None,
                model_count=0,
                error=detail,
                stages=(
                    _verification_stage("credential", "failed", detail),
                    _verification_stage("catalog", "not_run", "Not checked"),
                    _verification_stage(
                        "model",
                        "not_run",
                        "Not checked",
                        model_id=_clean_optional(model_id),
                    ),
                ),
            )

        started = time.monotonic()
        credential = _verification_stage(
            "credential",
            "passed",
            _credential_detail(connection.credential_source),
        )
        catalog_started = time.monotonic()
        catalog = self.catalog.list_models(connection, refresh=True)
        catalog_latency = round((time.monotonic() - catalog_started) * 1000)
        if catalog.source == "manual" and not catalog.stale:
            catalog_stage = _verification_stage(
                "catalog",
                "skipped",
                "Manual model list; no catalog request was sent",
                latency_ms=catalog_latency,
                model_count=len(catalog.models),
            )
        elif catalog.source == "remote" and not catalog.stale:
            catalog_stage = _verification_stage(
                "catalog",
                "passed",
                f"Discovered {len(catalog.models)} models",
                latency_ms=catalog_latency,
                model_count=len(catalog.models),
            )
        else:
            catalog_stage = _verification_stage(
                "catalog",
                "failed",
                catalog.error or "The provider model catalog could not be verified",
                latency_ms=catalog_latency,
                model_count=len(catalog.models),
            )

        clean_model = _clean_optional(model_id)
        model_stage = _verification_stage(
            "model",
            "not_run",
            "Choose a model to run a minimal verification request",
            model_id=clean_model,
        )
        if clean_model is not None:
            model_stage = self._verify_model(
                resolver,
                connection_id=connection.id,
                model_id=clean_model,
                catalog=catalog,
            )

        if clean_model is not None:
            ok = model_stage["status"] == "passed"
            status = "ready" if ok else "error"
            error = None if ok else str(model_stage["detail"])
        elif catalog_stage["status"] == "passed":
            ok = True
            status = "connected"
            error = None
        elif catalog_stage["status"] == "skipped":
            ok = True
            status = "limited"
            error = None
        else:
            ok = False
            status = "error"
            error = str(catalog_stage["detail"])

        return _verification_result(
            connection.id,
            status=status,
            ok=ok,
            started=started,
            model_count=len(catalog.models),
            error=error,
            stages=(credential, catalog_stage, model_stage),
        )

    def _verify_model(
        self,
        resolver: ConnectionResolver,
        *,
        connection_id: str,
        model_id: str,
        catalog,
    ) -> dict[str, Any]:
        catalog_model = next(
            (candidate for candidate in catalog.models if candidate.id == model_id),
            None,
        )
        try:
            profile = resolver.execution_profile(
                ExecutionSelection(
                    connection_id=connection_id,
                    model_id=model_id,
                ),
                phase="implementation",
                model_limits=(
                    (
                        catalog_model.context_window,
                        catalog_model.max_output_tokens,
                    )
                    if catalog_model is not None
                    else None
                ),
                reasoning_capabilities=(
                    catalog_model.reasoning if catalog_model is not None else None
                ),
            )
            provider = resolver.build_provider(profile)
        except (TypeError, ValueError) as exc:
            return _verification_stage(
                "model",
                "failed",
                _safe_configuration_error(exc),
                model_id=model_id,
            )

        started = time.monotonic()
        try:
            response = _run_probe_isolated(
                provider.chat(
                    messages=[{"role": "user", "content": _MODEL_PROBE_PROMPT}],
                    model=profile.model_id,
                    max_tokens=min(16, profile.max_output_tokens),
                    temperature=0,
                    reasoning_effort=None,
                ),
                timeout=_MODEL_PROBE_TIMEOUT_SECONDS,
            )
        except TimeoutError:
            return _verification_stage(
                "model",
                "failed",
                "The model verification request timed out",
                latency_ms=round((time.monotonic() - started) * 1000),
                model_id=model_id,
            )
        except Exception as exc:  # noqa: BLE001 - sanitized product boundary
            return _verification_stage(
                "model",
                "failed",
                _safe_configuration_error(exc),
                latency_ms=round((time.monotonic() - started) * 1000),
                model_id=model_id,
            )

        latency = round((time.monotonic() - started) * 1000)
        if response.finish_reason == "error" or response.error_status_code is not None:
            return _verification_stage(
                "model",
                "failed",
                _model_error_detail(response),
                latency_ms=latency,
                model_id=model_id,
            )
        return _verification_stage(
            "model",
            "passed",
            "The provider accepted a real inference request",
            latency_ms=latency,
            model_id=model_id,
        )

    def resolve(
        self,
        workspace: str | Path,
        selection: ExecutionSelection | None,
        *,
        phase: str = "implementation",
    ) -> ExecutionProfile:
        return self.resolve_phases(
            workspace,
            selection,
            phases=(phase,),
        )[phase]

    def resolve_phases(
        self,
        workspace: str | Path,
        selection: ExecutionSelection | None,
        *,
        phases: tuple[str, ...],
    ) -> dict[str, ExecutionProfile]:
        """Resolve several phase profiles from one configuration snapshot."""

        if not phases or len(set(phases)) != len(phases):
            raise InvalidArgumentError("phases must be a non-empty unique sequence")
        try:
            config = load_config_for_workspace(workspace)
            resolver = ConnectionResolver(config, self.credentials)
            profiles: dict[str, ExecutionProfile] = {}
            for phase in phases:
                connection, model = resolver.resolve_selection(
                    selection,
                    phase=phase,
                )
                cached = self.catalog.cached_model(connection, model)
                profiles[phase] = resolver.execution_profile(
                    selection,
                    phase=phase,
                    model_limits=(
                        (cached.context_window, cached.max_output_tokens)
                        if cached is not None
                        else None
                    ),
                    reasoning_capabilities=(
                        cached.reasoning if cached is not None else None
                    ),
                )
            return profiles
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

        profile_data["label"] = str(profile_data.get("label") or connection_id).strip()
        template = str(profile_data.get("template") or "custom").strip().lower()
        if find_by_name(template) is None:
            raise InvalidArgumentError(f"unknown provider template: {template}")
        profile_data["template"] = template
        if "apiBase" in profile_data:
            profile_data["apiBase"] = _clean_optional(profile_data["apiBase"])
        if "apiKeyEnv" in profile_data:
            profile_data["apiKeyEnv"] = _clean_optional(profile_data["apiKeyEnv"])
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


def _verification_stage(
    stage_id: str,
    status: str,
    detail: str,
    *,
    latency_ms: int | None = None,
    model_count: int | None = None,
    model_id: str | None = None,
) -> dict[str, Any]:
    return {
        "id": stage_id,
        "status": status,
        "detail": detail[:300],
        "latencyMs": latency_ms,
        "modelCount": model_count,
        "modelId": model_id,
    }


def _verification_result(
    connection_id: str,
    *,
    status: str,
    ok: bool,
    started: float | None,
    model_count: int,
    error: str | None,
    stages: tuple[dict[str, Any], ...],
) -> dict[str, Any]:
    return {
        "connectionId": connection_id,
        "status": status,
        "ok": ok,
        "latencyMs": (
            round((time.monotonic() - started) * 1000) if started is not None else 0
        ),
        "modelCount": model_count,
        "error": error,
        "stages": list(stages),
    }


def _credential_detail(source: str) -> str:
    return {
        "environment": "Credential resolved from the configured environment variable",
        "credential_store": "Credential loaded from DeepCode private storage",
        "legacy_config": "Credential loaded from legacy DeepCode configuration",
        "not_required": "This local or direct connection does not require a credential",
    }.get(source, "Credential is configured")


def _run_probe_isolated(coroutine: Any, *, timeout: float) -> Any:
    """Run one probe coroutine on a dedicated thread with its own loop.

    The verification RPC is a synchronous handler that may execute inside a
    host that already runs an event loop (the Desktop sidecar does); a bare
    ``asyncio.run`` there raises and used to make verification structurally
    unavailable. A private thread has no running loop by definition, so the
    probe works in every host the same way.
    """
    import concurrent.futures

    def probe() -> Any:
        return asyncio.run(asyncio.wait_for(coroutine, timeout=timeout))

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        return executor.submit(probe).result()


def _safe_configuration_error(exc: Exception) -> str:
    if isinstance(exc, ValueError):
        return str(exc)[:300]
    return f"{type(exc).__name__}: model verification could not be completed"


def _model_error_detail(response: Any) -> str:
    status = response.error_status_code
    if status == 401:
        return "The provider rejected the API credential"
    if status == 403:
        return "The credential does not have access to this model"
    if status == 404:
        return "The endpoint or selected model was not found"
    if status == 408:
        return "The model verification request timed out"
    if status == 429:
        return "The provider reported a rate, quota, or balance limit"
    if isinstance(status, int) and status >= 500:
        return "The provider is temporarily unavailable"
    if response.error_kind == "timeout":
        return "The model verification request timed out"
    if response.error_kind == "connection":
        return "DeepCode could not connect to the model endpoint"
    return "The provider rejected the model verification request"


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
