"""
Intelligent Agent Orchestration Engine for Research-to-Code Automation

This module serves as the core orchestration engine that coordinates multiple specialized
AI agents to automate the complete research-to-code transformation pipeline:

1. Research Analysis Agent - Intelligent content processing and extraction
2. Workspace Infrastructure Agent - Automated environment synthesis
3. Code Architecture Agent - AI-driven design and planning
4. Reference Intelligence Agent - Automated knowledge discovery
5. Repository Acquisition Agent - Intelligent code repository management
6. Codebase Intelligence Agent - Advanced relationship analysis
7. Code Implementation Agent - AI-powered code synthesis

Core Features:
- Multi-agent coordination with intelligent task distribution
- Local environment automation for seamless deployment
- Real-time progress monitoring with comprehensive error handling
- Adaptive workflow optimization based on processing requirements
- Advanced intelligence analysis with configurable performance modes

Architecture:
- Async/await based high-performance agent coordination
- Modular agent design with specialized role separation
- Intelligent resource management and optimization
- Comprehensive logging and monitoring infrastructure
"""

import asyncio
import json
import os
import re
import textwrap
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

# MCP Agent imports
from core.compat import Agent, RequestParams
from core.agent_runtime.runner import AgentRunResult
from core.llm_runtime import attach_workflow_llm

# Local imports
from prompts.code_prompts import (
    PAPER_REFERENCE_ANALYZER_PROMPT,
    CHAT_AGENT_PLANNING_PROMPT,
)
from utils.file_processor import FileProcessor
from workflows.code_implementation_workflow import CodeImplementationWorkflow
from tools.pdf_downloader import move_file_to, download_file_to
from workflows.code_implementation_workflow_index import (
    CodeImplementationWorkflowWithIndex,
)
from utils.llm_utils import (
    should_use_document_segmentation,
    get_adaptive_prompts,
    get_token_limits,
)
from workflows.agents.document_segmentation_agent import prepare_document_segments
from workflows.agents.requirement_analysis_agent import RequirementAnalysisAgent
from workflows.environment import prepare_workflow_environment
from workflows.planning_runtime import (
    append_planning_attempt,
    build_planning_checkpoint_callback,
    clear_planning_checkpoint,
    coerce_text_to_minimal_plan,
    is_existing_plan_usable,
    read_planning_meta,
    validate_plan_text,
    write_planning_meta,
)
from workflows.plan_review_runtime import (
    PlanReviewCallback,
    PlanReviewCancelled,
    run_plan_review_gate,
)
from workflows.workflow_context import WorkflowContext

# Environment configuration
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"  # Prevent .pyc file generation

_DEFAULT_CODE_ANALYZER_TIMEOUT_S = 180


def _get_code_analyzer_timeout_s() -> int:
    """Return the wall-clock timeout for a single planning LLM attempt."""
    raw = (
        os.environ.get("DEEPCODE_CODE_ANALYZER_TIMEOUT_S")
        or os.environ.get("NANOBOT_CODE_ANALYZER_TIMEOUT_S")
        or ""
    ).strip()
    if not raw:
        return _DEFAULT_CODE_ANALYZER_TIMEOUT_S
    try:
        value = int(raw)
    except ValueError:
        print(
            f"⚠️ Invalid code analyzer timeout '{raw}', using default {_DEFAULT_CODE_ANALYZER_TIMEOUT_S}s"
        )
        return _DEFAULT_CODE_ANALYZER_TIMEOUT_S
    if value <= 0:
        print(
            f"⚠️ Non-positive code analyzer timeout '{raw}', using default {_DEFAULT_CODE_ANALYZER_TIMEOUT_S}s"
        )
        return _DEFAULT_CODE_ANALYZER_TIMEOUT_S
    return value


async def _generate_plan_with_single_agent(
    planner_agent: Agent,
    *,
    message: str,
    request_params: RequestParams,
    timeout_s: int,
    logger,
) -> AgentRunResult:
    """Generate a plan with the planner agent only.

    This path is used as the stable default for traditional full-document
    planning when the paper content is already preloaded locally. In practice
    it avoids the slowest branch in the parallel planning fan-out while still
    producing a complete YAML plan for the downstream workflow.
    """
    async with planner_agent:
        planner_llm = await attach_workflow_llm(planner_agent, phase="planning")
        logger.info(
            f"Single-agent planning started (timeout={timeout_s}s, agent={planner_agent.name})"
        )
        return await planner_llm.generate(
            message=message,
            request_params=request_params,
        )


def _load_paper_markdown_content(paper_dir: str, logger) -> tuple[str, str]:
    """Load the primary markdown artifact used for planning."""
    markdown_candidates = [
        filename
        for filename in os.listdir(paper_dir)
        if filename.endswith(".md") and not filename.endswith("implement_code_summary.md")
    ]
    if not markdown_candidates:
        raise FileNotFoundError(f"No markdown file found in {paper_dir}")

    paper_file_path = os.path.join(paper_dir, markdown_candidates[0])
    with open(paper_file_path, "r", encoding="utf-8") as f:
        paper_content = f.read()

    logger.info(
        f"Loaded paper markdown for planning: {paper_file_path} ({len(paper_content)} chars)"
    )
    return paper_file_path, paper_content


def _load_document_segments_context(
    paper_dir: str, *, max_segments: int = 8, max_chars: int = 24000
) -> Optional[str]:
    """Build deterministic planner context from the segmentation index."""
    index_path = os.path.join(paper_dir, "document_segments", "document_index.json")
    if not os.path.exists(index_path):
        return None

    with open(index_path, "r", encoding="utf-8") as f:
        index_data = json.load(f)

    segments = index_data.get("segments") or []
    if not segments:
        return None

    ranked_segments = sorted(
        segments,
        key=lambda seg: (
            float((seg.get("relevance_scores") or {}).get("code_planning", 0.0)),
            int(seg.get("char_count", 0)),
        ),
        reverse=True,
    )

    selected_segments: List[Dict[str, Any]] = []
    selected_chars = 0
    for segment in ranked_segments:
        content = (segment.get("content") or "").strip()
        if not content:
            continue
        content_len = len(content)
        if selected_segments and (
            len(selected_segments) >= max_segments
            or selected_chars + content_len > max_chars
        ):
            continue
        selected_segments.append(segment)
        selected_chars += content_len
        if len(selected_segments) >= max_segments or selected_chars >= max_chars:
            break

    if not selected_segments:
        return None

    overview = (
        f"document_type={index_data.get('document_type', 'unknown')}, "
        f"strategy={index_data.get('segmentation_strategy', 'unknown')}, "
        f"total_segments={index_data.get('total_segments', len(segments))}"
    )
    chunks = [f"SEGMENT OVERVIEW: {overview}"]
    for idx, segment in enumerate(selected_segments, start=1):
        relevance = (segment.get("relevance_scores") or {}).get("code_planning", 0.0)
        chunks.append(
            textwrap.dedent(
                f"""\
                --- SEGMENT {idx} ---
                title: {segment.get('title', f'Segment {idx}')}
                content_type: {segment.get('content_type', 'general')}
                code_planning_relevance: {relevance}
                keywords: {", ".join((segment.get('keywords') or [])[:12])}
                content:
                {segment.get('content', '').strip()}
                """
            ).strip()
        )

    return "\n\n".join(chunks)


def _build_planning_message(
    *,
    paper_dir: str,
    paper_content: str,
    use_segmentation: bool,
    segmented_context: Optional[str],
) -> str:
    """Create planner input for segmented or full-document planning."""
    if use_segmentation and segmented_context:
        return textwrap.dedent(
            f"""\
            Create a complete implementation plan for the research paper in `{paper_dir}`.

            Use the segmented document context below as the authoritative source. Produce a full YAML reproduction plan with all five required sections. Do not defer work to other agents and do not describe a parallel analysis process.

            === SEGMENTED DOCUMENT CONTEXT START ===
            {segmented_context}
            === SEGMENTED DOCUMENT CONTEXT END ===
            """
        )

    return textwrap.dedent(
        f"""\
        Analyze the research paper provided below and generate a comprehensive code reproduction plan.

        === PAPER CONTENT START ===
        {paper_content}
        === PAPER CONTENT END ===

        Based on this paper, generate a complete implementation plan detailed enough for independent implementation.
        """
    )


def _planner_instruction(base_prompt: str, *, use_segmentation: bool) -> str:
    """Return the planner prompt aligned with the tools actually attached."""
    if not use_segmentation:
        return base_prompt
    return (
        base_prompt
        + "\n\n# RUNTIME TOOL CONTRACT OVERRIDE\n"
        + "You do not have access to any tools in this planning step. The segmented document context is already provided in the user message and is authoritative. Do not request, mention, or emit read_document_segments calls. Do not output XML-like <tool_call> text. Produce the final YAML plan immediately with these exact required keys: file_structure, implementation_components, validation_approach, environment_setup, implementation_strategy.\n"
    )


def _is_deferred_planning_output(text: str) -> bool:
    """Return True when the planner defers work instead of producing a plan."""
    lowered = (text or "").lower()
    return any(
        marker in lowered
        for marker in (
            "<tool_call",
            "read_document_segments",
            "need to gather more",
            "let me read",
            "before creating the complete plan",
        )
    )


