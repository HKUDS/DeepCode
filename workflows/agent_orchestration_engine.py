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
import yaml
from typing import Any, Callable, Dict, List, Optional, Tuple

# MCP Agent imports
from mcp_agent.agents.agent import Agent
from mcp_agent.workflows.llm.augmented_llm import RequestParams
from mcp_agent.workflows.parallel.parallel_llm import ParallelLLM

# Local imports
from prompts.code_prompts import (
    PAPER_INPUT_ANALYZER_PROMPT,
    PAPER_DOWNLOADER_PROMPT,
    PAPER_REFERENCE_ANALYZER_PROMPT,
    CHAT_AGENT_PLANNING_PROMPT,
)
from utils.file_processor import FileProcessor
from workflows.code_implementation_workflow import CodeImplementationWorkflow
from workflows.code_implementation_workflow_index import (
    CodeImplementationWorkflowWithIndex,
)
from utils.llm_utils import (
    get_preferred_llm_class,
    should_use_document_segmentation,
    get_adaptive_agent_config,
    get_adaptive_prompts,
)
from workflows.agents.document_segmentation_agent import prepare_document_segments

# Environment configuration
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"  # Prevent .pyc file generation


def _assess_output_completeness(text: str) -> float:
    """
    精准评估YAML格式实现计划的完整性

    基于CODE_PLANNING_PROMPT_TRADITIONAL的实际要求：
    1. 检查5个必需的YAML sections是否都存在
    2. 验证YAML结构的完整性（开始和结束标记）
    3. 检查最后一行是否被截断
    4. 验证最小合理长度

    Returns:
        float: 完整性分数 (0.0-1.0)，越高表示越完整
    """
    if not text or len(text.strip()) < 500:
        return 0.0

    score = 0.0
    text_lower = text.lower()

    # 1. 检查5个必需的YAML sections (权重: 0.5 - 最重要)
    # 这是prompt明确要求的5个sections
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

    # 2. 检查YAML结构完整性 (权重: 0.2)
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

    # 3. 检查最后一行完整性 (权重: 0.15)
    lines = text.strip().split("\n")
    if lines:
        last_line = lines[-1].strip()
        # YAML的最后一行通常是缩进的内容行或结束标记
        if (
            last_line.endswith(("```", ".", ":", "]", "}"))
            or last_line.startswith(("-", "*", " "))  # YAML列表项或缩进内容
            or (
                len(last_line) < 100 and not last_line.endswith(",")
            )  # 短行且不是被截断的
        ):
            score += 0.15
        else:
            # 长行且没有合适的结尾，很可能被截断
            print(f"   ⚠️  Last line suspicious: '{last_line[-50:]}'")

    # 4. 检查合理的最小长度 (权重: 0.15)
    # 一个完整的5-section计划应该至少8000字符
    length = len(text)
    if length >= 10000:
        score += 0.15
    elif length >= 5000:
        score += 0.10
    elif length >= 2000:
        score += 0.05

    print(f"   📏 Content length: {length} chars")

    return min(score, 1.0)


def _adjust_params_for_retry(params: RequestParams, retry_count: int) -> RequestParams:
    """
    激进的token增长策略以确保完整输出

    策略说明：
    - 第1次重试：大幅增加到40000 tokens（确保有足够空间输出完整YAML）
    - 第2次重试：进一步增加到60000 tokens（处理极端情况）
    - 降低temperature提高稳定性和可预测性

    为什么需要这么多tokens？
    - ParallelLLM的fan_out agents会生成长篇分析结果（各5000+ tokens）
    - fan_in agent接收这些结果作为输入context
    - 需要输出包含5个详细sections的完整YAML（10000+ tokens）
    - 因此需要为OUTPUT预留充足的token空间
    """
    # 激进的token增长策略
    if retry_count == 0:
        # 第一次重试：直接跳到40K，确保有足够输出空间
        new_max_tokens = 40000
    elif retry_count == 1:
        # 第二次重试：进一步增加到60K
        new_max_tokens = 60000
    else:
        # 第三次及以上：使用最大限制
        new_max_tokens = 80000

    # 随着重试次数增加，降低temperature以获得更一致、更可预测的输出
    new_temperature = max(params.temperature - (retry_count * 0.15), 0.05)

    print(f"🔧 Adjusting parameters for retry {retry_count + 1}:")
    print(f"   Token limit: {params.maxTokens} → {new_max_tokens}")
    print(f"   Temperature: {params.temperature:.2f} → {new_temperature:.2f}")
    print("   💡 Strategy: Ensure sufficient output space for complete 5-section YAML")

    return RequestParams(
        maxTokens=new_max_tokens,  # 注意：使用 camelCase
        temperature=new_temperature,
    )


