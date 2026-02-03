"""
LLM utility functions for DeepCode project.

This module provides common LLM-related utilities to avoid circular imports
and reduce code duplication across the project.
"""

import os
import yaml
from typing import Any, Type, Dict, Tuple


def get_api_keys(secrets_path: str = "mcp_agent.secrets.yaml") -> Dict[str, str]:
    """
    Get API keys from environment variables or secrets file.

    Environment variables take precedence:
    - GOOGLE_API_KEY or GEMINI_API_KEY
    - ANTHROPIC_API_KEY
    - OPENAI_API_KEY

    Args:
        secrets_path: Path to the secrets YAML file

    Returns:
        Dict with 'google', 'anthropic', 'openai' keys
    """
    secrets = {}
    if os.path.exists(secrets_path):
        with open(secrets_path, "r", encoding="utf-8") as f:
            secrets = yaml.safe_load(f) or {}

    return {
        "google": (
            os.environ.get("GOOGLE_API_KEY")
            or os.environ.get("GEMINI_API_KEY")
            or secrets.get("google", {}).get("api_key", "")
        ).strip(),
        "anthropic": (
            os.environ.get("ANTHROPIC_API_KEY")
            or secrets.get("anthropic", {}).get("api_key", "")
        ).strip(),
        "openai": (
            os.environ.get("OPENAI_API_KEY")
            or secrets.get("openai", {}).get("api_key", "")
        ).strip(),
    }


def load_api_config(secrets_path: str = "mcp_agent.secrets.yaml") -> Dict[str, Any]:
    """
    Load API configuration with environment variable override.

    Environment variables take precedence over YAML values:
    - GOOGLE_API_KEY or GEMINI_API_KEY
    - ANTHROPIC_API_KEY
    - OPENAI_API_KEY

    Args:
        secrets_path: Path to the secrets YAML file

    Returns:
        Dict with provider configs including api_key values
    """
    # Load base config from YAML
    config = {}
    if os.path.exists(secrets_path):
        with open(secrets_path, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f) or {}

    # Get keys with env var override
    keys = get_api_keys(secrets_path)

    # Merge into config structure
    for provider, key in keys.items():
        if key:
            config.setdefault(provider, {})["api_key"] = key

    return config


def _get_llm_class(provider: str) -> Type[Any]:
    """Lazily import and return the LLM class for a given provider."""
    if provider == "anthropic":
        from mcp_agent.workflows.llm.augmented_llm_anthropic import (
            AnthropicAugmentedLLM,
        )

        return AnthropicAugmentedLLM
    elif provider == "openai":
        from mcp_agent.workflows.llm.augmented_llm_openai import OpenAIAugmentedLLM

        return OpenAIAugmentedLLM
    elif provider == "google":
        from mcp_agent.workflows.llm.augmented_llm_google import GoogleAugmentedLLM

        return GoogleAugmentedLLM
    else:
        raise ValueError(f"Unknown provider: {provider}")


def get_preferred_llm_class(config_path: str = "mcp_agent.secrets.yaml") -> Type[Any]:
    """
    Select the LLM class based on user preference and API key availability.

    Priority:
    1. Check mcp_agent.config.yaml for llm_provider preference
    2. Verify the preferred provider has API key
    3. Fallback to first available provider

    Args:
        config_path: Path to the secrets YAML configuration file

    Returns:
        class: The preferred LLM class
    """
    try:
        # Get API keys with environment variable override
        keys = get_api_keys(config_path)
        google_key = keys["google"]
        anthropic_key = keys["anthropic"]
        openai_key = keys["openai"]

        # Read user preference from main config
        main_config_path = "mcp_agent.config.yaml"
        preferred_provider = None
        if os.path.exists(main_config_path):
            with open(main_config_path, "r", encoding="utf-8") as f:
                main_config = yaml.safe_load(f)
                preferred_provider = main_config.get("llm_provider", "").strip().lower()

        # Map of providers to their keys and class names
        provider_keys = {
            "anthropic": (anthropic_key, "AnthropicAugmentedLLM"),
            "google": (google_key, "GoogleAugmentedLLM"),
            "openai": (openai_key, "OpenAIAugmentedLLM"),
        }

        # Try user's preferred provider first
        if preferred_provider and preferred_provider in provider_keys:
            api_key, class_name = provider_keys[preferred_provider]
            if api_key:
                print(f"🤖 Using {class_name} (user preference: {preferred_provider})")
                return _get_llm_class(preferred_provider)
            else:
                print(
                    f"⚠️ Preferred provider '{preferred_provider}' has no API key, checking alternatives..."
                )

        # Fallback: try providers in order of availability
        for provider, (api_key, class_name) in provider_keys.items():
            if api_key:
                print(f"🤖 Using {class_name} ({provider} API key found)")
                return _get_llm_class(provider)

        # No API keys found - default to google
        print("⚠️ No API keys configured, falling back to GoogleAugmentedLLM")
        return _get_llm_class("google")

    except Exception as e:
        print(f"🤖 Error reading config file {config_path}: {e}")
        print("🤖 Falling back to GoogleAugmentedLLM")
        return _get_llm_class("google")