def _assess_output_completeness(text: str) -> float:
    """
    Accurately assess the completeness of YAML-formatted implementation plans.

    Based on the actual requirements of CODE_PLANNING_PROMPT_TRADITIONAL:
    1. Check if all 5 required YAML sections are present
    2. Verify YAML structure integrity (start and end markers)
    3. Check if the last line is truncated
    4. Verify minimum reasonable length

    Returns:
        float: Completeness score (0.0-1.0), higher indicates more complete
    """
    if not text or len(text.strip()) < 500:
        return 0.0

    score = 0.0
    text_lower = text.lower()

    # 1. Check for 5 required YAML sections (weight: 0.5 - most important)
    # These are the 5 sections explicitly required by the prompt
    required_sections = [
        "file_structure:",
        "implementation_components:",
        "validation_approach:",
        "environment_setup:",
        "implementation_strategy:",
    ]

    sections_found = sum(1 for section in required_sections if section in text_lower)
    section_score = sections_found / len(required_sections)
    score += section_score * 0.5

    print(f"   📋 Required sections: {sections_found}/{len(required_sections)}")

    # 2. Check YAML structure integrity (weight: 0.2)
    has_yaml_start = any(
        marker in text
        for marker in ["```yaml", "complete_reproduction_plan:", "paper_info:"]
    )
    has_yaml_end = any(
        marker in text[-500:]
        for marker in ["```", "implementation_strategy:", "validation_approach:"]
    )

    if has_yaml_start and has_yaml_end:
        score += 0.2
    elif has_yaml_start:
        score += 0.1

    # 3. Check last line integrity (weight: 0.15)
    lines = text.strip().split("\n")
    if lines:
        last_line = lines[-1].strip()
        # YAML's last line is usually an indented content line or end marker
        if (
            last_line.endswith(("```", ".", ":", "]", "}"))
            or last_line.startswith(
                ("-", "*", " ")
            )  # YAML list items or indented content
            or (
                len(last_line) < 100 and not last_line.endswith(",")
            )  # Short line and not truncated
        ):
            score += 0.15
        else:
            # Long line without proper ending, likely truncated
            print(f"   ⚠️  Last line suspicious: '{last_line[-50:]}'")

    # 4. Check reasonable minimum length (weight: 0.15)
    # A complete 5-section plan should be at least 8000 characters
    length = len(text)
    if length >= 10000:
        score += 0.15
    elif length >= 5000:
        score += 0.10
    elif length >= 2000:
        score += 0.05

    print(f"   📏 Content length: {length} chars")

    return min(score, 1.0)


def _adjust_params_for_retry(
    params: RequestParams, retry_count: int
) -> RequestParams:
    """
    Token减少策略以适应模型context限制

    策略说明（针对总context有限的模型，例如 qwen/qwen-max 的 32768 token 限制）：
    - 第1次重试：REDUCE到retry_max_tokens（从 deepcode_config.json 读取）
    - 第2次重试：REDUCE到retry_max_tokens的80%
    - 第3次重试：REDUCE到retry_max_tokens的60%
    - 降低temperature提高稳定性和可预测性

    为什么要REDUCE而不是INCREASE？
    - 模型总 context = input + output。
    - 当遇到 "maximum context length exceeded" 错误时，说明 input + requested_output 超限。
    - INCREASING max_tokens 只会让问题更严重；正确做法是 DECREASE output tokens
      为更多 input 留出空间。
    """
    _, retry_max_tokens = get_token_limits()

    # Token减少策略 - 为input腾出更多空间
    if retry_count == 0:
        # 第一次重试：使用配置的retry_max_tokens
        new_max_tokens = retry_max_tokens
    elif retry_count == 1:
        # 第二次重试：减少到retry_max_tokens的80%
        new_max_tokens = int(retry_max_tokens * 0.9)
    else:
        # 第三次及以上：减少到retry_max_tokens的60%
        new_max_tokens = int(retry_max_tokens * 0.8)

    # Decrease temperature with each retry to get more consistent and predictable output
    new_temperature = max(params.temperature - (retry_count * 0.15), 0.05)

    print(f"🔧 Adjusting parameters for retry {retry_count + 1}:")
    print(f"   Token limit: {params.maxTokens} → {new_max_tokens}")
    print(f"   Temperature: {params.temperature:.2f} → {new_temperature:.2f}")
    print(
        "   💡 Strategy: REDUCE output tokens to fit within model's total context limit"
    )

    # return RequestParams(
    #     maxTokens=new_max_tokens,  # 注意：使用 camelCase
    #     temperature=new_temperature,
    # )
    return new_max_tokens, new_temperature


async def execute_requirement_analysis_workflow(
    user_input: str,
    analysis_mode: str,
    user_answers: Optional[Dict[str, str]] = None,
    logger=None,
    progress_callback: Optional[Callable[[int, str], None]] = None,
) -> Dict[str, Any]:
    """
    Lightweight orchestrator to run requirement-analysis-specific flows.
    """

    normalized_input = (user_input or "").strip()
    if not normalized_input:
        return {
            "status": "error",
            "error": "User requirement input cannot be empty.",
        }

    user_answers = user_answers or {}

    try:
        async with RequirementAnalysisAgent(logger=logger) as agent:
            if progress_callback:
                progress_callback(5, "🤖 Initializing requirement analysis agent...")

            if analysis_mode == "generate_questions":
                questions = await agent.generate_guiding_questions(normalized_input)
                if progress_callback:
                    progress_callback(100, "🧠 Guiding questions generated.")
                return {
                    "status": "success",
                    "result": json.dumps(questions, ensure_ascii=False),
                }

            if analysis_mode == "summarize_requirements":
                summary = await agent.summarize_detailed_requirements(
                    normalized_input, user_answers
                )
                if progress_callback:
                    progress_callback(100, "📄 Requirement document created.")
                return {"status": "success", "result": summary}

            raise ValueError(f"Unsupported analysis_mode: {analysis_mode}")

    except Exception as exc:
        message = str(exc)
        if logger:
            try:
                logger.error("Requirement analysis workflow failed: %s", message)
            except Exception:
                pass
        return {"status": "error", "error": message}


def get_default_search_server() -> str:
    """Return the default auxiliary search server name from runtime config."""
    try:
        from core.compat.runtime import get_runtime

        default_server = get_runtime().config.tools.default_search_server or "filesystem"
        print(f"🔍 Using search server: {default_server}")
        return default_server
    except Exception as e:
        print(f"⚠️ Could not read default search server from config: {e}")
        print("🔍 Falling back to default search server: filesystem")
        return "filesystem"


def get_search_server_names(
    additional_servers: Optional[List[str]] = None,
) -> List[str]:
    """
    Get server names list with fetch plus the configured auxiliary server.

    Args:
        additional_servers: Optional list of additional servers to include

    Returns:
        List[str]: List of server names including fetch and optional extra servers
    """
    default_search = get_default_search_server()
    server_names = ["fetch"]

    if default_search and default_search not in server_names:
        server_names.append(default_search)

    if additional_servers:
        # Add additional servers, avoiding duplicates
        for server in additional_servers:
            if server not in server_names:
                server_names.append(server)

    return server_names


_URL_RE = re.compile(r"https?://|www\.", re.IGNORECASE)
_CHAT_PLANNING_WEB_KEYWORDS = (
    "url",
    "link",
    "web",
    "website",
    "fetch",
    "download",
    "github",
    "gitlab",
    "arxiv",
    "链接",
    "网址",
    "网页",
    "联网",
    "在线",
    "下载",
)


def _chat_planning_needs_fetch(user_input: str) -> bool:
    """Return whether chat planning needs network-backed fetch tools."""
    text = user_input or ""
    if _URL_RE.search(text):
        return True
    lowered = text.lower()
    return any(keyword in lowered for keyword in _CHAT_PLANNING_WEB_KEYWORDS)


def get_chat_planning_server_names(user_input: str) -> List[str]:
    """Expose fetch to chat planning only when the request asks for web context."""
    return ["fetch"] if _chat_planning_needs_fetch(user_input) else []


async def acquire_input_artifact(ctx: WorkflowContext, logger) -> None:
    """Phase 2 - deterministic input acquisition.

    Routes ``ctx.input_source`` to the right MCP file-downloader tool purely
    based on ``ctx.input_kind`` (already classified by Phase 0+1 in
    :func:`workflows.environment.prepare_workflow_environment`). No LLM
    call, no JSON parsing, no fallback heuristics. On failure it raises so
    that ``execute_multi_agent_research_pipeline``'s outer ``try/except``
    reports it through the standard error path.

    Replaces the legacy ``run_research_analyzer`` (LLM-classify) +
    ``run_resource_processor`` (LLM-fallback wrapper) +
    ``orchestrate_research_analysis_agent`` (sequencer) trio. Saves about
    one LLM round-trip and 10 s of artificial sleep per task.
    """

    paper_dir = str(ctx.task_dir)
    target_pdf_name = "paper.pdf"

    logger.info(f"📋 Paper ID: {ctx.task_id}")
    logger.info(f"📂 Paper directory: {paper_dir}")
    logger.info(f"📥 Processing {ctx.input_kind}: {ctx.input_source}")

    if ctx.input_kind == "url":
        operation_result = await download_file_to(
            url=ctx.input_source,
            destination=paper_dir,
            filename=target_pdf_name,
        )
    else:
        operation_result = await move_file_to(
            source=ctx.input_source,
            destination=paper_dir,
            filename=target_pdf_name,
        )

    if "[SUCCESS]" not in operation_result or "[ERROR]" in operation_result:
        logger.error(f"❌ Input acquisition failed: {operation_result}")
        raise RuntimeError(
            f"Failed to acquire input artifact for task {ctx.task_id}: "
            f"{operation_result}"
        )

    logger.info(f"✅ Input acquired:\n{operation_result}")