def get_default_search_server(config_path: str = "mcp_agent.config.yaml"):
    """
    Get the default search server from configuration.

    Args:
        config_path: Path to the main configuration file

    Returns:
        str: The default search server name ("brave" or "bocha-mcp")
    """
    try:
        if os.path.exists(config_path):
            with open(config_path, "r", encoding="utf-8") as f:
                config = yaml.safe_load(f)

            default_server = config.get("default_search_server", "brave")
            print(f"🔍 Using search server: {default_server}")
            return default_server
        else:
            print(f"⚠️ Config file {config_path} not found, using default: brave")
            return "brave"
    except Exception as e:
        print(f"⚠️ Error reading config file {config_path}: {e}")
        print("🔍 Falling back to default search server: brave")
        return "brave"


def get_search_server_names(
    additional_servers: Optional[List[str]] = None,
) -> List[str]:
    """
    Get server names list with the configured default search server.

    Args:
        additional_servers: Optional list of additional servers to include

    Returns:
        List[str]: List of server names including the default search server
    """
    default_search = get_default_search_server()
    server_names = [default_search]

    if additional_servers:
        # Add additional servers, avoiding duplicates
        for server in additional_servers:
            if server not in server_names:
                server_names.append(server)

    return server_names


def extract_clean_json(llm_output: str) -> str:
    """
    Extract clean JSON from LLM output, removing all extra text and formatting.

    Args:
        llm_output: Raw LLM output

    Returns:
        str: Clean JSON string
    """
    try:
        # Try to parse the entire output as JSON first
        json.loads(llm_output.strip())
        return llm_output.strip()
    except json.JSONDecodeError:
        pass

    # Remove markdown code blocks
    if "```json" in llm_output:
        pattern = r"```json\s*(.*?)\s*```"
        match = re.search(pattern, llm_output, re.DOTALL)
        if match:
            json_text = match.group(1).strip()
            try:
                json.loads(json_text)
                return json_text
            except json.JSONDecodeError:
                pass

    # Find JSON object starting with {
    lines = llm_output.split("\n")
    json_lines = []
    in_json = False
    brace_count = 0

    for line in lines:
        stripped = line.strip()
        if not in_json and stripped.startswith("{"):
            in_json = True
            json_lines = [line]
            brace_count = stripped.count("{") - stripped.count("}")
        elif in_json:
            json_lines.append(line)
            brace_count += stripped.count("{") - stripped.count("}")
            if brace_count == 0:
                break

    if json_lines:
        json_text = "\n".join(json_lines).strip()
        try:
            json.loads(json_text)
            return json_text
        except json.JSONDecodeError:
            pass

    # Last attempt: use regex to find JSON
    pattern = r"\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}"
    matches = re.findall(pattern, llm_output, re.DOTALL)
    for match in matches:
        try:
            json.loads(match)
            return match
        except json.JSONDecodeError:
            continue

    # If all methods fail, return original output
    return llm_output


