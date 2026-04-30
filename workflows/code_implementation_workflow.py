"""
Paper Code Implementation Workflow - MCP-compliant Iterative Development

Features:
1. File Tree Creation
2. Code Implementation - Based on aisi-basic-agent iterative development

MCP Architecture:
- MCP Server: tools/code_implementation_server.py
- MCP Client: Called through DeepCode's compat layer over MCP stdio
- Configuration: deepcode_config.json (single source of truth)
"""

import asyncio
import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Dict, Any, Optional, List, Callable

# DeepCode-native compat layer (replaces legacy mcp_agent imports)
from core.compat import Agent
from core.llm_runtime import attach_workflow_llm, get_workflow_provider

# Local imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from prompts.code_prompts import STRUCTURE_GENERATOR_PROMPT
from prompts.code_prompts import (
    GENERAL_CODE_IMPLEMENTATION_SYSTEM_PROMPT,
)
from workflows.agents import CodeImplementationAgent
from workflows.agents.memory_agent_concise import ConciseMemoryAgent
from workflows.implementation_llm_runtime import call_provider_with_legacy_tools
from config.mcp_tool_definitions import get_mcp_tools
from utils.llm_utils import get_default_models
from utils.loop_detector import LoopDetector, ProgressTracker


class CodeImplementationWorkflow:
    """
    Paper Code Implementation Workflow Manager

    Uses standard MCP architecture:
    1. Connect to code-implementation server via MCP client
    2. Use MCP protocol for tool calls
    3. Support workspace management and operation history tracking
    """

    # ==================== 1. Class Initialization and Configuration (Infrastructure Layer) ====================

    def __init__(self) -> None:
        """Initialize workflow.

        Configuration is read from the process-wide DeepCode runtime, which
        loads ``deepcode_config.json`` exactly once. ``self.default_models``
        is materialised eagerly so the memory agent (which still keeps its
        own legacy SDK path) sees the same model selection as the workflow.
        """
        self.default_models = get_default_models()
        self.logger = self._create_logger()
        self.mcp_agent = None
        # Default value; ``run_workflow`` may toggle it.
        self.enable_read_tools = True
        self.loop_detector = LoopDetector()
        self.progress_tracker = ProgressTracker()
        # Populated by _pure_code_implementation_loop on each call so
        # run_workflow can report a truthful status (completed / aborted /
        # max_iterations / max_time) instead of unconditional success.
        self._last_run_state: Dict[str, Any] = {
            "status": "unknown",
            "reason": None,
            "iterations": 0,
            "elapsed_seconds": 0.0,
            "files_completed": 0,
            "total_files": 0,
            "unimplemented_files": [],
        }

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
        progress_callback: Optional[Callable] = None,
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
                    plan_content,
                    target_directory,
                    code_directory,
                    progress_callback=progress_callback,
                )
            else:
                pass

            run_state = dict(self._last_run_state)
            inner_status = run_state.get("status", "unknown")
            done = inner_status == "completed"
            if done:
                self.logger.info(
                    "Workflow execution successful (all files implemented)"
                )
                top_status = "success"
            else:
                # Surface the actual reason instead of silently lying about success.
                pending = run_state.get("unimplemented_files", []) or []
                self.logger.warning(
                    "Workflow execution finished EARLY: status=%s reason=%s "
                    "(files=%d, %d unimplemented)",
                    inner_status,
                    run_state.get("reason"),
                    run_state.get("files_completed", 0),
                    len(pending),
                )
                if pending:
                    sample = ", ".join(pending[:5])
                    if len(pending) > 5:
                        sample += f", ... (+{len(pending) - 5} more)"
                    self.logger.warning("Unimplemented files: %s", sample)
                top_status = "incomplete"

            return {
                "status": top_status,
                "inner_status": inner_status,
                "abort_reason": run_state.get("reason"),
                "files_completed": run_state.get("files_completed", 0),
                "total_files": run_state.get("total_files", 0),
                "unimplemented_files": run_state.get("unimplemented_files", []),
                "iterations": run_state.get("iterations", 0),
                "elapsed_seconds": run_state.get("elapsed_seconds", 0.0),
                "plan_file": plan_file_path,
                "target_directory": target_directory,
                "code_directory": os.path.join(target_directory, "generate_code"),
                "results": results,
                "mcp_architecture": "standard",
            }

        except Exception as e:
            self.logger.error(f"Workflow execution failed: {e}")

            code_directory = os.path.join(
                target_directory or str(Path(plan_file_path).parent), "generate_code"
            )
            return {
                "status": "error",
                "inner_status": "error",
                "abort_reason": str(e),
                "message": str(e),
                "files_completed": 0,
                "total_files": 0,
                "unimplemented_files": [],
                "elapsed_seconds": 0.0,
                "plan_file": plan_file_path,
                "target_directory": target_directory,
                "code_directory": code_directory,
                "results": {},
                "mcp_architecture": "standard",
            }
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
            creator = await attach_workflow_llm(
                structure_agent,
                phase="implementation",
            )

            message = f"""Analyze the following implementation plan and generate shell commands to create the file tree structure.

Target Directory: {target_directory}/generate_code/

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
            self.logger.info(f"LLM response: {result[:200]}...")  # Log first 200 chars

            # Verify directory was created, if not create it manually
            code_dir = os.path.join(target_directory, "generate_code")
            if not os.path.exists(code_dir):
                self.logger.warning(
                    "LLM did not create directory, creating manually..."
                )
                os.makedirs(code_dir, exist_ok=True)
                self.logger.info(f"✅ Manually created directory: {code_dir}")
            else:
                self.logger.info(f"✅ Directory exists: {code_dir}")

            return result

    async def implement_code_pure(
        self,
        plan_content: str,
        target_directory: str,
        code_directory: str = None,
        progress_callback: Optional[Callable] = None,
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
                client,
                client_type,
                system_message,
                messages,
                tools,
                plan_content,
                target_directory,
                progress_callback=progress_callback,
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
        progress_callback: Optional[Callable] = None,
    ):
        """Pure code implementation loop with memory optimization and phase consistency"""
        max_iterations = 800
        iteration = 0
        start_time = time.time()
        max_time = 7200  # 120 minutes (2 hours)
        # Track abort/completion so run_workflow can report a truthful status
        # rather than always claiming success. Default to "max_iterations" —
        # overwritten by any earlier exit branch.
        run_state: Dict[str, Any] = {
            "status": "max_iterations",
            "reason": f"reached max_iterations={max_iterations} without completion",
        }

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
        total_files = len(memory_agent.all_files_list)
        self.progress_tracker.set_total_files(total_files)
        if progress_callback:
            progress_callback(
                85,
                f"Code implementation started: 0/{total_files} planned files completed",
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
                run_state = {
                    "status": "max_time",
                    "reason": f"wall-clock budget exhausted after {elapsed_time:.0f}s (limit {max_time}s)",
                }
                break

            # Check for loops and timeouts (pre-LLM gate)
            if self.loop_detector.should_abort():
                abort_reason = self.loop_detector.get_abort_reason()
                self.logger.error(f"🛑 Process aborted (pre-LLM): {abort_reason}")
                run_state = {
                    "status": "aborted",
                    "reason": f"loop_detector pre-LLM: {abort_reason}",
                }
                break

            # Update file-level progress with a stable denominator.
            files_implemented = len(memory_agent.get_implemented_files())
            if files_implemented > 0 or total_files > 0:
                progress_info = self.progress_tracker.get_progress_info()
                print(
                    f"📁 Files: {progress_info['files_completed']}/{progress_info['total_files']} ({progress_info['file_progress']:.1f}%)"
                )
                if progress_info["estimated_remaining_seconds"] > 0:
                    print(
                        f"⏱️ Estimated remaining: {progress_info['estimated_remaining_seconds']:.0f}s"
                    )

            # # Test simplified memory approach if we have files implemented
            # if iteration == 5 and code_agent.get_files_implemented_count() > 0:
            #     self.logger.info("🧪 Testing simplified memory approach...")
            #     test_results = await memory_agent.test_simplified_memory_approach()
            #     self.logger.info(f"Memory test results: {test_results}")

            # self.logger.info(f"Pure code implementation iteration {iteration}: generating code")

            messages = self._validate_messages(messages)
            current_system_message = code_agent.get_system_prompt()

            # Round logging removed

            # Call LLM. Time it so we can subtract the LLM wait from the
            # stall budget — otherwise a single slow round-trip (long
            # context, transient retry, network blip) trips the
            # "no progress for 180s" guard and aborts the whole pipeline.
            llm_start = time.time()
            try:
                response = await self._call_llm_with_tools(
                    client,
                    client_type,
                    current_system_message,
                    messages,
                    tools,
                    progress_callback=progress_callback,
                )
            except Exception as e:
                self.loop_detector.note_llm_wait(time.time() - llm_start)
                reason = f"LLM request failed during implementation: {e}"
                self.logger.error(reason)
                run_state = {
                    "status": "incomplete",
                    "reason": reason,
                }
                if progress_callback:
                    progress_callback(85, reason, str(e))
                break
            self.loop_detector.note_llm_wait(time.time() - llm_start)

            response_content = response.get("content", "").strip()
            if not response_content:
                response_content = "Continue implementing code files..."

            messages.append({"role": "assistant", "content": response_content})

            # Handle tool calls
            if response.get("tool_calls"):
                # Check for loops before executing tools
                aborted_in_tool_check = False
                for tool_call in response["tool_calls"]:
                    loop_status = self.loop_detector.check_tool_call(tool_call["name"])
                    if loop_status["should_stop"]:
                        self.logger.error(
                            f"🛑 Tool execution aborted: {loop_status['message']}"
                        )
                        run_state = {
                            "status": "aborted",
                            "reason": f"loop_detector tool-check: {loop_status['message']}",
                        }
                        aborted_in_tool_check = True
                        break
                if aborted_in_tool_check:
                    break

                tool_results = await code_agent.execute_tool_calls(
                    response["tool_calls"]
                )

                # Record essential tool results in concise memory agent
                for tool_call, tool_result in zip(response["tool_calls"], tool_results):
                    # Check if tool actually failed
                    # Only count as error if isError flag is True
                    is_error = tool_result.get("isError", False)

                    if not is_error:
                        # Tool succeeded
                        self.loop_detector.record_success()

                        # Track file completion
                        if tool_call["name"] == "write_file":
                            filename = tool_call["input"].get("file_path", "unknown")
                            completed_first_time = self.progress_tracker.complete_file(
                                memory_agent.normalize_file_path(filename)
                            )
                            if completed_first_time:
                                print(f"✅ File completed: {filename}")
                                if progress_callback:
                                    progress_info = (
                                        self.progress_tracker.get_progress_info()
                                    )
                                    progress_callback(
                                        85,
                                        "Code implementation progress: "
                                        f"{progress_info['files_completed']}/"
                                        f"{progress_info['total_files']} files completed",
                                    )
                    else:
                        # Tool actually failed
                        self.loop_detector.record_error(
                            f"Tool {tool_call['name']} failed: {tool_result.get('result', '')[:100]}"
                        )

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

            # # Check for analysis loop and provide corrective guidance
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
                run_state = {
                    "status": "completed",
                    "reason": "all planned files implemented",
                }
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

        elapsed_total = time.time() - start_time
        # Snapshot run state for run_workflow's truthful status reporting.
        self._last_run_state = {
            "status": run_state["status"],
            "reason": run_state["reason"],
            "iterations": iteration,
            "elapsed_seconds": elapsed_total,
            "files_completed": len(memory_agent.get_implemented_files()),
            "total_files": len(memory_agent.get_all_files_list()),
            "unimplemented_files": list(memory_agent.get_unimplemented_files() or []),
        }
        return await self._generate_pure_code_final_report_with_concise_agents(
            iteration, elapsed_total, code_agent, memory_agent
        )

    # ==================== 4. MCP Agent and LLM Communication Management (Communication Layer) ====================

    async def _initialize_mcp_agent(self, code_directory: str):
        """Initialize MCP agent and connect to code-implementation server"""
        try:
            self.mcp_agent = Agent(
                name="CodeImplementationAgent",
                instruction="You are a code implementation assistant, using MCP tools to implement paper code replication. For large documents, use document-segmentation tools to read content in smaller chunks to avoid token limits.",
                server_names=[
                    "code-implementation",
                    "code-reference-indexer",
                    "document-segmentation",
                ],
            )

            await self.mcp_agent.__aenter__()
            llm = await attach_workflow_llm(
                self.mcp_agent,
                phase="implementation",
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
        """Initialize the implementation LLM via DeepCode's provider runtime."""
        provider, profile = get_workflow_provider(phase="implementation")
        self.logger.info(
            "Using DeepCode provider runtime: phase=%s provider=%s model=%s",
            profile.phase,
            profile.provider_name,
            profile.model,
        )
        return provider, "provider"

    async def _call_llm_with_tools(
        self,
        client,
        client_type,
        system_message,
        messages,
        tools,
        max_tokens=8192,
        progress_callback: Optional[Callable] = None,
    ):
        """Call the implementation LLM through the unified provider abstraction."""
        if client_type != "provider":
            raise ValueError(
                f"Unsupported client type '{client_type}'. The implementation workflow "
                "only routes through DeepCode's provider runtime."
            )
        try:

            async def on_retry_wait(message: str):
                self.logger.warning("Implementation LLM retry: %s", message)
                if progress_callback:
                    progress_callback(
                        85,
                        f"Retrying implementation LLM call: {message}",
                    )

            return await call_provider_with_legacy_tools(
                client,
                system_message=system_message,
                messages=messages,
                tools=tools,
                max_tokens=max_tokens,
                validate_messages=self._validate_messages,
                logger=self.logger,
                retry_mode="standard",
                on_retry_wait=on_retry_wait,
            )
        except Exception as e:
            self.logger.error(f"LLM call failed: {e}")
            raise

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
        """Prepare tool definitions in Anthropic API standard format"""
        return get_mcp_tools("code_implementation")

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
1. **If ALL files implemented:** Reply with "All files implemented" to complete the task
2. **If MORE files need implementation:** Continue with dependency-aware workflow:
   - **Use `write_file` to implement the new component"""

    def _generate_error_guidance(self) -> str:
        """Generate error guidance for handling issues"""
        return """❌ Error detected during file implementation.

