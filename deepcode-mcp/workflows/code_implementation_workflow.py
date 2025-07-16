"""
Paper Code Implementation Workflow - MCP-compliant Iterative Development

Features:
1. File Tree Creation
2. Code Implementation - Based on aisi-basic-agent iterative development

MCP Architecture:
- MCP Server: tools/code_implementation_server.py
- MCP Client: Called through mcp_agent framework
- Configuration: mcp_agent.config.yaml
"""

import asyncio
import json
import logging
import os
import sys
import time
import yaml
from pathlib import Path
from typing import Dict, Any, Optional, List

# MCP Agent imports
from mcp_agent.agents.agent import Agent
from mcp_agent.workflows.llm.augmented_llm_anthropic import AnthropicAugmentedLLM

# Local imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from prompts.code_prompts import STRUCTURE_GENERATOR_PROMPT
from prompts.code_prompts import PURE_CODE_IMPLEMENTATION_SYSTEM_PROMPT, GENERAL_CODE_IMPLEMENTATION_SYSTEM_PROMPT
from workflows.agents import CodeImplementationAgent, MemoryAgent
from workflows.agents.memory_agent_concise import ConciseMemoryAgent
from config.mcp_tool_definitions import get_mcp_tools
from utils.dialogue_logger import DialogueLogger, extract_paper_id_from_path


# os.environ['https_proxy'] = 'http://127.0.0.1:7890'
# os.environ['http_proxy'] = 'http://127.0.0.1:7890'

class CodeImplementationWorkflow:
    """
    Paper Code Implementation Workflow Manager
    
    Uses standard MCP architecture:
    1. Connect to code-implementation server via MCP client
    2. Use MCP protocol for tool calls
    3. Support workspace management and operation history tracking
    """
    
    # ==================== 1. Class Initialization and Configuration (Infrastructure Layer) ====================
    
    def __init__(self, config_path: str = "mcp_agent.secrets.yaml"):
        """Initialize workflow with configuration"""
        self.config_path = config_path
        self.api_config = self._load_api_config()
        self.logger = self._create_logger()
        self.mcp_agent = None
        self.dialogue_logger = None
        self.enable_read_tools = True  # Default value, will be overridden by run_workflow parameter

    def _load_api_config(self) -> Dict[str, Any]:
        """Load API configuration from YAML file"""
        try:
            with open(self.config_path, 'r', encoding='utf-8') as f:
                return yaml.safe_load(f)
        except Exception as e:
            raise Exception(f"Failed to load API config: {e}")

    def _create_logger(self) -> logging.Logger:
        """Create and configure logger"""
        logger = logging.getLogger(__name__)
        # Don't add handlers to child loggers - let them propagate to root
        logger.setLevel(logging.INFO)
        return logger

    def _read_plan_file(self, plan_file_path: str) -> str:
        """Read implementation plan file"""
        plan_path = Path(plan_file_path)
        if not plan_path.exists():
            raise FileNotFoundError(f"Implementation plan file not found: {plan_file_path}")
        
        with open(plan_path, 'r', encoding='utf-8') as f:
            return f.read()

    def _check_file_tree_exists(self, target_directory: str) -> bool:
        """Check if file tree structure already exists"""
        code_directory = os.path.join(target_directory, "generate_code")
        return os.path.exists(code_directory) and len(os.listdir(code_directory)) > 0

    # ==================== 2. Public Interface Methods (External API Layer) ====================

    async def run_workflow(self, plan_file_path: str, target_directory: Optional[str] = None, pure_code_mode: bool = False, enable_read_tools: bool = True):
        """Run complete workflow - Main public interface"""
        # Set the read tools configuration
        self.enable_read_tools = enable_read_tools
        
        # Initialize dialogue logger first (outside try block)
        paper_id = extract_paper_id_from_path(plan_file_path)
        self.dialogue_logger = DialogueLogger(paper_id,target_directory)
        
        try:
            plan_content = self._read_plan_file(plan_file_path)
            
            if target_directory is None:
                target_directory = str(Path(plan_file_path).parent)
            
            # Calculate code directory for workspace alignment
            code_directory = os.path.join(target_directory, "generate_code")
            
            self.logger.info("=" * 80)
            self.logger.info("🚀 STARTING CODE IMPLEMENTATION WORKFLOW")
            self.logger.info("=" * 80)
            self.logger.info(f"📄 Plan file: {plan_file_path}")
            self.logger.info(f"📂 Plan file parent: {target_directory}")
            self.logger.info(f"🎯 Code directory (MCP workspace): {code_directory}")
            self.logger.info(f"⚙️  Read tools: {'ENABLED' if self.enable_read_tools else 'DISABLED'}")
            self.logger.info("=" * 80)
            
            results = {}
            
            # Check if file tree exists
            if self._check_file_tree_exists(target_directory):
                self.logger.info("File tree exists, skipping creation")
                results["file_tree"] = "Already exists, skipped creation"
            else:
                self.logger.info("Creating file tree...")
                results["file_tree"] = await self.create_file_structure(plan_content, target_directory)
            
            # Code implementation
            if pure_code_mode:
                self.logger.info("Starting pure code implementation...")
                results["code_implementation"] = await self.implement_code_pure(plan_content, target_directory, code_directory)
            else:
                pass
            
            self.logger.info("Workflow execution successful")
            
            # Finalize dialogue logger
            if self.dialogue_logger:
                final_summary = f"Workflow completed successfully for paper {paper_id}. Results: {results}"
                self.dialogue_logger.finalize_session(final_summary)
            
            return {
                "status": "success",
                "plan_file": plan_file_path,
                "target_directory": target_directory,
                "code_directory": os.path.join(target_directory, "generate_code"),
                "results": results,
                "mcp_architecture": "standard"
            }
            
        except Exception as e:
            self.logger.error(f"Workflow execution failed: {e}")
            
            # Finalize dialogue logger with error information
            if self.dialogue_logger:
                error_summary = f"Workflow failed for paper {paper_id}. Error: {str(e)}"
                self.dialogue_logger.finalize_session(error_summary)
            
            return {"status": "error", "message": str(e), "plan_file": plan_file_path}
        finally:
            await self._cleanup_mcp_agent()

    async def create_file_structure(self, plan_content: str, target_directory: str) -> str:
        """Create file tree structure based on implementation plan"""
        self.logger.info("Starting file tree creation...")
        
        structure_agent = Agent(
            name="StructureGeneratorAgent",
            instruction=STRUCTURE_GENERATOR_PROMPT,
            server_names=["command-executor"],
        )
        
        async with structure_agent:
            creator = await structure_agent.attach_llm(AnthropicAugmentedLLM)
            
            message = f"""Analyze the following implementation plan and generate shell commands to create the file tree structure.

Target Directory: {target_directory}/generate_code

Implementation Plan:
{plan_content}

Tasks:
1. Find the file tree structure in the implementation plan
2. Generate shell commands (mkdir -p, touch) to create that structure
3. Use the execute_commands tool to run the commands and create the file structure

Requirements:
- Use mkdir -p to create directories
- Use touch to create files
- Include __init__.py file for Python packages
- Use relative paths to the target directory
- Execute commands to actually create the file structure"""
            
            result = await creator.generate_str(message=message)
            self.logger.info("File tree structure creation completed")
            return result

    async def implement_code_pure(self, plan_content: str, target_directory: str, code_directory: str = None) -> str:
        """Pure code implementation - focus on code writing without testing"""
        self.logger.info("Starting pure code implementation (no testing)...")
        
        # Use provided code_directory or calculate it (for backwards compatibility)
        if code_directory is None:
            code_directory = os.path.join(target_directory, "generate_code")
            
        self.logger.info(f"🎯 Using code directory (MCP workspace): {code_directory}")
        
        if not os.path.exists(code_directory):
            raise FileNotFoundError("File tree structure not found, please run file tree creation first")
        
        try:
            client, client_type = await self._initialize_llm_client()
            await self._initialize_mcp_agent(code_directory)
            
            tools = self._prepare_mcp_tool_definitions()
            system_message = GENERAL_CODE_IMPLEMENTATION_SYSTEM_PROMPT
            messages = []
            