def get_token_limits(config_path: str = "mcp_agent.config.yaml") -> Tuple[int, int]:
    """
    Get token limits from configuration.

    Args:
        config_path: Path to the main configuration file

    Returns:
        tuple: (base_max_tokens, retry_max_tokens)
    """
    # Default values that work with qwen/qwen-max (32768 total context)
    default_base = 20000
    default_retry = 15000

    try:
        if os.path.exists(config_path):
            with open(config_path, "r", encoding="utf-8") as f:
                config = yaml.safe_load(f)

            openai_config = config.get("openai", {})
            base_tokens = openai_config.get("base_max_tokens", default_base)
            retry_tokens = openai_config.get("retry_max_tokens", default_retry)

            print(
                f"⚙️ Token limits from config: base={base_tokens}, retry={retry_tokens}"
            )
            return base_tokens, retry_tokens
        else:
            print(
                f"⚠️ Config file {config_path} not found, using defaults: base={default_base}, retry={default_retry}"
            )
            return default_base, default_retry
    except Exception as e:
        print(f"⚠️ Error reading token config from {config_path}: {e}")
        print(
            f"🔧 Falling back to default token limits: base={default_base}, retry={default_retry}"
        )
        return default_base, default_retry


def get_default_models(config_path: str = "mcp_agent.config.yaml"):
    """
    Get default models from configuration file.

    Args:
        config_path: Path to the configuration file

    Returns:
        dict: Dictionary with 'anthropic', 'openai', 'google' default models,
              plus 'google_planning' and 'google_implementation' for phase-specific models
    """
    try:
        if os.path.exists(config_path):
            with open(config_path, "r", encoding="utf-8") as f:
                config = yaml.safe_load(f)

            # Handle null values in config sections
            anthropic_config = config.get("anthropic") or {}
            openai_config = config.get("openai") or {}
            google_config = config.get("google") or {}

            anthropic_model = anthropic_config.get(
                "default_model", "claude-sonnet-4-20250514"
            )
            openai_model = openai_config.get("default_model", "o3-mini")
            google_model = google_config.get("default_model", "gemini-2.0-flash")

            # Phase-specific models (fall back to default if not specified)
            # Google
            google_planning = google_config.get("planning_model", google_model)
            google_implementation = google_config.get(
                "implementation_model", google_model
            )
            # Anthropic
            anthropic_planning = anthropic_config.get("planning_model", anthropic_model)
            anthropic_implementation = anthropic_config.get(
                "implementation_model", anthropic_model
            )
            # OpenAI
            openai_planning = openai_config.get("planning_model", openai_model)
            openai_implementation = openai_config.get(
                "implementation_model", openai_model
            )

            return {
                "anthropic": anthropic_model,
                "openai": openai_model,
                "google": google_model,
                "google_planning": google_planning,
                "google_implementation": google_implementation,
                "anthropic_planning": anthropic_planning,
                "anthropic_implementation": anthropic_implementation,
                "openai_planning": openai_planning,
                "openai_implementation": openai_implementation,
            }
        else:
            print(f"Config file {config_path} not found, using default models")
            return _get_fallback_models()

    except Exception as e:
        print(f"❌Error reading config file {config_path}: {e}")
        return _get_fallback_models()


def _get_fallback_models():
    """Return fallback model configuration when config file is unavailable."""
    google = "gemini-2.0-flash"
    anthropic = "claude-sonnet-4-20250514"
    openai = "o3-mini"
    return {
        "google": google,
        "google_planning": google,
        "google_implementation": google,
        "anthropic": anthropic,
        "anthropic_planning": anthropic,
        "anthropic_implementation": anthropic,
        "openai": openai,
        "openai_planning": openai,
        "openai_implementation": openai,
    }