async def run_code_analyzer(
    paper_dir: str, logger, use_segmentation: bool = True
) -> str:
    """
    Run code planning through a single authoritative planner.

    Segmentation may be used to prepare a compact deterministic context, but
    final plan generation is always performed by one planner agent. This keeps
    planning architecture consistent and avoids fan-out deadlocks.

    Args:
        paper_dir: Directory path containing the research paper and related resources
        logger: Logger instance for logging information
        use_segmentation: Whether to use document segmentation capabilities

    Returns:
        str: Comprehensive implementation plan generated by the planner
    """
    print(
        f"?? Code analysis mode: {'Segmented' if use_segmentation else 'Traditional'}"
    )
    print("   ?? Planner architecture: single authoritative planner")

    paper_file_path, paper_content = _load_paper_markdown_content(paper_dir, logger)
    logger.info(f"Planning source markdown: {paper_file_path}")

    segmented_context = None
    if use_segmentation:
        segmented_context = _load_document_segments_context(paper_dir)
        if segmented_context:
            logger.info("Using segmented planner context derived from document_index.json")
        else:
            logger.warning(
                "Segmented planning requested but no usable segments were found; falling back to full-document planning context"
            )
            use_segmentation = False

    prompts = get_adaptive_prompts(use_segmentation)
    code_planner_agent = Agent(
        name="CodePlannerAgent",
        instruction=_planner_instruction(
            prompts["code_planning"], use_segmentation=use_segmentation
        ),
        server_names=[],
    )

    base_max_tokens, _ = get_token_limits()
    max_iterations = 5 if use_segmentation else 2
    current_max_tokens = base_max_tokens
    current_temperature = 0.2 if use_segmentation else 0.3
    planning_mode = "segmented" if use_segmentation else "traditional"
    message = _build_planning_message(
        paper_dir=paper_dir,
        paper_content=paper_content,
        use_segmentation=use_segmentation,
        segmented_context=segmented_context,
    )

    max_retries = 3
    retry_count = 0
    best_invalid_result = ""
    best_invalid_score = -1.0
    best_invalid_validation: Dict[str, Any] | None = None
    final_planning_error: str | None = None
    request_timeout_s = _get_code_analyzer_timeout_s()
    logger.info(
        "Using single planner path "
        f"(segmentation={use_segmentation}, segmented_context={bool(segmented_context)})"
    )

    while retry_count < max_retries:
        attempt = retry_count + 1
        attempt_record: Dict[str, Any] = {
            "attempt": attempt,
            "max_retries": max_retries,
            "mode": planning_mode,
            "segmentation": use_segmentation,
            "segmented_context": bool(segmented_context),
            "max_iterations": max_iterations,
            "max_tokens": current_max_tokens,
            "temperature": current_temperature,
        }
        attempt_logged = False
        try:
            print(
                f"?? Attempting code analysis (attempt {attempt}/{max_retries})"
            )
            logger.info(
                f"Code planning attempt {attempt}/{max_retries} started "
                f"(timeout={request_timeout_s}s, segmentation={use_segmentation}, "
                f"paper_content_loaded={bool(paper_content)})"
            )
            enhanced_params = RequestParams(
                maxTokens=current_max_tokens,
                temperature=current_temperature,
                max_iterations=max_iterations,
                llm_timeout_s=request_timeout_s,
                enforce_default_max_iterations=False,
                checkpoint_callback=build_planning_checkpoint_callback(
                    paper_dir,
                    attempt=attempt,
                    mode=planning_mode,
                ),
            )
            run_result = await _generate_plan_with_single_agent(
                code_planner_agent,
                message=message,
                request_params=enhanced_params,
                timeout_s=request_timeout_s,
                logger=logger,
            )
            result = (run_result.final_content or "").strip()
            attempt_record.update(
                {
                    "status": run_result.stop_reason,
                    "result_chars": len(result),
                    "tools_used": run_result.tools_used,
                    "usage": run_result.usage,
                    "runner_error": run_result.error,
                }
            )

            if not result:
                attempt_record["error"] = "Code planning agent returned empty output"
                append_planning_attempt(paper_dir, attempt_record)
                attempt_logged = True
                raise ValueError("Code planning agent returned empty output")

            normalized_result = result.strip()
            if normalized_result.lower().startswith("error calling llm:"):
                attempt_record["error"] = normalized_result
                append_planning_attempt(paper_dir, attempt_record)
                attempt_logged = True
                raise RuntimeError(normalized_result)
            if normalized_result.lower().startswith("error:"):
                attempt_record["error"] = normalized_result
                append_planning_attempt(paper_dir, attempt_record)
                attempt_logged = True
                raise RuntimeError(normalized_result)
            if run_result.stop_reason == "max_iterations":
                attempt_record["error"] = (
                    f"planner reached max_iterations={max_iterations}"
                )
                append_planning_attempt(paper_dir, attempt_record)
                attempt_logged = True
                raise RuntimeError(attempt_record["error"])

            print(f"?? Code analysis result:\n{result}")

            completeness_score = _assess_output_completeness(result)
            plan_validation = validate_plan_text(result)
            print(f"?? Output completeness score: {completeness_score:.2f}/1.0")
            attempt_record.update(
                {
                    "completeness_score": completeness_score,
                    "plan_validation": plan_validation,
                }
            )

            if completeness_score >= 0.8 and plan_validation.get("valid", False):
                print(
                    f"??Code analysis completed successfully (length: {len(result)} chars)"
                )
                attempt_record["status"] = "success"
                append_planning_attempt(paper_dir, attempt_record)
                write_planning_meta(
                    paper_dir,
                    {
                        "status": "success",
                        "source": "generated",
                        "mode": planning_mode,
                        "attempts": attempt,
                        "stop_reason": run_result.stop_reason,
                        "completeness_score": completeness_score,
                        "plan_validation": plan_validation,
                        "tools_used": run_result.tools_used,
                        "usage": run_result.usage,
                        "plan_chars": len(result),
                    },
                )
                clear_planning_checkpoint(paper_dir)
                return result

            if _is_deferred_planning_output(result):
                logger.warning(
                    "Code planner deferred to unavailable tools; using provided segmented context for fallback plan"
                )
                best_invalid_result = (
                    segmented_context or paper_content[:6000] or result
                ).strip()
                best_invalid_score = max(best_invalid_score, completeness_score)
                best_invalid_validation = plan_validation
                retry_count = max_retries
                break

            if len(result) > 200 and completeness_score > best_invalid_score:
                best_invalid_result = result
                best_invalid_score = completeness_score
                best_invalid_validation = plan_validation

            attempt_record["status"] = "incomplete"
            append_planning_attempt(paper_dir, attempt_record)
            attempt_logged = True
            print(
                f"??? Output appears truncated (score: {completeness_score:.2f}), retrying with enhanced parameters..."
            )
            missing = plan_validation.get("missing_sections") or []
            if missing:
                logger.warning(f"Code planning missing required sections: {missing}")
            new_max_tokens, new_temperature = _adjust_params_for_retry(
                enhanced_params, retry_count
            )
            current_max_tokens = new_max_tokens
            current_temperature = new_temperature
            retry_count += 1

        except asyncio.TimeoutError:
            timeout_msg = (
                f"Code planning attempt {attempt}/{max_retries} timed out "
                f"after {request_timeout_s}s while waiting for the LLM response"
            )
            logger.error(timeout_msg)
            print(f"Timeout: {timeout_msg}")
            if not attempt_logged:
                attempt_record.update({"status": "timeout", "error": timeout_msg})
                append_planning_attempt(paper_dir, attempt_record)
            retry_count += 1
            if retry_count >= max_retries:
                final_planning_error = timeout_msg
                break
        except Exception as e:
            logger.error(
                f"Code planning attempt {attempt}/{max_retries} failed: {type(e).__name__}: {e}"
            )
            print(f"??Error in code analysis attempt {attempt}: {e}")
            if not attempt_logged:
                attempt_record.update(
                    {
                        "status": "error",
                        "error": f"{type(e).__name__}: {e}",
                    }
                )
                append_planning_attempt(paper_dir, attempt_record)
            retry_count += 1
            if retry_count >= max_retries:
                final_planning_error = f"{type(e).__name__}: {e}"
                break

    fallback_source = best_invalid_result
    if len(fallback_source.strip()) < 500:
        fallback_source = (segmented_context or paper_content[:6000] or fallback_source).strip()

    if fallback_source:
        coerced_plan = coerce_text_to_minimal_plan(fallback_source, paper_dir=paper_dir)
        coerced_validation = validate_plan_text(coerced_plan)
        if coerced_validation.get("valid", False):
            logger.warning(
                "Code planning fell back to minimal schema wrapper after "
                f"{max_retries} invalid attempts; previous validation={best_invalid_validation}; "
                f"final_error={final_planning_error}"
            )
            write_planning_meta(
                paper_dir,
                {
                    "status": "success",
                    "source": "coerced_from_freeform",
                    "mode": planning_mode,
                    "attempts": max_retries,
                    "completeness_score": best_invalid_score,
                    "plan_validation": coerced_validation,
                    "original_plan_validation": best_invalid_validation,
                    "final_error": final_planning_error,
                    "plan_chars": len(coerced_plan),
                },
            )
            clear_planning_checkpoint(paper_dir)
            return coerced_plan

    print(f"??? Returning potentially incomplete result after {max_retries} attempts")
    write_planning_meta(
        paper_dir,
        {
            "status": "error",
            "source": "generated",
            "mode": planning_mode,
            "attempts": max_retries,
            "error": final_planning_error or "exhausted retries without usable plan",
        },
    )
    raise RuntimeError(
        final_planning_error
        or f"Code planning exhausted {max_retries} attempts without producing a usable plan"
    )


async def github_repo_download(search_result: str, paper_dir: str, logger) -> str:
    """
    Download GitHub repositories based on search results.

    Args:
        search_result: Result from GitHub repository search
        paper_dir: Directory where the paper and its code will be stored
        logger: Logger instance for logging information

    Returns:
        str: Download result
    """
    github_download_agent = Agent(
        name="GithubDownloadAgent",
        instruction="Download github repo to the directory {paper_dir}/code_base".format(
            paper_dir=paper_dir
        ),
        server_names=["filesystem", "github-downloader"],
    )

    async with github_download_agent:
        print("GitHub downloader: Downloading repositories...")
        downloader = await attach_workflow_llm(
            github_download_agent,
            phase="planning",
        )

        # Set higher token output for GitHub download
        github_params = RequestParams(
            maxTokens=4096,  # Using camelCase
            temperature=0.1,
        )

        return await downloader.generate_str(
            message=search_result, request_params=github_params
        )