#             implementation_message = f"""**TASK: Implement Research Paper Reproduction Code**

# You are implementing a complete, working codebase that reproduces the core algorithms, experiments, and methods described in a research paper. Your goal is to create functional code that can replicate the paper's key results and contributions.

# **What you need to do:**
# - Analyze the paper content and reproduction plan to understand requirements
# - Implement all core algorithms mentioned in the main body of the paper
# - Create the necessary components following the planned architecture
# - Test each component to ensure functionality
# - Integrate components into a cohesive, executable system
# - Focus on reproducing main contributions rather than appendix-only experiments

# **RESOURCES:**
# - **Paper & Reproduction Plan**: `{target_directory}/` (contains .md paper files and initial_plan.txt with detailed implementation guidance)
# - **Reference Code Indexes**: `{target_directory}/indexes/` (JSON files with implementation patterns from related codebases)
# - **Implementation Directory**: `{code_directory}/` (your working directory for all code files)

# **CURRENT OBJECTIVE:** 
# Start by reading the reproduction plan (`{target_directory}/initial_plan.txt`) to understand the implementation strategy, then examine the paper content to identify the first priority component to implement. Use the search_code tool to find relevant reference implementations from the indexes directory (`{target_directory}/indexes/*.json`) before coding.

# ---
# **START:** Review the plan above and begin implementation."""
            implementation_message = f"""**Task: Implement code based on the following reproduction plan**

**Code Reproduction Plan:**
{plan_content}

**Working Directory:** {code_directory}

**Current Objective:** Begin implementation by analyzing the plan structure, examining the current project layout, and implementing the first foundation file according to the plan's priority order."""
       
            
            messages.append({"role": "user", "content": implementation_message})
            
            result = await self._pure_code_implementation_loop(
                client, client_type, system_message, messages, tools, plan_content, target_directory
            )
            
            return result
            
        finally:
            await self._cleanup_mcp_agent()

    # ==================== 3. Core Business Logic (Implementation Layer) ====================

    async def _pure_code_implementation_loop(self, client, client_type, system_message, messages, tools, plan_content, target_directory):
        """Pure code implementation loop with memory optimization and phase consistency"""
        max_iterations = 100
        iteration = 0
        start_time = time.time()
        max_time = 2400  # 40 minutes
        
        # Sliding window configuration
        WINDOW_SIZE = 1
        SUMMARY_TRIGGER = 8
        
        # Initialize specialized agents
        code_agent = CodeImplementationAgent(self.mcp_agent, self.logger, self.enable_read_tools)
        memory_agent = ConciseMemoryAgent(plan_content, self.logger, target_directory)
        
        # Log read tools configuration
        read_tools_status = "ENABLED" if self.enable_read_tools else "DISABLED"
        self.logger.info(f"🔧 Read tools (read_file, read_code_mem): {read_tools_status}")
        if not self.enable_read_tools:
            self.logger.info("🚫 No read mode: read_file and read_code_mem tools will be skipped")
        
        # Connect code agent with memory agent for summary generation
        # Note: Concise memory agent doesn't need LLM client for summary generation
        code_agent.set_memory_agent(memory_agent, client, client_type)
        
        # Initialize memory agent with iteration 0
        memory_agent.start_new_round(iteration=0)
        
        # Preserve initial plan (never compressed)
        initial_plan_message = messages[0] if messages else None
        
        # Log initial system prompt if dialogue logger is available
        if self.dialogue_logger and system_message:
            self.dialogue_logger.log_complete_exchange(
                system_prompt=system_message,
                user_message=initial_plan_message['content'] if initial_plan_message else "",
                round_type="initialization",
                context={"max_iterations": max_iterations, "max_time": max_time},
                summary="Initial workflow setup and system prompt configuration"
            )
        
        while iteration < max_iterations:
            iteration += 1
            elapsed_time = time.time() - start_time
            
            if elapsed_time > max_time:
                self.logger.warning(f"Time limit reached: {elapsed_time:.2f}s")
                break
            
            # # Test simplified memory approach if we have files implemented
            # if iteration == 5 and code_agent.get_files_implemented_count() > 0:
            #     self.logger.info("🧪 Testing simplified memory approach...")
            #     test_results = await memory_agent.test_simplified_memory_approach()
            #     self.logger.info(f"Memory test results: {test_results}")
            
            # self.logger.info(f"Pure code implementation iteration {iteration}: generating code")
            
            messages = self._validate_messages(messages)
            current_system_message = code_agent.get_system_prompt()
            
            # Start logging round if dialogue logger is available
            if self.dialogue_logger:
                context = {
                    "iteration": iteration,
                    "elapsed_time": time.time() - start_time,
                    "files_implemented": code_agent.get_files_implemented_count(),
                    "message_count": len(messages)
                }
                self.dialogue_logger.start_new_round("implementation", context)
                
                # Log system prompt for this round
                self.dialogue_logger.log_system_prompt(current_system_message, "implementation_system")
                
                # Log the last user message if available
                if messages and messages[-1].get("role") == "user":
                    self.dialogue_logger.log_user_message(messages[-1]["content"], "implementation_guidance")
            
            # Call LLM
            response = await self._call_llm_with_tools(
                client, client_type, current_system_message, messages, tools
            )
            
            response_content = response.get("content", "").strip()
            if not response_content:
                response_content = "Continue implementing code files..."
            
            messages.append({"role": "assistant", "content": response_content})
            
            # Log assistant response
            if self.dialogue_logger:
                self.dialogue_logger.log_assistant_response(response_content, "implementation_response")
            
            # Handle tool calls
            if response.get("tool_calls"):
                # Log tool calls
                if self.dialogue_logger:
                    self.dialogue_logger.log_tool_calls(response["tool_calls"])
                
                tool_results = await code_agent.execute_tool_calls(response["tool_calls"])
                
                # Record essential tool results in concise memory agent
                for tool_call, tool_result in zip(response["tool_calls"], tool_results):
                    memory_agent.record_tool_result(
                        tool_name=tool_call["name"],
                        tool_input=tool_call["input"],
                        tool_result=tool_result.get("result")
                    )
                
                # NEW LOGIC: Check if write_file was called and trigger memory optimization immediately
                write_file_detected = any(tool_call["name"] == "write_file" for tool_call in response["tool_calls"])
                # if write_file_detected:
                #     self.logger.info(f"🔄 write_file detected - preparing memory optimization for next round")
                
                # Log tool results
                if self.dialogue_logger:
                    self.dialogue_logger.log_tool_results(tool_results)
                
                # Determine guidance based on results
                has_error = self._check_tool_results_for_errors(tool_results)
                files_count = code_agent.get_files_implemented_count()
                
                if has_error:
                    guidance = self._generate_error_guidance()
                else:
                    guidance = self._generate_success_guidance(files_count)
                
                compiled_response = self._compile_user_response(tool_results, guidance)
                messages.append({"role": "user", "content": compiled_response})
                
                # Log the compiled user response
                if self.dialogue_logger:
                    self.dialogue_logger.log_user_message(compiled_response, "tool_results_feedback")
                
                # NEW LOGIC: Apply memory optimization immediately after write_file detection
                if memory_agent.should_trigger_memory_optimization(messages, code_agent.get_files_implemented_count()):
                    # Capture messages before optimization
                    messages_before_optimization = messages.copy()
                    messages_before_count = len(messages)
                    
                    # Log memory optimization round
                    if self.dialogue_logger:
                        memory_context = {
                            "trigger_reason": "write_file_detected",
                            "message_count_before": len(messages),
                            "files_implemented": code_agent.get_files_implemented_count(),
                            "approach": "clear_after_write_file"
                        }
                        self.dialogue_logger.start_new_round("memory_optimization", memory_context)
                    
                    # Apply concise memory optimization
                    files_implemented_count = code_agent.get_files_implemented_count()
                    current_system_message = code_agent.get_system_prompt()
                    messages = memory_agent.apply_memory_optimization(current_system_message, messages, files_implemented_count)
                    messages_after_count = len(messages)
                    
                    compression_ratio = (messages_before_count - messages_after_count) / messages_before_count * 100 if messages_before_count > 0 else 0
                    
                    # Log memory optimization with detailed content
                    if self.dialogue_logger:
                        memory_stats = memory_agent.get_memory_statistics(files_implemented_count)
                        
                        # Log the detailed memory optimization including message content
                        self.dialogue_logger.log_memory_optimization(
                            messages_before=messages_before_optimization,
                            messages_after=messages,
                            optimization_stats=memory_stats,
                            approach="clear_after_write_file"
                        )
                        
                        # Log additional metadata
                        self.dialogue_logger.log_metadata("compression_ratio", f"{compression_ratio:.1f}%")
                        self.dialogue_logger.log_metadata("messages_before", messages_before_count)
                        self.dialogue_logger.log_metadata("messages_after", messages_after_count)
                        self.dialogue_logger.log_metadata("approach", "clear_after_write_file")
                        
                        memory_round_summary = f"IMMEDIATE memory optimization after write_file. " + \
                                              f"Messages: {messages_before_count} → {messages_after_count}, " + \
                                              f"Files tracked: {memory_stats['implemented_files_tracked']}"
                        self.dialogue_logger.complete_round(memory_round_summary)
                
            else:
                files_count = code_agent.get_files_implemented_count()
                no_tools_guidance = self._generate_no_tools_guidance(files_count)
                messages.append({"role": "user", "content": no_tools_guidance})
                
                # Log the no tools guidance
                if self.dialogue_logger:
                    self.dialogue_logger.log_user_message(no_tools_guidance, "no_tools_guidance")
            
            # Check for analysis loop and provide corrective guidance
            if code_agent.is_in_analysis_loop():
                analysis_loop_guidance = code_agent.get_analysis_loop_guidance()
                messages.append({"role": "user", "content": analysis_loop_guidance})
                self.logger.warning(f"Analysis loop detected and corrective guidance provided")
                
                # Log analysis loop detection
                if self.dialogue_logger:
                    self.dialogue_logger.log_user_message(analysis_loop_guidance, "analysis_loop_correction")
            
            # Complete the round with summary
            if self.dialogue_logger:
                files_count = code_agent.get_files_implemented_count()
                round_summary = f"Iteration {iteration} completed. Files implemented: {files_count}. " + \
                               f"Tool calls: {len(response.get('tool_calls', []))}. " + \
                               f"Response length: {len(response_content)} chars."
                self.dialogue_logger.log_metadata("files_implemented", files_count)
                self.dialogue_logger.log_metadata("tool_calls_count", len(response.get('tool_calls', [])))
                self.dialogue_logger.log_metadata("response_length", len(response_content))
                self.dialogue_logger.complete_round(round_summary)
            
            # # Test summary functionality after every 10 iterations (reduced frequency)
            # if iteration % 10 == 0 and code_agent.get_files_implemented_count() > 0:
            #     self.logger.info(f"🧪 Testing summary functionality at iteration {iteration}")
            #     optimization_success = await code_agent.test_summary_optimization()
            #     if optimization_success:
            #         self.logger.info("✅ Summary optimization working correctly")
            #     else:
            #         self.logger.warning("⚠️ Summary optimization may not be working")
            
            # Update memory agent state with current file implementations
            files_implemented = code_agent.get_files_implemented_count()
            # memory_agent.sync_with_code_agent(files_implemented)
            
            # Record file implementations in memory agent (for the current round)
            for file_info in code_agent.get_implementation_summary()["completed_files"]:
                memory_agent.record_file_implementation(file_info["file"])
            
            # REMOVED: Old memory optimization logic - now happens immediately after write_file
            # Memory optimization is now triggered immediately after write_file detection
            
            # Start new round for next iteration, sync with workflow iteration
            memory_agent.start_new_round(iteration=iteration)
            
            # Check completion
            if any(keyword in response_content.lower() for keyword in [
                "all files implemented", 
                "implementation complete", 
                "all phases completed",
                "reproduction plan fully implemented"
            ]):
                self.logger.info("Code implementation declared complete")
                
                # Log completion
                if self.dialogue_logger:
                    completion_context = {
                        "completion_reason": "implementation_complete",
                        "final_files_count": code_agent.get_files_implemented_count(),
                        "total_iterations": iteration,
                        "total_time": time.time() - start_time
                    }
                    self.dialogue_logger.log_complete_exchange(
                        user_message="Implementation completion detected",
                        assistant_response=response_content,
                        round_type="completion",
                        context=completion_context,
                        summary="Implementation workflow completed successfully"
                    )
                break
            
            # Emergency trim if too long
            if len(messages) > 50:
                self.logger.warning("Emergency message trim - applying concise memory optimization")
                
                # Capture messages before emergency optimization
                messages_before_emergency = messages.copy()
                messages_before_count = len(messages)
                
                # Log emergency memory optimization
                if self.dialogue_logger:
                    emergency_context = {
                        "trigger_reason": "emergency_trim",
                        "message_count_before": len(messages),
                        "files_implemented": code_agent.get_files_implemented_count(),
                        "approach": "emergency_memory_optimization"
                    }
                    self.dialogue_logger.start_new_round("emergency_memory_optimization", emergency_context)
                
                # Apply emergency memory optimization
                current_system_message = code_agent.get_system_prompt()
                files_implemented_count = code_agent.get_files_implemented_count()
                messages = memory_agent.apply_memory_optimization(current_system_message, messages, files_implemented_count)
                messages_after_count = len(messages)
                
                # Log emergency optimization details
                if self.dialogue_logger:
                    memory_stats = memory_agent.get_memory_statistics(files_implemented_count)
                    
                    # Log the detailed emergency memory optimization
                    self.dialogue_logger.log_memory_optimization(
                        messages_before=messages_before_emergency,
                        messages_after=messages,
                        optimization_stats=memory_stats,
                        approach="emergency_memory_optimization"
                    )
                    
                    emergency_summary = f"Emergency memory optimization triggered. " + \
                                       f"Messages: {messages_before_count} → {messages_after_count}"
                    self.dialogue_logger.complete_round(emergency_summary)
        
        return await self._generate_pure_code_final_report_with_concise_agents(
            iteration, time.time() - start_time, code_agent, memory_agent
        )

    # ==================== 4. MCP Agent and LLM Communication Management (Communication Layer) ====================

    async def _initialize_mcp_agent(self, code_directory: str):
        """Initialize MCP agent and connect to code-implementation server"""
        try:
            self.mcp_agent = Agent(
                name="CodeImplementationAgent",
                instruction="You are a code implementation assistant, using MCP tools to implement paper code replication.",
                server_names=["code-implementation", "code-reference-indexer"],
            )
            
            await self.mcp_agent.__aenter__()
            llm = await self.mcp_agent.attach_llm(AnthropicAugmentedLLM)
            
            # Set workspace to the target code directory
            workspace_result = await self.mcp_agent.call_tool(
                "set_workspace", 
                {"workspace_path": code_directory}
            )
            self.logger.info(f"Workspace setup result: {workspace_result}")
            
            return llm
                
        except Exception as e:
            self.logger.error(f"Failed to initialize MCP agent: {e}")
            if self.mcp_agent:
                try:
                    await self.mcp_agent.__aexit__(None, None, None)
                except:
                    pass
                self.mcp_agent = None
            raise

    async def _cleanup_mcp_agent(self):
        """Clean up MCP agent resources"""
        if self.mcp_agent:
            try:
                await self.mcp_agent.__aexit__(None, None, None)
                self.logger.info("MCP agent connection closed")
            except Exception as e:
                self.logger.warning(f"Error closing MCP agent: {e}")
            finally:
                self.mcp_agent = None

    async def _initialize_llm_client(self):
        """Initialize LLM client (Anthropic or OpenAI)"""
        # Try Anthropic API first
        try:
            anthropic_key = self.api_config.get('anthropic', {}).get('api_key')
            if anthropic_key:
                from anthropic import AsyncAnthropic
                client = AsyncAnthropic(api_key=anthropic_key)
                # Test connection
                await client.messages.create(
                    model="claude-sonnet-4-20250514",
                    # model="claude-3-5-sonnet-20241022",
                    max_tokens=10,
                    messages=[{"role": "user", "content": "test"}]
                )
                self.logger.info("Using Anthropic API")
                return client, "anthropic"
        except Exception as e:
            self.logger.warning(f"Anthropic API unavailable: {e}")
        
        # Try OpenAI API
        try:
            openai_key = self.api_config.get('openai', {}).get('api_key')
            if openai_key:
                from openai import AsyncOpenAI
                client = AsyncOpenAI(api_key=openai_key)
                # Test connection
                await client.chat.completions.create(
                    model="gpt-3.5-turbo",
                    max_tokens=10,
                    messages=[{"role": "user", "content": "test"}]
                )
                self.logger.info("Using OpenAI API")
                return client, "openai"
        except Exception as e:
            self.logger.warning(f"OpenAI API unavailable: {e}")
        
        raise ValueError("No available LLM API")

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
            validated_messages = [{"role": "user", "content": "Please continue implementing code"}]
        
        try:
            response = await client.messages.create(
                model="claude-sonnet-4-20250514",
                # model="claude-3-5-sonnet-20241022",
                system=system_message,
                messages=validated_messages,
                tools=tools,
                max_tokens=max_tokens,
                temperature=0.2
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
                tool_calls.append({
                    "id": block.id,
                    "name": block.name,
                    "input": block.input
                })
        
        return {"content": content, "tool_calls": tool_calls}

    async def _call_openai_with_tools(self, client, system_message, messages, tools, max_tokens):
        """Call OpenAI API"""
        openai_tools = []
        for tool in tools:
            openai_tools.append({
                "type": "function",
                "function": {
                    "name": tool["name"],
                    "description": tool["description"],
                    "parameters": tool["input_schema"]
                }
            })
        
        openai_messages = [{"role": "system", "content": system_message}]
        openai_messages.extend(messages)
        
        response = await client.chat.completions.create(
            model="gpt-4-1106-preview",
            messages=openai_messages,
            tools=openai_tools if openai_tools else None,
            max_tokens=max_tokens,
            temperature=0.2
        )
        
        message = response.choices[0].message
        content = message.content or ""
        
        tool_calls = []
        if message.tool_calls:
            for tool_call in message.tool_calls:
                tool_calls.append({
                    "id": tool_call.id,
                    "name": tool_call.function.name,
                    "input": json.loads(tool_call.function.arguments)
                })
        
        return {"content": content, "tool_calls": tool_calls}

    # ==================== 5. Tools and Utility Methods (Utility Layer) ====================

    def _validate_messages(self, messages: List[Dict]) -> List[Dict]:
        """Validate and clean message list"""
        valid_messages = []
        for msg in messages:
            content = msg.get("content", "").strip()
            if content:
                valid_messages.append({
                    "role": msg.get("role", "user"),
                    "content": content
                })
            else:
                self.logger.warning(f"Skipping empty message: {msg}")
        return valid_messages

    def _prepare_mcp_tool_definitions(self) -> List[Dict[str, Any]]:
        """Prepare tool definitions in Anthropic API standard format"""
        return get_mcp_tools("code_implementation")

    def _check_tool_results_for_errors(self, tool_results: List[Dict]) -> bool:
        """Check tool results for errors"""
        for result in tool_results:
            try:
                if hasattr(result['result'], 'content') and result['result'].content:
                    content_text = result['result'].content[0].text
                    parsed_result = json.loads(content_text)
                    if parsed_result.get('status') == 'error':
                        return True
                elif isinstance(result['result'], str):
                    if "error" in result['result'].lower():
                        return True
            except (json.JSONDecodeError, AttributeError, IndexError):
                result_str = str(result['result'])
                if "error" in result_str.lower():
                    return True
        return False

    # ==================== 6. User Interaction and Feedback (Interaction Layer) ====================

    def _generate_success_guidance(self, files_count: int) -> str:
        """Generate concise success guidance for continuing implementation"""
        return f"""✅ File implementation completed successfully! 

📊 **Progress Status:** {files_count} files implemented

🎯 **Next Action:** Continue with dependency-aware implementation workflow.

⚡ **Development Cycle for Next File:**
1. **➡️ FIRST: Call `read_code_mem`** to understand existing implementations and dependencies
2. **Then: `write_file`** to implement the new component
3. **Finally: Test** if needed

💡 **Key Point:** Always start with `read_code_mem` to query summaries of ALREADY IMPLEMENTED files before creating new ones."""

    def _generate_error_guidance(self) -> str:
        """Generate error guidance for handling issues"""
        return """❌ Error detected during file implementation.

🔧 **Action Required:**
1. Review the error details above
2. Fix the identified issue
3. Continue with proper development cycle for next file:
   - **Start with `read_code_mem`** to understand existing implementations
   - **Then `write_file`** to implement properly
   - **Test** if needed
4. Ensure proper error handling in future implementations

💡 **Remember:** Always begin with `read_code_mem` to query summaries of ALREADY IMPLEMENTED files."""

    def _generate_no_tools_guidance(self, files_count: int) -> str:
        """Generate concise guidance when no tools are called"""
        return f"""⚠️ No tool calls detected in your response.

📊 **Current Progress:** {files_count} files implemented

🚨 **Action Required:** You must use tools to implement the next file. Follow the development cycle:

⚡ **Development Cycle - START HERE:**
1. **➡️ FIRST: Call `read_code_mem`** to understand existing implementations
2. **Then: `write_file`** to implement the new component
3. **Finally: Test** if needed

🚨 **Critical:** Start with `read_code_mem` to query summaries of ALREADY IMPLEMENTED files, then use `write_file` to implement - not just explanations!"""

    def _compile_user_response(self, tool_results: List[Dict], guidance: str) -> str:
        """Compile tool results and guidance into a single user response"""
        response_parts = []
        
        if tool_results:
            response_parts.append("🔧 **Tool Execution Results:**")
            for tool_result in tool_results:
                tool_name = tool_result['tool_name']
                result_content = tool_result['result']
                response_parts.append(f"```\nTool: {tool_name}\nResult: {result_content}\n```")
        
        if guidance:
            response_parts.append("\n" + guidance)
        
        return "\n\n".join(response_parts)

    # ==================== 7. Reporting and Output (Output Layer) ====================

    async def _generate_pure_code_final_report_with_concise_agents(
        self, 
        iterations: int, 
        elapsed_time: float, 
        code_agent: CodeImplementationAgent, 
        memory_agent: ConciseMemoryAgent
    ):
        """Generate final report using concise agent statistics"""
        try:
            code_stats = code_agent.get_implementation_statistics()
            memory_stats = memory_agent.get_memory_statistics(code_stats['files_implemented_count'])
            
            if self.mcp_agent:
                history_result = await self.mcp_agent.call_tool("get_operation_history", {"last_n": 30})
                history_data = json.loads(history_result) if isinstance(history_result, str) else history_result
            else:
                history_data = {"total_operations": 0, "history": []}
            
            write_operations = 0
            files_created = []
            if "history" in history_data:
                for item in history_data["history"]:
                    if item.get("action") == "write_file":
                        write_operations += 1
                        file_path = item.get("details", {}).get("file_path", "unknown")
                        files_created.append(file_path)
            
            report = f"""
# Pure Code Implementation Completion Report (Write-File-Based Memory Mode)

## Execution Summary
- Implementation iterations: {iterations}
- Total elapsed time: {elapsed_time:.2f} seconds
- Files implemented: {code_stats['total_files_implemented']}
- File write operations: {write_operations}
- Total MCP operations: {history_data.get('total_operations', 0)}

## Read Tools Configuration
- Read tools enabled: {code_stats['read_tools_status']['read_tools_enabled']}
- Status: {code_stats['read_tools_status']['status']}
- Tools affected: {', '.join(code_stats['read_tools_status']['tools_affected'])}

## Agent Performance
### Code Implementation Agent
- Files tracked: {code_stats['files_implemented_count']}
- Technical decisions: {code_stats['technical_decisions_count']}
- Constraints tracked: {code_stats['constraints_count']}
- Architecture notes: {code_stats['architecture_notes_count']}
- Dependency analysis performed: {code_stats['dependency_analysis_count']}
- Files read for dependencies: {code_stats['files_read_for_dependencies']}
- Last summary triggered at file count: {code_stats['last_summary_file_count']}

### Concise Memory Agent (Write-File-Based)
- Last write_file detected: {memory_stats['last_write_file_detected']}
- Should clear memory next: {memory_stats['should_clear_memory_next']}
- Files implemented count: {memory_stats['implemented_files_tracked']}
- Current round: {memory_stats['current_round']}
- Concise mode active: {memory_stats['concise_mode_active']}
- Current round tool results: {memory_stats['current_round_tool_results']}
- Essential tools recorded: {memory_stats['essential_tools_recorded']}

## Files Created
"""
            for file_path in files_created[-20:]:
                report += f"- {file_path}\n"
            
            if len(files_created) > 20:
                report += f"... and {len(files_created) - 20} more files\n"
            
            report += """
## Architecture Features
✅ WRITE-FILE-BASED Memory Agent - Clear after each file generation
✅ After write_file: Clear history → Keep system prompt + initial plan + tool results
✅ Tool accumulation: read_code_mem, read_file, search_reference_code until next write_file
✅ Clean memory cycle: write_file → clear → accumulate → write_file → clear
✅ Essential tool recording with write_file detection
✅ Specialized agent separation for clean code organization
✅ MCP-compliant tool execution
✅ Production-grade code with comprehensive type hints
✅ Intelligent dependency analysis and file reading
✅ Automated read_file usage for implementation context
✅ Eliminates conversation clutter between file generations
✅ Focused memory for efficient next file generation
"""
            return report
            
        except Exception as e:
            self.logger.error(f"Failed to generate final report: {e}")
            return f"Failed to generate final report: {str(e)}"

    # ==================== 8. Testing and Debugging (Testing Layer) ====================

    async def test_code_reference_indexer(self):
        """Test code reference indexer integration"""
        self.logger.info("=" * 60)
        self.logger.info("TESTING CODE REFERENCE INDEXER INTEGRATION")
        self.logger.info("=" * 60)
        
        try:
            # Initialize MCP agent with code reference indexer
            test_directory = "test_workspace"
            await self._initialize_mcp_agent(test_directory)
            
            # if not self.mcp_agent:
            #     self.logger.error("Failed to initialize MCP agent")
            #     return False
            
            # Test 1: Get indexes overview with new unified approach
            self.logger.info("\n📁 Test 1: Getting indexes overview...")
            indexes_path = "/Users/lizongwei/Desktop/LLM_research/Code-Agent/deepcode-mcp/deepcode_lab/papers/1/indexes"
            # indexes_path = "/data2/bjdwhzzh/project-hku/Code-Agent2.0/Code-Agent/deepcode-mcp/agent_folders/papers/1/indexes"  
            # try:
            #     overview_result = await self.mcp_agent.call_tool(
            #         "get_indexes_overview", 
            #         {"indexes_path": indexes_path}
            #     )
            #     self.logger.info(f"✅ get_indexes_overview result: {overview_result}")
            # except Exception as e:
            #     self.logger.error(f"❌ get_indexes_overview failed: {e}")
            
            # # Test 2: Search reference code with unified tool (combines all three previous steps)
            # self.logger.info("\n🔍 Test 2: Searching reference code with unified tool...")
            try:
                search_result = await self.mcp_agent.call_tool(
                    "search_code_references", 
                    {
                        "indexes_path": indexes_path,
                        "target_file": "models/transformer.py",
                        "keywords": "transformer,attention,pytorch",
                        "max_results": 5
                    }
                )
                self.logger.info(f"✅ search_code_references result length: {len(str(search_result))}")
                
                # Parse and display summary
                if isinstance(search_result, str):
                    import json
                    try:
                        parsed_result = json.loads(search_result)
                        self.logger.info(f"📊 Unified Search Summary:")
                        self.logger.info(f"  - Status: {parsed_result.get('status', 'unknown')}")
                        self.logger.info(f"  - Target File: {parsed_result.get('target_file', 'unknown')}")
                        self.logger.info(f"  - Indexes Path: {parsed_result.get('indexes_path', 'unknown')}")
                        self.logger.info(f"  - References Found: {parsed_result.get('total_references_found', 0)}")
                        self.logger.info(f"  - Relationships Found: {parsed_result.get('total_relationships_found', 0)}")
                        self.logger.info(f"  - Indexes Loaded: {parsed_result.get('indexes_loaded', [])}")
                        self.logger.info(f"  - Total Indexes: {parsed_result.get('total_indexes_loaded', 0)}")
                    except json.JSONDecodeError:
                        self.logger.info(f"Raw result preview: {str(search_result)[:200]}...")
                        
            except Exception as e:
                self.logger.error(f"❌ search_code_references failed: {e}")
            
            # Test 3: Check MCP tool definitions for new unified tools
            self.logger.info("\n🛠️ Test 3: Checking MCP tool definitions...")
            try:
                from config.mcp_tool_definitions import get_mcp_tools
                tools = get_mcp_tools("code_implementation")
                reference_tools = [tool for tool in tools if any(keyword in tool['name'] for keyword in ['reference', 'indexes', 'code_references'])]
                self.logger.info(f"✅ Reference tools found: {len(reference_tools)}")
                for tool in reference_tools:
                    self.logger.info(f"  - {tool['name']}: {tool['description'][:100]}...")
                    # Show unified tool parameters
                    if tool['name'] == 'search_code_references':
                        required_params = tool['input_schema']['required']
                        self.logger.info(f"    Required parameters: {required_params}")
            except Exception as e:
                self.logger.error(f"❌ Tool definitions check failed: {e}")
            
            self.logger.info("\n" + "=" * 60)
            self.logger.info("✅ UNIFIED CODE REFERENCE INDEXER TESTING COMPLETED")
            self.logger.info("🔧 New unified approach: One tool call instead of three")
            self.logger.info("📋 Tools tested: get_indexes_overview, search_code_references")
            self.logger.info("=" * 60)
            
            return True
            
        except Exception as e:
            self.logger.error(f"❌ Test failed with error: {e}")
            return False
        finally:
            await self._cleanup_mcp_agent()


# ==================== 9. Program Entry Point (Entry Layer) ====================

async def main():
    """Main function for running the workflow"""
    # Configure root logger carefully to avoid duplicates
    root_logger = logging.getLogger()
    if not root_logger.handlers:
        handler = logging.StreamHandler()
        formatter = logging.Formatter('%(levelname)s:%(name)s:%(message)s')
        handler.setFormatter(formatter)
        root_logger.addHandler(handler)
        root_logger.setLevel(logging.INFO)
    
    workflow = CodeImplementationWorkflow()
    
    print("=" * 60)
    print("Code Implementation Workflow with UNIFIED Reference Indexer")
    print("=" * 60)
    print("Select mode:")
    print("1. Test Code Reference Indexer Integration")
    print("2. Run Full Implementation Workflow")
    print("3. Run Implementation with Pure Code Mode")
    print("4. Test Read Tools Configuration")
    
    # mode_choice = input("Enter choice (1-4, default: 3): ").strip()
    
    # For testing purposes, we'll run the test first
    # if mode_choice == "4":
    #     print("Testing Read Tools Configuration...")
        
    #     # Create a test workflow normally
    #     test_workflow = CodeImplementationWorkflow()
        
    #     # Create a mock code agent for testing
    #     print("\n🧪 Testing with read tools DISABLED:")
    #     test_agent_disabled = CodeImplementationAgent(None, enable_read_tools=False)
    #     await test_agent_disabled.test_read_tools_configuration()
        
    #     print("\n🧪 Testing with read tools ENABLED:")
    #     test_agent_enabled = CodeImplementationAgent(None, enable_read_tools=True)
    #     await test_agent_enabled.test_read_tools_configuration()
        
    #     print("✅ Read tools configuration testing completed!")
    #     return
    
    # print("Running Code Reference Indexer Integration Test...")
    test_success = await workflow.test_code_reference_indexer()
    test_success = True
    if test_success:
        print("\n" + "=" * 60)
        print("🎉 UNIFIED Code Reference Indexer Integration Test PASSED!")
        print("🔧 Three-step process successfully merged into ONE tool")
        print("=" * 60)
        
        # Ask if user wants to continue with actual workflow
        print("\nContinuing with workflow execution...")
        
        plan_file = "/Users/lizongwei/Desktop/LLM_research/Code-Agent/deepcode-mcp/deepcode_lab/papers/5/initial_plan.txt"
        # plan_file = "/data2/bjdwhzzh/project-hku/Code-Agent2.0/Code-Agent/deepcode-mcp/agent_folders/papers/1/initial_plan.txt"
        target_directory = "/Users/lizongwei/Desktop/LLM_research/Code-Agent/deepcode-mcp/deepcode_lab/papers/5/"
        print("Implementation Mode Selection:")
        print("1. Pure Code Implementation Mode (Recommended)")
        print("2. Iterative Implementation Mode")
        
        pure_code_mode = True
        mode_name = "Pure Code Implementation Mode with Memory Agent Architecture + Code Reference Indexer"
        print(f"Using: {mode_name}")
        
        # Configure read tools - modify this parameter to enable/disable read tools
        enable_read_tools = True  # Set to False to disable read_file and read_code_mem tools
        read_tools_status = "ENABLED" if enable_read_tools else "DISABLED"
        print(f"🔧 Read tools (read_file, read_code_mem): {read_tools_status}")
        
        # NOTE: To test without read tools, change the line above to:
        # enable_read_tools = False
        
        result = await workflow.run_workflow(plan_file, target_directory=target_directory, pure_code_mode=pure_code_mode, enable_read_tools=enable_read_tools)
        
        print("=" * 60)
        print("Workflow Execution Results:")
        print(f"Status: {result['status']}")
        print(f"Mode: {mode_name}")
        
        if result['status'] == 'success':
            print(f"Code Directory: {result['code_directory']}")
            print(f"MCP Architecture: {result.get('mcp_architecture', 'unknown')}")
            print("Execution completed!")
        else:
            print(f"Error Message: {result['message']}")
        
        print("=" * 60)
        print("✅ Using Standard MCP Architecture with Memory Agent + Code Reference Indexer")
        
    else:
        print("\n" + "=" * 60)
        print("❌ Code Reference Indexer Integration Test FAILED!")
        print("Please check the configuration and try again.")
        print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
