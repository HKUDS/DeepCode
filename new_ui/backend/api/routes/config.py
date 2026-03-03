"""
Configuration API Routes
Handles LLM provider and settings management
"""

from fastapi import APIRouter, HTTPException
import yaml

from settings import (
    load_mcp_config,
    load_secrets,
    get_llm_provider,
    get_llm_models,
    is_indexing_enabled,
    CONFIG_PATH,
)
from models.requests import LLMProviderUpdateRequest
from models.responses import ConfigResponse, SettingsResponse


router = APIRouter()


@router.get("/settings", response_model=SettingsResponse)
async def get_settings():
    """Get current application settings"""
    config = load_mcp_config()
    provider = get_llm_provider()
    models = get_llm_models(provider)

    return SettingsResponse(
        llm_provider=provider,
        models=models,
        indexing_enabled=is_indexing_enabled(),
        document_segmentation=config.get("document_segmentation", {}),
    )


@router.get("/llm-providers", response_model=ConfigResponse)
async def get_llm_providers():
    """Get available LLM providers and their configurations"""
    secrets = load_secrets()

    # Get available providers (those with API keys configured)
    available_providers = []
    for provider in ["google", "anthropic", "openai"]:
        if secrets.get(provider, {}).get("api_key"):
            available_providers.append(provider)

    current_provider = get_llm_provider()
    models = get_llm_models(current_provider)

    return ConfigResponse(
        llm_provider=current_provider,
        available_providers=available_providers,
        models=models,
        indexing_enabled=is_indexing_enabled(),
    )


@router.put("/llm-provider")
async def set_llm_provider(request: LLMProviderUpdateRequest):
    """Update the preferred LLM provider"""
    secrets = load_secrets()

    # Verify provider has an API key
    if not secrets.get(request.provider, {}).get("api_key"):
        raise HTTPException(
            status_code=400,
            detail=f"Provider '{request.provider}' does not have an API key configured",
        )

    # Update config file
    try:
        config = load_mcp_config()
        config["llm_provider"] = request.provider

        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            yaml.dump(config, f, default_flow_style=False)

        return {
            "status": "success",
            "message": f"LLM provider updated to '{request.provider}'",
            "provider": request.provider,
        }

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to update configuration: {str(e)}",
        )