async def paper_reference_analyzer(paper_dir: str, logger) -> str:
    """
    Run the paper reference analysis and GitHub repository workflow.

    Args:
        analysis_result: Result from the paper analyzer
        logger: Logger instance for logging information

    Returns:
        str: Reference analysis result
    """
    reference_analysis_agent = Agent(
        name="ReferenceAnalysisAgent",
        instruction=PAPER_REFERENCE_ANALYZER_PROMPT,
        server_names=["filesystem", "fetch"],
    )
    message = f"""Analyze the research paper in directory: {paper_dir}

Please locate and analyze the markdown (.md) file containing the research paper. **Focus specifically on the References/Bibliography section** to identify and analyze the 5 most relevant references that have GitHub repositories.

Goal: Find the most valuable GitHub repositories from the paper's reference list for code implementation reference."""

    async with reference_analysis_agent:
        print("Reference analyzer: Connected to server, analyzing references...")
        analyzer = await attach_workflow_llm(
            reference_analysis_agent,
            phase="planning",
        )

        # Filter tools to only essential ones for reference analysis
        reference_params = RequestParams(
            maxTokens=4096,
            temperature=0.2,
            tool_filter={
                "filesystem": {"read_text_file", "list_directory"},
                "fetch": {"fetch"},
            },
        )

        reference_result = await analyzer.generate_str(
            message=message, request_params=reference_params
        )
        return reference_result


async def synthesize_workspace_infrastructure_agent(
    ctx: WorkflowContext, logger
) -> Dict[str, str]:
    """
    Synthesize the per-task workspace by reading the converted markdown into ``ctx``.

    All directory allocation and path resolution is owned by
    :func:`workflows.environment.prepare_workflow_environment`; this function
    only loads the markdown body (Phase 2 wrote it to ``ctx.task_dir``) and
    fills ``ctx.paper_md_path`` / ``ctx.standardized_text``. The legacy
    ``dir_info`` dict is then derived from ``ctx`` for downstream phases.
    """
    md_path = FileProcessor.find_markdown_file(str(ctx.task_dir))
    if not md_path:
        raise ValueError(
            f"No markdown file found in task directory: {ctx.task_dir}"
        )

    content = await FileProcessor.read_file_content(md_path)
    structured_content = FileProcessor.parse_markdown_sections(content)
    standardized_text = FileProcessor.standardize_output(structured_content)

    ctx.paper_md_path = Path(md_path)
    ctx.standardized_text = standardized_text
    if ctx.paper_path is None:
        ctx.paper_path = ctx.paper_md_path

    print("🏗️ Intelligent workspace infrastructure synthesized:")
    print(f"   Workspace root : {ctx.workspace_root}")
    print(f"   Task directory : {ctx.task_dir}")
    print(f"   Markdown source: {ctx.paper_md_path}")

    return ctx.to_dir_info()


async def orchestrate_reference_intelligence_agent(
    dir_info: Dict[str, str], logger, progress_callback: Optional[Callable] = None
) -> str:
    """
    Orchestrate intelligent reference analysis with automated research discovery.

    This agent autonomously processes research references and discovers
    related work using advanced AI-powered analysis algorithms.

    Args:
        dir_info: Workspace infrastructure metadata
        logger: Logger instance for intelligence tracking
        progress_callback: Progress callback function for monitoring

    Returns:
        str: Comprehensive reference intelligence analysis result
    """
    if progress_callback:
        progress_callback(50, "🧠 Orchestrating reference intelligence discovery...")

    reference_path = dir_info["reference_path"]

    # Check if reference analysis already exists
    if os.path.exists(reference_path):
        print(f"Found existing reference analysis at {reference_path}")
        with open(reference_path, "r", encoding="utf-8") as f:
            return f.read()

    # Execute reference analysis
    reference_result = await paper_reference_analyzer(dir_info["paper_dir"], logger)

    # Save reference analysis result
    with open(reference_path, "w", encoding="utf-8") as f:
        f.write(reference_result)
    print(f"Reference analysis saved to {reference_path}")

    return reference_result


async def orchestrate_document_preprocessing_agent(
    dir_info: Dict[str, str], logger
) -> Dict[str, Any]:
    """
    Orchestrate adaptive document preprocessing with intelligent segmentation control.

    This agent autonomously determines whether to use document segmentation based on
    configuration settings and document size, then applies the appropriate processing strategy.

    Args:
        dir_info: Workspace infrastructure metadata
        logger: Logger instance for preprocessing tracking

    Returns:
        dict: Document preprocessing result with segmentation metadata
    """

    try:
        print("🔍 Starting adaptive document preprocessing...")
        print(f"   Paper directory: {dir_info['paper_dir']}")

        # Step 1: Check if any markdown files exist
        md_files = []
        try:
            md_files = [
                f for f in os.listdir(dir_info["paper_dir"]) if f.endswith(".md")
            ]
        except Exception as e:
            print(f"⚠️ Error reading paper directory: {e}")

        if not md_files:
            print("ℹ️ No markdown files found - skipping document preprocessing")
            dir_info["segments_ready"] = False
            dir_info["use_segmentation"] = False
            return {
                "status": "skipped",
                "reason": "no_markdown_files",
                "paper_dir": dir_info["paper_dir"],
                "segments_ready": False,
                "use_segmentation": False,
            }

        # Step 2: Read document content to determine size
        md_path = os.path.join(dir_info["paper_dir"], md_files[0])
        try:
            # Check if file is actually a PDF by reading the first few bytes
            with open(md_path, "rb") as f:
                header = f.read(8)
                if header.startswith(b"%PDF"):
                    # If we find a PDF file where we expected markdown, try to convert it
                    print(f"⚠️ Found PDF file instead of markdown: {md_path}")
                    print("🔄 Attempting to convert PDF to markdown...")

                    # Try to convert the PDF to markdown
                    try:
                        from tools.pdf_downloader import SimplePdfConverter

                        converter = SimplePdfConverter()
                        conversion_result = converter.convert_pdf_to_markdown(md_path)

                        if conversion_result["success"]:
                            print(
                                f"✅ PDF converted to markdown: {conversion_result['output_file']}"
                            )
                            # Use the converted markdown file instead
                            md_path = conversion_result["output_file"]
                        else:
                            raise IOError(
                                f"PDF conversion failed: {conversion_result['error']}"
                            )
                    except Exception as conv_error:
                        raise IOError(
                            f"File {md_path} is a PDF file, not a text file. PDF conversion failed: {str(conv_error)}"
                        )

            with open(md_path, "r", encoding="utf-8") as f:
                document_content = f.read()
        except Exception as e:
            print(f"⚠️ Error reading document content: {e}")
            dir_info["segments_ready"] = False
            dir_info["use_segmentation"] = False
            return {
                "status": "error",
                "error_message": f"Failed to read document: {str(e)}",
                "paper_dir": dir_info["paper_dir"],
                "segments_ready": False,
                "use_segmentation": False,
            }

        # Step 3: Determine if segmentation should be used
        should_segment, reason = should_use_document_segmentation(document_content)

        print(f"📊 Segmentation decision: {should_segment}")
        print(f"   Reason: {reason}")

        # Store decision in dir_info for downstream agents
        dir_info["use_segmentation"] = should_segment

        if should_segment:
            print("🔧 Using intelligent document segmentation workflow...")

            # Prepare document segments using the segmentation agent
            segmentation_result = await prepare_document_segments(
                paper_dir=dir_info["paper_dir"], logger=logger
            )

            if segmentation_result["status"] == "success":
                print("✅ Document segmentation completed successfully!")
                print(f"   Segments directory: {segmentation_result['segments_dir']}")
                print("   🧠 Intelligent segments ready for planning agents")

                # Add segment information to dir_info for downstream agents
                dir_info["segments_dir"] = segmentation_result["segments_dir"]
                dir_info["segments_ready"] = True

                return segmentation_result

            else:
                print(
                    f"⚠️ Document segmentation failed: {segmentation_result.get('error_message', 'Unknown error')}"
                )
                print("   Falling back to traditional full-document processing...")
                dir_info["segments_ready"] = False
                dir_info["use_segmentation"] = False

                return {
                    "status": "fallback_to_traditional",
                    "original_error": segmentation_result.get(
                        "error_message", "Unknown error"
                    ),
                    "paper_dir": dir_info["paper_dir"],
                    "segments_ready": False,
                    "use_segmentation": False,
                    "fallback_reason": "segmentation_failed",
                }
        else:
            print("📖 Using traditional full-document reading workflow...")
            dir_info["segments_ready"] = False

            return {
                "status": "traditional",
                "reason": reason,
                "paper_dir": dir_info["paper_dir"],
                "segments_ready": False,
                "use_segmentation": False,
                "document_size": len(document_content),
            }

    except Exception as e:
        print(f"❌ Error during document preprocessing: {e}")
        print("   Continuing with traditional full-document processing...")

        # Ensure fallback settings
        dir_info["segments_ready"] = False
        dir_info["use_segmentation"] = False

        return {
            "status": "error",
            "paper_dir": dir_info["paper_dir"],
            "segments_ready": False,
            "use_segmentation": False,
            "error_message": str(e),
        }


