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


class MultiTurnCodeAnalysisWorkflow:
    """
    Multi-turn conversation workflow controller for code analysis agents.
    
    Replaces the MCP ParallelLLM approach with individual multi-turn conversations
    for each analysis agent, similar to revision agent pattern.
    """
    
    def __init__(self, paper_dir: str, logger, use_segmentation: bool, agent_config: dict, prompts: dict):
        self.paper_dir = paper_dir
        self.logger = logger
        self.use_segmentation = use_segmentation
        self.agent_config = agent_config
        self.prompts = prompts
        
        # Initialize API configuration and models
        self._load_configurations()
    
    def _load_configurations(self):
        """Load API configurations and default models"""
        import yaml
        import os
        from utils.llm_utils import get_default_models
        
        # Load API configuration
        config_path = "mcp_agent.secrets.yaml"
        try:
            if os.path.exists(config_path):
                with open(config_path, "r", encoding="utf-8") as f:
                    self.api_config = yaml.safe_load(f) or {}
            else:
                self.logger.warning(f"API config file {config_path} not found")
                self.api_config = {}
        except Exception as e:
            self.logger.error(f"Error loading API config: {e}")
            self.api_config = {}
        
        # Load default models
        self.default_models = get_default_models()
    
    async def execute_sequential_analysis_workflows(self) -> str:
        """
        Execute analysis workflows in proper sequence:
        1. ConceptAnalysisAgent and AlgorithmAnalysisAgent run in parallel
        2. CodePlannerAgent runs after the first two complete, using their outputs
        
        Returns:
            str: Integrated analysis result from all agents
        """
        import asyncio
        
        self.logger.info("🚀 Starting sequential multi-turn analysis workflows")
        
        # Phase 1: Run ConceptAnalysisAgent and AlgorithmAnalysisAgent in parallel
        self.logger.info("📊 Phase 1: Running ConceptAnalysisAgent and AlgorithmAnalysisAgent in parallel")
        
        try:
            concept_result, algorithm_result = await asyncio.gather(
                self._execute_concept_analysis_workflow(),
                self._execute_algorithm_analysis_workflow(),
                return_exceptions=True
            )
        except Exception as e:
            self.logger.error(f"Error in Phase 1 parallel execution: {e}")
            return f"Error in Phase 1 analysis execution: {e}"
        
        # Handle Phase 1 results
        concept_analysis = self._handle_workflow_result(concept_result, "ConceptAnalysisAgent")
        algorithm_analysis = self._handle_workflow_result(algorithm_result, "AlgorithmAnalysisAgent")
        
        self.logger.info("✅ Phase 1 completed - ConceptAnalysisAgent and AlgorithmAnalysisAgent finished")
        
        # Phase 2: Run CodePlannerAgent with outputs from Phase 1
        self.logger.info("📋 Phase 2: Running CodePlannerAgent with Phase 1 outputs")
        
        try:
            planning_result = await self._execute_code_planning_workflow_with_inputs(
                concept_analysis, algorithm_analysis
            )
        except Exception as e:
            self.logger.error(f"Error in Phase 2 execution: {e}")
            planning_result = {
                "success": False,
                "error": str(e),
                "content": f"CodePlannerAgent failed: {e}",
                "iterations": 0
            }
        
        # Combine all results
        results = {
            "concept_analysis": concept_analysis,
            "algorithm_analysis": algorithm_analysis,
            "code_planning": planning_result
        }
        
        # Integrate results into final comprehensive analysis
        integrated_result = self._integrate_analysis_results(results)
        
        self.logger.info("✅ All sequential analysis workflows completed")
        return integrated_result
    
    def _handle_workflow_result(self, result, agent_name: str) -> dict:
        """Handle workflow result, including exceptions"""
        if isinstance(result, Exception):
            self.logger.error(f"❌ {agent_name} workflow failed: {result}")
            return {
                "success": False,
                "error": str(result),
                "content": f"Agent {agent_name} failed to complete analysis",
                "iterations": 0
            }
        return result
    
    async def _execute_concept_analysis_workflow(self) -> dict:
        """Execute multi-turn conversation workflow for ConceptAnalysisAgent"""
        return await self._execute_single_agent_workflow(
            agent_name="ConceptAnalysisAgent",
            agent_prompt=self.prompts["concept_analysis"],
            server_names=self.agent_config["concept_analysis"],
            task_description="Comprehensive system architecture and conceptual framework analysis",
            tools=self._prepare_concept_analysis_tools()
        )
    
    async def _execute_algorithm_analysis_workflow(self) -> dict:
        """Execute multi-turn conversation workflow for AlgorithmAnalysisAgent"""
        return await self._execute_single_agent_workflow(
            agent_name="AlgorithmAnalysisAgent",
            agent_prompt=self.prompts["algorithm_analysis"],
            server_names=self.agent_config["algorithm_analysis"],
            task_description="Complete algorithm extraction and technical detail analysis",
            tools=self._prepare_algorithm_analysis_tools()
        )
    
    async def _execute_code_planning_workflow(self) -> dict:
        """Execute multi-turn conversation workflow for CodePlannerAgent (legacy method for compatibility)"""
        return await self._execute_single_agent_workflow(
            agent_name="CodePlannerAgent",
            agent_prompt=self.prompts["code_planning"],
            server_names=self.agent_config["code_planner"],
            task_description="Integration of analysis results into comprehensive implementation plan",
            tools=self._prepare_code_planning_tools()
        )
    
    async def _execute_code_planning_workflow_with_inputs(self, concept_analysis: dict, algorithm_analysis: dict) -> dict:
        """
        Execute multi-turn conversation workflow for CodePlannerAgent with inputs from previous agents.
        
        CodePlannerAgent receives and integrates outputs from ConceptAnalysisAgent and AlgorithmAnalysisAgent.
        """
        try:
            self.logger.info("🎯 Starting CodePlannerAgent workflow with Phase 1 inputs")
            
            # Initialize LLM client
            client, client_type = await self._initialize_llm_client()
            
            # Prepare tools (only brave and filesystem)
            tools = self._prepare_code_planning_tools_simplified()
            
            # Build enhanced system message with Phase 1 results
            system_message = self._build_code_planner_system_message_with_inputs(
                concept_analysis, algorithm_analysis, tools
            )
            
            # Create initial user message with Phase 1 outputs
            user_message = self._build_code_planner_user_message_with_inputs(
                concept_analysis, algorithm_analysis
            )
            
            # Initialize conversation tracking
            agent_result = {
                "agent_name": "CodePlannerAgent",
                "task_description": "Integration of ConceptAnalysis and AlgorithmAnalysis results into comprehensive implementation plan",
                "success": False,
                "content": "",
                "tools_used": [],
                "reasoning_steps": [],
                "iterations": 0,
                "completion_details": None,
                "error": None,
                "phase1_inputs": {
                    "concept_analysis_success": concept_analysis.get("success", False),
                    "algorithm_analysis_success": algorithm_analysis.get("success", False),
                    "total_phase1_iterations": concept_analysis.get("iterations", 0) + algorithm_analysis.get("iterations", 0)
                }
            }
            
            # Execute multi-turn conversation loop with increased iterations
            messages = [{"role": "user", "content": user_message}]
            max_iterations = 50  # Increased as requested
            iteration = 0
            analysis_completed = False
            
            while iteration < max_iterations and not analysis_completed:
                iteration += 1
                agent_result["iterations"] = iteration
                
                self.logger.info(f"🔄 CodePlannerAgent iteration {iteration}/{max_iterations}")
                
                # Call LLM with tools
                response = await self._call_llm_with_tools(
                    client, client_type, system_message, messages, tools
                )
                
                # Process response and check for completion
                analysis_completed = await self._process_agent_response(
                    response, messages, agent_result, iteration
                )
                
                # Check termination conditions
                if analysis_completed:
                    self.logger.info(f"✅ CodePlannerAgent completed planning in {iteration} iterations")
                    agent_result["success"] = True
                elif iteration >= max_iterations:
                    self.logger.warning(f"⚠️ CodePlannerAgent reached maximum iterations ({max_iterations})")
                    agent_result["error"] = "Reached maximum iterations without completion"
                    break
            
            return agent_result
            
        except Exception as e:
            self.logger.error(f"❌ CodePlannerAgent workflow failed: {e}")
            return {
                "agent_name": "CodePlannerAgent",
                "task_description": "Integration of analysis results into comprehensive implementation plan",
                "success": False,
                "content": "",
                "tools_used": [],
                "reasoning_steps": [],
                "iterations": 0,
                "completion_details": None,
                "error": str(e)
            }
    
    async def _execute_single_agent_workflow(self, agent_name: str, agent_prompt: str, 
                                           server_names: list, task_description: str, tools: list) -> dict:
        """
        Execute multi-turn conversation workflow for a single agent.
        
        This is the core method that implements the multi-turn conversation pattern
        similar to revision agent's _execute_single_task_conversation.
        """
        try:
            self.logger.info(f"🎯 Starting {agent_name} multi-turn workflow")
            
            # Initialize LLM client
            client, client_type = await self._initialize_llm_client()
            
            # Prepare system message for the agent
            system_message = self._build_agent_system_message(
                agent_name, agent_prompt, task_description, tools
            )
            
            # Create initial user message
            user_message = self._build_initial_user_message(agent_name, task_description)
            
            # Initialize conversation tracking
            agent_result = {
                "agent_name": agent_name,
                "task_description": task_description,
                "success": False,
                "content": "",
                "tools_used": [],
                "reasoning_steps": [],
                "iterations": 0,
                "completion_details": None,
                "error": None
            }
            
            # Execute multi-turn conversation loop
            messages = [{"role": "user", "content": user_message}]
            max_iterations = 50  # Increased as requested
            iteration = 0
            analysis_completed = False
            
            while iteration < max_iterations and not analysis_completed:
                iteration += 1
                agent_result["iterations"] = iteration
                
                self.logger.info(f"🔄 {agent_name} iteration {iteration}/{max_iterations}")
                
                # Call LLM with tools
                response = await self._call_llm_with_tools(
                    client, client_type, system_message, messages, tools
                )
                
                # Process response and check for completion
                analysis_completed = await self._process_agent_response(
                    response, messages, agent_result, iteration
                )
                
                # Check termination conditions
                if analysis_completed:
                    self.logger.info(f"✅ {agent_name} completed analysis in {iteration} iterations")
                    agent_result["success"] = True
                elif iteration >= max_iterations:
                    self.logger.warning(f"⚠️ {agent_name} reached maximum iterations")
                    agent_result["error"] = "Reached maximum iterations without completion"
                    break
            
            return agent_result
            
        except Exception as e:
            self.logger.error(f"❌ {agent_name} workflow failed: {e}")
            return {
                "agent_name": agent_name,
                "task_description": task_description,
                "success": False,
                "content": "",
                "tools_used": [],
                "reasoning_steps": [],
                "iterations": 0,
                "completion_details": None,
                "error": str(e)
            }
    
    def _build_agent_system_message(self, agent_name: str, agent_prompt: str, 
                                   task_description: str, tools: list) -> str:
        """Build system message for the agent"""
        segmentation_mode = 'Enabled' if self.use_segmentation else 'Disabled'
        agent_area = agent_name.replace('Agent', '')
        tool_names = [tool.get("name", "") for tool in tools]
        
        system_message = f"""{agent_prompt}

MULTI-TURN ANALYSIS WORKFLOW:
You are {agent_name} in a multi-turn conversation workflow. Your specific task is:

{task_description}

Paper Directory: {self.paper_dir}
Segmentation Mode: {segmentation_mode}

WORKFLOW INSTRUCTIONS:
- Use available tools to thoroughly analyze the research paper
- Focus on your expertise area: {agent_area}
- Conduct comprehensive, iterative analysis
- Use multiple tool calls to build complete understanding
- Provide detailed, structured output suitable for integration

TERMINATION CONDITIONS:
- You have completed comprehensive analysis in your domain
- You indicate analysis is "complete", "finished", or "done"
- Maximum 15 iterations reached

Available tools: {tool_names}"""
        
        return system_message
    
    def _build_initial_user_message(self, agent_name: str, task_description: str) -> str:
        """Build initial user message for the agent"""
        agent_type = agent_name.replace('Agent', '').lower()
        
        return f"""Please perform comprehensive {agent_type} analysis of the research paper in directory: {self.paper_dir}

Your specific responsibilities:
{task_description}

Please start by examining the paper using appropriate tools and conduct thorough analysis.
Use your expertise to extract all relevant information for your domain.

Begin your analysis now."""
    
    async def _process_agent_response(self, response, messages, agent_result, iteration) -> bool:
        """Process agent response and check for completion"""
        try:
            # Extract response content
            response_content = response.get("content", "").strip()
            agent_result["reasoning_steps"].append(f"Iteration {iteration}: {response_content[:300]}...")
            
            # Add response to conversation
            messages.append({"role": "assistant", "content": response_content})
            
            # Store latest content
            agent_result["content"] = response_content
            
            # Process tool calls if any
            if response.get("tool_calls"):
                tool_results = await self._execute_tool_calls(
                    response["tool_calls"], agent_result, iteration
                )
                
                # Add tool results to conversation
                if tool_results:
                    tool_summary = "Tool execution results:\\n" + "\\n".join(tool_results)
                    messages.append({"role": "user", "content": tool_summary})
                
                # Add continuation prompt
                continuation_prompt = """Please continue your analysis based on the tool results.
If you have gathered sufficient information for comprehensive analysis, provide your final structured output.
Otherwise, continue using tools to gather more information."""
                messages.append({"role": "user", "content": continuation_prompt})
            else:
                # No tool calls - encourage tool usage or completion
                if iteration < 3:
                    messages.append({
                        "role": "user",
                        "content": "Please use the available tools to analyze the research paper. Start with examining the document structure and content."
                    })
                else:
                    messages.append({
                        "role": "user",
                        "content": "Please provide your final analysis results if completed, or continue using tools to gather more information."
                    })
            
            # Check for completion indicators
            completion_indicators = [
                "analysis complete", "analysis finished", "analysis done",
                "comprehensive analysis complete", "final analysis",
                "analysis is complete", "completed the analysis",
                "finished analyzing", "analysis concluded"
            ]
            
            if any(indicator in response_content.lower() for indicator in completion_indicators):
                self.logger.info("🎯 Analysis completion detected in agent response")
                agent_result["completion_details"] = "Agent indicated analysis completion"
                return True
            
            # Check if substantial analysis has been provided
            if len(response_content) > 2000 and iteration >= 5:
                self.logger.info("🎯 Substantial analysis content detected")
                agent_result["completion_details"] = "Substantial analysis content provided"
                return True
            
            return False
            
        except Exception as e:
            self.logger.error(f"Error processing agent response: {e}")
            return False
    
    async def _execute_tool_calls(self, tool_calls, agent_result, iteration) -> list:
        """Execute tool calls and return results"""
        tool_results = []
        
        for tool_call in tool_calls:
            tool_name = tool_call.get("name", "unknown")
            tool_input = tool_call.get("input", {})
            
            # Track tool usage
            if tool_name not in agent_result["tools_used"]:
                agent_result["tools_used"].append(tool_name)
            
            self.logger.info(f"🔧 Executing tool: {tool_name}")
            
            # For now, simulate tool execution
            # In production, this would integrate with actual MCP tools
            tool_result = f"Tool {tool_name} executed with input: {str(tool_input)[:100]}..."
            tool_results.append(tool_result)
        
        return tool_results
    
    def _prepare_concept_analysis_tools(self) -> list:
        """Prepare tools for ConceptAnalysisAgent"""
        try:
            if self.use_segmentation:
                return [
                    {"name": "read_document_segments", "description": "Read document segments with intelligent filtering", "input_schema": {"type": "object", "properties": {"query": {"type": "string"}}}},
                    {"name": "analyze_document_structure", "description": "Analyze document structure and sections", "input_schema": {"type": "object", "properties": {"document_path": {"type": "string"}}}},
                    {"name": "list_directory", "description": "List directory contents", "input_schema": {"type": "object", "properties": {"path": {"type": "string"}}}}
                ]
            else:
                return [
                    {"name": "read_file", "description": "Read complete files", "input_schema": {"type": "object", "properties": {"file_path": {"type": "string"}}}},
                    {"name": "list_directory", "description": "List directory contents", "input_schema": {"type": "object", "properties": {"path": {"type": "string"}}}},
                    {"name": "get_file_info", "description": "Get file information", "input_schema": {"type": "object", "properties": {"file_path": {"type": "string"}}}}
                ]
        except Exception as e:
            self.logger.warning(f"Error preparing concept analysis tools: {e}")
            return []
    
    def _prepare_algorithm_analysis_tools(self) -> list:
        """Prepare tools for AlgorithmAnalysisAgent"""
        try:
            tools = self._prepare_concept_analysis_tools()  # Base tools
            
            # Add search capabilities for algorithm extraction
            if self.use_segmentation:
                tools.extend([
                    {"name": "search_algorithms", "description": "Search for algorithm sections in document", "input_schema": {"type": "object", "properties": {"query": {"type": "string"}}}},
                    {"name": "extract_formulas", "description": "Extract mathematical formulas and equations", "input_schema": {"type": "object", "properties": {"document_path": {"type": "string"}}}}
                ])
            
            # Add search server tools if configured
            for server in self.agent_config.get("algorithm_analysis", []):
                if server in ["brave", "bocha-mcp"]:
                    tools.append({
                        "name": f"search_{server}",
                        "description": f"Search using {server} for additional algorithm information",
                        "input_schema": {"type": "object", "properties": {"query": {"type": "string"}}}
                    })
            
            return tools
        except Exception as e:
            self.logger.warning(f"Error preparing algorithm analysis tools: {e}")
            return []
    
    def _prepare_code_planning_tools(self) -> list:
        """Prepare tools for CodePlannerAgent (legacy method for compatibility)"""
        try:
            tools = self._prepare_algorithm_analysis_tools()  # Include all previous tools
            
            # Add planning-specific tools
            tools.extend([
                {"name": "create_file_structure", "description": "Create project file structure", "input_schema": {"type": "object", "properties": {"structure": {"type": "string"}}}},
                {"name": "plan_implementation", "description": "Plan implementation strategy", "input_schema": {"type": "object", "properties": {"requirements": {"type": "string"}}}},
                {"name": "validate_requirements", "description": "Validate implementation requirements", "input_schema": {"type": "object", "properties": {"requirements": {"type": "string"}}}}
            ])
            
            return tools
        except Exception as e:
            self.logger.warning(f"Error preparing code planning tools: {e}")
            return []
    
    def _prepare_code_planning_tools_simplified(self) -> list:
        """Prepare simplified tools for CodePlannerAgent (only brave and filesystem)"""
        try:
            tools = []
            
            # Only include brave and filesystem tools as requested
            tools.extend([
                # Filesystem tools
                {"name": "read_file", "description": "Read complete files", "input_schema": {"type": "object", "properties": {"file_path": {"type": "string"}}}},
                {"name": "list_directory", "description": "List directory contents", "input_schema": {"type": "object", "properties": {"path": {"type": "string"}}}},
                {"name": "get_file_info", "description": "Get file information", "input_schema": {"type": "object", "properties": {"file_path": {"type": "string"}}}},
                
                # Brave search tool
                {"name": "search_brave", "description": "Search using Brave for additional implementation information", "input_schema": {"type": "object", "properties": {"query": {"type": "string"}}}},
                
                # Planning-specific tools
                {"name": "create_file_structure", "description": "Create project file structure", "input_schema": {"type": "object", "properties": {"structure": {"type": "string"}}}},
                {"name": "plan_implementation", "description": "Plan implementation strategy", "input_schema": {"type": "object", "properties": {"requirements": {"type": "string"}}}},
                {"name": "validate_requirements", "description": "Validate implementation requirements", "input_schema": {"type": "object", "properties": {"requirements": {"type": "string"}}}}
            ])
            
            return tools
        except Exception as e:
            self.logger.warning(f"Error preparing simplified code planning tools: {e}")
            return []
    
    def _build_code_planner_system_message_with_inputs(self, concept_analysis: dict, algorithm_analysis: dict, tools: list) -> str:
        """Build system message for CodePlannerAgent with Phase 1 inputs"""
        tool_names = [tool.get("name", "") for tool in tools]
        
        # Extract success status and content from Phase 1
        concept_success = concept_analysis.get("success", False)
        algorithm_success = algorithm_analysis.get("success", False)
        concept_content = concept_analysis.get("content", "No concept analysis available")
        algorithm_content = algorithm_analysis.get("content", "No algorithm analysis available")
        
        system_message = f"""{self.prompts["code_planning"]}

PHASE 2 - CODE PLANNING WITH PHASE 1 INPUTS:
You are CodePlannerAgent in a multi-turn conversation workflow. You are receiving outputs from Phase 1 agents and must integrate them into a comprehensive implementation plan.

PHASE 1 ANALYSIS RESULTS:

=== ConceptAnalysisAgent Output (Status: {'✅ Success' if concept_success else '❌ Failed'}) ===
{concept_content}

=== AlgorithmAnalysisAgent Output (Status: {'✅ Success' if algorithm_success else '❌ Failed'}) ===
{algorithm_content}

YOUR TASK:
Integrate the above analysis results into a comprehensive, detailed implementation plan that includes:
1. Complete system architecture based on concept analysis
2. All algorithms and technical details from algorithm analysis  
3. Detailed file structure and implementation roadmap
4. Step-by-step implementation strategy

Paper Directory: {self.paper_dir}
Segmentation Mode: {'Enabled' if self.use_segmentation else 'Disabled'}

WORKFLOW INSTRUCTIONS:
- Analyze and synthesize the Phase 1 outputs above
- Use available tools (brave search, filesystem) to gather additional information if needed
- Create a comprehensive implementation plan
- Focus on practical, actionable implementation details
- Provide structured output suitable for code implementation

TERMINATION CONDITIONS:
- You have created a comprehensive implementation plan integrating both Phase 1 outputs
- You indicate planning is "complete", "finished", or "done"
- Maximum 50 iterations reached

Available tools: {tool_names}"""
        
        return system_message
    
    def _build_code_planner_user_message_with_inputs(self, concept_analysis: dict, algorithm_analysis: dict) -> str:
        """Build initial user message for CodePlannerAgent with Phase 1 inputs"""
        
        concept_status = "✅ Successful" if concept_analysis.get("success", False) else "❌ Failed"
        algorithm_status = "✅ Successful" if algorithm_analysis.get("success", False) else "❌ Failed"
        
        return f"""You are CodePlannerAgent. Please integrate the Phase 1 analysis results into a comprehensive implementation plan.

PHASE 1 RESULTS SUMMARY:
- ConceptAnalysisAgent: {concept_status} ({concept_analysis.get('iterations', 0)} iterations)
- AlgorithmAnalysisAgent: {algorithm_status} ({algorithm_analysis.get('iterations', 0)} iterations)

The detailed outputs from both agents are provided in your system message above.

YOUR MISSION:
Create a comprehensive code implementation plan that synthesizes:
1. The conceptual framework and system architecture from ConceptAnalysisAgent
2. The algorithms, formulas, and technical details from AlgorithmAnalysisAgent
3. Additional research using brave search if needed
4. A practical, step-by-step implementation roadmap

Please start by analyzing the Phase 1 outputs and then create your comprehensive implementation plan.

Begin your integration and planning process now."""
    
    def _integrate_analysis_results(self, results: dict) -> str:
        """Integrate results from all agents into final comprehensive plan"""
        try:
            self.logger.info("🔄 Integrating analysis results from all agents")
            
            concept_result = results.get("concept_analysis", {})
            algorithm_result = results.get("algorithm_analysis", {})
            planning_result = results.get("code_planning", {})
            
            # Helper function for success status
            def success_status(result):
                return '✅ Success' if result.get('success') else '❌ Failed'
            
            # Create integrated result
            integrated_result = f"""# Comprehensive Code Analysis and Implementation Plan

## Agent Analysis Summary
- ConceptAnalysisAgent: {success_status(concept_result)} ({concept_result.get('iterations', 0)} iterations)
- AlgorithmAnalysisAgent: {success_status(algorithm_result)} ({algorithm_result.get('iterations', 0)} iterations)
- CodePlannerAgent: {success_status(planning_result)} ({planning_result.get('iterations', 0)} iterations)

## 1. Conceptual Framework Analysis
{concept_result.get('content', 'Analysis not available')}

## 2. Algorithm and Technical Details
{algorithm_result.get('content', 'Analysis not available')}

## 3. Implementation Plan and Strategy
{planning_result.get('content', 'Analysis not available')}

## Integration Summary
Paper Directory: {self.paper_dir}
Segmentation Mode: {'Enabled' if self.use_segmentation else 'Disabled'}
Total Agent Iterations: {concept_result.get('iterations', 0) + algorithm_result.get('iterations', 0) + planning_result.get('iterations', 0)}
Overall Success: {'✅ All agents completed successfully' if all(r.get('success', False) for r in results.values()) else '⚠️ Some agents encountered issues'}
"""
            
            self.logger.info("✅ Analysis results integration completed")
            return integrated_result
            
        except Exception as e:
            self.logger.error(f"❌ Error integrating analysis results: {e}")
            return f"Error integrating analysis results: {e}"
    
    # ==================== LLM Communication Methods ====================
    
    async def _initialize_llm_client(self):
        """Initialize LLM client (Anthropic or OpenAI) based on API key availability"""
        anthropic_key = self.api_config.get("anthropic", {}).get("api_key", "")
        openai_key = self.api_config.get("openai", {}).get("api_key", "")
        
        # Try Anthropic API first if key is available
        if anthropic_key and anthropic_key.strip():
            try:
                from anthropic import AsyncAnthropic
                
                client = AsyncAnthropic(api_key=anthropic_key)
                # Test connection
                await client.messages.create(
                    model=self.default_models["anthropic"],
                    max_tokens=20,
                    messages=[{"role": "user", "content": "test"}],
                )
                self.logger.info(f"Using Anthropic API with model: {self.default_models['anthropic']}")
                return client, "anthropic"
            except Exception as e:
                self.logger.warning(f"Anthropic API unavailable: {e}")
        
        # Try OpenAI API if Anthropic failed or key not available
        if openai_key and openai_key.strip():
            try:
                from openai import AsyncOpenAI
                
                openai_config = self.api_config.get("openai", {})
                base_url = openai_config.get("base_url")
                
                if base_url:
                    client = AsyncOpenAI(api_key=openai_key, base_url=base_url)
                else:
                    client = AsyncOpenAI(api_key=openai_key)
                
                # Test connection
                try:
                    await client.chat.completions.create(
                        model=self.default_models["openai"],
                        max_tokens=20,
                        messages=[{"role": "user", "content": "test"}],
                    )
                except Exception as e:
                    if "max_tokens" in str(e) and "max_completion_tokens" in str(e):
                        await client.chat.completions.create(
                            model=self.default_models["openai"],
                            max_completion_tokens=20,
                            messages=[{"role": "user", "content": "test"}],
                        )
                    else:
                        raise
                
                self.logger.info(f"Using OpenAI API with model: {self.default_models['openai']}")
                if base_url:
                    self.logger.info(f"Using custom base URL: {base_url}")
                return client, "openai"
            except Exception as e:
                self.logger.warning(f"OpenAI API unavailable: {e}")
        
        raise ValueError("No available LLM API - please check your API keys in configuration")
    
    async def _call_llm_with_tools(self, client, client_type, system_message, messages, tools, max_tokens=8192):
        """Call LLM with tools"""
        try:
            if client_type == "anthropic":
                return await self._call_anthropic_with_tools(client, system_message, messages, tools, max_tokens)
            elif client_type == "openai":
                return await self._call_openai_with_tools(client, system_message, messages, tools, max_tokens)
            else:
                raise ValueError(f"Unsupported client type: {client_type}")
        except Exception as e:
            self.logger.error(f"LLM call failed: {e}")
            raise
    
    async def _call_anthropic_with_tools(self, client, system_message, messages, tools, max_tokens):
        """Call Anthropic API"""
        validated_messages = self._validate_messages(messages)
        if not validated_messages:
            validated_messages = [{"role": "user", "content": "Please continue with analysis"}]
        
        try:
            response = await client.messages.create(
                model=self.default_models["anthropic"],
                system=system_message,
                messages=validated_messages,
                tools=tools,
                max_tokens=max_tokens,
                temperature=0.2,
            )
        except Exception as e:
            self.logger.error(f"Anthropic API call failed: {e}")
            raise
        
        content = ""
        tool_calls = []
        
        for block in response.content:
            if block.type == "text":
                content += block.text
            elif block.type == "tool_use":
                tool_calls.append({"id": block.id, "name": block.name, "input": block.input})
        
        return {"content": content, "tool_calls": tool_calls}
    
    async def _call_openai_with_tools(self, client, system_message, messages, tools, max_tokens):
        """Call OpenAI API"""
        # Convert tools to OpenAI format
        openai_tools = []
        for tool in tools:
            openai_tools.append({
                "type": "function",
                "function": {
                    "name": tool["name"],
                    "description": tool["description"],
                    "parameters": tool["input_schema"],
                },
            })
        
        validated_messages = self._validate_messages(messages)
        if not validated_messages:
            validated_messages = [{"role": "user", "content": "Please continue with analysis"}]
        
        # Add system message to the beginning
        full_messages = [{"role": "system", "content": system_message}] + validated_messages
        
        try:
            try:
                response = await client.chat.completions.create(
                    model=self.default_models["openai"],
                    messages=full_messages,
                    tools=openai_tools if openai_tools else None,
                    max_tokens=max_tokens,
                    temperature=0.2,
                )
            except Exception as e:
                if "max_tokens" in str(e) and "max_completion_tokens" in str(e):
                    response = await client.chat.completions.create(
                        model=self.default_models["openai"],
                        messages=full_messages,
                        tools=openai_tools if openai_tools else None,
                        max_completion_tokens=max_tokens,
                        temperature=0.2,
                    )
                else:
                    raise
        except Exception as e:
            self.logger.error(f"OpenAI API call failed: {e}")
            raise
        
        content = response.choices[0].message.content or ""
        tool_calls = []
        
        if response.choices[0].message.tool_calls:
            import json
            for tool_call in response.choices[0].message.tool_calls:
                tool_calls.append({
                    "id": tool_call.id,
                    "name": tool_call.function.name,
                    "input": json.loads(tool_call.function.arguments)
                })
        
        return {"content": content, "tool_calls": tool_calls}
    
    def _validate_messages(self, messages):
        """Validate and clean messages for LLM API"""
        validated = []
        for msg in messages:
            if msg.get("role") in ["user", "assistant"] and msg.get("content"):
                validated.append({
                    "role": msg["role"],
                    "content": str(msg["content"])
                })
        return validated


