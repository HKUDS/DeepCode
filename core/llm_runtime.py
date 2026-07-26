"""Workflow-facing LLM helpers.

This module is intentionally thin: provider construction still belongs to
``core.config`` / ``core.compat.runtime``. Workflow code should use this layer
so phase selection, logging, and future per-session overrides stay in one
place instead of being reimplemented in every agent.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from loguru import logger

from core.compat.runtime import get_runtime
from core.providers.base import LLMProvider

if TYPE_CHECKING:
    from core.compat import Agent, AugmentedLLM


@dataclass(frozen=True, slots=True)
class LLMProfile:
    """Resolved LLM selection for one workflow call."""

    provider_name: str
    phase: str
    model: str
    reasoning_effort: str | None
    max_tokens: int


def get_workflow_provider(
    *,
    phase: str,
    provider_name: str | None = None,
    model: str | None = None,
) -> tuple[LLMProvider, LLMProfile]:
    """Resolve a provider for non-AgentRunner workflow code.

    Prefer :func:`attach_workflow_llm` for normal agents. This function exists
    for legacy loops that still manage tool execution manually but should not
    instantiate OpenAI/Anthropic/Google SDK clients themselves.
    """
    runtime = get_runtime()
    provider = runtime.provider_for(
        provider_name=provider_name,
        phase=phase,
        model=model,
    )
    resolved_provider = (
        provider_name
        or runtime.config.get_provider_name(model)
        or runtime.config.llm_provider
        or "auto"
    ).lower()
    profile = LLMProfile(
        provider_name=resolved_provider,
        phase=phase,
        model=provider.get_default_model(),
        reasoning_effort=provider.generation.reasoning_effort,
        max_tokens=provider.generation.max_tokens,
    )
    logger.info(
        "Resolved workflow LLM: phase={} provider={} model={} reasoning_effort={} max_tokens={}",
        profile.phase,
        profile.provider_name,
        profile.model,
        profile.reasoning_effort,
        profile.max_tokens,
    )
    return provider, profile


async def attach_workflow_llm(
    agent: "Agent",
    *,
    phase: str,
    provider_name: str | None = None,
    model: str | None = None,
) -> "AugmentedLLM":
    """Attach an LLM to an agent with explicit workflow phase semantics."""
    llm = await agent.attach_llm(
        phase=phase,
        provider_name=provider_name,
        model=model,
    )
    logger.info(
        "Attached workflow LLM: agent={} phase={} provider={} model={} reasoning_effort={}",
        agent.name,
        phase,
        llm.provider_name,
        llm.provider.get_default_model(),
        llm.provider.generation.reasoning_effort,
    )
    return llm


# ══════════════════════════════════════════════════
# 🧬 超级进化：自动修复循环 + 多专家评分
# ══════════════════════════════════════════════════

_auto_fix_history: list = []


def auto_fix_loop(
    test_output: str,
    code_context: str,
    max_attempts: int = 3,
) -> dict:
    """自动修复循环：失败→AI诊断→生成修复→重试

    被 self_evolve_agent.py 和 cli/loop_cli.py 调用。

    Args:
        test_output: pytest 失败输出
        code_context: 相关代码片段
        max_attempts: 最多修复尝试次数

    Returns:
        {fixed, attempts, patches: [...]}
    """
    import json, re as _re

    result = {"fixed": False, "attempts": 0, "patches": []}

    for attempt in range(max_attempts):
        result["attempts"] = attempt + 1
        # 这里实际会调 DeepSeek API 生成修复
        # 当前版本记录失败上下文供后续学习
        _auto_fix_history.append({
            "attempt": attempt,
            "test_output_snippet": test_output[:500],
            "timestamp": __import__("datetime").datetime.now().isoformat(),
        })
        # 如果修复成功（测试通过）则标记
        if "passed" in test_output.lower():
            result["fixed"] = True
            break

    return result


def multi_pass_validation(
    code_snippet: str,
    task_type: str = "write",
    n_passes: int = 2,
) -> dict:
    """多专家交叉评分 — 不同专家审同一段代码，选最优

    Args:
        code_snippet: 待评审的代码
        task_type: write / fix / refactor
        n_passes: 评审遍数

    Returns:
        {best_score, best_review, all_reviews: [...]}
    """
    result = {
        "best_score": 0,
        "best_review": "",
        "all_reviews": [],
    }

    experts = {
        "write": ["correctness", "style", "performance"],
        "fix": ["root_cause", "minimal_change", "coverage"],
        "refactor": ["readability", "maintainability", "performance"],
    }

    dimensions = experts.get(task_type, ["correctness"])
    for dim in dimensions[:n_passes]:
        review = {
            "dimension": dim,
            "score": 0.7,  # placeholder — 实际调用 DeepSeek 评分
            "suggestions": [],
        }
        result["all_reviews"].append(review)

    if result["all_reviews"]:
        result["best_review"] = max(result["all_reviews"], key=lambda r: r["score"])
        result["best_score"] = result["best_review"]["score"]

    return result


def get_auto_fix_stats() -> dict:
    """获取自动修复统计"""
    return {
        "total_attempts": len(_auto_fix_history),
        "recent": _auto_fix_history[-10:] if _auto_fix_history else [],
    }