async def orchestrate_code_planning_agent(
    dir_info: Dict[str, str], logger, progress_callback: Optional[Callable] = None
):
    """
    Orchestrate intelligent code planning with automated design analysis.

    This agent autonomously generates optimal code reproduction plans and implementation
    strategies using AI-driven code analysis and planning principles.

    Args:
        dir_info: Workspace infrastructure metadata
        logger: Logger instance for planning tracking
        progress_callback: Progress callback function for monitoring
    """
    if progress_callback:
        progress_callback(65, "📋 Generating implementation plan and code structure...")

    initial_plan_path = dir_info["initial_plan_path"]
    reusable, reuse_info = is_existing_plan_usable(
        initial_plan_path,
        paper_dir=dir_info["paper_dir"],
    )
    if reusable:
        print(f"Found reusable initial plan at {initial_plan_path}")
        if not reuse_info.get("meta") or reuse_info["meta"].get("status") != "success":
            write_planning_meta(
                dir_info["paper_dir"],
                {
                    "status": "success",
                    "source": "existing",
                    "initial_plan_path": initial_plan_path,
                    "plan_chars": reuse_info.get("plan_chars", 0),
                    "plan_validation": reuse_info.get("plan_validation"),
                },
            )
        return

    if os.path.exists(initial_plan_path):
        print(
            f"Existing initial plan is not reusable; regenerating ({reuse_info.get('reason', 'unknown')})"
        )
    should_generate_plan = not reusable

    # Generate or regenerate the plan after resume validation.
    if should_generate_plan:
        # Use segmentation setting from preprocessing phase
        use_segmentation = dir_info.get("use_segmentation", True)
        print(f"📊 Planning mode: {'Segmented' if use_segmentation else 'Traditional'}")

        # First, verify there's a markdown file to analyze
        import glob

        md_files = glob.glob(os.path.join(dir_info["paper_dir"], "*.md"))
        md_files = [
            f for f in md_files if not f.endswith("implement_code_summary.md")
        ]  # Exclude summary

        if not md_files:
            error_msg = f"❌ No markdown file found in {dir_info['paper_dir']}. PDF conversion may have failed."
            print(error_msg)
            print(f"   Paper directory: {dir_info['paper_dir']}")
            print(f"   Directory exists: {os.path.exists(dir_info['paper_dir'])}")
            if os.path.exists(dir_info["paper_dir"]):
                all_files = os.listdir(dir_info["paper_dir"])
                print(f"   Available files ({len(all_files)}): {all_files}")

                # Check for PDF files that might need conversion
                pdf_files = [f for f in all_files if f.endswith(".pdf")]
                if pdf_files:
                    print(f"   Found PDF files that weren't converted: {pdf_files}")
            else:
                print("   ⚠️ Directory doesn't exist!")
            raise ValueError(error_msg)

        print(f"📄 Found markdown file for analysis: {os.path.basename(md_files[0])}")

        initial_plan_result = await run_code_analyzer(
            dir_info["paper_dir"], logger, use_segmentation=use_segmentation
        )

        # Check if plan is empty or invalid
        if not initial_plan_result or len(initial_plan_result.strip()) < 100:
            error_msg = f"❌ Code planning failed: Generated plan is empty or too short ({len(initial_plan_result)} chars)"
            print(error_msg)
            raise ValueError(error_msg)

        plan_validation = validate_plan_text(initial_plan_result)
        if not plan_validation.get("valid", False):
            missing = plan_validation.get("missing_sections") or []
            raise ValueError(
                f"Code planning produced invalid plan; missing sections: {missing}"
            )

        with open(initial_plan_path, "w", encoding="utf-8") as f:
            f.write(initial_plan_result)
        current_meta = read_planning_meta(dir_info["paper_dir"]) or {}
        write_planning_meta(
            dir_info["paper_dir"],
            {
                **current_meta,
                "status": "success",
                "source": current_meta.get("source", "generated"),
                "initial_plan_path": initial_plan_path,
                "plan_saved": True,
                "plan_chars": len(initial_plan_result),
                "plan_validation": plan_validation,
            },
        )
        clear_planning_checkpoint(dir_info["paper_dir"])
        print(
            f"✅ Initial plan saved to {initial_plan_path} ({len(initial_plan_result)} chars)"
        )


async def automate_repository_acquisition_agent(
    reference_result: str,
    dir_info: Dict[str, str],
    logger,
    progress_callback: Optional[Callable] = None,
):
    """
    Automate intelligent repository acquisition with AI-guided selection.

    This agent autonomously identifies, evaluates, and acquires relevant
    repositories using intelligent filtering and automated download protocols.

    Args:
        reference_result: Reference intelligence analysis result
        dir_info: Workspace infrastructure metadata
        logger: Logger instance for acquisition tracking
        progress_callback: Progress callback function for monitoring
    """
    if progress_callback:
        progress_callback(60, "🤖 Automating intelligent repository acquisition...")

    await asyncio.sleep(5)  # Brief pause for stability

    try:
        download_result = await github_repo_download(
            reference_result, dir_info["paper_dir"], logger
        )

        # Save download results
        with open(dir_info["download_path"], "w", encoding="utf-8") as f:
            f.write(download_result)
        print(f"GitHub download results saved to {dir_info['download_path']}")

        # Verify if any repositories were actually downloaded
        code_base_path = os.path.join(dir_info["paper_dir"], "code_base")
        if os.path.exists(code_base_path):
            downloaded_repos = [
                d
                for d in os.listdir(code_base_path)
                if os.path.isdir(os.path.join(code_base_path, d))
                and not d.startswith(".")
            ]

            if downloaded_repos:
                print(
                    f"Successfully downloaded {len(downloaded_repos)} repositories: {downloaded_repos}"
                )
            else:
                print(
                    "GitHub download phase completed, but no repositories were found in the code_base directory"
                )
                print("This might indicate:")
                print(
                    "1. No relevant repositories were identified in the reference analysis"
                )
                print(
                    "2. Repository downloads failed due to access permissions or network issues"
                )
                print(
                    "3. The download agent encountered errors during the download process"
                )
        else:
            print(f"Code base directory was not created: {code_base_path}")

    except Exception as e:
        print(f"Error during GitHub repository download: {e}")
        # Still save the error information
        error_message = f"GitHub download failed: {str(e)}"
        with open(dir_info["download_path"], "w", encoding="utf-8") as f:
            f.write(error_message)
        print(f"GitHub download error saved to {dir_info['download_path']}")
        raise e  # Re-raise to be handled by the main pipeline


async def orchestrate_codebase_intelligence_agent(
    dir_info: Dict[str, str], logger, progress_callback: Optional[Callable] = None
) -> Dict:
    """
    Orchestrate intelligent codebase analysis with automated knowledge extraction.

    This agent autonomously processes and indexes codebases using advanced
    AI algorithms for intelligent relationship mapping and knowledge synthesis.

    Args:
        dir_info: Workspace infrastructure metadata
        logger: Logger instance for intelligence tracking
        progress_callback: Progress callback function for monitoring

    Returns:
        dict: Comprehensive codebase intelligence analysis result
    """
    if progress_callback:
        progress_callback(70, "🧮 Orchestrating codebase intelligence analysis...")

    print(
        "Initiating intelligent codebase analysis with AI-powered relationship mapping..."
    )
    await asyncio.sleep(2)  # Brief pause before starting indexing

    # Check if code_base directory exists and has content
    code_base_path = os.path.join(dir_info["paper_dir"], "code_base")
    if not os.path.exists(code_base_path):
        print(f"Code base directory not found: {code_base_path}")
        return {
            "status": "skipped",
            "message": "No code base directory found - skipping indexing",
        }

    # Check if there are any repositories in the code_base directory
    try:
        repo_dirs = [
            d
            for d in os.listdir(code_base_path)
            if os.path.isdir(os.path.join(code_base_path, d)) and not d.startswith(".")
        ]

        if not repo_dirs:
            print(f"No repositories found in {code_base_path}")
            print("This might be because:")
            print("1. GitHub download phase didn't complete successfully")
            print("2. No relevant repositories were identified for download")
            print("3. Repository download failed due to access issues")
            print("Continuing with code implementation without codebase indexing...")

            # Save a report about the skipped indexing
            skip_report = {
                "status": "skipped",
                "reason": "no_repositories_found",
                "message": f"No repositories found in {code_base_path}",
                "suggestions": [
                    "Check if GitHub download phase completed successfully",
                    "Verify if relevant repositories were identified in reference analysis",
                    "Check network connectivity and GitHub access permissions",
                ],
            }

            with open(dir_info["index_report_path"], "w", encoding="utf-8") as f:
                f.write(str(skip_report))
            print(f"Indexing skip report saved to {dir_info['index_report_path']}")

            return skip_report

    except Exception as e:
        print(f"Error checking code base directory: {e}")
        return {
            "status": "error",
            "message": f"Error checking code base directory: {str(e)}",
        }

    try:
        from workflows.codebase_index_workflow import run_codebase_indexing

        print(f"Found {len(repo_dirs)} repositories to index: {repo_dirs}")

        # Run codebase index workflow
        index_result = await run_codebase_indexing(
            paper_dir=dir_info["paper_dir"],
            initial_plan_path=dir_info["initial_plan_path"],
            logger=logger,
        )

        # Log indexing results
        if index_result["status"] == "success":
            print("Code indexing completed successfully!")
            print(
                f"Indexed {index_result['statistics']['total_repositories'] if index_result.get('statistics') else len(index_result['output_files'])} repositories"
            )
            print(f"Generated {len(index_result['output_files'])} index files")

            # Save indexing results to file
            with open(dir_info["index_report_path"], "w", encoding="utf-8") as f:
                f.write(str(index_result))
            print(f"Indexing report saved to {dir_info['index_report_path']}")

        elif index_result["status"] == "warning":
            print(f"Code indexing completed with warnings: {index_result['message']}")
        else:
            print(f"Code indexing failed: {index_result['message']}")

        return index_result

    except Exception as e:
        print(f"Error during codebase indexing workflow: {e}")
        print("Continuing with code implementation despite indexing failure...")

        # Save error report
        error_report = {
            "status": "error",
            "message": str(e),
            "phase": "codebase_indexing",
            "recovery_action": "continuing_with_code_implementation",
        }

        with open(dir_info["index_report_path"], "w", encoding="utf-8") as f:
            f.write(str(error_report))
        print(f"Indexing error report saved to {dir_info['index_report_path']}")

        return error_report


