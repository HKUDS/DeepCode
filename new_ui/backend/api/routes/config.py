"""Configuration API routes.

Reads / writes the active LLM provider against the shared
``deepcode_config.json``. The shape of the responses is preserved so the
existing frontend (``SettingsPage``) does not need to be rewritten in
this PR.
"""

from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException

from core.compat.runtime import set_runtime
from core.providers.registry import find_by_name

from settings import (
    CONFIG_PATH,
    get_api_key,
    get_document_segmentation,
    get_llm_models,
    get_llm_provider,
    is_indexing_enabled,
    list_available_providers,
)
from services.openrouter_models import list_openrouter_models
from models.requests import LLMModelsUpdateRequest, LLMProviderUpdateRequest
from models.responses import (
    ConfigResponse,
    OpenRouterModelsResponse,
    SettingsResponse,
)


router = APIRouter()


@router.get("/settings", response_model=SettingsResponse)
async def get_settings():
    """Return the current application settings."""
    provider = get_llm_provider()
    return SettingsResponse(
        llm_provider=provider,
        models=get_llm_models(provider),
        indexing_enabled=is_indexing_enabled(),
        document_segmentation=get_document_segmentation(),
    )


@router.get("/llm-providers", response_model=ConfigResponse)
async def get_llm_providers():
    """List available providers and the currently active one."""
    current_provider = get_llm_provider()
    return ConfigResponse(
        llm_provider=current_provider,
        available_providers=list_available_providers(),
        models=get_llm_models(current_provider),
        indexing_enabled=is_indexing_enabled(),
    )


@router.get("/openrouter/models", response_model=OpenRouterModelsResponse)
async def get_openrouter_models(
    supported_parameters: str | None = None,
    force_refresh: bool = False,
):
    """Return OpenRouter model ids and metadata for the settings UI."""
    return list_openrouter_models(
        supported_parameters=supported_parameters,
        force_refresh=force_refresh,
    )


@router.put("/llm-provider")
async def set_llm_provider(request: LLMProviderUpdateRequest):
    """Force a specific provider for all phases by setting ``agents.defaults.provider``."""
    spec = find_by_name(request.provider)
    if spec is None:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown provider '{request.provider}'.",
        )

    needs_key = not (spec.is_oauth or spec.is_local or spec.is_direct)
    if needs_key and not get_api_key(spec.name):
        raise HTTPException(
            status_code=400,
            detail=f"Provider '{spec.name}' has no apiKey configured in deepcode_config.json",
        )

    try:
        config: dict = {}
        if CONFIG_PATH.exists():
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                config = json.load(f) or {}

        agents = config.setdefault("agents", {})
        defaults = agents.setdefault("defaults", {})
        defaults["provider"] = spec.name

        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2, ensure_ascii=False)
            f.write("\n")

        # Force the runtime to reload on the next access so subsequent
        # workflow calls see the new provider selection.
        set_runtime(None)

        return {
            "status": "success",
            "message": f"LLM provider updated to '{spec.name}'",
            "provider": spec.name,
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to update configuration: {str(e)}",
        )


@router.put("/llm-models")
async def set_llm_models(request: LLMModelsUpdateRequest):
    """Update default/planning/implementation models and reload runtime."""
    spec = find_by_name(request.provider)
    if spec is None:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown provider '{request.provider}'.",
        )

    needs_key = not (spec.is_oauth or spec.is_local or spec.is_direct)
    if needs_key and not get_api_key(spec.name):
        raise HTTPException(
            status_code=400,
            detail=f"Provider '{spec.name}' has no apiKey configured in deepcode_config.json",
        )

    models = {
        "default": request.default_model.strip(),
        "planning": request.planning_model.strip(),
        "implementation": request.implementation_model.strip(),
    }
    missing = [phase for phase, model in models.items() if not model]
    if missing:
        raise HTTPException(
            status_code=400,
            detail=f"Missing model id for phase(s): {', '.join(missing)}",
        )

    try:
        config: dict = {}
        if CONFIG_PATH.exists():
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                config = json.load(f) or {}

        agents = config.setdefault("agents", {})
        defaults = agents.setdefault("defaults", {})
        defaults["provider"] = spec.name
        defaults["model"] = models["default"]

        planning = agents.setdefault("planning", {})
        planning["provider"] = spec.name
        planning["model"] = models["planning"]

        implementation = agents.setdefault("implementation", {})
        implementation["provider"] = spec.name
        implementation["model"] = models["implementation"]

        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2, ensure_ascii=False)
            f.write("\n")

        set_runtime(None)

        return {
            "status": "success",
            "message": "LLM models updated. New workflows will use the selected models.",
            "provider": spec.name,
            "models": models,
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to update model configuration: {str(e)}",
        )
