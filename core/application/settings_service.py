"""Safe settings projection and validated user-config updates."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from core.application.config_store import ConfigStore, deep_merge
from core.application.errors import (
    InvalidArgumentError,
    ProjectNotTrustedError,
)
from core.application.execution_security_policy import ExecutionSecurityPolicy
from core.application.project_service import ProjectService
from core.config import (
    DeepCodeConfig,
    home_config_path,
    load_config,
    load_config_for_workspace,
)
from core.domain.execution_security import (
    ExecutionAccessPreset,
    normalize_permission_rules,
)
from core.domain.project import TrustState
from core.providers.catalog import known_model_ids, resolve_model_info
from core.providers.registry import PROVIDERS

_ALLOWED_ROOTS = {"agents", "providers", "security"}
_AGENT_SECTIONS = {"defaults", "planning", "implementation"}
_AGENT_FIELDS = {
    "connection",
    "provider",
    "model",
    "maxTokens",
    "temperature",
    "reasoningEffort",
    "defaultPreset",
    "baseMaxTokens",
    "retryMaxTokens",
    "maxTokensPolicy",
    "maxToolIterations",
    "maxToolResultChars",
    "contextWindowTokens",
}
_PROVIDER_FIELDS = {"apiKey", "apiBase", "extraHeaders"}
_SECURITY_FIELDS = {"accessPreset", "permissionMode", "permissions", "sandbox"}


class SettingsService:
    def __init__(
        self,
        projects: ProjectService | None = None,
        store: ConfigStore | None = None,
        execution_security_policy: ExecutionSecurityPolicy | None = None,
    ) -> None:
        self.projects = projects
        self.store = store or ConfigStore()
        self.execution_security_policy = (
            execution_security_policy or ExecutionSecurityPolicy()
        )

    def read(self, project_id: str | None = None) -> dict[str, Any]:
        workspace = self._workspace(project_id)
        config = (
            load_config_for_workspace(workspace)
            if workspace is not None and workspace.is_dir()
            else load_config(config_path=self.store.path)
        )
        return self._view(config, workspace=workspace)

    def update(
        self,
        patch: dict[str, Any],
        *,
        scope: str = "user",
        project_id: str | None = None,
    ) -> dict[str, Any]:
        self._validate_patch(patch)
        if scope == "project" and "providers" in patch:
            raise InvalidArgumentError(
                "provider connections and credentials are user-scoped only"
            )
        store = self._store(scope, project_id)
        store.mutate(lambda current: _merge_settings_patch(current, patch))
        return self.read(project_id)

    def _workspace(self, project_id: str | None) -> Path | None:
        if project_id is None:
            return None
        if self.projects is None:
            raise InvalidArgumentError("project-scoped settings are unavailable")
        project = self.projects.read(project_id)
        return Path(project.canonical_path).resolve(strict=False)

    def _store(self, scope: str, project_id: str | None) -> ConfigStore:
        if scope == "user":
            return self.store
        if scope != "project":
            raise InvalidArgumentError("settings scope must be user or project")
        if project_id is None or self.projects is None:
            raise InvalidArgumentError(
                "projectId is required for project-scoped settings"
            )
        project = self.projects.read(project_id)
        if project.trust_state is not TrustState.TRUSTED:
            raise ProjectNotTrustedError(
                "project must be trusted before its settings can be changed"
            )
        try:
            workspace = Path(project.canonical_path).resolve(strict=True)
        except OSError as exc:
            raise InvalidArgumentError(
                f"project path does not exist: {project.canonical_path}"
            ) from exc
        if not workspace.is_dir():
            raise InvalidArgumentError("project path must be a directory")
        return ConfigStore(workspace / home_config_path().name)

    def _view(
        self,
        config: DeepCodeConfig,
        *,
        workspace: Path | None,
    ) -> dict[str, Any]:
        providers = []
        for spec in PROVIDERS:
            provider = getattr(config.providers, spec.name)
            env_configured = bool(spec.env_key and os.environ.get(spec.env_key))
            providers.append(
                {
                    "name": spec.name,
                    "label": spec.label,
                    "configured": bool(provider.api_key)
                    or env_configured
                    or spec.is_local,
                    "credentialSource": (
                        "environment"
                        if env_configured
                        else "config"
                        if provider.api_key
                        else "not_required"
                        if spec.is_local or spec.is_direct
                        else "missing"
                    ),
                    "apiBase": provider.api_base or spec.default_api_base or None,
                    "local": spec.is_local,
                }
            )
        models = []
        for model_id in known_model_ids():
            info = resolve_model_info(model_id)
            models.append(
                {
                    "id": model_id,
                    "contextWindow": info.context_window,
                    "maxOutputTokens": info.max_output_tokens,
                    "source": info.source,
                }
            )
        security_fields = getattr(config.security, "model_fields_set", set())
        user_raw = self.store.read()
        project_raw = (
            ConfigStore(workspace / home_config_path().name).read()
            if workspace is not None and workspace.is_dir()
            else {}
        )
        user_access_preset = _raw_access_preset(user_raw)
        project_access_preset = _raw_access_preset(project_raw)
        resolved_security = self.execution_security_policy.resolve_default(
            str(workspace or Path.cwd()),
            security_config=config.security,
        )
        return {
            "configPath": str(self.store.path),
            "agents": config.agents.model_dump(by_alias=True),
            "security": config.security.model_dump(by_alias=True),
            "permissionModeExplicit": "permission_mode" in security_fields,
            "userAccessPreset": user_access_preset,
            "projectAccessPreset": project_access_preset,
            "resolvedDefaultSecurityProfile": resolved_security.to_dict(),
            "resolvedDefaultSecuritySource": _resolved_security_source(
                user_raw,
                project_raw,
            ),
            "providers": providers,
            "models": models,
        }

    @staticmethod
    def _validate_patch(patch: dict[str, Any]) -> None:
        if not isinstance(patch, dict) or not patch:
            raise InvalidArgumentError("settings patch must be a non-empty object")
        unknown = set(patch) - _ALLOWED_ROOTS
        if unknown:
            raise InvalidArgumentError(
                f"unsupported settings section(s): {', '.join(sorted(unknown))}"
            )
        agents = patch.get("agents")
        if agents is not None:
            _require_object(agents, "agents")
            for section, values in agents.items():
                if section not in _AGENT_SECTIONS:
                    raise InvalidArgumentError(f"unsupported agents section: {section}")
                _require_fields(values, _AGENT_FIELDS, f"agents.{section}")
        providers = patch.get("providers")
        if providers is not None:
            _require_object(providers, "providers")
            known = {spec.name for spec in PROVIDERS}
            for name, values in providers.items():
                if name not in known:
                    raise InvalidArgumentError(f"unsupported provider: {name}")
                _require_fields(values, _PROVIDER_FIELDS, f"providers.{name}")
        security = patch.get("security")
        if security is not None:
            _require_fields(security, _SECURITY_FIELDS, "security")
            if "permissions" in security:
                try:
                    normalize_permission_rules(security["permissions"])
                except (TypeError, ValueError) as exc:
                    raise InvalidArgumentError(
                        f"invalid security.permissions: {exc}"
                    ) from exc

        try:
            DeepCodeConfig.model_validate(patch)
        except Exception as exc:
            raise InvalidArgumentError(f"invalid settings patch: {exc}") from exc


def _require_object(value: Any, name: str) -> None:
    if not isinstance(value, dict):
        raise InvalidArgumentError(f"{name} must be an object")


def _require_fields(value: Any, allowed: set[str], name: str) -> None:
    _require_object(value, name)
    unknown = set(value) - allowed
    if unknown:
        raise InvalidArgumentError(
            f"unsupported {name} field(s): {', '.join(sorted(unknown))}"
        )


def _merge_settings_patch(
    current: dict[str, Any],
    patch: dict[str, Any],
) -> dict[str, Any]:
    """Merge settings while giving optional access overrides real unset semantics."""

    merged = deep_merge(current, patch)
    security_patch = patch.get("security")
    if (
        isinstance(security_patch, dict)
        and "accessPreset" in security_patch
        and security_patch["accessPreset"] is None
    ):
        security = merged.get("security")
        if isinstance(security, dict):
            security.pop("accessPreset", None)
            security.pop("access_preset", None)
            if not security:
                merged.pop("security", None)
    return merged


def _raw_security(config: dict[str, Any]) -> dict[str, Any]:
    security = config.get("security")
    return security if isinstance(security, dict) else {}


def _raw_access_preset(config: dict[str, Any]) -> str | None:
    security = _raw_security(config)
    raw = security.get("accessPreset", security.get("access_preset"))
    if raw is None:
        return None
    return ExecutionAccessPreset(str(raw)).value


def _resolved_security_source(
    user_config: dict[str, Any],
    project_config: dict[str, Any],
) -> str:
    project_security = _raw_security(project_config)
    user_security = _raw_security(user_config)
    if _raw_access_preset(project_config) is not None:
        return "project"
    if _raw_access_preset(user_config) is not None:
        return "user"
    if os.environ.get("DEEPCODE_PERMISSION_MODE", "").strip():
        return "environment"
    if "permissionMode" in project_security or "permission_mode" in project_security:
        return "project_legacy"
    if "permissionMode" in user_security or "permission_mode" in user_security:
        return "user_legacy"
    return "built_in"
