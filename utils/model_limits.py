"""
Model Limits and Capabilities Detection

This module provides utilities to detect LLM model capabilities and limits
dynamically, avoiding hardcoded values and supporting model changes.
"""

from typing import Dict, Tuple, Optional
import yaml


# Model capability database
# Format: {model_name_pattern: {max_completion_tokens, max_context_tokens, cost_per_1m_input, cost_per_1m_output}}
MODEL_LIMITS = {
    # OpenAI Models
    "gpt-4o-mini": {
        "max_completion_tokens": 16384,
        "max_context_tokens": 128000,
        "input_cost_per_1m": 0.15,
        "output_cost_per_1m": 0.60,
        "provider": "openai"
    },
    "gpt-4o": {
        "max_completion_tokens": 16384,
        "max_context_tokens": 128000,
        "input_cost_per_1m": 2.50,
        "output_cost_per_1m": 10.00,
        "provider": "openai"
    },
    "gpt-4-turbo": {
        "max_completion_tokens": 4096,
        "max_context_tokens": 128000,
        "input_cost_per_1m": 10.00,
        "output_cost_per_1m": 30.00,
        "provider": "openai"
    },
    "gpt-4": {
        "max_completion_tokens": 8192,
        "max_context_tokens": 8192,
        "input_cost_per_1m": 30.00,
        "output_cost_per_1m": 60.00,
        "provider": "openai"
    },
    "gpt-3.5-turbo": {
        "max_completion_tokens": 4096,
        "max_context_tokens": 16385,
        "input_cost_per_1m": 0.50,
        "output_cost_per_1m": 1.50,
        "provider": "openai"
    },
    "o1-mini": {
        "max_completion_tokens": 65536,
        "max_context_tokens": 128000,
        "input_cost_per_1m": 3.00,
        "output_cost_per_1m": 12.00,
        "provider": "openai"
    },
    "o1": {
        "max_completion_tokens": 100000,
        "max_context_tokens": 200000,
        "input_cost_per_1m": 15.00,
        "output_cost_per_1m": 60.00,
        "provider": "openai"
    },
    # Anthropic Models
    "claude-3-5-sonnet": {
        "max_completion_tokens": 8192,
        "max_context_tokens": 200000,
        "input_cost_per_1m": 3.00,
        "output_cost_per_1m": 15.00,
        "provider": "anthropic"
    },
    "claude-3-opus": {
        "max_completion_tokens": 4096,
        "max_context_tokens": 200000,
        "input_cost_per_1m": 15.00,
        "output_cost_per_1m": 75.00,
        "provider": "anthropic"
    },
    "claude-3-sonnet": {
        "max_completion_tokens": 4096,
        "max_context_tokens": 200000,
        "input_cost_per_1m": 3.00,
        "output_cost_per_1m": 15.00,
        "provider": "anthropic"
    },
    "claude-3-haiku": {
        "max_completion_tokens": 4096,
        "max_context_tokens": 200000,
        "input_cost_per_1m": 0.25,
        "output_cost_per_1m": 1.25,
        "provider": "anthropic"
    },
}


def get_model_from_config(config_path: str = "mcp_agent.config.yaml") -> Optional[str]:
    """
    Get the default model from configuration file.
    
    Args:
        config_path: Path to the configuration file
        
    Returns:
        Model name or None if not found
    """
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f)
            
        # Check OpenAI config first
        if "openai" in config and "default_model" in config["openai"]:
            return config["openai"]["default_model"]
        
        # Check Anthropic config
        if "anthropic" in config and "default_model" in config["anthropic"]:
            return config["anthropic"]["default_model"]
            
        return None
    except Exception as e:
        print(f"⚠️ Warning: Could not read model from config: {e}")
        return None


