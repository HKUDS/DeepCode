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
- Adaptive workflow optimization based on processing requirements#是否需要执行全部步骤
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
from dataclasses import asdict

import yaml
from typing import Callable, Dict, Optional, Tuple, List

# MCP Agent imports
from mcp_agent.agents.agent import Agent
from mcp_agent.workflows.llm.augmented_llm_anthropic import AnthropicAugmentedLLM
from mcp_agent.workflows.llm.augmented_llm_openai import OpenAIAugmentedLLM
from mcp_agent.workflows.llm.augmented_llm import RequestParams
from mcp_agent.workflows.parallel.parallel_llm import ParallelLLM
# Local imports
from prompts.code_prompts import (
    PAPER_INPUT_ANALYZER_PROMPT,
    PAPER_DOWNLOADER_PROMPT,
    PAPER_REFERENCE_ANALYZER_PROMPT,
    PAPER_ALGORITHM_ANALYSIS_PROMPT,
    PAPER_CONCEPT_ANALYSIS_PROMPT,
    CODE_PLANNING_PROMPT,
    CHAT_AGENT_PLANNING_PROMPT,
)
from utils.file_processor import FileProcessor
from workflows.code_implementation_workflow import CodeImplementationWorkflow
from workflows.code_implementation_workflow_index import (
    CodeImplementationWorkflowWithIndex,
)
from workflows.userinloop import  _prompt_user_to_select_route, \
    _run_code_analyzer_for_selected_route, _generate_route_options, _generate_route_options_for_chat, \
    run_chat_planning_agent,run_user_in_loop

# Environment configuration
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"  # Prevent .pyc file generation


def get_preferred_llm_class(config_path: str = "mcp_agent.secrets.yaml"):
    """
    Automatically select the LLM class based on API key availability in configuration.

    Reads from YAML config file and returns AnthropicAugmentedLLM if anthropic.api_key
    is available, otherwise returns OpenAIAugmentedLLM.

    Args:
        config_path: Path to the YAML configuration file

    Returns:
        class: The preferred LLM class
    """
    try:
        # Try to read the configuration file
        if os.path.exists(config_path):
            with open(config_path, "r", encoding="utf-8") as f:
                config = yaml.safe_load(f)

            # Check for anthropic API key in config
            anthropic_config = config.get("anthropic", {})
            anthropic_key = anthropic_config.get("api_key", "")

            if anthropic_key and anthropic_key.strip() and not anthropic_key == "":
                # print("🤖 Using AnthropicAugmentedLLM (Anthropic API key found in config)")
                return AnthropicAugmentedLLM
            else:
                # print("🤖 Using OpenAIAugmentedLLM (Anthropic API key not configured)")
                return OpenAIAugmentedLLM
        else:
            print(f"🤖 Config file {config_path} not found, using OpenAIAugmentedLLM")
            return OpenAIAugmentedLLM

    except Exception as e:
        print(f"🤖 Error reading config file {config_path}: {e}")
        print("🤖 Falling back to OpenAIAugmentedLLM")
        return OpenAIAugmentedLLM


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
            server_names=["brave"],
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
                max_tokens=6144,
                temperature=0.3,
            )

            print(
                f"🔄 Making LLM request with params: max_tokens={analysis_params.max_tokens}, temperature={analysis_params.temperature}"
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
            max_tokens=4096,
            temperature=0.2,
        )

        return await processor.generate_str(
            message=analysis_result, request_params=processor_params
        )