async def synthesize_code_implementation_agent(
    dir_info: Dict[str, str],
    logger,
    progress_callback: Optional[Callable] = None,
    enable_indexing: bool = True,
) -> Dict:
    """
    Synthesize intelligent code implementation with automated development.

    This agent autonomously generates high-quality code implementations using
    AI-powered development strategies and intelligent code synthesis algorithms.

    Args:
        dir_info: Workspace infrastructure metadata
        logger: Logger instance for implementation tracking
        progress_callback: Progress callback function for monitoring
        enable_indexing: Whether to enable code reference indexing for enhanced implementation

    Returns:
        dict: Comprehensive code implementation synthesis result
    """
    if progress_callback:
        progress_callback(85, "🔬 Synthesizing intelligent code implementation...")

    print(
        "Launching intelligent code synthesis with AI-driven implementation strategies..."
    )
    await asyncio.sleep(3)  # Brief pause before starting implementation

    try:
        # Create code implementation workflow instance based on indexing preference
        if enable_indexing:
            print(
                "🔍 Using enhanced code implementation workflow with reference indexing..."
            )
            code_workflow = CodeImplementationWorkflowWithIndex()
        else:
            print("⚡ Using standard code implementation workflow (fast mode)...")
            code_workflow = CodeImplementationWorkflow()

        # Check if initial plan file exists
        if os.path.exists(dir_info["initial_plan_path"]):
            print(f"Using initial plan from {dir_info['initial_plan_path']}")

            # Run code implementation workflow with pure code mode
            # Pass segmentation information to help with token management
            use_segmentation = dir_info.get("use_segmentation", False)
            print(f"🔧 Code implementation using segmentation: {use_segmentation}")

            implementation_result = await code_workflow.run_workflow(
                plan_file_path=dir_info["initial_plan_path"],
                target_directory=dir_info["paper_dir"],
                pure_code_mode=True,  # Focus on code implementation, skip testing
                progress_callback=progress_callback,
            )

            # Log implementation results truthfully — distinguish full
            # completion from an early termination (loop_detector abort,
            # max_iterations, max_time, etc.). The legacy code unconditionally
            # printed "completed successfully!" which masked partial output.
            inner_status = implementation_result.get("inner_status") or implementation_result.get("status")
            files_done = implementation_result.get("files_completed", 0)
            unimpl = implementation_result.get("unimplemented_files", []) or []
            if inner_status == "completed":
                print("✅ Code implementation completed successfully (all planned files written)!")
                print(f"   Code directory : {implementation_result['code_directory']}")
                print(f"   Files written  : {files_done}")
            else:
                reason = implementation_result.get("abort_reason") or "unknown"
                print(
                    f"⚠️  Code implementation finished EARLY — status={inner_status}, "
                    f"files_written={files_done}, unimplemented={len(unimpl)}"
                )
                print(f"   Reason         : {reason}")
                print(f"   Code directory : {implementation_result['code_directory']}")
                if unimpl:
                    sample = ", ".join(unimpl[:5])
                    if len(unimpl) > 5:
                        sample += f", ... (+{len(unimpl) - 5} more)"
                    print(f"   Missing files  : {sample}")

            with open(
                dir_info["implementation_report_path"], "w", encoding="utf-8"
            ) as f:
                f.write(str(implementation_result))
            print(
                f"Implementation report saved to {dir_info['implementation_report_path']}"
            )

            return implementation_result
        else:
            print(
                f"Initial plan file not found at {dir_info['initial_plan_path']}, skipping code implementation"
            )
            return {
                "status": "warning",
                "message": "Initial plan not found - code implementation skipped",
            }

    except Exception as e:
        print(f"Error during code implementation workflow: {e}")
        return {"status": "error", "message": str(e)}


async def run_chat_planning_agent(user_input: str, logger) -> str:
    """
    Run the chat-based planning agent for user-provided coding requirements.

    This agent transforms user's coding description into a comprehensive implementation plan
    that can be directly used for code generation. It handles both academic and engineering
    requirements with intelligent context adaptation.

    Args:
        user_input: User's coding requirements and description
        logger: Logger instance for logging information

    Returns:
        str: Comprehensive implementation plan in YAML format
    """
    try:
        print("💬 Starting chat-based planning agent...")
        print(f"Input length: {len(user_input) if user_input else 0}")
        print(f"Input preview: {user_input[:200] if user_input else 'None'}...")

        if not user_input or user_input.strip() == "":
            raise ValueError(
                "Empty or None user_input provided to run_chat_planning_agent"
            )

        # Create the chat planning agent
        chat_planning_agent = Agent(
            name="ChatPlanningAgent",
            instruction=CHAT_AGENT_PLANNING_PROMPT,
            server_names=get_chat_planning_server_names(user_input),
        )

        async with chat_planning_agent:
            print("chat_planning: Connected to server, calling list_tools...")
            try:
                tools = await chat_planning_agent.list_tools()
                print(
                    "Tools available:",
                    tools.model_dump() if hasattr(tools, "model_dump") else str(tools),
                )
            except Exception as e:
                print(f"Failed to list tools: {e}")

            try:
                planner = await attach_workflow_llm(
                    chat_planning_agent,
                    phase="planning",
                )
                print("✅ LLM attached successfully")
            except Exception as e:
                print(f"❌ Failed to attach LLM: {e}")
                raise

            # Set higher token output for comprehensive planning
            planning_params = RequestParams(
                maxTokens=8192,  # Using camelCase - Higher token limit for detailed plans
                temperature=0.2,  # Lower temperature for more structured output
            )

            print(
                f"🔄 Making LLM request with params: maxTokens={planning_params.maxTokens}, temperature={planning_params.temperature}"
            )

            # Format the input message for the agent
            formatted_message = f"""Please analyze the following coding requirements and generate a comprehensive implementation plan:

User Requirements:
{user_input}

Please provide a detailed implementation plan that covers all aspects needed for successful development."""

            try:
                raw_result = await planner.generate_str(
                    message=formatted_message, request_params=planning_params
                )

                print("✅ Planning request completed")
                print(f"Raw result type: {type(raw_result)}")
                print(f"Raw result length: {len(raw_result) if raw_result else 0}")

                if not raw_result:
                    print("❌ CRITICAL: raw_result is empty or None!")
                    raise ValueError("Chat planning agent returned empty result")

            except Exception as e:
                print(f"❌ Planning generation failed: {e}")
                print(f"Exception type: {type(e)}")
                raise

            # NOTE: Per-call structured logging is handled centrally by
            # core.observability (see core/providers/openai_compat.py and
            # anthropic.py — every chat() call writes a record to the
            # task-scoped llm.jsonl).

            if not raw_result or raw_result.strip() == "":
                print("❌ CRITICAL: Planning result is empty!")
                raise ValueError("Chat planning agent produced empty output")

            print("🎯 Chat planning completed successfully")
            print(f"Planning result preview: {raw_result[:500]}...")

            return raw_result

    except Exception as e:
        print(f"❌ run_chat_planning_agent failed: {e}")
        print(f"Exception details: {type(e).__name__}: {str(e)}")
        raise


