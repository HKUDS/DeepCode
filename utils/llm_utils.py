"""Workflow-facing helpers backed by ``deepcode_config.json``.

All callers route through the parsed :class:`core.config.DeepCodeConfig`
(resolved lazily from the global runtime) so every entry-point — CLI,
UI, workflows — sees one source of truth instead of re-reading the
JSON file.
"""

from __future__ import annotations

from typing import Any, Dict, Tuple

from core.config import DeepCodeConfig


def _resolve_config(config: DeepCodeConfig | None = None) -> DeepCodeConfig:
    """Return *config* if supplied, else the process-wide runtime config.

    Imports are kept local to avoid pulling the whole runtime on simple
    helper calls (e.g. CLI startup before the user even runs a pipeline).
    """
    if config is not None:
        return config
    from core.compat.runtime import get_runtime

    return get_runtime().config


def get_default_models(config: DeepCodeConfig | None = None) -> Dict[str, str]:
    """Return the model name resolved for each phase.

    Always returns the same keys (``"anthropic"``, ``"openai"``, ``"google"``,
    plus their ``_planning`` / ``_implementation`` variants) so legacy
    callers that index in by provider name keep working. With nanobot-style
    configuration the *value* for every key is the model picked for the
    relevant phase: when a phase override is set, planning/implementation
    diverge; otherwise they all collapse to the default model.
    """
    cfg = _resolve_config(config)
    default_model = cfg.resolve_phase("default").model
    planning_model = cfg.resolve_phase("planning").model
    implementation_model = cfg.resolve_phase("implementation").model

    return {
        "anthropic": default_model,
        "openai": default_model,
        "google": default_model,
        "anthropic_planning": planning_model,
        "openai_planning": planning_model,
        "google_planning": planning_model,
        "anthropic_implementation": implementation_model,
        "openai_implementation": implementation_model,
        "google_implementation": implementation_model,
    }


def get_token_limits(config: DeepCodeConfig | None = None) -> Tuple[int, int]:
    """Return ``(base_max_tokens, retry_max_tokens)``.

    ``base`` defaults to ``agents.defaults.maxTokens`` (or
    ``baseMaxTokens`` if explicitly set). ``retry`` defaults to
    ``retryMaxTokens`` or 75% of base.
    """
    cfg = _resolve_config(config)
    defaults = cfg.agents.defaults
    base = defaults.base_max_tokens or defaults.max_tokens
    retry = defaults.retry_max_tokens or max(1, int(base * 0.75))
    return base, retry


def get_document_segmentation_config(
    config: DeepCodeConfig | None = None,
) -> Dict[str, Any]:
    """Return the document-segmentation policy as a plain dict."""
    try:
        cfg = _resolve_config(config)
    except Exception:
        # CLI startup may import this before the JSON config is in place;
        # fall back to safe defaults so the UI still renders.
        return {"enabled": True, "size_threshold_chars": 50000}

    seg = cfg.document_segmentation
    return {
        "enabled": seg.enabled,
        "size_threshold_chars": seg.size_threshold_chars,
    }


def should_use_document_segmentation(
    document_content: str,
    config: DeepCodeConfig | None = None,
) -> Tuple[bool, str]:
    """Decide whether segmentation is needed for *document_content*."""
    seg = get_document_segmentation_config(config)

    if not seg["enabled"]:
        return False, "Document segmentation disabled in configuration"

    doc_size = len(document_content)
    threshold = seg["size_threshold_chars"]

    if doc_size > threshold:
        return (
            True,
            f"Document size ({doc_size:,} chars) exceeds threshold ({threshold:,} chars)",
        )
    return (
        False,
        f"Document size ({doc_size:,} chars) below threshold ({threshold:,} chars)",
    )


def get_adaptive_agent_config(
    use_segmentation: bool, search_server_names: list | None = None
) -> Dict[str, list]:
    """Return per-agent server lists, swapping in the segmentation server when asked."""
    if search_server_names is None:
        search_server_names = []

    config = {
        "concept_analysis": [],
        "algorithm_analysis": list(search_server_names),
        "code_planner": list(search_server_names),
    }

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
    """Return the right system prompts for segmented vs. monolithic reading."""
    from prompts.code_prompts import (
        CODE_PLANNING_PROMPT,
        CODE_PLANNING_PROMPT_TRADITIONAL,
        PAPER_ALGORITHM_ANALYSIS_PROMPT,
        PAPER_ALGORITHM_ANALYSIS_PROMPT_TRADITIONAL,
        PAPER_CONCEPT_ANALYSIS_PROMPT,
        PAPER_CONCEPT_ANALYSIS_PROMPT_TRADITIONAL,
    )

    if use_segmentation:
        return {
            "concept_analysis": PAPER_CONCEPT_ANALYSIS_PROMPT,
            "algorithm_analysis": PAPER_ALGORITHM_ANALYSIS_PROMPT,
            "code_planning": CODE_PLANNING_PROMPT,
        }
    return {
        "concept_analysis": PAPER_CONCEPT_ANALYSIS_PROMPT_TRADITIONAL,
        "algorithm_analysis": PAPER_ALGORITHM_ANALYSIS_PROMPT_TRADITIONAL,
        "code_planning": CODE_PLANNING_PROMPT_TRADITIONAL,
    }


__all__ = [
    "get_adaptive_agent_config",
    "get_adaptive_prompts",
    "get_default_models",
    "get_document_segmentation_config",
    "get_token_limits",
    "should_use_document_segmentation",
]
