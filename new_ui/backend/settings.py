"""
Configuration management for DeepCode New UI Backend
Reads from existing mcp_agent.config.yaml and mcp_agent.secrets.yaml
"""

from pathlib import Path
from typing import Optional, Dict, Any

import yaml
from pydantic_settings import BaseSettings


# Project paths
BACKEND_DIR = Path(__file__).resolve().parent
NEW_UI_DIR = BACKEND_DIR.parent
PROJECT_ROOT = NEW_UI_DIR.parent
CONFIG_PATH = PROJECT_ROOT / "mcp_agent.config.yaml"
SECRETS_PATH = PROJECT_ROOT / "mcp_agent.secrets.yaml"


class Settings(BaseSettings):
    """Application settings"""

    # Server settings
    host: str = "0.0.0.0"
    port: int = 8000
    debug: bool = True

    # CORS settings
    cors_origins: list = ["http://localhost:5173", "http://localhost:3000"]

    # File upload settings
    max_upload_size: int = 100 * 1024 * 1024  # 100MB
    upload_dir: str = str(PROJECT_ROOT / "uploads")

    # Session settings
    session_timeout: int = 3600  # 1 hour

    class Config:
        env_prefix = "DEEPCODE_"


settings = Settings()


def load_mcp_config() -> Dict[str, Any]:
    """Load main MCP agent configuration"""
    if not CONFIG_PATH.exists():
        return {}

    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def load_secrets() -> Dict[str, Any]:
    """Load API secrets configuration"""
    if not SECRETS_PATH.exists():
        return {}

    with open(SECRETS_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def get_llm_provider() -> str:
    """Get the preferred LLM provider from config"""
    config = load_mcp_config()
    return config.get("llm_provider", "google")


def get_llm_models(provider: Optional[str] = None) -> Dict[str, str]:
    """Get the model configuration for a provider"""
    config = load_mcp_config()
    provider = provider or get_llm_provider()

    provider_config = config.get(provider, {})
    return {
        "default": provider_config.get("default_model", ""),
        "planning": provider_config.get("planning_model", ""),
        "implementation": provider_config.get("implementation_model", ""),
    }


def get_api_key(provider: str) -> Optional[str]:
    """Get API key for a specific provider"""
    secrets = load_secrets()
    provider_secrets = secrets.get(provider, {})
    return provider_secrets.get("api_key")


def is_indexing_enabled() -> bool:
    """Check if document indexing is enabled"""
    config = load_mcp_config()
    doc_seg = config.get("document_segmentation", {})
    return doc_seg.get("enabled", False)