def get_model_limits(model_name: Optional[str] = None, config_path: str = "mcp_agent.config.yaml") -> Dict:
    """
    Get the limits and capabilities for a specific model.
    
    Args:
        model_name: Name of the model (if None, reads from config)
        config_path: Path to the configuration file
        
    Returns:
        Dictionary with model limits and capabilities
    """
    # Get model name from config if not provided
    if not model_name:
        model_name = get_model_from_config(config_path)
    
    if not model_name:
        print("⚠️ Warning: Could not determine model, using safe defaults")
        return {
            "max_completion_tokens": 4096,
            "max_context_tokens": 8192,
            "input_cost_per_1m": 1.00,
            "output_cost_per_1m": 3.00,
            "provider": "unknown"
        }
    
    # Find matching model in database
    for pattern, limits in MODEL_LIMITS.items():
        if pattern.lower() in model_name.lower():
            print(f"📊 Detected model: {model_name} → {pattern}")
            print(f"   Max completion tokens: {limits['max_completion_tokens']}")
            print(f"   Max context tokens: {limits['max_context_tokens']}")
            return limits.copy()
    
    # Model not in database - use conservative defaults
    print(f"⚠️ Warning: Model '{model_name}' not in database, using conservative defaults")
    return {
        "max_completion_tokens": 4096,
        "max_context_tokens": 8192,
        "input_cost_per_1m": 1.00,
        "output_cost_per_1m": 3.00,
        "provider": "unknown"
    }


def get_safe_max_tokens(
    model_name: Optional[str] = None, 
    config_path: str = "mcp_agent.config.yaml",
    safety_margin: float = 0.9
) -> int:
    """
    Get a safe max_tokens value for the model with a safety margin.
    
    Args:
        model_name: Name of the model (if None, reads from config)
        config_path: Path to the configuration file
        safety_margin: Percentage of max to use (0.9 = 90% of max)
        
    Returns:
        Safe max_tokens value
    """
    limits = get_model_limits(model_name, config_path)
    safe_tokens = int(limits["max_completion_tokens"] * safety_margin)
    print(f"🔧 Safe max_tokens for {model_name or 'current model'}: {safe_tokens} ({safety_margin*100:.0f}% of {limits['max_completion_tokens']})")
    return safe_tokens


def calculate_token_cost(
    input_tokens: int,
    output_tokens: int,
    model_name: Optional[str] = None,
    config_path: str = "mcp_agent.config.yaml"
) -> float:
    """
    Calculate the cost for a given number of tokens.
    
    Args:
        input_tokens: Number of input/prompt tokens
        output_tokens: Number of output/completion tokens
        model_name: Name of the model (if None, reads from config)
        config_path: Path to the configuration file
        
    Returns:
        Total cost in dollars
    """
    limits = get_model_limits(model_name, config_path)
    
    input_cost = (input_tokens / 1_000_000) * limits["input_cost_per_1m"]
    output_cost = (output_tokens / 1_000_000) * limits["output_cost_per_1m"]
    total_cost = input_cost + output_cost
    
    return total_cost


def get_retry_token_limits(
    base_tokens: int,
    retry_count: int,
    model_name: Optional[str] = None,
    config_path: str = "mcp_agent.config.yaml"
) -> int:
    """
    Get adjusted token limits for retries, respecting model maximum.
    
    Args:
        base_tokens: Base token limit
        retry_count: Current retry attempt (0, 1, 2, ...)
        model_name: Name of the model (if None, reads from config)
        config_path: Path to the configuration file
        
    Returns:
        Adjusted token limit for retry
    """
    limits = get_model_limits(model_name, config_path)
    max_allowed = limits["max_completion_tokens"]
    
    # Increase tokens with each retry, but cap at model maximum
    if retry_count == 0:
        # First retry: 87.5% of max
        new_tokens = int(max_allowed * 0.875)
    elif retry_count == 1:
        # Second retry: 95% of max
        new_tokens = int(max_allowed * 0.95)
    else:
        # Third+ retry: Use max with small safety margin
        new_tokens = int(max_allowed * 0.98)
    
    # Ensure we don't exceed the model's hard limit
    new_tokens = min(new_tokens, max_allowed)
    
    print(f"🔧 Retry {retry_count + 1}: Adjusting tokens from {base_tokens} → {new_tokens} (max: {max_allowed})")
    
    return new_tokens


def get_provider_from_model(model_name: Optional[str] = None, config_path: str = "mcp_agent.config.yaml") -> str:
    """
    Determine the provider (openai/anthropic) for a given model.
    
    Args:
        model_name: Name of the model (if None, reads from config)
        config_path: Path to the configuration file
        
    Returns:
        Provider name: "openai", "anthropic", or "unknown"
    """
    limits = get_model_limits(model_name, config_path)
    return limits.get("provider", "unknown")

