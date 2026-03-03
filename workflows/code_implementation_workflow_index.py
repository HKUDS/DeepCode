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
from pathlib import Path
from typing import Dict, Any, Optional, List

# MCP Agent imports
from mcp_agent.agents.agent import Agent

# Local imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from prompts.code_prompts import STRUCTURE_GENERATOR_PROMPT
from prompts.code_prompts import (
    PURE_CODE_IMPLEMENTATION_SYSTEM_PROMPT_INDEX,
)
from workflows.agents import CodeImplementationAgent
from workflows.agents.memory_agent_concise import ConciseMemoryAgent
from config.mcp_tool_definitions_index import get_mcp_tools
from utils.llm_utils import get_preferred_llm_class, get_default_models, load_api_config
# DialogueLogger removed - no longer needed


class CodeImplementationWorkflowWithIndex:
    """
    Paper Code Implementation Workflow Manager with Code Reference Indexer

    Uses standard MCP architecture with enhanced indexing capabilities:
    1. Connect to code-implementation server via MCP client
    2. Use MCP protocol for tool calls
    3. Support workspace management and operation history tracking
    4. Integrated code reference indexer for enhanced code understanding
    """

    # ==================== 1. Class Initialization and Configuration (Infrastructure Layer) ====================

    def __init__(self, config_path: str = "mcp_agent.secrets.yaml"):
        """Initialize workflow with configuration"""
        self.config_path = config_path
        # Derive main config path from secrets path (same directory)
        secrets_dir = os.path.dirname(os.path.abspath(config_path))
        self.main_config_path = os.path.join(secrets_dir, "mcp_agent.config.yaml")
        self.api_config = self._load_api_config()
        self.default_models = get_default_models(self.main_config_path)
        self.logger = self._create_logger()
        self.mcp_agent = None
        self.enable_read_tools = (
            True  # Default value, will be overridden by run_workflow parameter
        )

    def _load_api_config(self) -> Dict[str, Any]:
        """Load API configuration with environment variable override."""
        try:
            return load_api_config(self.config_path)
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
            raise FileNotFoundError(
                f"Implementation plan file not found: {plan_file_path}"
            )

        with open(plan_path, "r", encoding="utf-8") as f:
            return f.read()

    def _check_file_tree_exists(self, target_directory: str) -> bool:
        """Check if file tree structure already exists"""
        code_directory = os.path.join(target_directory, "generate_code")
        return os.path.exists(code_directory) and len(os.listdir(code_directory)) > 0

    # ==================== 2. Public Interface Methods (External API Layer) ====================

    async def run_workflow(
        self,
        plan_file_path: str,
        target_directory: Optional[str] = None,
        pure_code_mode: bool = False,
        enable_read_tools: bool = True,
    ):
        """Run complete workflow - Main public interface"""
        # Set the read tools configuration
        self.enable_read_tools = enable_read_tools

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
            self.logger.info(
                f"⚙️  Read tools: {'ENABLED' if self.enable_read_tools else 'DISABLED'}"
            )
            self.logger.info("=" * 80)

            results = {}

            # Check if file tree exists
            if self._check_file_tree_exists(target_directory):
                self.logger.info("File tree exists, skipping creation")
                results["file_tree"] = "Already exists, skipped creation"
            else:
                self.logger.info("Creating file tree...")
                results["file_tree"] = await self.create_file_structure(
                    plan_content, target_directory
                )

            # Code implementation
            if pure_code_mode:
                self.logger.info("Starting pure code implementation...")
                results["code_implementation"] = await self.implement_code_pure(
                    plan_content, target_directory, code_directory
                )
            else:
                pass

            self.logger.info("Workflow execution successful")

            return {
                "status": "success",
                "plan_file": plan_file_path,
                "target_directory": target_directory,
                "code_directory": os.path.join(target_directory, "generate_code"),
                "results": results,
                "mcp_architecture": "standard",
            }

        except Exception as e:
            self.logger.error(f"Workflow execution failed: {e}")

            return {"status": "error", "message": str(e), "plan_file": plan_file_path}
        finally:
            await self._cleanup_mcp_agent()

    async def create_file_structure(
        self, plan_content: str, target_directory: str
    ) -> str:
        """Create file tree structure based on implementation plan"""
        self.logger.info("Starting file tree creation...")

        structure_agent = Agent(
            name="StructureGeneratorAgent",
            instruction=STRUCTURE_GENERATOR_PROMPT,
            server_names=["command-executor"],
        )

        async with structure_agent:
            creator = await structure_agent.attach_llm(
                get_preferred_llm_class(self.config_path)
            )

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

    async def implement_code_pure(
        self, plan_content: str, target_directory: str, code_directory: str = None
    ) -> str:
        """Pure code implementation - focus on code writing without testing"""
        self.logger.info("Starting pure code implementation (no testing)...")

        # Use provided code_directory or calculate it (for backwards compatibility)
        if code_directory is None:
            code_directory = os.path.join(target_directory, "generate_code")

        self.logger.info(f"🎯 Using code directory (MCP workspace): {code_directory}")

        if not os.path.exists(code_directory):
            self.logger.warning(
                f"Code directory does not exist, creating it: {code_directory}"
            )
            os.makedirs(code_directory, exist_ok=True)
            self.logger.info(f"✅ Code directory created: {code_directory}")

        try:
            client, client_type = await self._initialize_llm_client()
            await self._initialize_mcp_agent(code_directory)

            tools = self._prepare_mcp_tool_definitions()
            system_message = PURE_CODE_IMPLEMENTATION_SYSTEM_PROMPT_INDEX
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
                client,
                client_type,
                system_message,
                messages,
                tools,
                plan_content,
                target_directory,
            )

            return result

        finally:
            await self._cleanup_mcp_agent()

    # ==================== 3. Core Business Logic (Implementation Layer) ====================

    async def _pure_code_implementation_loop(
        self,
        client,
        client_type,
        system_message,
        messages,
        tools,
        plan_content,
        target_directory,
    ):
        """Pure code implementation loop with memory optimization and phase consistency"""
        max_iterations = 800
        iteration = 0
        start_time = time.time()
        max_time = 7200  # 120 minutes (2 hours)

        # Initialize specialized agents
        code_agent = CodeImplementationAgent(
            self.mcp_agent, self.logger, self.enable_read_tools
        )

        # Pass code_directory to memory agent for file extraction
        code_directory = os.path.join(target_directory, "generate_code")
        memory_agent = ConciseMemoryAgent(
            plan_content,
            self.logger,
            target_directory,
            self.default_models,
            code_directory,
        )

        # Log read tools configuration
        read_tools_status = "ENABLED" if self.enable_read_tools else "DISABLED"
        self.logger.info(
            f"🔧 Read tools (read_file, read_code_mem): {read_tools_status}"
        )
        if not self.enable_read_tools:
            self.logger.info(
                "🚫 No read mode: read_file and read_code_mem tools will be skipped"
            )

        # Connect code agent with memory agent for summary generation
        # Note: Concise memory agent doesn't need LLM client for summary generation
        code_agent.set_memory_agent(memory_agent, client, client_type)

        # Initialize memory agent with iteration 0
        memory_agent.start_new_round(iteration=0)

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

            # Round logging removed

            # Call LLM
            response = await self._call_llm_with_tools(
                client, client_type, current_system_message, messages, tools
            )

            response_content = response.get("content", "").strip()
            if not response_content:
                response_content = "Continue implementing code files..."

            messages.append({"role": "assistant", "content": response_content})

            # Handle tool calls
            if response.get("tool_calls"):
                tool_results = await code_agent.execute_tool_calls(
                    response["tool_calls"]
                )

                # Record essential tool results in concise memory agent
                for tool_call, tool_result in zip(response["tool_calls"], tool_results):
                    memory_agent.record_tool_result(
                        tool_name=tool_call["name"],
                        tool_input=tool_call["input"],
                        tool_result=tool_result.get("result"),
                    )

                # NEW LOGIC: Check if write_file was called and trigger memory optimization immediately

                # Determine guidance based on results
                has_error = self._check_tool_results_for_errors(tool_results)
                files_count = code_agent.get_files_implemented_count()

                if has_error:
                    guidance = self._generate_error_guidance()
                else:
                    guidance = self._generate_success_guidance(files_count)

                compiled_response = self._compile_user_response(tool_results, guidance)
                messages.append({"role": "user", "content": compiled_response})

                # NEW LOGIC: Apply memory optimization immediately after write_file detection
                if memory_agent.should_trigger_memory_optimization(
                    messages, code_agent.get_files_implemented_count()
                ):
                    # Memory optimization triggered

                    # Apply concise memory optimization
                    files_implemented_count = code_agent.get_files_implemented_count()
                    current_system_message = code_agent.get_system_prompt()
                    messages = memory_agent.apply_memory_optimization(
                        current_system_message, messages, files_implemented_count
                    )

                    # Memory optimization completed

            else:
                files_count = code_agent.get_files_implemented_count()
                no_tools_guidance = self._generate_no_tools_guidance(files_count)
                messages.append({"role": "user", "content": no_tools_guidance})

            # Check for analysis loop and provide corrective guidance
            # if code_agent.is_in_analysis_loop():
            #     analysis_loop_guidance = code_agent.get_analysis_loop_guidance()
            #     messages.append({"role": "user", "content": analysis_loop_guidance})
            #     self.logger.warning(
            #         "Analysis loop detected and corrective guidance provided"
            #     )

            # Record file implementations in memory agent (for the current round)
            for file_info in code_agent.get_implementation_summary()["completed_files"]:
                memory_agent.record_file_implementation(file_info["file"])

            # REMOVED: Old memory optimization logic - now happens immediately after write_file
            # Memory optimization is now triggered immediately after write_file detection

            # Start new round for next iteration, sync with workflow iteration
            memory_agent.start_new_round(iteration=iteration)

            # Check completion based on actual unimplemented files list
            unimplemented_files = memory_agent.get_unimplemented_files()
            if not unimplemented_files:  # Empty list means all files implemented
                self.logger.info(
                    "✅ Code implementation complete - All files implemented"
                )
                break

            # Emergency trim if too long
            if len(messages) > 50:
                self.logger.warning(
                    "Emergency message trim - applying concise memory optimization"
                )

                current_system_message = code_agent.get_system_prompt()
                files_implemented_count = code_agent.get_files_implemented_count()
                messages = memory_agent.apply_memory_optimization(
                    current_system_message, messages, files_implemented_count
                )

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
            llm = await self.mcp_agent.attach_llm(
                get_preferred_llm_class(self.config_path)
            )

            # Set workspace to the target code directory
            workspace_result = await self.mcp_agent.call_tool(
                "set_workspace", {"workspace_path": code_directory}
            )
            self.logger.info(f"Workspace setup result: {workspace_result}")

            return llm

        except Exception as e:
            self.logger.error(f"Failed to initialize MCP agent: {e}")
            if self.mcp_agent:
                try:
                    await self.mcp_agent.__aexit__(None, None, None)
                except Exception:
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
        """Initialize LLM client based on llm_provider preference and API key availability"""
        # Get API keys
        anthropic_key = self.api_config.get("anthropic", {}).get("api_key", "")
        openai_key = self.api_config.get("openai", {}).get("api_key", "")
        google_key = self.api_config.get("google", {}).get("api_key", "")

        # Read user preference from main config
        preferred_provider = None
        try:
            import yaml

            # Derive config path from secrets path (same directory)
            secrets_dir = os.path.dirname(os.path.abspath(self.config_path))
            config_path = os.path.join(secrets_dir, "mcp_agent.config.yaml")
            if os.path.exists(config_path):
                with open(config_path, "r", encoding="utf-8") as f:
                    config = yaml.safe_load(f)
                    preferred_provider = config.get("llm_provider", "").strip().lower()
        except Exception as e:
            self.logger.warning(f"Could not read llm_provider preference: {e}")

        # Define provider initialization functions
        async def init_anthropic():
            if not (anthropic_key and anthropic_key.strip()):
                return None
            try:
                from anthropic import AsyncAnthropic

                client = AsyncAnthropic(api_key=anthropic_key)
                await client.messages.create(
                    model=self.default_models["anthropic"],
                    max_tokens=20,
                    messages=[{"role": "user", "content": "test"}],
                )
                self.logger.info(
                    f"Using Anthropic API with model: {self.default_models['anthropic']}"
                )
                return client, "anthropic"
            except Exception as e:
                self.logger.warning(f"Anthropic API unavailable: {e}")
                return None

        async def init_google():
            if not (google_key and google_key.strip()):
                return None
            try:
                from google import genai

                client = genai.Client(api_key=google_key)
                try:
                    test_response = await client.aio.models.generate_content(
                        model=self.default_models.get("google", "gemini-2.0-flash"),
                        contents="test",
                    )

                    self.logger.info(
                        "Google API connection successful: " + str(test_response)
                    )
                except Exception as test_err:
                    self.logger.warning(
                        f"Could not test Google API: {test_err}, but will try to use client"
                    )

                self.logger.info(
                    f"Using Google API with model: {self.default_models.get('google', 'gemini-2.0-flash')}"
                )
                return client, "google"
            except Exception as e:
                self.logger.warning(f"Google API unavailable: {e}")
                return None

        async def init_openai():
            if not (openai_key and openai_key.strip()):
                return None
            try:
                from openai import AsyncOpenAI

                openai_config = self.api_config.get("openai", {})
                base_url = openai_config.get("base_url")

                if base_url:
                    client = AsyncOpenAI(api_key=openai_key, base_url=base_url)
                else:
                    client = AsyncOpenAI(api_key=openai_key)

                model_name = self.default_models.get("openai", "o3-mini")

                try:
                    await client.chat.completions.create(
                        model=model_name,
                        max_tokens=20,
                        messages=[{"role": "user", "content": "test"}],
                    )
                except Exception as e:
                    if "max_tokens" in str(e) and "max_completion_tokens" in str(e):
                        self.logger.info(
                            f"Model {model_name} requires max_completion_tokens parameter"
                        )
                        await client.chat.completions.create(
                            model=model_name,
                            max_completion_tokens=20,
                            messages=[{"role": "user", "content": "test"}],
                        )
                    else:
                        raise
                self.logger.info(f"Using OpenAI API with model: {model_name}")
                if base_url:
                    self.logger.info(f"Using custom base URL: {base_url}")
                return client, "openai"
            except Exception as e:
                self.logger.warning(f"OpenAI API unavailable: {e}")
                return None

        # Map providers to their init functions
        provider_init_map = {
            "anthropic": init_anthropic,
            "google": init_google,
            "openai": init_openai,
        }

        # Try preferred provider first
        if preferred_provider and preferred_provider in provider_init_map:
            self.logger.info(f"🎯 Trying preferred provider: {preferred_provider}")
            result = await provider_init_map[preferred_provider]()
            if result:
                return result
            else:
                self.logger.warning(
                    f"⚠️ Preferred provider '{preferred_provider}' unavailable, trying alternatives..."
                )

        # Fallback: try providers in order
        for provider_name, init_func in provider_init_map.items():
            if provider_name == preferred_provider:
                continue  # Already tried
            result = await init_func()
            if result:
                return result

        raise ValueError(
            "No available LLM API - please check your API keys in configuration"
        )

    async def _call_llm_with_tools(
        self, client, client_type, system_message, messages, tools, max_tokens=8192
    ):
        """Call LLM with tools"""
        try:
            if client_type == "anthropic":
                return await self._call_anthropic_with_tools(
                    client, system_message, messages, tools, max_tokens
                )
            elif client_type == "openai":
                return await self._call_openai_with_tools(
                    client, system_message, messages, tools, max_tokens
                )
            elif client_type == "google":
                return await self._call_google_with_tools(
                    client, system_message, messages, tools, max_tokens
                )
            else:
                raise ValueError(f"Unsupported client type: {client_type}")
        except Exception as e:
            self.logger.error(f"LLM call failed: {e}")
            raise

    async def _call_anthropic_with_tools(
        self, client, system_message, messages, tools, max_tokens
    ):
        """Call Anthropic API"""
        validated_messages = self._validate_messages(messages)
        if not validated_messages:
            validated_messages = [
                {"role": "user", "content": "Please continue implementing code"}
            ]

        try:
            # Use implementation-specific model for code generation
            impl_model = self.default_models.get(
                "anthropic_implementation", self.default_models["anthropic"]
            )
            self.logger.info(f"🔧 Code generation using model: {impl_model}")
            response = await client.messages.create(
                model=impl_model,
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
                tool_calls.append(
                    {"id": block.id, "name": block.name, "input": block.input}
                )

        return {"content": content, "tool_calls": tool_calls}

    async def _call_google_with_tools(
        self, client, system_message, messages, tools, max_tokens
    ):
        """
        Call Google Gemini API with tools

        Note: Google Gemini uses a completely different API structure.
        The client here is expected to be google.genai.Client from google-genai SDK.

        Reference: https://ai.google.dev/gemini-api/docs/function-calling
        """
        try:
            from google.genai import types
        except ImportError:
            raise ImportError("google-genai package is required for Google API calls")

        validated_messages = self._validate_messages(messages)
        if not validated_messages:
            validated_messages = [
                {"role": "user", "content": "Please continue implementing code"}
            ]

        # Convert messages to Google Gemini format (types.Content)
        # Gemini expects: role="user" or role="model" (not "assistant")
        gemini_messages = []
        for msg in validated_messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")

            # Convert role names: "assistant" -> "model"
            if role == "assistant":
                role = "model"
            elif role not in ["user", "model"]:
                # Skip unsupported roles or convert to user
                role = "user"

            gemini_messages.append(
                types.Content(role=role, parts=[types.Part.from_text(text=content)])
            )

        # Convert tools to Google Gemini format (types.Tool with FunctionDeclaration)
        # Following the EXACT pattern from GoogleAugmentedLLM line 92-103
        # IMPORTANT: Each tool should be wrapped in its own Tool object!
        gemini_tools = []
        if tools:
            for tool in tools:
                # Transform the input_schema to be Gemini-compatible
                parameters = self._transform_schema_for_gemini(tool["input_schema"])

                # Each tool gets its own Tool wrapper (not all in one!)
                gemini_tools.append(
                    types.Tool(
                        function_declarations=[
                            types.FunctionDeclaration(
                                name=tool["name"],
                                description=tool["description"],
                                parameters=parameters,
                            )
                        ]
                    )
                )

        # Create config with system instruction and tools
        config = types.GenerateContentConfig(
            max_output_tokens=max_tokens,
            temperature=0.2,
            system_instruction=system_message if system_message else None,
            tools=gemini_tools if gemini_tools else None,
            # Disable automatic function calling - we handle it manually
            automatic_function_calling=types.AutomaticFunctionCallingConfig(
                disable=True
            ),
        )

        try:
            # Google Gemini API call using the native SDK
            # client is google.genai.Client instance
            # Use implementation-specific model for code generation
            impl_model = self.default_models.get(
                "google_implementation", self.default_models["google"]
            )
            self.logger.info(f"🔧 Code generation using model: {impl_model}")
            response = await client.aio.models.generate_content(
                model=impl_model,
                contents=gemini_messages,
                config=config,
            )
        except Exception as e:
            self.logger.error(f"Google API call failed: {e}")
            raise

        # Parse Gemini response (types.GenerateContentResponse)
        # Following the pattern from augmented_llm_google.py lines 145-165
        content = ""
        tool_calls = []

        if response and hasattr(response, "candidates") and response.candidates:
            candidate = response.candidates[0]

            if hasattr(candidate, "content") and candidate.content:
                if hasattr(candidate.content, "parts") and candidate.content.parts:
                    for part in candidate.content.parts:
                        # Handle text content
                        if hasattr(part, "text") and part.text:
                            content += part.text

                        # Handle function calls
                        # Check for function_call attribute, matching augmented_llm_google.py line 164
                        if hasattr(part, "function_call") and part.function_call:
                            fc = part.function_call
                            # Extract function call details
                            # Note: Gemini function_call has name and args attributes
                            tool_call = {
                                "id": getattr(
                                    fc, "id", getattr(fc, "name", "")
                                ),  # Use name as fallback for id
                                "name": fc.name if hasattr(fc, "name") else "",
                                "input": dict(fc.args)
                                if hasattr(fc, "args") and fc.args
                                else {},
                            }
                            self.logger.debug(
                                f"Google function_call parsed: {tool_call}"
                            )
                            tool_calls.append(tool_call)

        return {"content": content, "tool_calls": tool_calls}

    def _transform_schema_for_gemini(self, schema: dict) -> dict:
        """
        Transform JSON Schema to OpenAPI Schema format compatible with Gemini.

        This is based on the transform_mcp_tool_schema from GoogleAugmentedLLM.
        Key transformations:
        1. Convert camelCase to snake_case
        2. Remove unsupported fields (default, additionalProperties)
        3. Handle nullable types via anyOf
        """
        if not isinstance(schema, dict):
            return schema

        # Fields to exclude
        EXCLUDED_PROPERTIES = {"default", "additionalProperties"}

        # camelCase to snake_case mappings
        CAMEL_TO_SNAKE = {
            "anyOf": "any_of",
            "maxLength": "max_length",
            "minLength": "min_length",
            "minProperties": "min_properties",
            "maxProperties": "max_properties",
            "maxItems": "max_items",
            "minItems": "min_items",
        }

        result = {}

        for key, value in schema.items():
            # Skip excluded properties
            if key in EXCLUDED_PROPERTIES:
                continue

            # Convert camelCase to snake_case
            snake_key = CAMEL_TO_SNAKE.get(key, key)

            # Handle nested structures
            if key == "properties" and isinstance(value, dict):
                result[snake_key] = {
                    prop_k: self._transform_schema_for_gemini(prop_v)
                    for prop_k, prop_v in value.items()
                }
            elif key == "items" and isinstance(value, dict):
                result[snake_key] = self._transform_schema_for_gemini(value)
            elif key == "anyOf" and isinstance(value, list):
                # Handle nullable types (Type | None)
                has_null = any(
                    isinstance(item, dict) and item.get("type") == "null"
                    for item in value
                )
                if has_null:
                    result["nullable"] = True

                # Get first non-null schema
                for item in value:
                    if isinstance(item, dict) and item.get("type") != "null":
                        transformed = self._transform_schema_for_gemini(item)
                        for k, v in transformed.items():
                            if k not in result:
                                result[k] = v
                        break
            else:
                result[snake_key] = value

        return result

    def _repair_truncated_json(self, json_str: str, tool_name: str = "") -> dict:
        """
        Advanced JSON repair for truncated or malformed JSON from LLM responses.

        Handles:
        - Missing closing braces/brackets
        - Truncated string values
        - Missing required fields
        - Trailing commas
        """
        import re

        # Step 1: Try basic fixes first
        fixed = json_str.strip()

        # Remove trailing commas
        fixed = re.sub(r",\s*}", "}", fixed)
        fixed = re.sub(r",\s*]", "]", fixed)

        try:
            return json.loads(fixed)
        except json.JSONDecodeError as e:
            print("   🔧 Attempting advanced JSON repair...")

            # Step 2: Check for truncation issues
            if e.msg == "Expecting value":
                # Likely truncated - try to close open structures
                fixed = self._close_json_structures(fixed)
                try:
                    return json.loads(fixed)
                except (json.JSONDecodeError, ValueError, TypeError):
                    pass

            # Step 3: Try to extract partial valid JSON
            if e.msg.startswith("Expecting") and e.pos:
                # Truncate at error position and try to close
                truncated = fixed[: e.pos]
                closed = self._close_json_structures(truncated)
                try:
                    partial = json.loads(closed)
                    print("   ✅ Extracted partial JSON successfully")
                    return partial
                except (json.JSONDecodeError, ValueError, TypeError):
                    pass

            # Step 4: Tool-specific defaults for critical tools
            if tool_name == "write_file":
                # For write_file, try to extract at least file_path
                file_path_match = re.search(r'"file_path"\s*:\s*"([^"]*)"', fixed)
                if file_path_match:
                    print("   ⚠️  write_file JSON truncated, using minimal structure")
                    return {
                        "file_path": file_path_match.group(1),
                        "content": "",  # Empty content is better than crashing
                    }

            # Step 5: Last resort - return error indicator
            print("   ❌ JSON repair failed completely")
            return None

    def _close_json_structures(self, json_str: str) -> str:
        """
        Intelligently close unclosed JSON structures.
        Counts braces and brackets to determine what needs closing.
        """
        # Count open structures
        open_braces = json_str.count("{") - json_str.count("}")
        open_brackets = json_str.count("[") - json_str.count("]")

        # Check if we're in the middle of a string
        quote_count = json_str.count('"')
        in_string = (quote_count % 2) != 0

        result = json_str

        # Close string if needed
        if in_string:
            result += '"'

        # Close brackets first (inner structures)
        result += "]" * open_brackets

        # Close braces
        result += "}" * open_braces

        return result

    async def _call_openai_with_tools(
        self, client, system_message, messages, tools, max_tokens
    ):
        """Call OpenAI API with robust JSON error handling and retry mechanism"""
        openai_tools = []
        for tool in tools:
            openai_tools.append(
                {
                    "type": "function",
                    "function": {
                        "name": tool["name"],
                        "description": tool["description"],
                        "parameters": tool["input_schema"],
                    },
                }
            )

        openai_messages = [{"role": "system", "content": system_message}]
        openai_messages.extend(messages)

        # Retry mechanism for API calls
        max_retries = 3
        retry_delay = 2  # seconds

        # Use implementation-specific model for code generation
        impl_model = self.default_models.get(
            "openai_implementation", self.default_models["openai"]
        )
        self.logger.info(f"🔧 Code generation using model: {impl_model}")

        for attempt in range(max_retries):
            try:
                # Try max_tokens first, fallback to max_completion_tokens if unsupported
                try:
                    response = await client.chat.completions.create(
                        model=impl_model,
                        messages=openai_messages,
                        tools=openai_tools if openai_tools else None,
                        max_tokens=max_tokens,
                        temperature=0.2,
                    )
                except Exception as e:
                    if "max_tokens" in str(e) and "max_completion_tokens" in str(e):
                        # Retry with max_completion_tokens for models that require it
                        response = await client.chat.completions.create(
                            model=impl_model,
                            messages=openai_messages,
                            tools=openai_tools if openai_tools else None,
                            max_completion_tokens=max_tokens,
                        )
                    else:
                        raise

                # Validate response structure
                if (
                    not response
                    or not hasattr(response, "choices")
                    or not response.choices
                ):
                    raise ValueError("Invalid API response: missing choices")

                if not response.choices[0] or not hasattr(
                    response.choices[0], "message"
                ):
                    raise ValueError("Invalid API response: missing message in choice")

                message = response.choices[0].message
                content = message.content or ""

                # Successfully got a valid response
                break

            except json.JSONDecodeError as e:
                print(
                    f"\n❌ JSON Decode Error in API response (attempt {attempt + 1}/{max_retries}):"
                )
                print(f"   Error: {e}")
                print(f"   Position: line {e.lineno}, column {e.colno}")

                if attempt < max_retries - 1:
                    print(f"   ⏳ Retrying in {retry_delay} seconds...")
                    await asyncio.sleep(retry_delay)
                    retry_delay *= 2  # Exponential backoff
                else:
                    print("   ❌ All retries exhausted")
                    raise

            except (ValueError, AttributeError, TypeError) as e:
                print(f"\n❌ API Response Error (attempt {attempt + 1}/{max_retries}):")
                print(f"   Error type: {type(e).__name__}")
                print(f"   Error: {e}")

                if attempt < max_retries - 1:
                    print(f"   ⏳ Retrying in {retry_delay} seconds...")
                    await asyncio.sleep(retry_delay)
                    retry_delay *= 2
                else:
                    print("   ❌ All retries exhausted")
                    # Return empty response instead of crashing
                    return {
                        "content": "API error - unable to get valid response",
                        "tool_calls": [],
                    }

            except Exception as e:
                print(
                    f"\n❌ Unexpected API Error (attempt {attempt + 1}/{max_retries}):"
                )
                print(f"   Error type: {type(e).__name__}")
                print(f"   Error: {e}")

                if attempt < max_retries - 1:
                    print(f"   ⏳ Retrying in {retry_delay} seconds...")
                    await asyncio.sleep(retry_delay)
                    retry_delay *= 2
                else:
                    print("   ❌ All retries exhausted")
                    raise

        tool_calls = []
        if message.tool_calls:
            for tool_call in message.tool_calls:
                try:
                    # Attempt to parse tool call arguments
                    parsed_input = json.loads(tool_call.function.arguments)
                    tool_calls.append(
                        {
                            "id": tool_call.id,
                            "name": tool_call.function.name,
                            "input": parsed_input,
                        }
                    )
                except json.JSONDecodeError as e:
                    # Detailed JSON parsing error logging
                    print("\n❌ JSON Parsing Error in tool call:")
                    print(f"   Tool: {tool_call.function.name}")
                    print(f"   Error: {e}")
                    print("   Raw arguments (first 500 chars):")
                    print(f"   {tool_call.function.arguments[:500]}")
                    print(f"   Error position: line {e.lineno}, column {e.colno}")
                    print(
                        f"   Problem at: ...{tool_call.function.arguments[max(0, e.pos-50):e.pos+50]}..."
                    )

                    # Attempt advanced JSON repair
                    repaired = self._repair_truncated_json(
                        tool_call.function.arguments, tool_call.function.name
                    )

                    if repaired:
                        print("   ✅ JSON repaired successfully")
                        tool_calls.append(
                            {
                                "id": tool_call.id,
                                "name": tool_call.function.name,
                                "input": repaired,
                            }
                        )
                    else:
                        # Skip this tool call if repair failed
                        print("   ⚠️  Skipping unrepairable tool call")
                        continue

        return {"content": content, "tool_calls": tool_calls}

    # ==================== 5. Tools and Utility Methods (Utility Layer) ====================

    def _validate_messages(self, messages: List[Dict]) -> List[Dict]:
        """Validate and clean message list"""
        valid_messages = []
        for msg in messages:
            content = msg.get("content", "").strip()
            if content:
                valid_messages.append(
                    {"role": msg.get("role", "user"), "content": content}
                )
            else:
                self.logger.warning(f"Skipping empty message: {msg}")
        return valid_messages

    def _prepare_mcp_tool_definitions(self) -> List[Dict[str, Any]]:
        """Prepare tool definitions in Anthropic API standard format with filtering"""
        # Get all available tools
        all_tools = get_mcp_tools("code_implementation")

        # Define essential tools for code implementation
        essential_tool_names = {"write_file", "search_code_references"}

        # Filter to only essential tools
        filtered_tools = [
            tool for tool in all_tools if tool.get("name") in essential_tool_names
        ]

        self.logger.info(
            f"🔧 Tool filtering: {len(filtered_tools)}/{len(all_tools)} tools enabled"
        )
        self.logger.info(
            f"   Available tools: {[tool.get('name') for tool in filtered_tools]}"
        )

        return filtered_tools

        # return get_mcp_tools("code_implementation")

    def _check_tool_results_for_errors(self, tool_results: List[Dict]) -> bool:
        """Check tool results for errors with JSON repair capability"""
        for result in tool_results:
            try:
                if hasattr(result["result"], "content") and result["result"].content:
                    content_text = result["result"].content[0].text

                    # First attempt: try direct JSON parsing
                    try:
                        parsed_result = json.loads(content_text)
                        if parsed_result.get("status") == "error":
                            return True
                    except json.JSONDecodeError as e:
                        # JSON parsing failed - try to repair
                        print("\n⚠️  JSON parsing failed in tool result check:")
                        print(f"   Error: {e}")
                        print(
                            f"   Position: line {e.lineno}, column {e.colno}, char {e.pos}"
                        )
                        print(f"   Content length: {len(content_text)} chars")
                        print(f"   First 300 chars: {content_text[:300]}")

                        # Attempt to repair the JSON
                        repaired = self._repair_truncated_json(content_text)
                        if repaired:
                            print("   ✅ Tool result JSON repaired successfully")
                            if repaired.get("status") == "error":
                                return True
                        else:
                            # Fallback: check for "error" keyword in text
                            if "error" in content_text.lower():
                                return True

                elif isinstance(result["result"], str):
                    if "error" in result["result"].lower():
                        return True

            except (AttributeError, IndexError) as e:
                # Unexpected result structure
                print(f"\n⚠️  Unexpected result structure: {type(e).__name__}: {e}")
                result_str = str(result["result"])
                if "error" in result_str.lower():
                    return True
        return False

    # ==================== 6. User Interaction and Feedback (Interaction Layer) ====================

    def _generate_success_guidance(self, files_count: int) -> str:
        """Generate concise success guidance for continuing implementation"""
        return f"""✅ File implementation completed successfully!

📊 **Progress Status:** {files_count} files implemented

🎯 **Next Action:** Check if ALL files from the reproduction plan are implemented.

⚡ **Decision Process:**
1. **If ALL files are implemented:** Use `execute_python` or `execute_bash` to test the complete implementation, then respond "**implementation complete**" to end the conversation
2. **If MORE files need implementation:** Continue with dependency-aware workflow:
   - **Start with `read_code_mem`** to understand existing implementations and dependencies
   - **Optionally use `search_code_references`** for reference patterns (OPTIONAL - use for inspiration only, original paper specs take priority)
   - **Then `write_file`** to implement the new component
   - **Finally: Test** if needed

💡 **Key Point:** Always verify completion status before continuing with new file creation."""

    def _generate_error_guidance(self) -> str:
        """Generate error guidance for handling issues"""
        return """❌ Error detected during file implementation.

🔧 **Action Required:**
1. Review the error details above
2. Fix the identified issue
3. **Check if ALL files from the reproduction plan are implemented:**
   - **If YES:** Use `execute_python` or `execute_bash` to test the complete implementation, then respond "**implementation complete**" to end the conversation
   - **If NO:** Continue with proper development cycle for next file:
     - **Start with `read_code_mem`** to understand existing implementations
     - **Optionally use `search_code_references`** for reference patterns (OPTIONAL - for inspiration only)
     - **Then `write_file`** to implement properly
     - **Test** if needed
4. Ensure proper error handling in future implementations

💡 **Remember:** Always verify if all planned files are implemented before continuing with new file creation."""

    def _generate_no_tools_guidance(self, files_count: int) -> str:
        """Generate concise guidance when no tools are called"""
        return f"""⚠️ No tool calls detected in your response.

📊 **Current Progress:** {files_count} files implemented

🚨 **Action Required:** You must use tools. **FIRST check if ALL files from the reproduction plan are implemented:**

⚡ **Decision Process:**
1. **If ALL files are implemented:** Use `execute_python` or `execute_bash` to test the complete implementation, then respond "**implementation complete**" to end the conversation
2. **If MORE files need implementation:** Follow the development cycle:
   - **Start with `read_code_mem`** to understand existing implementations
   - **Optionally use `search_code_references`** for reference patterns (OPTIONAL - for inspiration only)
   - **Then `write_file`** to implement the new component
   - **Finally: Test** if needed

🚨 **Critical:** Always verify completion status first, then use appropriate tools - not just explanations!"""

    def _compile_user_response(self, tool_results: List[Dict], guidance: str) -> str:
        """Compile tool results and guidance into a single user response"""
        response_parts = []

        if tool_results:
            response_parts.append("🔧 **Tool Execution Results:**")
            for tool_result in tool_results:
                tool_name = tool_result["tool_name"]
                result_content = tool_result["result"]
                response_parts.append(
                    f"```\nTool: {tool_name}\nResult: {result_content}\n```"
                )

        if guidance:
            response_parts.append("\n" + guidance)

        return "\n\n".join(response_parts)

    # ==================== 7. Reporting and Output (Output Layer) ====================

    async def _generate_pure_code_final_report_with_concise_agents(
        self,
        iterations: int,
        elapsed_time: float,
        code_agent: CodeImplementationAgent,
        memory_agent: ConciseMemoryAgent,
    ):
        """Generate final report using concise agent statistics"""
        try:
            code_stats = code_agent.get_implementation_statistics()
            memory_stats = memory_agent.get_memory_statistics(
                code_stats["files_implemented_count"]
            )

            if self.mcp_agent:
                history_result = await self.mcp_agent.call_tool(
                    "get_operation_history", {"last_n": 30}
                )
                history_data = (
                    json.loads(history_result)
                    if isinstance(history_result, str)
                    else history_result
                )
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


async def main():
    """Main function for running the workflow"""
    # Configure root logger carefully to avoid duplicates
    root_logger = logging.getLogger()
    if not root_logger.handlers:
        handler = logging.StreamHandler()
        formatter = logging.Formatter("%(levelname)s:%(name)s:%(message)s")
        handler.setFormatter(formatter)
        root_logger.addHandler(handler)
        root_logger.setLevel(logging.INFO)

    workflow = CodeImplementationWorkflowWithIndex()

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

    test_success = True
    if test_success:
        print("\n" + "=" * 60)
        print("🎉 UNIFIED Code Reference Indexer Integration Test PASSED!")
        print("🔧 Three-step process successfully merged into ONE tool")
        print("=" * 60)

        # Ask if user wants to continue with actual workflow
        print("\nContinuing with workflow execution...")

        plan_file = "/data2/bjdwhzzh/project-hku/Deepcode_collections/DeepCode/deepcode_lab/papers/54_only_code_gen/initial_plan.txt"
        # plan_file = "/data2/bjdwhzzh/project-hku/Code-Agent2.0/Code-Agent/deepcode-mcp/agent_folders/papers/1/initial_plan.txt"
        target_directory = "/data2/bjdwhzzh/project-hku/Deepcode_collections/DeepCode/deepcode_lab/papers/54_only_code_gen/"
        print("Implementation Mode Selection:")
        print("1. Pure Code Implementation Mode (Recommended)")
        print("2. Iterative Implementation Mode")

        pure_code_mode = True
        mode_name = "Pure Code Implementation Mode with Memory Agent Architecture + Code Reference Indexer"
        print(f"Using: {mode_name}")

        # Configure read tools - modify this parameter to enable/disable read tools
        enable_read_tools = (
            True  # Set to False to disable read_file and read_code_mem tools
        )
        read_tools_status = "ENABLED" if enable_read_tools else "DISABLED"
        print(f"🔧 Read tools (read_file, read_code_mem): {read_tools_status}")

        # NOTE: To test without read tools, change the line above to:
        # enable_read_tools = False

        result = await workflow.run_workflow(
            plan_file,
            target_directory=target_directory,
            pure_code_mode=pure_code_mode,
            enable_read_tools=enable_read_tools,
        )

        print("=" * 60)
        print("Workflow Execution Results:")
        print(f"Status: {result['status']}")
        print(f"Mode: {mode_name}")

        if result["status"] == "success":
            print(f"Code Directory: {result['code_directory']}")
            print(f"MCP Architecture: {result.get('mcp_architecture', 'unknown')}")
            print("Execution completed!")
        else:
            print(f"Error Message: {result['message']}")

        print("=" * 60)
        print(
            "✅ Using Standard MCP Architecture with Memory Agent + Code Reference Indexer"
        )

    else:
        print("\n" + "=" * 60)
        print("❌ Code Reference Indexer Integration Test FAILED!")
        print("Please check the configuration and try again.")
        print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