async def run_research_analyzer(prompt_text: str, logger) -> str:
    """
    Run the research analysis workflow using ResearchAnalyzerAgent.

    Args:
        prompt_text: Input prompt text containing research information
        logger: Logger instance for logging information

    Returns:
        str: Analysis result from the agent
    """
    try:
        # Log input information for debugging
        print("📊 Starting research analysis...")
        print(f"Input prompt length: {len(prompt_text) if prompt_text else 0}")
        print(f"Input preview: {prompt_text[:200] if prompt_text else 'None'}...")

        if not prompt_text or prompt_text.strip() == "":
            raise ValueError(
                "Empty or None prompt_text provided to run_research_analyzer"
            )

        analyzer_agent = Agent(
            name="ResearchAnalyzerAgent",
            instruction=PAPER_INPUT_ANALYZER_PROMPT,
            server_names=get_search_server_names(),
        )

        async with analyzer_agent:
            print("analyzer: Connected to server, calling list_tools...")
            try:
                tools = await analyzer_agent.list_tools()
                print(
                    "Tools available:",
                    tools.model_dump() if hasattr(tools, "model_dump") else str(tools),
                )
            except Exception as e:
                print(f"Failed to list tools: {e}")

            try:
                analyzer = await analyzer_agent.attach_llm(get_preferred_llm_class())
                print("✅ LLM attached successfully")
            except Exception as e:
                print(f"❌ Failed to attach LLM: {e}")
                raise

            # Set higher token output for research analysis
            analysis_params = RequestParams(
                maxTokens=6144,  # 使用 camelCase
                temperature=0.3,
            )

            print(
                f"🔄 Making LLM request with params: maxTokens={analysis_params.maxTokens}, temperature={analysis_params.temperature}"
            )

            try:
                raw_result = await analyzer.generate_str(
                    message=prompt_text, request_params=analysis_params
                )

                print("✅ LLM request completed")
                print(f"Raw result type: {type(raw_result)}")
                print(f"Raw result length: {len(raw_result) if raw_result else 0}")

                if not raw_result:
                    print("❌ CRITICAL: raw_result is empty or None!")
                    print("This could indicate:")
                    print("1. LLM API call failed silently")
                    print("2. API rate limiting or quota exceeded")
                    print("3. Network connectivity issues")
                    print("4. MCP server communication problems")
                    raise ValueError("LLM returned empty result")

            except Exception as e:
                print(f"❌ LLM generation failed: {e}")
                print(f"Exception type: {type(e)}")
                raise

            # Clean LLM output to ensure only pure JSON is returned
            try:
                clean_result = extract_clean_json(raw_result)
                print(f"Raw LLM output: {raw_result}")
                print(f"Cleaned JSON output: {clean_result}")

                # Log to SimpleLLMLogger
                if hasattr(logger, "log_response"):
                    logger.log_response(
                        clean_result,
                        model="ResearchAnalyzer",
                        agent="ResearchAnalyzerAgent",
                    )

                if not clean_result or clean_result.strip() == "":
                    print("❌ CRITICAL: clean_result is empty after JSON extraction!")
                    print(f"Original raw_result was: {raw_result}")
                    raise ValueError("JSON extraction resulted in empty output")

                return clean_result

            except Exception as e:
                print(f"❌ JSON extraction failed: {e}")
                print(f"Raw result was: {raw_result}")
                raise

    except Exception as e:
        print(f"❌ run_research_analyzer failed: {e}")
        print(f"Exception details: {type(e).__name__}: {str(e)}")
        raise


async def run_resource_processor(analysis_result: str, logger) -> str:
    """
    Run the resource processing workflow using ResourceProcessorAgent.

    Args:
        analysis_result: Result from the research analyzer
        logger: Logger instance for logging information

    Returns:
        str: Processing result from the agent
    """
    processor_agent = Agent(
        name="ResourceProcessorAgent",
        instruction=PAPER_DOWNLOADER_PROMPT,
        server_names=["filesystem", "file-downloader"],
    )

    async with processor_agent:
        print("processor: Connected to server, calling list_tools...")
        tools = await processor_agent.list_tools()
        print(
            "Tools available:",
            tools.model_dump() if hasattr(tools, "model_dump") else str(tools),
        )

        processor = await processor_agent.attach_llm(get_preferred_llm_class())

        # Set higher token output for resource processing
        processor_params = RequestParams(
            maxTokens=4096,  # 使用 camelCase
            temperature=0.2,
        )

        return await processor.generate_str(
            message=analysis_result, request_params=processor_params
        )