def get_document_segmentation_config(
    config_path: str = "mcp_agent.config.yaml",
) -> Dict[str, Any]:
    """
    Get document segmentation configuration from config file.

    Args:
        config_path: Path to the main configuration file

    Returns:
        Dict containing segmentation configuration with default values
    """
    try:
        if os.path.exists(config_path):
            with open(config_path, "r", encoding="utf-8") as f:
                config = yaml.safe_load(f)

            # Get document segmentation config with defaults
            seg_config = config.get("document_segmentation", {})
            return {
                "enabled": seg_config.get("enabled", True),
                "size_threshold_chars": seg_config.get("size_threshold_chars", 50000),
            }
        else:
            print(
                f"📄 Config file {config_path} not found, using default segmentation settings"
            )
            return {"enabled": True, "size_threshold_chars": 50000}

    except Exception as e:
        print(f"📄 Error reading segmentation config from {config_path}: {e}")
        print("📄 Using default segmentation settings")
        return {"enabled": True, "size_threshold_chars": 50000}


def should_use_document_segmentation(
    document_content: str, config_path: str = "mcp_agent.config.yaml"
) -> Tuple[bool, str]:
    """
    Determine whether to use document segmentation based on configuration and document size.

    Args:
        document_content: The content of the document to analyze
        config_path: Path to the configuration file

    Returns:
        Tuple of (should_segment, reason) where:
        - should_segment: Boolean indicating whether to use segmentation
        - reason: String explaining the decision
    """
    seg_config = get_document_segmentation_config(config_path)

    if not seg_config["enabled"]:
        return False, "Document segmentation disabled in configuration"

    doc_size = len(document_content)
    threshold = seg_config["size_threshold_chars"]

    if doc_size > threshold:
        return (
            True,
            f"Document size ({doc_size:,} chars) exceeds threshold ({threshold:,} chars)",
        )
    else:
        return (
            False,
            f"Document size ({doc_size:,} chars) below threshold ({threshold:,} chars)",
        )


def get_adaptive_agent_config(
    use_segmentation: bool, search_server_names: list = None
) -> Dict[str, list]:
    """
    Get adaptive agent configuration based on whether to use document segmentation.

    Args:
        use_segmentation: Whether to include document-segmentation server
        search_server_names: Base search server names (from get_search_server_names)

    Returns:
        Dict containing server configurations for different agents
    """
    if search_server_names is None:
        search_server_names = []

    # Base configuration
    config = {
        "concept_analysis": [],
        "algorithm_analysis": search_server_names.copy(),
        "code_planner": search_server_names.copy(),
    }

    # Add document-segmentation server if needed
    if use_segmentation:
        config["concept_analysis"] = ["document-segmentation"]
        if "document-segmentation" not in config["algorithm_analysis"]:
            config["algorithm_analysis"].append("document-segmentation")
        if "document-segmentation" not in config["code_planner"]:
            config["code_planner"].append("document-segmentation")
    else:
        config["concept_analysis"] = ["filesystem"]
        if "filesystem" not in config["algorithm_analysis"]:
            config["algorithm_analysis"].append("filesystem")
        if "filesystem" not in config["code_planner"]:
            config["code_planner"].append("filesystem")

    return config


def get_adaptive_prompts(use_segmentation: bool) -> Dict[str, str]:
    """
    Get appropriate prompt versions based on segmentation usage.

    Args:
        use_segmentation: Whether to use segmented reading prompts

    Returns:
        Dict containing prompt configurations
    """
    # Import here to avoid circular imports
    from prompts.code_prompts import (
        PAPER_CONCEPT_ANALYSIS_PROMPT,
        PAPER_ALGORITHM_ANALYSIS_PROMPT,
        CODE_PLANNING_PROMPT,
        PAPER_CONCEPT_ANALYSIS_PROMPT_TRADITIONAL,
        PAPER_ALGORITHM_ANALYSIS_PROMPT_TRADITIONAL,
        CODE_PLANNING_PROMPT_TRADITIONAL,
    )

    if use_segmentation:
        return {
            "concept_analysis": PAPER_CONCEPT_ANALYSIS_PROMPT,
            "algorithm_analysis": PAPER_ALGORITHM_ANALYSIS_PROMPT,
            "code_planning": CODE_PLANNING_PROMPT,
        }
    else:
        return {
            "concept_analysis": PAPER_CONCEPT_ANALYSIS_PROMPT_TRADITIONAL,
            "algorithm_analysis": PAPER_ALGORITHM_ANALYSIS_PROMPT_TRADITIONAL,
            "code_planning": CODE_PLANNING_PROMPT_TRADITIONAL,
        }