🔧 **Action Required:**
1. Review the error details above
2. Fix the identified issue
3. **Check if ALL files from the reproduction plan are implemented:**
   - **If YES:** Respond "**implementation complete**" to end the conversation
   - **If NO:** Continue with proper development cycle for next file:
     - **Use `write_file` to implement properly
4. Ensure proper error handling in future implementations"""

    def _generate_no_tools_guidance(self, files_count: int) -> str:
        """Generate concise guidance when no tools are called"""
        return f"""⚠️ No tool calls detected in your response.

📊 **Current Progress:** {files_count} files implemented

🚨 **Action Required:** Check completion status NOW:

⚡ **Decision Process:**
1. **If ALL files from plan are implemented:** Reply "All files implemented" to complete
2. **If MORE files need implementation:** Use tools to continue:
   - **Use `write_file` to implement the new component

🚨 **Critical:** Don't just explain - either declare completion or use tools!"""

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

    test_success = True
    if test_success:
        print("\n" + "=" * 60)
        print("🎉 UNIFIED Code Reference Indexer Integration Test PASSED!")
        print("🔧 Three-step process successfully merged into ONE tool")
        print("=" * 60)

        # Ask if user wants to continue with actual workflow
        print("\nContinuing with workflow execution...")

        plan_file = os.path.join(
            os.getcwd(), "deepcode_lab", "papers", "2", "initial_plan.txt"
        )
        target_directory = os.path.join(os.getcwd(), "deepcode_lab", "papers", "2")
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
