"""Configuration management for the DeepCode New UI backend.

The backend reads from the same ``deepcode_config.json`` that the rest of
DeepCode uses. We expose a few thin helpers (``get_llm_provider``,
``get_llm_models`` etc.) so the FastAPI routes don't have to know about the
Pydantic schema directly.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

from pydantic_settings import BaseSettings

from core.config import (
    DeepCodeConfig,
    default_config_path,
    load_config,
)
from core.providers.registry import find_by_name


# ---------------------------------------------------------------------------
# Project paths
# ---------------------------------------------------------------------------

BACKEND_DIR = Path(__file__).resolve().parent
NEW_UI_DIR = BACKEND_DIR.parent
PROJECT_ROOT = NEW_UI_DIR.parent
CONFIG_PATH: Path = default_config_path()


class Settings(BaseSettings):
    """Application settings (server-side only, separate from LLM config)."""

    host: str = "0.0.0.0"
    port: int = 8000
    debug: bool = True

    # Environment: "docker" for production, anything else for development
    env: str = ""

    # CORS settings — in Docker mode the frontend is served by FastAPI
    # (same origin) so the explicit list below is enough for dev.
    cors_origins: list = [
        "http://localhost:5173",
        "http://localhost:3000",
        "http://localhost:8000",
    ]

    max_upload_size: int = 100 * 1024 * 1024  # 100 MB
    upload_dir: str = str(PROJECT_ROOT / "uploads")
    session_timeout: int = 3600  # seconds

    class Config:
        env_prefix = "DEEPCODE_"


settings = Settings()


# ---------------------------------------------------------------------------
# DeepCode JSON config helpers
# ---------------------------------------------------------------------------


def load_deepcode_config() -> DeepCodeConfig:
    """Load the JSON config from disk on every call (no caching)."""
    return load_config()


def get_llm_provider() -> str:
    """Return the active provider name (matched against the registry)."""
    cfg = load_deepcode_config()
    forced = (cfg.agents.defaults.provider or "auto").lower()
    if forced != "auto":
        return forced
    matched = cfg.get_provider_name(cfg.agents.defaults.model)
    return matched or "auto"


def get_llm_models(provider: Optional[str] = None) -> Dict[str, str]:
    """Return the resolved per-phase models.

    ``provider`` is accepted for API compatibility but the values returned
    no longer depend on it — phases are resolved against
    ``agents.defaults`` and ``agents.<phase>`` overrides.
    """
    cfg = load_deepcode_config()
    return {
        "default": cfg.resolve_phase("default").model,
        "planning": cfg.resolve_phase("planning").model,
        "implementation": cfg.resolve_phase("implementation").model,
    }


def get_api_key(provider: str) -> Optional[str]:
    """Return the configured API key for a provider name (or ``None``)."""
    cfg = load_deepcode_config()
    block = getattr(cfg.providers, provider.lower(), None)
    return block.api_key if block else None


def is_indexing_enabled() -> bool:
    """Return ``True`` when document segmentation/indexing is enabled."""
    return load_deepcode_config().document_segmentation.enabled


def list_available_providers() -> list[str]:
    """Return providers that look usable (have an apiKey or are local/oauth)."""
    cfg = load_deepcode_config()
    available: list[str] = []
    for spec in (
        find_by_name(name) for name in cfg.providers.model_dump(by_alias=False).keys()
    ):
        if spec is None:
            continue
        block = getattr(cfg.providers, spec.name, None)
        if block is None:
            continue
        if spec.is_oauth or spec.is_local:
            available.append(spec.name)
            continue
        if block.api_key:
            available.append(spec.name)
    return available


def get_document_segmentation() -> Dict[str, Any]:
    """Return the document-segmentation block as a plain dict (snake_case keys)."""
    seg = load_deepcode_config().document_segmentation
    return {
        "enabled": seg.enabled,
        "size_threshold_chars": seg.size_threshold_chars,
    }
