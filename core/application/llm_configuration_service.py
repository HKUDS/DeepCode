"""Application service for shared CLI/Desktop LLM configuration."""

from __future__ import annotations

import asyncio
import time
from dataclasses import replace
from pathlib import Path
from typing import Any

from core.application.config_store import (
    ConfigRevisionConflict,
    ConfigStore,
)
from core.application.errors import ConflictError, InvalidArgumentError
from core.application.project_service import ProjectService
from core.application.provider_verification import (
    verification_stage as _verification_stage,
    model_error_detail as _model_error_detail,
    verify_agent,
)
from core.config import (
    ConnectionProfileConfig,
    DeepCodeConfig,
    load_config,
    load_config_for_workspace,
)
from core.domain.execution_profile import ExecutionProfile, ExecutionSelection
from core.providers.catalog_service import ModelCatalogService, ModelCatalog
from core.providers.credentials import CredentialStore
from core.providers.profiles import ConnectionResolver, validate_connection_id
from core.providers.oauth import ProviderOAuthManager
from core.providers.reasoning import infer_reasoning_capabilities
from core.providers.registry import PROVIDERS, find_by_name

_PROFILE_FIELDS = {
    "id",
    "label",
    "template",
    "adapter",
    "protocol",
    "auth",
    "compat",
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
        self.oauth = ProviderOAuthManager(self.credentials)

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

    def upsert(
        self,
        value: dict[str, Any],
        *,
        expected_revision: str | None = None,
    ) -> dict[str, Any]:
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
            if normalized["auth"] == "oauth" and api_key is not None:
                raise InvalidArgumentError(
                    "Use provider login for OAuth connections, or select API-key authentication"
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
            self._mutate_config(transform, expected_revision)
            self.credentials.begin_login(
                connection_id
            )  # invalidate pending flows after configuration changes
        if clear_api_key:
            self.credentials.clear(connection_id)
        if api_key is not None:
            self.credentials.set(connection_id, api_key)
        return self.list_connections()

    def remove(
        self,
        connection_id: str,
        *,
        expected_revision: str | None = None,
    ) -> dict[str, Any]:
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

        self._mutate_config(transform, expected_revision)
        credential_removed = self.credentials.clear(clean_id)
        return {
            "removed": removed or credential_removed,
            **self.list_connections(),
        }

    def close(self) -> None:
        self.oauth.close()

    def login_start(self, connection_id: str, *, open_browser: bool = False) -> dict:
        connection = self._resolver().resolve_connection(connection_id)
        if connection.auth != "oauth" or not connection.enabled:
            raise InvalidArgumentError(
                "Save an enabled OpenRouter OAuth connection before signing in"
            )
        return self.oauth.start(connection.id, open_browser=open_browser)

    def login_poll(self, flow_id: str) -> dict:
        return self.oauth.poll(flow_id)

    def login_cancel(self, flow_id: str) -> dict:
        return self.oauth.cancel(flow_id)

    def logout(self, connection_id: str) -> dict:
        connection = self._resolver().resolve_connection(connection_id)
        if connection.auth != "oauth":
            raise InvalidArgumentError(
                "The selected connection does not use Provider login"
            )
        self.credentials.clear(connection.id)
        return {
            "disconnected": True,
            "remoteRevoked": False,
            "manageUrl": "https://openrouter.ai/settings/keys",
        }

    def discover_models(
        self,
        *,
        connection_id: str | None = None,
        template: str | None = None,
        api_base: str | None = None,
        api_key: str | None = None,
        project_id: str | None = None,
        draft: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Probe an endpoint AS SHOWN in an editor form; writes nothing.

        An existing connection supplies the baseline (its stored key stays
        usable); a template covers create-before-first-save. An unsaved base
        URL or freshly typed key overrides for this one probe only and never
        leaves memory — discovery returns candidates, adopting writes
        (the dsh rule).
        """
        resolver = (
            self._draft_resolver(draft, project_id)
            if draft is not None
            else self._resolver(project_id=project_id)
        )
        try:
            if draft is not None:
                base = resolver.resolve_connection(str(draft["id"]))
            elif connection_id and connection_id.strip():
                base = resolver.resolve_connection(connection_id.strip())
            elif template and template.strip():
                base = resolver.template_connection(template.strip())
            else:
                raise InvalidArgumentError(
                    "provider discovery needs a connectionId or a template"
                )
        except ValueError as exc:
            raise InvalidArgumentError(str(exc)) from exc
        overrides: dict[str, Any] = {}
        if api_base and api_base.strip():
            overrides["api_base"] = api_base.strip()
        if api_key and api_key.strip():
            overrides["api_key"] = api_key.strip()
        connection = replace(base, **overrides) if overrides else base
        try:
            models = self.catalog.probe(connection)
        except Exception as exc:  # noqa: BLE001 - surfaced, never raised to UI
            return {"models": [], "error": _safe_configuration_error(exc)}
        return {"models": [model.to_dict() for model in models], "error": None}

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
        draft: dict[str, Any] | None = None,
        mode: str = "quick",
    ) -> dict[str, Any]:
        """Check one connection and optionally run a minimal real model request.

        Catalog discovery and inference are deliberately separate stages. Some
        OpenAI-compatible endpoints do not expose ``/models`` while still
        supporting inference; conversely, a public catalog does not prove that a
        credential may call a particular model.
        """

        if mode not in {"quick", "agent"}:
            raise InvalidArgumentError("Unknown verification mode")
        if mode == "agent" and not _clean_optional(model_id):
            raise InvalidArgumentError("Agent verification requires a model")
        if draft is not None and draft.get("id") != connection_id:
            raise InvalidArgumentError(
                "Draft connection ID does not match the selected connection"
            )
        try:
            resolver = (
                self._draft_resolver(draft, project_id)
                if draft is not None
                else self._resolver(project_id=project_id)
            )
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
        if draft is None:
            catalog = self.catalog.list_models(connection, refresh=True)
        else:
            # Form probes never persist catalogs, credentials or route revisions.
            try:
                models = (
                    self.catalog.probe(connection)
                    if connection.model_catalog != "manual"
                    else ()
                )
                catalog = ModelCatalog(
                    connection_id=connection.id,
                    models=models,
                    source="remote"
                    if connection.model_catalog != "manual"
                    else "manual",
                    stale=False,
                )
            except Exception as exc:
                catalog = ModelCatalog(
                    connection_id=connection.id,
                    models=(),
                    source="fallback",
                    stale=True,
                    error=_safe_configuration_error(exc),
                )
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
        agent_stages = []
        if clean_model is not None and mode == "agent":
            agent_stages = self._verify_agent(
                resolver, connection.id, clean_model, catalog
            )
        elif clean_model is not None:
            model_stage = self._verify_model(
                resolver,
                connection_id=connection.id,
                model_id=clean_model,
                catalog=catalog,
            )

        if mode == "agent":
            required = {"stream", "tool", "continuation"}
            ok = all(
                any(
                    stage["id"] == name and stage["status"] == "passed"
                    for stage in agent_stages
                )
                for name in required
            ) and not any(stage["status"] == "failed" for stage in agent_stages)
            status = "ready" if ok else "error"
            error = None if ok else "Agent compatibility verification did not pass"
        elif clean_model is not None:
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
            stages=(credential, catalog_stage, *agent_stages)
            if mode == "agent"
            else (credential, catalog_stage, model_stage),
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
                persist_revision=False,
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

        async def probe():
            try:
                return await provider.chat(
                    messages=[{"role": "user", "content": _MODEL_PROBE_PROMPT}],
                    model=profile.model_id,
                    max_tokens=min(16, profile.max_output_tokens),
                    temperature=0,
                    reasoning_effort=None,
                )
            finally:
                await provider.aclose()

        started = time.monotonic()
        try:
            response = _run_probe_isolated(
                probe(), timeout=_MODEL_PROBE_TIMEOUT_SECONDS
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

    def _draft_resolver(
        self, draft: dict[str, Any], project_id: str | None
    ) -> ConnectionResolver:
        connection_id, key, clear = self._parse_mutation(draft)
        config = self._config(project_id=project_id).model_copy(deep=True)
        providers = config.providers.model_dump(by_alias=True, exclude_none=True)
        existing = providers.get("profiles", {}).get(connection_id)
        normalized = self._normalize_profile(
            draft, connection_id=connection_id, existing=existing, providers=providers
        )
        config.providers.profiles[connection_id] = (
            ConnectionProfileConfig.model_validate(normalized)
        )
        overrides = {connection_id: key} if key is not None or clear else {}
        return ConnectionResolver(
            config, self.credentials, credential_overrides=overrides
        )

    def _verify_agent(self, resolver, connection_id, model_id, catalog):
        selected = next((item for item in catalog.models if item.id == model_id), None)
        try:
            profile = resolver.execution_profile(
                ExecutionSelection(connection_id, model_id),
                model_limits=(selected.context_window, selected.max_output_tokens)
                if selected
                else None,
                reasoning_capabilities=selected.reasoning if selected else None,
                persist_revision=False,
            )
            provider = resolver.build_provider(profile)
            return _run_probe_isolated(verify_agent(provider, profile), timeout=95)
        except Exception as exc:
            return [
                _verification_stage(
                    "stream",
                    "failed",
                    _safe_configuration_error(exc),
                    model_id=model_id,
                )
            ]

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

    def _mutate_config(self, transform, expected_revision: str | None) -> None:
        try:
            self.config_store.mutate(transform, expected_revision=expected_revision)
        except ConfigRevisionConflict as exc:
            raise ConflictError(str(exc)) from exc

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
            "protocol",
            "auth",
            "compat",
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
        "not_required": "This connection does not require a credential",
        "request": "Unsaved credential supplied for this verification only",
        "oauth": "Credential bound to the signed-in OpenRouter account",
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
        "protocol": legacy.get("protocol", "auto"),
        "auth": legacy.get("auth", "api_key"),
        "compat": legacy.get("compat", {}),
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