async def run_code_analyzer(
    paper_dir: str, logger, use_segmentation: bool = True
) -> str:
    """
    Run the adaptive code analysis workflow using multiple agents for comprehensive code planning.

    This function orchestrates three specialized agents with adaptive configuration:
    - ConceptAnalysisAgent: Analyzes system architecture and conceptual framework
    - AlgorithmAnalysisAgent: Extracts algorithms, formulas, and technical details
    - CodePlannerAgent: Integrates outputs into a comprehensive implementation plan

    Args:
        paper_dir: Directory path containing the research paper and related resources
        logger: Logger instance for logging information
        use_segmentation: Whether to use document segmentation capabilities

    Returns:
        str: Comprehensive analysis result from the coordinated agents
    """
    # Get adaptive configuration based on segmentation usage
    search_server_names = get_search_server_names()
    agent_config = get_adaptive_agent_config(use_segmentation, search_server_names)
    prompts = get_adaptive_prompts(use_segmentation)

    print(
        f"📊 Code analysis mode: {'Segmented' if use_segmentation else 'Traditional'}"
    )
    print(f"   Agent configurations: {agent_config}")

    concept_analysis_agent = Agent(
        name="ConceptAnalysisAgent",
        instruction=prompts["concept_analysis"],
        server_names=agent_config["concept_analysis"],
    )
    algorithm_analysis_agent = Agent(
        name="AlgorithmAnalysisAgent",
        instruction=prompts["algorithm_analysis"],
        server_names=agent_config["algorithm_analysis"],
    )
    code_planner_agent = Agent(
        name="CodePlannerAgent",
        instruction=prompts["code_planning"],
        server_names=agent_config["code_planner"],
    )

    code_aggregator_agent = ParallelLLM(
        fan_in_agent=code_planner_agent,
        fan_out_agents=[concept_analysis_agent, algorithm_analysis_agent],
        llm_factory=get_preferred_llm_class(),
    )

    # Advanced token management system with dynamic scaling
    # 关键优化：ParallelLLM需要为输出预留充足空间
    # fan_in agent会接收fan_out agents的完整输出作为context，然后需要生成完整YAML
    if use_segmentation:
        # 分段模式：输入已优化，但仍需大量输出空间
        max_tokens_limit = 30000  # 充足的输出空间确保5个sections完整生成
        temperature = 0.2  # 稍微降低temperature以提高一致性
        print("🧠 Using SEGMENTED mode: max_tokens=30000 for complete YAML output")
    else:
        # 传统模式：需要更多输出空间应对长篇分析结果
        max_tokens_limit = 30000  # 足够的空间确保完整输出
        temperature = 0.3
        print("🧠 Using TRADITIONAL mode: max_tokens=30000 for complete YAML output")

    enhanced_params = RequestParams(
        maxTokens=max_tokens_limit,  # 注意：使用 camelCase 而不是 snake_case
        temperature=temperature,
    )

    # Concise message for multi-agent paper analysis and code planning
    message = f"""Analyze the research paper in directory: {paper_dir}

Please locate and analyze the markdown (.md) file containing the research paper. Based on your analysis, generate a comprehensive code reproduction plan that includes:

1. Complete system architecture and component breakdown
2. All algorithms, formulas, and implementation details
3. Detailed file structure and implementation roadmap

The goal is to create a reproduction plan detailed enough for independent implementation."""

    # 智能输出完整性检查和重试机制
    max_retries = 3
    retry_count = 0

    while retry_count < max_retries:
        try:
            print(
                f"🚀 Attempting code analysis (attempt {retry_count + 1}/{max_retries})"
            )
            result = await code_aggregator_agent.generate_str(
                message=message, request_params=enhanced_params
            )

            # 检查输出完整性的高级指标
            completeness_score = _assess_output_completeness(result)
            print(f"📊 Output completeness score: {completeness_score:.2f}/1.0")

            if completeness_score >= 0.8:  # 输出被认为是完整的
                print(
                    f"✅ Code analysis completed successfully (length: {len(result)} chars)"
                )
                return result
            else:
                print(
                    f"⚠️ Output appears truncated (score: {completeness_score:.2f}), retrying with enhanced parameters..."
                )
                # 动态调整参数进行重试
                enhanced_params = _adjust_params_for_retry(enhanced_params, retry_count)
                retry_count += 1

        except Exception as e:
            print(f"❌ Error in code analysis attempt {retry_count + 1}: {e}")
            retry_count += 1
            if retry_count >= max_retries:
                raise

    # 如果所有重试都失败，返回最后一次的结果
    print(f"⚠️ Returning potentially incomplete result after {max_retries} attempts")
    return result


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
        downloader = await github_download_agent.attach_llm(get_preferred_llm_class())

        # Set higher token output for GitHub download
        github_params = RequestParams(
            maxTokens=4096,  # 使用 camelCase
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

Focus on:
1. **References section analysis** - Extract all citations from the References/Bibliography part
2. References with high-quality GitHub implementations
3. Papers cited for methodology, algorithms, or core techniques
4. Related work that shares similar technical approaches
5. Implementation references that could provide code patterns

Goal: Find the most valuable GitHub repositories from the paper's reference list for code implementation reference."""

    async with reference_analysis_agent:
        print("Reference analyzer: Connected to server, analyzing references...")
        analyzer = await reference_analysis_agent.attach_llm(get_preferred_llm_class())

        reference_result = await analyzer.generate_str(message=message)
        return reference_result


async def _process_input_source(input_source: str, logger) -> str:
    """
    Process and validate input source (file path or URL).

    Args:
        input_source: Input source (file path or analysis result)
        logger: Logger instance

    Returns:
        str: Processed input source
    """
    if input_source.startswith("file://"):
        file_path = input_source[7:]
        if os.name == "nt" and file_path.startswith("/"):
            file_path = file_path.lstrip("/")
        return file_path
    return input_source


async def orchestrate_research_analysis_agent(
    input_source: str, logger, progress_callback: Optional[Callable] = None
) -> Tuple[str, str]:
    """
    Orchestrate intelligent research analysis and resource processing automation.

    This agent coordinates multiple AI components to analyze research content
    and process associated resources with automated workflow management.

    Args:
        input_source: Research input source for analysis
        logger: Logger instance for process tracking
        progress_callback: Progress callback function for workflow monitoring

    Returns:
        tuple: (analysis_result, resource_processing_result)
    """
    # Step 1: Research Analysis
    if progress_callback:
        progress_callback(
            10, "📊 Analyzing research content and extracting key information..."
        )
    analysis_result = await run_research_analyzer(input_source, logger)

    # Add brief pause for system stability
    await asyncio.sleep(5)

    # Step 2: Download Processing
    if progress_callback:
        progress_callback(
            25, "📥 Processing downloads and preparing document structure..."
        )
    download_result = await run_resource_processor(analysis_result, logger)

    return analysis_result, download_result


async def synthesize_workspace_infrastructure_agent(
    download_result: str, logger, workspace_dir: Optional[str] = None
) -> Dict[str, str]:
    """
    Synthesize intelligent research workspace infrastructure with automated structure generation.

    This agent autonomously creates and configures the optimal workspace architecture
    for research project implementation with AI-driven path optimization.

    Args:
        download_result: Resource processing result from analysis agent
        logger: Logger instance for infrastructure tracking
        workspace_dir: Optional workspace directory path for environment customization

    Returns:
        dict: Comprehensive workspace infrastructure metadata
    """
    # Parse download result to get file information
    result = await FileProcessor.process_file_input(
        download_result, base_dir=workspace_dir
    )
    paper_dir = result["paper_dir"]

    # Log workspace infrastructure synthesis
    print("🏗️ Intelligent workspace infrastructure synthesized:")
    print(f"   Base workspace environment: {workspace_dir or 'auto-detected'}")
    print(f"   Research workspace: {paper_dir}")
    print("   AI-driven path optimization: active")

    return {
        "paper_dir": paper_dir,
        "standardized_text": result["standardized_text"],
        "reference_path": os.path.join(paper_dir, "reference.txt"),
        "initial_plan_path": os.path.join(paper_dir, "initial_plan.txt"),
        "download_path": os.path.join(paper_dir, "github_download.txt"),
        "index_report_path": os.path.join(paper_dir, "codebase_index_report.txt"),
        "implementation_report_path": os.path.join(
            paper_dir, "code_implementation_report.txt"
        ),
        "workspace_dir": workspace_dir,
    }


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
                    raise IOError(
                        f"File {md_path} is a PDF file, not a text file. Please convert it to markdown format or use PDF processing tools."
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
        progress_callback(40, "🏗️ Synthesizing intelligent code architecture...")

    initial_plan_path = dir_info["initial_plan_path"]

    # Check if initial plan already exists
    if not os.path.exists(initial_plan_path):
        # Use segmentation setting from preprocessing phase
        use_segmentation = dir_info.get("use_segmentation", True)
        print(f"📊 Planning mode: {'Segmented' if use_segmentation else 'Traditional'}")

        initial_plan_result = await run_code_analyzer(
            dir_info["paper_dir"], logger, use_segmentation=use_segmentation
        )
        with open(initial_plan_path, "w", encoding="utf-8") as f:
            f.write(initial_plan_result)
        print(f"Initial plan saved to {initial_plan_path}")


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
            config_path="mcp_agent.secrets.yaml",
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
            implementation_result = await code_workflow.run_workflow(
                plan_file_path=dir_info["initial_plan_path"],
                target_directory=dir_info["paper_dir"],
                pure_code_mode=True,  # Focus on code implementation, skip testing
            )

            # Log implementation results
            if implementation_result["status"] == "success":
                print("Code implementation completed successfully!")
                print(f"Code directory: {implementation_result['code_directory']}")

                # Save implementation results to file
                with open(
                    dir_info["implementation_report_path"], "w", encoding="utf-8"
                ) as f:
                    f.write(str(implementation_result))
                print(
                    f"Implementation report saved to {dir_info['implementation_report_path']}"
                )

            else:
                print(
                    f"Code implementation failed: {implementation_result.get('message', 'Unknown error')}"
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
            server_names=get_search_server_names(),  # Dynamic search server configuration
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
                planner = await chat_planning_agent.attach_llm(
                    get_preferred_llm_class()
                )
                print("✅ LLM attached successfully")
            except Exception as e:
                print(f"❌ Failed to attach LLM: {e}")
                raise

            # Set higher token output for comprehensive planning
            planning_params = RequestParams(
                maxTokens=8192,  # 使用 camelCase - Higher token limit for detailed plans
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

            # Log to SimpleLLMLogger
            if hasattr(logger, "log_response"):
                logger.log_response(
                    raw_result, model="ChatPlanningAgent", agent="ChatPlanningAgent"
                )

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
    try:
        # Phase 0: Workspace Setup
        if progress_callback:
            progress_callback(5, "🔄 Setting up workspace for file processing...")

        print("🚀 Initializing intelligent multi-agent research orchestration system")

        # Setup local workspace directory
        workspace_dir = os.path.join(os.getcwd(), "deepcode_lab")
        os.makedirs(workspace_dir, exist_ok=True)

        print("📁 Working environment: local")
        print(f"📂 Workspace directory: {workspace_dir}")
        print("✅ Workspace status: ready")

        # Log intelligence functionality status
        if enable_indexing:
            print("🧠 Advanced intelligence analysis enabled - comprehensive workflow")
        else:
            print("⚡ Optimized mode - advanced intelligence analysis disabled")

        # Phase 1: Input Processing and Validation
        input_source = await _process_input_source(input_source, logger)

        # Phase 2: Research Analysis and Resource Processing (if needed)
        if isinstance(input_source, str) and (
            input_source.endswith((".pdf", ".docx", ".txt", ".html", ".md"))
            or input_source.startswith(("http", "file://"))
        ):
            (
                analysis_result,
                download_result,
            ) = await orchestrate_research_analysis_agent(
                input_source, logger, progress_callback
            )
        else:
            download_result = input_source  # Use input directly if already processed

        # Phase 3: Workspace Infrastructure Synthesis
        if progress_callback:
            progress_callback(
                40, "🏗️ Synthesizing intelligent workspace infrastructure..."
            )

        dir_info = await synthesize_workspace_infrastructure_agent(
            download_result, logger, workspace_dir
        )
        await asyncio.sleep(30)

        # Phase 3.5: Document Segmentation and Preprocessing

        segmentation_result = await orchestrate_document_preprocessing_agent(
            dir_info, logger
        )

        # Handle segmentation result
        if segmentation_result["status"] == "success":
            print("✅ Document preprocessing completed successfully!")
            print(
                f"   📊 Using segmentation: {dir_info.get('use_segmentation', False)}"
            )
            if dir_info.get("segments_ready", False):
                print(
                    f"   📁 Segments directory: {segmentation_result.get('segments_dir', 'N/A')}"
                )
        elif segmentation_result["status"] == "fallback_to_traditional":
            print("⚠️ Document segmentation failed, using traditional processing")
            print(
                f"   Original error: {segmentation_result.get('original_error', 'Unknown')}"
            )
        else:
            print(
                f"⚠️ Document preprocessing encountered issues: {segmentation_result.get('error_message', 'Unknown')}"
            )

        # Phase 4: Code Planning Orchestration
        await orchestrate_code_planning_agent(dir_info, logger, progress_callback)

        # Phase 5: Reference Intelligence (only when indexing is enabled)
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

        # Phase 6: Repository Acquisition Automation (optional)
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

        # Phase 7: Codebase Intelligence Orchestration (optional)
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

        # Phase 8: Code Implementation Synthesis
        implementation_result = await synthesize_code_implementation_agent(
            dir_info, logger, progress_callback, enable_indexing
        )

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

        # Add implementation status to summary
        if implementation_result["status"] == "success":
            pipeline_summary += "\n🎉 Code implementation completed successfully!"
            pipeline_summary += (
                f"\n📁 Code generated in: {implementation_result['code_directory']}"
            )
            return pipeline_summary
        elif implementation_result["status"] == "warning":
            pipeline_summary += (
                f"\n⚠️ Code implementation: {implementation_result['message']}"
            )
            return pipeline_summary
        else:
            pipeline_summary += (
                f"\n❌ Code implementation failed: {implementation_result['message']}"
            )
            return pipeline_summary

    except Exception as e:
        print(f"Error in execute_multi_agent_research_pipeline: {e}")
        raise e


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

        # Create workspace directory structure for chat mode
        # First, let's create a temporary directory structure that mimics a paper workspace
        import time

        # Generate a unique paper directory name
        timestamp = str(int(time.time()))
        paper_name = f"chat_project_{timestamp}"

        # Use workspace directory
        chat_paper_dir = os.path.join(workspace_dir, "papers", paper_name)

        os.makedirs(chat_paper_dir, exist_ok=True)

        # Create a synthetic markdown file with user requirements
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
"""

        # Save the markdown file
        markdown_file_path = os.path.join(chat_paper_dir, f"{paper_name}.md")
        with open(markdown_file_path, "w", encoding="utf-8") as f:
            f.write(markdown_content)

        print(f"💾 Created chat project workspace: {chat_paper_dir}")
        print(f"📄 Saved requirements to: {markdown_file_path}")

        # Create a download result that matches FileProcessor expectations
        synthetic_download_result = json.dumps(
            {
                "status": "success",
                "paper_path": markdown_file_path,
                "input_type": "chat_input",
                "paper_info": {
                    "title": "User-Provided Coding Requirements",
                    "source": "chat_input",
                    "description": "Implementation plan generated from user requirements",
                },
            }
        )

        dir_info = await synthesize_workspace_infrastructure_agent(
            synthetic_download_result, logger, workspace_dir
        )
        await asyncio.sleep(10)  # Brief pause for file system operations

        # Phase 3: Save Planning Result
        if progress_callback:
            progress_callback(70, "📝 Saving implementation plan...")

        # Save the planning result to the initial_plan.txt file (same location as Phase 4 in original pipeline)
        initial_plan_path = dir_info["initial_plan_path"]
        with open(initial_plan_path, "w", encoding="utf-8") as f:
            f.write(planning_result)
        print(f"💾 Implementation plan saved to {initial_plan_path}")

        # Phase 4: Code Implementation Synthesis (same as Phase 8 in original pipeline)
        if progress_callback:
            progress_callback(85, "🔬 Synthesizing intelligent code implementation...")

        implementation_result = await synthesize_code_implementation_agent(
            dir_info, logger, progress_callback, enable_indexing
        )

        # Final Status Report
        pipeline_summary = f"Chat-based planning and implementation pipeline completed for {dir_info['paper_dir']}"

        # Add implementation status to summary
        if implementation_result["status"] == "success":
            pipeline_summary += "\n🎉 Code implementation completed successfully!"
            pipeline_summary += (
                f"\n📁 Code generated in: {implementation_result['code_directory']}"
            )
            pipeline_summary += (
                "\n💬 Generated from user requirements via chat interface"
            )
            return pipeline_summary
        elif implementation_result["status"] == "warning":
            pipeline_summary += (
                f"\n⚠️ Code implementation: {implementation_result['message']}"
            )
            return pipeline_summary
        else:
            pipeline_summary += (
                f"\n❌ Code implementation failed: {implementation_result['message']}"
            )
            return pipeline_summary

    except Exception as e:
        print(f"Error in execute_chat_based_planning_pipeline: {e}")
        raise e