async def execute_multi_agent_research_pipeline(
    input_source: str,
    logger,
    progress_callback: Optional[Callable] = None,
    enable_indexing: bool = True,
    task_id: Optional[str] = None,
    plan_review_callback: Optional[PlanReviewCallback] = None,
) -> str:
    """
    Execute the complete intelligent multi-agent research orchestration pipeline.

    This is the main AI orchestration engine that coordinates autonomous research workflow agents:
    - Local workspace automation for seamless environment management
    - Intelligent research analysis with automated content processing
    - AI-driven code architecture synthesis and design automation
    - Reference intelligence discovery with automated knowledge extraction (optional)
    - Codebase intelligence orchestration with automated relationship analysis (optional)
    - Intelligent code implementation synthesis with AI-powered development

    Args:
        input_source: Research input source (file path, URL, or preprocessed analysis)
        logger: Logger instance for comprehensive workflow intelligence tracking
        progress_callback: Progress callback function for real-time monitoring
        enable_indexing: Whether to enable advanced intelligence analysis (default: True)

    Returns:
        str: The comprehensive pipeline execution result with status and outcomes
    """
    # Track the final status so the finally block can persist it back to the
    # session store regardless of which branch (return / except / cancel) we
    # take. Also remember the resolved task_id, since `ctx` may not exist if
    # prepare_workflow_environment raises before assignment.
    _resolved_task_id: Optional[str] = task_id
    _final_status: str = "running"

    try:
        # Phase 0+1: Unified workspace + input housekeeping (no LLM)
        print("🚀 Initializing intelligent multi-agent research orchestration system")
        if enable_indexing:
            print("🧠 Advanced intelligence analysis enabled - comprehensive workflow")
        else:
            print("⚡ Optimized mode - advanced intelligence analysis disabled")

        ctx = await prepare_workflow_environment(
            raw_input=input_source,
            enable_indexing=enable_indexing,
            task_kind="paper2code",
            task_id=task_id,
            progress_cb=progress_callback,
            logger=logger,
        )

        # Bind the resolved task_id into the async context so every loguru
        # call below routes to <task_dir>/logs/system.jsonl. Safe even when
        # an outer caller (UI WorkflowService) has already bound: ContextVar
        # tokens stack and we restore on exit.
        from core.observability import bind_task as _bind_task
        from core.observability import pop_task as _pop_task

        _task_token = _bind_task(ctx.task_id)
        _resolved_task_id = ctx.task_id

        print(f"📁 Workspace : {ctx.workspace_root}")
        print(f"📂 Task dir  : {ctx.task_dir}")
        print(f"🔖 Task ID   : {ctx.task_id}")

        # Phase 2: Input Acquisition (25%)
        if progress_callback:
            progress_callback(25, "📥 Acquiring input artifact...")
        print("📊 Progress: 25% - Input Acquisition")

        if ctx.skip_research_analysis:
            print(f"✅ Resume mode: reusing existing task directory {ctx.task_dir}")
        else:
            await acquire_input_artifact(ctx, logger)

        # Phase 3: Workspace Infrastructure Synthesis (40%)
        if progress_callback:
            progress_callback(
                40, "🏗️ Synthesizing intelligent workspace infrastructure..."
            )
        print("📊 Progress: 40% - Workspace Setup")

        dir_info = await synthesize_workspace_infrastructure_agent(ctx, logger)

        # Phase 4: Document Segmentation and Preprocessing (50%)
        if progress_callback:
            progress_callback(50, "📄 Processing and segmenting document content...")
        print("📊 Progress: 50% - Document Preprocessing")

        segmentation_result = await orchestrate_document_preprocessing_agent(
            dir_info, logger
        )

        # Handle segmentation result. Each known status has its own message;
        # the catch-all fallback now reports the actual status + every key in
        # the result dict so future surprises don't silently log "Unknown".
        seg_status = segmentation_result.get("status", "missing")
        if seg_status == "success":
            print("✅ Document preprocessing completed successfully!")
            print(
                f"   📊 Using segmentation: {dir_info.get('use_segmentation', False)}"
            )
            if dir_info.get("segments_ready", False):
                print(
                    f"   📁 Segments directory: {segmentation_result.get('segments_dir', 'N/A')}"
                )
        elif seg_status == "traditional":
            print(
                "📖 Document preprocessing: using traditional full-document workflow"
            )
            print(f"   Reason: {segmentation_result.get('reason', 'n/a')}")
            print(
                f"   Document size: {segmentation_result.get('document_size', 0)} chars"
            )
        elif seg_status == "skipped":
            print(
                f"ℹ️ Document preprocessing skipped — {segmentation_result.get('reason', 'n/a')}"
            )
        elif seg_status == "fallback_to_traditional":
            print("⚠️ Document segmentation failed, falling back to traditional processing")
            print(
                f"   Original error: {segmentation_result.get('original_error', 'n/a')}"
            )
            print(
                f"   Fallback reason: {segmentation_result.get('fallback_reason', 'n/a')}"
            )
        elif seg_status == "error":
            print(
                f"⚠️ Document preprocessing failed: {segmentation_result.get('error_message', 'no error_message provided')}"
            )
        else:
            # Unknown status — dump the entire result so we can diagnose later.
            print(
                f"⚠️ Document preprocessing returned unrecognised status='{seg_status}'."
            )
            print(f"   Full result: {segmentation_result}")

        # Phase 5: Code Planning Orchestration (65%)
        if progress_callback:
            progress_callback(
                65, "📋 Generating implementation plan and code structure..."
            )
        print("📊 Progress: 65% - Code Planning")

        await orchestrate_code_planning_agent(dir_info, logger, progress_callback)
        if not os.path.exists(dir_info["initial_plan_path"]):
            raise RuntimeError(
                "Code planning did not produce initial_plan.txt; aborting the pipeline before any subsequent phase"
            )

        if plan_review_callback:
            if progress_callback:
                progress_callback(66, "Reviewing implementation plan...")
            print("Progress: 66% - Plan Review")
            review_result = await run_plan_review_gate(
                initial_plan_path=dir_info["initial_plan_path"],
                paper_dir=dir_info["paper_dir"],
                callback=plan_review_callback,
                logger=logger,
            )
            print(f"Plan review completed: {review_result.get('status')}")

        # Phase 6: Reference Intelligence (only when indexing is enabled) (70%)
        if progress_callback:
            progress_callback(70, "🔍 Analyzing references and related work...")
        print("📊 Progress: 70% - Reference Analysis")

        if enable_indexing:
            reference_result = await orchestrate_reference_intelligence_agent(
                dir_info, logger, progress_callback
            )
        else:
            print("🔶 Skipping reference intelligence analysis (fast mode enabled)")
            # Create empty reference analysis result to maintain file structure consistency
            reference_result = "Reference intelligence analysis skipped - fast mode enabled for optimized processing"
            with open(dir_info["reference_path"], "w", encoding="utf-8") as f:
                f.write(reference_result)

        # Phase 7: Repository Acquisition Automation (optional) (75%)
        if progress_callback:
            progress_callback(75, "📦 Acquiring related repositories and codebases...")
        print("📊 Progress: 75% - Repository Acquisition")

        if enable_indexing:
            await automate_repository_acquisition_agent(
                reference_result, dir_info, logger, progress_callback
            )
        else:
            print("🔶 Skipping automated repository acquisition (fast mode enabled)")
            # Create empty download result file to maintain file structure consistency
            with open(dir_info["download_path"], "w", encoding="utf-8") as f:
                f.write(
                    "Automated repository acquisition skipped - fast mode enabled for optimized processing"
                )

        # Phase 8: Codebase Intelligence Orchestration (optional) (80%)
        if progress_callback:
            progress_callback(80, "🧠 Analyzing codebase intelligence and indexing...")
        print("📊 Progress: 80% - Codebase Intelligence")

        # Diagnostic stop-probe: lets developers halt the pipeline right at the
        # phase 8 entry to inspect intermediate state. Inactive unless the
        # environment variable is explicitly set to "8". Safe to leave in place.
        if os.getenv("DEEPCODE_STOP_AT_PHASE") == "8":
            print("🛑 [STOP_AT_PHASE_8] halt requested via DEEPCODE_STOP_AT_PHASE")
            raise RuntimeError(
                "[STOP_AT_PHASE_8] Pipeline halted at phase 8 entry as requested "
                "(DEEPCODE_STOP_AT_PHASE=8). Phase 1-7 outputs are preserved in the task dir."
            )

        if enable_indexing:
            index_result = await orchestrate_codebase_intelligence_agent(
                dir_info, logger, progress_callback
            )
        else:
            print("🔶 Skipping codebase intelligence orchestration (fast mode enabled)")
            # Create a skipped indexing result
            index_result = {
                "status": "skipped",
                "reason": "fast_mode_enabled",
                "message": "Codebase intelligence orchestration skipped for optimized processing",
            }
            with open(dir_info["index_report_path"], "w", encoding="utf-8") as f:
                f.write(str(index_result))

        # Phase 9: Code Implementation Synthesis (85%)
        if progress_callback:
            progress_callback(
                85, "💻 Implementing code based on analysis and planning..."
            )
        print("📊 Progress: 85% - Code Implementation")

        implementation_result = await synthesize_code_implementation_agent(
            dir_info, logger, progress_callback, enable_indexing
        )

        # Phase 10: Finalization (100%)
        if progress_callback:
            progress_callback(100, "🎉 Finalizing results and generating summary...")
        print("📊 Progress: 100% - Finalization")

        # Final Status Report
        if enable_indexing:
            pipeline_summary = (
                f"Multi-agent research pipeline completed for {dir_info['paper_dir']}"
            )
        else:
            pipeline_summary = f"Multi-agent research pipeline completed (fast mode) for {dir_info['paper_dir']}"

        # Add indexing status to summary
        if not enable_indexing:
            pipeline_summary += (
                "\n⚡ Fast mode: GitHub download and codebase indexing skipped"
            )
        elif index_result["status"] == "skipped":
            pipeline_summary += f"\n🔶 Codebase indexing: {index_result['message']}"
        elif index_result["status"] == "error":
            pipeline_summary += (
                f"\n❌ Codebase indexing failed: {index_result['message']}"
            )
        elif index_result["status"] == "success":
            pipeline_summary += "\n✅ Codebase indexing completed successfully"

        # Add implementation status to summary — distinguish "all done" from
        # "stopped early but partial output exists".
        impl_status = implementation_result["status"]
        impl_inner = implementation_result.get("inner_status", impl_status)
        implementation_metadata = {
            "status": impl_status,
            "inner_status": impl_inner,
            "abort_reason": implementation_result.get("abort_reason"),
            "files_completed": implementation_result.get("files_completed", 0),
            "total_files": implementation_result.get("total_files", 0),
            "unimplemented_files": implementation_result.get(
                "unimplemented_files", []
            )
            or [],
            "code_directory": implementation_result.get("code_directory"),
        }
        if impl_inner == "completed":
            pipeline_summary += "\n🎉 Code implementation completed successfully!"
            pipeline_summary += (
                f"\n📁 Code generated in: {implementation_result['code_directory']}"
            )
            pipeline_status = "completed"
        elif impl_status == "incomplete":
            files_done = implementation_result.get("files_completed", 0)
            unimpl = implementation_result.get("unimplemented_files", []) or []
            pipeline_summary += (
                f"\n⚠️ Code implementation finished EARLY — wrote {files_done} files, "
                f"{len(unimpl)} unimplemented (status={impl_inner}, "
                f"reason={implementation_result.get('abort_reason', 'unknown')})"
            )
            pipeline_summary += (
                f"\n📁 Partial code in: {implementation_result['code_directory']}"
            )
            pipeline_status = "incomplete"
        elif impl_status == "warning":
            pipeline_summary += (
                f"\n⚠️ Code implementation: {implementation_result.get('message', 'see logs')}"
            )
            pipeline_status = "completed_with_warnings"
        else:
            pipeline_summary += (
                f"\n❌ Code implementation failed: {implementation_result.get('message', 'see logs')}"
            )
            pipeline_status = "error"
        _final_status = pipeline_status
        return {
            "status": pipeline_status,
            "summary": pipeline_summary,
            "implementation": implementation_metadata,
            "paper_dir": dir_info["paper_dir"],
        }

    except PlanReviewCancelled:
        _final_status = "cancelled"
        raise
    except Exception as e:
        _final_status = "error"
        error_msg = f"Error in execute_multi_agent_research_pipeline: {e}"
        print(f"❌ {error_msg}")
        print(f"   Error type: {type(e).__name__}")
        print(f"   Error details: {str(e)}")

        # Display error in UI if progress callback available
        if progress_callback:
            progress_callback(0, "Pipeline failed", error_msg)

        # Ensure all resources are cleaned up on error
        import gc

        gc.collect()
        raise e
    finally:
        # Persist final task status into the session store so the on-disk
        # tasks.jsonl reflects reality (CLI/Backend share this codepath, so
        # both surfaces benefit). Best-effort — never let it mask the real
        # error.
        if _resolved_task_id and _final_status != "running":
            try:
                from core.observability import current_session_id as _cur_sid
                from core.sessions import get_default_store as _get_session_store

                _sid = _cur_sid()
                if _sid:
                    _metadata = None
                    if "implementation_metadata" in locals():
                        _metadata = {"implementation": implementation_metadata}
                    _get_session_store().update_task_status(
                        _sid, _resolved_task_id, _final_status, metadata=_metadata
                    )
            except Exception:
                pass

        # Always release the task contextvar so subsequent runs in the same
        # process (CLI loop, background job pool) start with a clean slate.
        # The token may not exist if prepare_workflow_environment failed.
        try:
            _pop_task(_task_token)  # type: ignore[name-defined]
        except (NameError, ValueError):
            pass