async def run_code_analyzer(
    paper_dir: str, logger, use_segmentation: bool = True
) -> str:
    """
    Run the adaptive code analysis workflow using multiple agents with multi-turn conversation workflows.

    This function orchestrates three specialized agents with individual multi-turn conversation workflows:
    - ConceptAnalysisAgent: Analyzes system architecture and conceptual framework
    - AlgorithmAnalysisAgent: Extracts algorithms, formulas, and technical details
    - CodePlannerAgent: Integrates outputs into a comprehensive implementation plan

    Each agent runs in its own multi-turn conversation workflow, similar to revision agent pattern.

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

    # Initialize multi-turn workflow controller
    workflow_controller = MultiTurnCodeAnalysisWorkflow(
        paper_dir=paper_dir,
        logger=logger,
        use_segmentation=use_segmentation,
        agent_config=agent_config,
        prompts=prompts
    )

    # Execute sequential multi-turn conversations: Phase 1 (parallel) + Phase 2 (sequential)
    result = await workflow_controller.execute_sequential_analysis_workflows()
    
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
                max_tokens=8192,  # Higher token limit for detailed plans
                temperature=0.2,  # Lower temperature for more structured output
            )

            print(
                f"🔄 Making LLM request with params: max_tokens={planning_params.max_tokens}, temperature={planning_params.temperature}"
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