async def run_code_analyzer(paper_dir: str, logger) -> str:
    """
    Run the code analysis workflow using multiple agents for comprehensive code planning.

    This function orchestrates three specialized agents:
    - ConceptAnalysisAgent: Analyzes system architecture and conceptual framework
    - AlgorithmAnalysisAgent: Extracts algorithms, formulas, and technical details
    - CodePlannerAgent: Integrates outputs into a comprehensive implementation plan

    Args:
        paper_dir: Directory path containing the research paper and related resources
        logger: Logger instance for logging information

    Returns:
        str: Comprehensive analysis result from the coordinated agents
    """
    concept_analysis_agent = Agent(
        name="ConceptAnalysisAgent",
        instruction=PAPER_CONCEPT_ANALYSIS_PROMPT,
        server_names=["filesystem"],
    )
    algorithm_analysis_agent = Agent(
        name="AlgorithmAnalysisAgent",
        instruction=PAPER_ALGORITHM_ANALYSIS_PROMPT,
        server_names=["filesystem", "brave"],
    )
    code_planner_agent = Agent(
        name="CodePlannerAgent",
        instruction=CODE_PLANNING_PROMPT,
        server_names=["brave"],
    )

    code_aggregator_agent = ParallelLLM(
        fan_in_agent=code_planner_agent,
        fan_out_agents=[concept_analysis_agent, algorithm_analysis_agent],
        llm_factory=get_preferred_llm_class(),
    )

    # Set higher token output limit
    enhanced_params = RequestParams(
        max_tokens=26384,
        temperature=0.3,
    )

    # Concise message for multi-agent paper analysis and code planning
    message = f"""Analyze the research paper in directory: {paper_dir}

Please locate and analyze the markdown (.md) file containing the research paper. Based on your analysis, generate a comprehensive code reproduction plan that includes:

1. Complete system architecture and component breakdown
2. All algorithms, formulas, and implementation details
3. Detailed file structure and implementation roadmap

The goal is to create a reproduction plan detailed enough for independent implementation."""

    result = await code_aggregator_agent.generate_str(
        message=message, request_params=enhanced_params
    )
    print(f"Code analysis result: {result}")
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
            max_tokens=4096,
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

"""
async def orchestrate_code_planning_agent(
    dir_info: Dict[str, str], logger, progress_callback: Optional[Callable] = None
):

    if progress_callback:
        progress_callback(40, "🏗️ Synthesizing intelligent code architecture...")

    initial_plan_path = dir_info["initial_plan_path"]

    # Check if initial plan already exists
    if not os.path.exists(initial_plan_path):
        initial_plan_result = await run_code_analyzer(dir_info["paper_dir"], logger)
        with open(initial_plan_path, "w", encoding="utf-8") as f:
            f.write(initial_plan_result)
        print(f"Initial plan saved to {initial_plan_path}")
"""
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
    paper_dir = dir_info["paper_dir"]

    if not os.path.exists(initial_plan_path):
        # 1) 先生成多路线
        route_opts = await _generate_route_options(paper_dir, logger)

        # 2) 用户选择（非交互环境可改成默认选 1）
        try:
            selected_idx = _prompt_user_to_select_route(route_opts)
        except Exception:
            selected_idx = 0
            print("⚠️ 交互选择不可用，默认选择路线 #1")

        selected = route_opts.options[selected_idx]

        # 3) 持久化路线选项与选择结果，便于排错与回溯
        os.makedirs(paper_dir, exist_ok=True)
        with open(os.path.join(paper_dir, "route_options.json"), "w", encoding="utf-8") as f:
            json.dump({"task_brief": route_opts.task_brief, "options": [asdict(o) for o in route_opts.options]},
                      f, ensure_ascii=False, indent=2)
        with open(os.path.join(paper_dir, "selected_route.json"), "w", encoding="utf-8") as f:
            json.dump(asdict(selected), f, ensure_ascii=False, indent=2)

        # 4) 严格按所选路线生成“最终实现计划”，写入 initial_plan.txt
        final_plan = await _run_code_analyzer_for_selected_route(paper_dir, selected, logger)
        with open(initial_plan_path, "w", encoding="utf-8") as f:
            f.write(final_plan)
        print(f"Initial plan (selected route) saved to {initial_plan_path}")


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




#核心工作流
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
        if enable_indexing:#考虑是否需要走完八个步骤
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
        if True:
            # 1) 生成多路线
            selected, route_opts, history_text = await run_user_in_loop(user_input, logger, interactive=True)

            # chat 模式：按选中路线生成最终实现计划（建议这里也禁用 server 注入）
            planning_result = await run_chat_planning_agent(user_input, selected, logger)





            #final_plan = await _run_code_analyzer_for_selected_route(user_input, selected, logger)


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

        #save the routeoptions
        route_dir = os.path.join(chat_paper_dir, "route_dir")
        os.makedirs(route_dir, exist_ok=True)
        with open(os.path.join(route_dir, "route_options.json"), "w", encoding="utf-8") as f:
            json.dump({"task_brief": route_opts.task_brief, "options": [asdict(o) for o in route_opts.options]},
                      f, ensure_ascii=False, indent=2)
        with open(os.path.join(route_dir, "selected_route.json"), "w", encoding="utf-8") as f:
            json.dump(asdict(selected), f, ensure_ascii=False, indent=2)

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
        print(f"plan(selected route) saved to {initial_plan_path}")


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