# Backward compatibility alias (deprecated)
async def paper_code_preparation(
    input_source: str, logger, progress_callback: Optional[Callable] = None
) -> str:
    """
    Deprecated: Use execute_multi_agent_research_pipeline instead.

    Args:
        input_source: Input source
        logger: Logger instance
        progress_callback: Progress callback function

    Returns:
        str: Pipeline result
    """
    print(
        "paper_code_preparation is deprecated. Use execute_multi_agent_research_pipeline instead."
    )
    return await execute_multi_agent_research_pipeline(
        input_source, logger, progress_callback
    )


async def execute_chat_based_planning_pipeline(
    user_input: str,
    logger,
    progress_callback: Optional[Callable] = None,
    enable_indexing: bool = True,
    task_id: Optional[str] = None,
    plan_review_callback: Optional[PlanReviewCallback] = None,
) -> str:
    """
    Execute the chat-based planning and implementation pipeline.

    This pipeline is designed for users who provide coding requirements directly through chat,
    bypassing the traditional paper analysis phases (Phase 0-7) and jumping directly to
    planning and code implementation.

    Pipeline Flow:
    - Chat Planning: Transform user input into implementation plan
    - Workspace Setup: Create necessary directory structure
    - Code Implementation: Generate code based on the plan

    Args:
        user_input: User's coding requirements and description
        logger: Logger instance for comprehensive workflow tracking
        progress_callback: Progress callback function for real-time monitoring
        enable_indexing: Whether to enable code reference indexing for enhanced implementation

    Returns:
        str: The pipeline execution result with status and outcomes
    """
    try:
        print("🚀 Initializing chat-based planning and implementation pipeline")
        print("💬 Chat mode: Direct user requirements to code implementation")

        # Phase 0: Workspace Setup
        if progress_callback:
            progress_callback(5, "🔄 Setting up workspace for file processing...")

        # Setup local workspace directory
        workspace_dir = os.path.join(os.getcwd(), "deepcode_lab")
        os.makedirs(workspace_dir, exist_ok=True)

        print("📁 Working environment: local")
        print(f"📂 Workspace directory: {workspace_dir}")
        print("✅ Workspace status: ready")

        # Phase 1: Chat-Based Planning
        if progress_callback:
            progress_callback(
                30,
                "💬 Generating comprehensive implementation plan from user requirements...",
            )

        print("🧠 Running chat-based planning agent...")
        planning_result = await run_chat_planning_agent(user_input, logger)

        # Phase 2: Workspace Infrastructure Synthesis
        if progress_callback:
            progress_callback(
                50, "🏗️ Synthesizing intelligent workspace infrastructure..."
            )

        # Create workspace directory structure for chat mode using the same
        # ``tasks/<prefix>_<uuid>/`` naming as paper2code, but with the
        # ``chat_`` prefix so the directory's modality is obvious at a glance.
        import time
        import uuid as _uuid

        from workflows.workflow_context import TASKS_DIRNAME, TASK_KIND_PREFIX

        timestamp = str(int(time.time()))
        chat_id = task_id or _uuid.uuid4().hex[:8]
        prefix = TASK_KIND_PREFIX["chat2code"]  # "chat"
        task_dirname = f"{prefix}_{chat_id}"

        chat_paper_dir = os.path.join(workspace_dir, TASKS_DIRNAME, task_dirname)
        os.makedirs(chat_paper_dir, exist_ok=True)

        # Use the same ``paper.md`` filename the paper2code flow standardised
        # on, so downstream dir_info / Phase 4-10 code can stay unaware of
        # which modality produced the markdown.
        markdown_content = f"""# User Coding Requirements

## Project Description
This is a coding project generated from user requirements via chat interface.

## User Requirements
{user_input}

## Generated Implementation Plan
The following implementation plan was generated by the AI chat planning agent:

```yaml
{planning_result}
```

## Project Metadata
- **Input Type**: Chat Input
- **Generation Method**: AI Chat Planning Agent
- **Timestamp**: {timestamp}
- **Task ID**: {chat_id}
"""

        markdown_file_path = os.path.join(chat_paper_dir, "paper.md")
        with open(markdown_file_path, "w", encoding="utf-8") as f:
            f.write(markdown_content)

        print(f"💾 Created chat project workspace: {chat_paper_dir}")
        print(f"📄 Saved requirements to: {markdown_file_path}")

        # Build a synthetic WorkflowContext so the chat pipeline can reuse
        # the same Phase 3 path-derivation logic the file pipeline uses.
        chat_ctx = WorkflowContext(
            task_id=chat_id,
            input_source=markdown_file_path,
            input_kind="md",
            workspace_root=Path(workspace_dir).resolve(),
            task_dir=Path(chat_paper_dir).resolve(),
            enable_indexing=enable_indexing,
            task_kind="chat2code",
            paper_path=Path(markdown_file_path),
            paper_md_path=Path(markdown_file_path),
        )
        dir_info = await synthesize_workspace_infrastructure_agent(chat_ctx, logger)
        await asyncio.sleep(10)  # Brief pause for file system operations

        # Phase 3: Save Planning Result
        if progress_callback:
            progress_callback(70, "📝 Saving implementation plan...")

        # Save the planning result to the initial_plan.txt file (same location as Phase 4 in original pipeline)
        initial_plan_path = dir_info["initial_plan_path"]
        with open(initial_plan_path, "w", encoding="utf-8") as f:
            f.write(planning_result)
        print(f"💾 Implementation plan saved to {initial_plan_path}")

        if plan_review_callback:
            if progress_callback:
                progress_callback(75, "Reviewing implementation plan...")
            print("Progress: 75% - Plan Review")
            review_result = await run_plan_review_gate(
                initial_plan_path=initial_plan_path,
                paper_dir=dir_info["paper_dir"],
                callback=plan_review_callback,
                logger=logger,
            )
            print(f"Plan review completed: {review_result.get('status')}")

        # Phase 4: Code Implementation Synthesis (same as Phase 8 in original pipeline)
        if progress_callback:
            progress_callback(85, "🔬 Synthesizing intelligent code implementation...")

        implementation_result = await synthesize_code_implementation_agent(
            dir_info, logger, progress_callback, enable_indexing
        )

        # Final Status Report
        pipeline_summary = f"Chat-based planning and implementation pipeline completed for {dir_info['paper_dir']}"

        # Add implementation status to summary — distinguish full completion
        # from an early termination (loop_detector abort, max_iterations, etc.).
        impl_status = implementation_result["status"]
        impl_inner = implementation_result.get("inner_status", impl_status)
        if impl_inner == "completed":
            pipeline_summary += "\n🎉 Code implementation completed successfully!"
            pipeline_summary += (
                f"\n📁 Code generated in: {implementation_result['code_directory']}"
            )
            pipeline_summary += (
                "\n💬 Generated from user requirements via chat interface"
            )
        elif impl_status == "incomplete":
            files_done = implementation_result.get("files_completed", 0)
            unimpl = implementation_result.get("unimplemented_files", []) or []
            pipeline_summary += (
                f"\n⚠️ Code implementation finished EARLY — wrote {files_done} files, "
                f"{len(unimpl)} unimplemented (status={impl_inner}, "
                f"reason={implementation_result.get('abort_reason', 'unknown')})"
            )
            pipeline_summary += (
                f"\n📁 Partial code in: {implementation_result['code_directory']}"
            )
        elif impl_status == "warning":
            pipeline_summary += (
                f"\n⚠️ Code implementation: {implementation_result.get('message', 'see logs')}"
            )
        else:
            pipeline_summary += (
                f"\n❌ Code implementation failed: {implementation_result.get('message', 'see logs')}"
            )
        return pipeline_summary

    except PlanReviewCancelled:
        raise
    except Exception as e:
        print(f"Error in execute_chat_based_planning_pipeline: {e}")
        raise e
