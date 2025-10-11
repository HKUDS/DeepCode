"""
Revision Agent for multi-file batch code revision and implementation

This module provides the RevisionAgent class and related functionality for:
- Multi-file batch code revision execution
- Iterative implementation with memory management
- Error-driven revision and remediation
- Integration with memory agents for progress tracking
"""

import asyncio
import json
import os
import time
import re
from typing import Dict, Any, List
from dataclasses import dataclass


@dataclass
class CodeRevisionResult:
    """Code revision results structure"""

    revision_success: bool
    tasks_completed: List[str]
    tasks_failed: List[str]
    files_created: List[str]
    files_modified: List[str]
    empty_files_implemented: int
    missing_files_created: int
    quality_issues_fixed: int
    revision_issues: List[str]
    final_project_health: str
    execution_logs: List[str]
    total_tasks: int
    completion_rate: float
    batch_operations: List[Dict[str, Any]]  # Track multi-file operations


class RevisionAgent:
    """
    Revision Agent for multi-file batch code revision and implementation

    Handles:
    - Multi-file batch processing and implementation
    - Iterative code revision with memory management
    - Error-driven revision and targeted fixes
    - Integration with memory agents for progress tracking
    """

    def __init__(
        self, logger, evaluation_state, mcp_revision_agent, memory_agent, config
    ):
        self.logger = logger
        self.evaluation_state = evaluation_state
        self.mcp_revision_agent = mcp_revision_agent  # The actual MCP agent
        self.memory_agent = memory_agent
        self.config = config
        self.max_files_per_batch = config.get("max_files_per_batch", 3)

    async def run_iterative_multi_file_revision_execution(self) -> bool:
        """
        PHASE 2: Intelligent Multi-Round Code Revision Execution
        Uses iterative Agent conversation to achieve comprehensive revision
        """
        try:
            self.logger.info(
                "⚙️ Starting intelligent multi-round revision execution phase"
            )
            self.logger.info(
                "🤖 Code Revise Agent will use iterative conversation to execute revisions intelligently"
            )

            # Verify revision report exists
            if not self.evaluation_state.revision_report:
                raise Exception(
                    "No revision report available from Analyzer Agent - cannot execute revisions"
                )

            revision_report_data = self.evaluation_state.revision_report.get(
                "revision_report", {}
            )
            if not revision_report_data:
                raise Exception("Invalid revision report structure from Analyzer Agent")

            revision_tasks = revision_report_data.get("revision_tasks", [])

            if not revision_tasks:
                self.logger.info(
                    "✅ No revision tasks needed - repository appears complete"
                )
                self.evaluation_state.code_revision = CodeRevisionResult(
                    revision_success=True,
                    tasks_completed=[],
                    tasks_failed=[],
                    files_created=[],
                    files_modified=[],
                    empty_files_implemented=0,
                    missing_files_created=0,
                    quality_issues_fixed=0,
                    revision_issues=[],
                    final_project_health="excellent",
                    execution_logs=["No revision needed"],
                    total_tasks=0,
                    completion_rate=100.0,
                    batch_operations=[],
                )
                return True

            self.logger.info(
                f"🔧 Starting intelligent revision execution of {len(revision_tasks)} revision tasks"
            )

            # Set up workspace for the Code Revise Agent
            try:
                tool_name = "set_workspace"
                tool_input = {"workspace_path": self.evaluation_state.repo_path}
                self.logger.info(self._format_tool_info(tool_name, tool_input))
                workspace_setup_result = await self.mcp_revision_agent.call_tool(
                    tool_name, tool_input
                )
                self.logger.info(
                    f"✅ Code Revise Agent workspace configured: {self.evaluation_state.repo_path}"
                )
            except Exception as e:
                self.logger.warning(
                    f"Failed to set workspace for Code Revise Agent: {e}"
                )

            # Initialize LLM client and tools
            client, client_type = await self._initialize_llm_client()
            tools = self._prepare_revision_tool_definitions()

            # Execute intelligent revision conversation
            revision_result = await self._execute_intelligent_revision_conversation(
                client, client_type, tools, revision_tasks, revision_report_data
            )

            # Store revision results
            if revision_result["success"]:
                self.evaluation_state.code_revision = revision_result["revision_data"]
                self.logger.info(
                    "✅ Intelligent revision conversation completed successfully"
                )
                return True
            else:
                self.logger.error(
                    f"❌ Intelligent revision conversation failed: {revision_result.get('error', 'Unknown error')}"
                )
                return False

        except Exception as e:
            self.evaluation_state.add_error(
                f"Multi-file revision execution failed: {e}"
            )
            # Create minimal revision result to allow workflow to continue
            self.evaluation_state.code_revision = CodeRevisionResult(
                revision_success=False,
                tasks_completed=[],
                tasks_failed=[],
                files_created=[],
                files_modified=[],
                empty_files_implemented=0,
                missing_files_created=0,
                quality_issues_fixed=0,
                revision_issues=[f"Multi-file revision error: {str(e)}"],
                final_project_health="critical",
                execution_logs=[f"Multi-file revision execution failed: {str(e)}"],
                total_tasks=0,
                completion_rate=0.0,
                batch_operations=[],
            )
            return False

    async def run_iterative_error_analysis_phase(self, sandbox_agent=None) -> bool:
        """
        PHASE 4: LSP-Enhanced Iterative Error Analysis and Remediation
        Uses comprehensive LSP tools for precise error analysis and code fixes
        """
        try:
            self.logger.info("🔄 Starting LSP-enhanced iterative error analysis phase")

            if not sandbox_agent or not sandbox_agent.sandbox_state:
                self.logger.error(
                    "❌ Sandbox not available, cannot run iterative error analysis"
                )
                return False

            # Step 0: Set up LSP servers for the repository
            self.logger.info("🚀 Setting up LSP servers for enhanced analysis")
            tool_name = "setup_lsp_servers"
            tool_input = {"repo_path": self.evaluation_state.repo_path}
            self.logger.info(self._format_tool_info(tool_name, tool_input))
            lsp_setup_result = await self.mcp_revision_agent.call_tool(
                tool_name, tool_input
            )
            self.logger.info("✅ LSP servers initialized")

            max_iterations = 50
            iteration = 0
            project_running = False

            start_time = time.time()
            error_analysis_reports = []
            lsp_fix_applications = []

            while iteration < max_iterations and not project_running:
                iteration += 1
                self.logger.info(
                    f"🔄 === LSP-ENHANCED ITERATION {iteration}/{max_iterations} ==="
                )

                # Step 1: Execute project in sandbox
                self.logger.info(
                    f"🚀 Step 1: Executing project in sandbox (iteration {iteration})"
                )
                execution_result = sandbox_agent.execute_project()

                if execution_result.success:
                    self.logger.info(
                        f"🎉 SUCCESS! Project executed successfully on iteration {iteration}"
                    )
                    project_running = True
                    break
                else:
                    self.logger.warning(
                        f"❌ Project execution failed on iteration {iteration}"
                    )
                    self.logger.warning(
                        f"💥 Error traceback: {execution_result.error_traceback[:500] if execution_result.error_traceback else 'None'}..."
                    )

                    if not execution_result.error_traceback:
                        execution_result.error_traceback = execution_result.stderr

                # Step 2: Intelligent Agent-Driven Error Analysis and Fixing
                if execution_result.error_traceback:
                    self.logger.info(
                        f"🔍 Step 2: Intelligent agent-driven error analysis (iteration {iteration})"
                    )

                    # Use intelligent error fixing with agent decision-making
                    fix_results = await self._execute_intelligent_error_fixing(
                        execution_result, iteration
                    )

                    if fix_results and fix_results.get("fixes_applied"):
                        lsp_fix_applications.extend(fix_results["fixes_applied"])
                        error_analysis_reports.append(
                            fix_results.get("analysis_report", {})
                        )

                        self.logger.info(
                            f"✅ Applied {len(fix_results['fixes_applied'])} intelligent fixes"
                        )

                        # Update memory agent with comprehensive changes
                        if self.memory_agent and fix_results.get("changed_files"):
                            await self._synchronize_revision_memory(
                                fix_results["changed_files"]
                            )
                    else:
                        self.logger.warning(
                            f"⚠️ No intelligent fixes could be applied in iteration {iteration}"
                        )
                        # Fallback to basic error handling
                        await self._apply_basic_error_fixes(
                            execution_result.error_traceback, iteration
                        )

                # Brief pause before next iteration
                if iteration < max_iterations and not project_running:
                    await asyncio.sleep(1)

            # Create comprehensive evaluation state with LSP results
            analysis_duration = time.time() - start_time
            self._update_evaluation_state_with_lsp_results(
                error_analysis_reports,
                lsp_fix_applications,
                iteration,
                project_running,
                analysis_duration,
            )

            return True

        except Exception as e:
            self.logger.error(f"❌ LSP-enhanced iterative analysis failed: {e}")
            self.evaluation_state.add_error(f"LSP-enhanced analysis failed: {e}")
            return False

    async def _execute_multi_file_revision_tasks(
        self, client, client_type, tools, revision_tasks, revision_report_data
    ) -> CodeRevisionResult:
        """Execute revision tasks with multi-file batching and memory management"""

        # Track revision progress
        tasks_completed = []
        tasks_failed = []
        files_created = []
        files_modified = []
        revision_issues = []
        execution_logs = []
        batch_operations = []

        total_tasks = len(revision_tasks)
        files_implemented_count = 0

        self.logger.info(
            f"🔄 Starting multi-file batch execution of {total_tasks} tasks"
        )

        # Prepare Code Revise Agent system message with multi-file context
        revision_summary = json.dumps(
            self._make_json_safe(revision_report_data), indent=2
        )

        from prompts.evaluation_prompts import CODE_REVISE_AGENT_PROMPT

        base_system_message = CODE_REVISE_AGENT_PROMPT.format(
            repo_path=self.evaluation_state.repo_path,
            docs_path=self.evaluation_state.docs_path,
            memory_path=self.evaluation_state.memory_path,
            revision_report_summary=revision_summary,
        )

        # Process each revision task with multi-file batching
        for task_index, task in enumerate(revision_tasks):
            task_id = task.get("task_id", f"task_{task_index}")
            priority = task.get("priority", "medium")
            description = task.get("description", "No description")

            self.logger.info(
                f"🔧 Multi-file batch execution: Task {task_index+1}/{total_tasks}"
            )
            self.logger.info(f"   📋 Task ID: {task_id}")
            self.logger.info(f"   🎯 Priority: {priority}")
            self.logger.info(f"   📝 Description: {description}")

            # Start new round in memory agent
            self.memory_agent.start_new_round(iteration=task_index + 1)

            # Extract files to be processed from this task
            files_to_process = self._extract_files_from_revision_task(task)

            if not files_to_process:
                self.logger.warning(
                    f"No files found in task {task_id} after extraction - skipping task"
                )
                tasks_failed.append(task_id)
                execution_logs.append(f"Task {task_id}: FAILED - No files found")
                continue

            self.logger.info(
                f"📁 Files to process in this task: {len(files_to_process)} files"
            )

            # Process files in batches
            task_success = True
            task_files_created = []
            task_files_modified = []
            task_batch_operations = []

            # Split files into batches
            file_batches = self._create_file_batches(
                files_to_process, self.max_files_per_batch
            )
            self.logger.info(
                f"📦 Processing {len(file_batches)} batches for task {task_id}"
            )

            for batch_index, file_batch in enumerate(file_batches):
                self.logger.info(
                    f"📦 Processing batch {batch_index+1}/{len(file_batches)}: {len(file_batch)} files"
                )

                # Execute multi-file batch revision
                batch_result = await self._execute_multi_file_batch_revision(
                    client,
                    client_type,
                    base_system_message,
                    tools,
                    task,
                    file_batch,
                    task_index,
                    batch_index,
                    files_implemented_count,
                )

                # Track batch operations
                task_batch_operations.append(batch_result)
                batch_operations.append(batch_result)

                # Process batch results
                batch_files_created = batch_result.get("files_created", [])
                batch_files_modified = batch_result.get("files_modified", [])
                batch_content_map = batch_result.get("content_map", {})

                task_files_created.extend(batch_files_created)
                task_files_modified.extend(batch_files_modified)
                files_created.extend(batch_files_created)
                files_modified.extend(batch_files_modified)
                files_implemented_count += len(batch_files_created) + len(
                    batch_files_modified
                )

                self.logger.info(
                    f"✅ Batch {batch_index+1} completed: {len(batch_files_created)} created, {len(batch_files_modified)} modified"
                )

                # Generate multi-file code summary after batch completion
                if batch_files_created or batch_files_modified:
                    await self._generate_multi_file_code_summary_after_batch(
                        client, client_type, batch_content_map, files_implemented_count
                    )

                # Record any batch issues
                if batch_result.get("error"):
                    revision_issues.append(
                        f"Task {task_id}, Batch {batch_index+1}: {batch_result['error']}"
                    )
                    self.logger.error(
                        f"❌ Batch {batch_index+1} failed: {batch_result['error']}"
                    )
                    task_success = False

                # Add to execution logs
                execution_logs.append(
                    f"Task {task_id}, Batch {batch_index+1}: {batch_result.get('status', 'unknown')}"
                )

            # Mark task as completed or failed
            if task_success and (task_files_created or task_files_modified):
                tasks_completed.append(task_id)
                self.logger.info(
                    f"✅ Task {task_id} completed successfully with multi-file batching"
                )
                self.logger.info(f"   📄 Files created: {len(task_files_created)}")
                self.logger.info(f"   📝 Files modified: {len(task_files_modified)}")
                self.logger.info(
                    f"   📦 Batches processed: {len(task_batch_operations)}"
                )
            else:
                tasks_failed.append(task_id)
                self.logger.error(
                    f"❌ Task {task_id} failed - no files were created or modified in any batch"
                )

            # Update memory agent with multi-file task completion
            if task_success and (task_files_created or task_files_modified):
                self.memory_agent.record_multi_file_implementation(
                    {
                        file_path: ""
                        for file_path in task_files_created + task_files_modified
                    }
                )

            # Log progress after each task
            completion_rate = (len(tasks_completed) / total_tasks) * 100
            self.logger.info(
                f"📊 Progress: {len(tasks_completed)}/{total_tasks} tasks completed ({completion_rate:.1f}%)"
            )

        # Calculate final statistics
        completion_rate = (
            (len(tasks_completed) / total_tasks) * 100 if total_tasks > 0 else 100.0
        )
        empty_files_implemented = len(
            [t for t in tasks_completed if "empty" in str(t).lower()]
        )
        missing_files_created = len(
            [t for t in tasks_completed if "missing" in str(t).lower()]
        )
        quality_issues_fixed = len(
            [t for t in tasks_completed if "quality" in str(t).lower()]
        )

        # Determine final project health based on completion rate
        if completion_rate >= 90:
            final_health = "excellent"
        elif completion_rate >= 75:
            final_health = "good"
        elif completion_rate >= 50:
            final_health = "needs_work"
        else:
            final_health = "critical"

        # Consider revision successful if completion rate >= 75%
        revision_success = completion_rate >= 75.0

        return CodeRevisionResult(
            revision_success=revision_success,
            tasks_completed=tasks_completed,
            tasks_failed=tasks_failed,
            files_created=files_created,
            files_modified=files_modified,
            empty_files_implemented=empty_files_implemented,
            missing_files_created=missing_files_created,
            quality_issues_fixed=quality_issues_fixed,
            revision_issues=revision_issues,
            final_project_health=final_health,
            execution_logs=execution_logs,
            total_tasks=total_tasks,
            completion_rate=completion_rate,
            batch_operations=batch_operations,
        )

    async def _execute_multi_file_batch_revision(
        self,
        client,
        client_type,
        base_system_message,
        tools,
        task,
        file_batch,
        task_index,
        batch_index,
        files_implemented_count,
    ):
        """Execute revision for a batch of files using multi-file tools with improved instructions"""
        try:
            # Log available tools for this batch
            available_tool_names = [tool.get("name", "") for tool in tools]
            write_multiple_available = "write_multiple_files" in available_tool_names

            self.logger.info(
                f"🔧 Batch {batch_index+1} - Available tools: {len(tools)}"
            )
            self.logger.info(
                f"📦 write_multiple_files available: {write_multiple_available}"
            )

            if not write_multiple_available:
                self.logger.error(
                    f"❌ CRITICAL: write_multiple_files not available for batch {batch_index+1}"
                )
                return {
                    "status": "failed",
                    "files_created": [],
                    "files_modified": [],
                    "content_map": {},
                    "error": "write_multiple_files tool not available",
                    "batch_size": len(file_batch),
                    "tools_used": [],
                }

            # Get current implemented files and all files for memory agent
            current_implemented_files = self._get_current_implemented_files()
            all_files_to_implement = self.evaluation_state.all_files_to_implement

            # Get memory statistics for context
            memory_stats = self.memory_agent.get_memory_statistics(
                all_files=all_files_to_implement,
                implemented_files=current_implemented_files,
            )

            # Determine if this is the first batch for the task
            is_first_batch = batch_index == 0

            # Create revision-specific concise messages
            task_description = task.get(
                "description", "Implement these files according to the plan"
            )

            # Use the memory agent to create concise messages for revision
            messages = self.memory_agent.create_concise_messages_revise(
                base_system_message,
                [],  # Empty original messages since we're using concise mode
                files_implemented_count,
                task_description,
                file_batch,
                is_first_batch,
                current_implemented_files,
                all_files_to_implement,
            )

            # Enhanced system message for this specific call
            enhanced_system_message = f"""{base_system_message}

CRITICAL TOOL USAGE INSTRUCTIONS:
- You MUST eventually call write_multiple_files for batch file implementation
- If needed, you can call other tools first (read_multiple_files, etc.) to understand context
- Parameter name is "file_implementations" (JSON string)
- Implement all requested files in one write_multiple_files call
- Follow the file list provided in the user message

Available tools for this batch: {available_tool_names}"""

            # Initialize batch result tracking
            batch_result = {
                "status": "in_progress",
                "files_created": [],
                "files_modified": [],
                "content_map": {},  # Maps file_path to content
                "error": None,
                "batch_size": len(file_batch),
                "tools_used": [],
                "llm_responses": [],
                "total_iterations": 0,
            }

            write_multiple_files_called = False
            max_iterations = 5  # Prevent infinite loops
            iteration = 0

            # Multi-iteration loop to allow other tools before write_multiple_files
            while iteration < max_iterations and not write_multiple_files_called:
                iteration += 1
                batch_result["total_iterations"] = iteration

                self.logger.info(
                    f"🤖 Batch {batch_index+1}, Iteration {iteration}: Calling LLM"
                )

                # Call LLM with tools for this file batch
                response = await self._call_llm_with_tools(
                    client, client_type, enhanced_system_message, messages, tools
                )

                # Log the LLM response for debugging
                response_content = response.get("content", "")
                batch_result["llm_responses"].append(
                    {
                        "iteration": iteration,
                        "content_length": len(response_content),
                        "tool_calls_count": len(response.get("tool_calls", [])),
                    }
                )

                self.logger.info(
                    f"🤖 Iteration {iteration} response: {len(response_content)} chars, {len(response.get('tool_calls', []))} tool calls"
                )

                # Add assistant response to conversation
                if response_content:
                    messages.append({"role": "assistant", "content": response_content})

                if response.get("tool_calls"):
                    # Process tool calls
                    tool_results_for_conversation = []

                    for tool_call in response["tool_calls"]:
                        tool_name = tool_call["name"]
                        tool_input = tool_call["input"]
                        batch_result["tools_used"].append(
                            f"{tool_name}(iter{iteration})"
                        )

                        self.logger.info(
                            f"🔧 Iteration {iteration}: Processing tool call: {tool_name}"
                        )

                        # Record tool call in memory agent BEFORE execution
                        self.memory_agent.record_tool_result(
                            tool_name, tool_input, None
                        )

                        # Handle write_multiple_files specially
                        if tool_name == "write_multiple_files":
                            write_multiple_files_called = True

                            if "file_implementations" not in tool_input:
                                error_msg = f"write_multiple_files called without file_implementations parameter. Available parameters: {list(tool_input.keys())}"
                                batch_result["error"] = error_msg
                                self.logger.error(f"❌ {error_msg}")
                                tool_results_for_conversation.append(
                                    f"❌ Error: {error_msg}"
                                )
                                continue

                            # Parse file implementations with robust error handling
                            try:
                                file_implementations_str = tool_input[
                                    "file_implementations"
                                ]
                                self.logger.info(
                                    f"📦 file_implementations parameter type: {type(file_implementations_str)}"
                                )
                                self.logger.info(
                                    f"📦 file_implementations content preview: {str(file_implementations_str)[:200]}..."
                                )

                                # Use robust parsing method
                                file_implementations = (
                                    self._parse_file_implementations_robust(
                                        file_implementations_str
                                    )
                                )

                                self.logger.info(
                                    f"📦 Successfully parsed {len(file_implementations)} files after robust processing"
                                )

                                for (
                                    target_file,
                                    file_content,
                                ) in file_implementations.items():
                                    full_file_path = os.path.join(
                                        self.evaluation_state.repo_path, target_file
                                    )

                                    if os.path.exists(full_file_path):
                                        batch_result["files_modified"].append(
                                            target_file
                                        )
                                    else:
                                        batch_result["files_created"].append(
                                            target_file
                                        )

                                    batch_result["content_map"][target_file] = (
                                        file_content
                                    )
                                    self.logger.info(
                                        f"📄 Prepared file: {target_file} ({len(str(file_content))} characters)"
                                    )

                            except Exception as e:
                                error_msg = f"Failed to parse file_implementations after all attempts: {e}"
                                batch_result["error"] = error_msg
                                self.logger.error(f"❌ {error_msg}")

                                # Try to continue with empty implementations for the expected files
                                self.logger.warning(
                                    "⚠️ Creating minimal implementations as fallback..."
                                )
                                file_implementations = {}
                                for file_path in file_batch:
                                    file_implementations[file_path] = (
                                        f"# Implementation for {file_path}\n# Auto-generated due to JSON parsing error\npass\n"
                                    )
                                    batch_result["files_created"].append(file_path)
                                    batch_result["content_map"][file_path] = (
                                        file_implementations[file_path]
                                    )

                                # Update the tool input for execution
                                tool_input["file_implementations"] = json.dumps(
                                    file_implementations
                                )

                        # Execute the tool call through Code Revise Agent
                        try:
                            self.logger.info(self._format_tool_info(tool_name, tool_input))
                            tool_result = await self.mcp_revision_agent.call_tool(
                                tool_name, tool_input
                            )

                            # Update tool result in memory agent AFTER execution
                            for record in reversed(
                                self.memory_agent.current_round_tool_results
                            ):
                                if record["tool_name"] == tool_name:
                                    record["tool_result"] = tool_result
                                    break

                            self.logger.info(
                                f"✅ Tool {tool_name} executed successfully"
                            )

                            # Format tool result for conversation
                            tool_result_content = (
                                self._format_tool_result_for_conversation(
                                    tool_name, tool_result
                                )
                            )
                            tool_results_for_conversation.append(tool_result_content)

                        except Exception as e:
                            error_msg = f"Tool {tool_name} execution failed: {str(e)}"
                            if not batch_result[
                                "error"
                            ]:  # Don't overwrite existing errors
                                batch_result["error"] = error_msg
                            self.logger.error(f"❌ {error_msg}")
                            tool_results_for_conversation.append(
                                f"❌ Tool {tool_name} failed: {str(e)}"
                            )

                    # Add tool results to conversation for next iteration
                    if tool_results_for_conversation:
                        compiled_tool_response = (
                            self._compile_tool_results_for_conversation(
                                tool_results_for_conversation,
                                write_multiple_files_called,
                                len(file_batch),
                            )
                        )
                        messages.append(
                            {"role": "user", "content": compiled_tool_response}
                        )

                else:
                    # No tool calls - provide guidance
                    self.logger.warning(
                        f"❌ No tool calls in iteration {iteration} for batch {batch_index+1}"
                    )
                    no_tools_guidance = f"""⚠️ No tool calls detected. You need to use tools to implement the {len(file_batch)} files.

Available tools: {available_tool_names}

REQUIRED ACTION:
1. If you need context: Use read_multiple_files, search_code, or other analysis tools first
2. THEN use write_multiple_files to implement all {len(file_batch)} files in one call
3. Files to implement: {file_batch}

Please call the appropriate tools now."""

                    messages.append({"role": "user", "content": no_tools_guidance})

            # Final status determination
            if write_multiple_files_called:
                batch_result["status"] = "completed"
                self.logger.info(
                    f"✅ write_multiple_files successfully called for batch {batch_index+1} after {iteration} iterations"
                )
            else:
                batch_result["status"] = "failed"
                if not batch_result["error"]:
                    batch_result["error"] = (
                        f"write_multiple_files was not called after {max_iterations} iterations - agent failed to implement {len(file_batch)} files"
                    )
                self.logger.error(
                    f"❌ CRITICAL: write_multiple_files not called for batch {batch_index+1} after {iteration} iterations"
                )

            return batch_result

        except Exception as e:
            self.logger.error(
                f"❌ Exception in _execute_multi_file_batch_revision: {e}"
            )
            return {
                "status": "failed",
                "files_created": [],
                "files_modified": [],
                "content_map": {},
                "error": str(e),
                "batch_size": len(file_batch),
                "tools_used": [],
                "exception": str(e),
            }

    async def _analyze_execution_error(
        self, error_traceback: str, iteration: int
    ) -> List[Dict[str, Any]]:
        """Analyze execution error and identify suspect files"""
        try:
            # Simple pattern matching for common error patterns
            suspect_files = []

            # Extract file paths from traceback
            file_pattern = r'File "([^"]+\.py)"'
            file_matches = re.findall(file_pattern, error_traceback)

            for file_path in file_matches:
                # Convert absolute path to relative path
                relative_path = os.path.relpath(
                    file_path, self.evaluation_state.repo_path
                )

                suspect_files.append(
                    {
                        "file_path": relative_path,
                        "confidence": 0.8,  # High confidence for files in traceback
                        "reasons": ["File appears in error traceback"],
                        "error_type": self._extract_error_type(error_traceback),
                    }
                )

            # Handle ModuleNotFoundError specially
            if (
                "ModuleNotFoundError" in error_traceback
                or "ImportError" in error_traceback
            ):
                module_match = re.search(
                    r"No module named ['\"]([^'\"]+)['\"]", error_traceback
                )
                if module_match:
                    missing_module = module_match.group(1)
                    # Check if it's a local module
                    potential_file = f"{missing_module.replace('.', '/')}.py"
                    suspect_files.append(
                        {
                            "file_path": potential_file,
                            "confidence": 0.9,
                            "reasons": [
                                "Missing module could be implemented as local file"
                            ],
                            "error_type": "ImportError",
                        }
                    )

            self.logger.info(
                f"🔍 Iteration {iteration}: Identified {len(suspect_files)} suspect files"
            )
            return suspect_files

        except Exception as e:
            self.logger.error(f"❌ Error analysis failed: {e}")
            return []

    async def _apply_lsp_targeted_fixes(
        self, error_report: Dict[str, Any], execution_result, iteration: int
    ) -> List[Dict[str, Any]]:
        """Apply LSP-powered targeted fixes based on comprehensive error analysis"""
        try:
            applied_fixes = []
            analysis_data = error_report.get("error_analysis_report", {})
            suspect_files = analysis_data.get("suspect_files", [])

            for suspect_file in suspect_files[:5]:  # Top 5 suspects
                file_path = suspect_file["file_path"]
                confidence = suspect_file["confidence_score"]
                error_context = suspect_file.get("error_context", [])
                lsp_symbols = suspect_file.get("lsp_symbols", [])

                self.logger.info(
                    f"🔧 LSP-powered fix for {file_path} (confidence: {confidence:.2f})"
                )

                # Step 1: Get LSP diagnostics and apply fixes
                try:
                    tool_name = "lsp_get_diagnostics"
                    tool_input = {
                        "repo_path": self.evaluation_state.repo_path,
                        "file_path": file_path,
                    }
                    self.logger.info(self._format_tool_info(tool_name, tool_input))
                    diagnostics_result = await self.mcp_revision_agent.call_tool(
                        tool_name, tool_input
                    )

                    if diagnostics_result:
                        diagnostics_content = self._extract_tool_result_content(
                            diagnostics_result
                        )
                        diagnostics_data = json.loads(diagnostics_content)
                        if diagnostics_data.get("status") == "success":
                            diagnostics = diagnostics_data.get("diagnostics", [])

                            # Step 2: Process error context to determine fix ranges
                            for context in error_context[:3]:  # Top 3 error contexts
                                line_num = context.get("line_number", 1)
                                function_name = context.get("function_name", "")

                                # Step 3: Generate precise code fixes using LSP
                                tool_name_fixes = "lsp_generate_code_fixes"
                                tool_input_fixes = {
                                    "repo_path": self.evaluation_state.repo_path,
                                    "file_path": file_path,
                                    "start_line": max(0, line_num - 2),
                                    "end_line": line_num + 2,
                                    "error_context": f"Function: {function_name}, Error: {context.get('code_line', '')}",
                                }
                                self.logger.info(self._format_tool_info(tool_name_fixes, tool_input_fixes))
                                fixes_result = await self.mcp_revision_agent.call_tool(
                                    tool_name_fixes, tool_input_fixes
                                )

                                if fixes_result:
                                    fixes_content = self._extract_tool_result_content(
                                        fixes_result
                                    )
                                    fixes_data = json.loads(fixes_content)
                                    if fixes_data.get("status") == "success":
                                        fix_proposals = fixes_data.get(
                                            "fix_proposals", []
                                        )

                                        # Step 4: Apply the best fix proposal
                                        for fix_proposal in fix_proposals[
                                            :1
                                        ]:  # Apply top fix
                                            if fix_proposal.get("edit"):
                                                # Apply LSP workspace edit
                                                tool_name_edit = "lsp_apply_workspace_edit"
                                                tool_input_edit = {
                                                    "repo_path": self.evaluation_state.repo_path,
                                                    "workspace_edit": json.dumps(
                                                        fix_proposal["edit"]
                                                    ),
                                                }
                                                self.logger.info(self._format_tool_info(tool_name_edit, tool_input_edit))
                                                edit_result = await self.mcp_revision_agent.call_tool(
                                                    tool_name_edit, tool_input_edit
                                                )

                                                if edit_result:
                                                    edit_content = self._extract_tool_result_content(
                                                        edit_result
                                                    )
                                                    edit_data = json.loads(edit_content)
                                                    if (
                                                        edit_data.get("status")
                                                        == "success"
                                                    ):
                                                        applied_fixes.append(
                                                            {
                                                                "file_path": file_path,
                                                                "function_name": function_name,
                                                                "line_number": line_num,
                                                                "fix_type": "lsp_workspace_edit",
                                                                "confidence": confidence,
                                                                "success": True,
                                                                "files_changed": edit_data.get(
                                                                    "total_files_changed",
                                                                    0,
                                                                ),
                                                                "iteration": iteration,
                                                            }
                                                        )
                                                        self.logger.info(
                                                            f"✅ Applied LSP workspace edit to {file_path}"
                                                        )
                                                    else:
                                                        self.logger.warning(
                                                            f"⚠️ Failed to apply LSP workspace edit to {file_path}"
                                                        )
                except Exception as e:
                    self.logger.warning(
                        f"⚠️ LSP diagnostics/actions failed for {file_path}: {e}"
                    )

                # Alternative: If no LSP actions available, try precise code generation
                if not any(
                    fix.get("success")
                    for fix in applied_fixes
                    if fix["file_path"] == file_path
                ):
                    try:
                        # Use generate_precise_code_fixes for fallback
                        tool_name_precise = "generate_precise_code_fixes"
                        tool_input_precise = {
                            "error_analysis_report": json.dumps(error_report),
                            "target_files": [file_path],
                            "fix_strategy": "targeted",
                        }
                        self.logger.info(self._format_tool_info(tool_name_precise, tool_input_precise))
                        precise_fixes_result = await self.mcp_revision_agent.call_tool(
                            tool_name_precise, tool_input_precise
                        )

                        if precise_fixes_result:
                            precise_fixes_content = self._extract_tool_result_content(
                                precise_fixes_result
                            )
                            precise_fixes_data = json.loads(precise_fixes_content)
                            if precise_fixes_data.get("status") == "success":
                                # Apply the generated fixes
                                tool_name_apply = "apply_code_fixes_with_diff"
                                tool_input_apply = {
                                    "fixes_json": precise_fixes_content,
                                    "repo_path": self.evaluation_state.repo_path,
                                    "dry_run": False,
                                }
                                self.logger.info(self._format_tool_info(tool_name_apply, tool_input_apply))
                                apply_fixes_result = await self.mcp_revision_agent.call_tool(
                                    tool_name_apply, tool_input_apply
                                )

                                if apply_fixes_result:
                                    apply_content = self._extract_tool_result_content(
                                        apply_fixes_result
                                    )
                                    apply_data = json.loads(apply_content)
                                    if apply_data.get("status") == "success":
                                        successful_apps = apply_data.get(
                                            "successful_applications", 0
                                        )
                                        if successful_apps > 0:
                                            applied_fixes.append(
                                                {
                                                    "file_path": file_path,
                                                    "fix_type": "precise_code_fix",
                                                    "confidence": confidence,
                                                    "success": True,
                                                    "fixes_applied": successful_apps,
                                                    "iteration": iteration,
                                                }
                                            )
                                            self.logger.info(
                                                f"✅ Applied {successful_apps} precise fixes to {file_path}"
                                            )
                    except Exception as e:
                        self.logger.warning(
                            f"⚠️ Precise code fixes failed for {file_path}: {e}"
                        )

            return applied_fixes

        except Exception as e:
            self.logger.error(f"❌ LSP-powered targeted fixes failed: {e}")
            return []

    def _extract_tool_result_content(self, tool_result):
        """Extract content from CallToolResult object"""
        try:
            if hasattr(tool_result, "content"):
                content = tool_result.content
                if isinstance(content, list) and len(content) > 0:
                    if hasattr(content[0], "text"):
                        return content[0].text
                    else:
                        return str(content[0])
                else:
                    return str(content)
            else:
                return str(tool_result)
        except Exception as e:
            self.logger.warning(f"⚠️ Failed to extract tool result content: {e}")
            return str(tool_result)

    async def _execute_intelligent_error_fixing(
        self, execution_result, iteration: int
    ) -> dict:
        """
        Intelligent error fixing using agent decision-making and tool selection
        Similar to _execute_multi_file_batch_revision but focused on error analysis and fixing
        """
        try:
            self.logger.info(
                f"🤖 Starting intelligent error fixing for iteration {iteration}"
            )

            # Initialize components
            client, client_type = await self._initialize_llm_client()
            tools = self._prepare_error_fixing_tool_definitions()

            # Create error context and system message
            error_context = self._create_error_context(execution_result, iteration)
            system_message = self._create_error_fixing_system_message(
                tools, error_context
            )
            user_message = self._create_error_fixing_user_message(error_context)

            # Initialize result tracking
            fixing_result = self._initialize_fixing_result()

            # Execute agent conversation loop
            fixing_result = await self._execute_error_fixing_conversation(
                client, client_type, system_message, user_message, tools, fixing_result
            )

            # Finalize and return result
            return self._finalize_fixing_result(fixing_result)

        except Exception as e:
            self.logger.error(f"❌ Intelligent error fixing failed: {e}")
            return self._create_error_fixing_failure_result(str(e))

    def _create_error_context(self, execution_result, iteration: int) -> dict:
        """Create error analysis context"""
        return {
            "error_traceback": execution_result.error_traceback,
            "stdout": execution_result.stdout,
            "stderr": execution_result.stderr,
            "exit_code": execution_result.exit_code,
            "command": execution_result.command,
            "working_directory": execution_result.working_directory,
            "iteration": iteration,
        }

    def _create_error_fixing_system_message(self, tools, error_context: dict) -> str:
        """Create system message for intelligent error fixing"""
        from prompts.evaluation_prompts import CODE_REVISE_AGENT_PROMPT

        return f"""{CODE_REVISE_AGENT_PROMPT.format(
            repo_path=self.evaluation_state.repo_path,
            docs_path=self.evaluation_state.docs_path,
            memory_path=self.evaluation_state.memory_path,
            revision_report_summary="Error fixing context"
        )}

INTELLIGENT ERROR FIXING MODE:
You are now in intelligent error fixing mode. Your task is to:

1. ANALYZE the execution error thoroughly
2. DECIDE which tools to use for analysis and fixing
3. EXECUTE the appropriate fixes based on your analysis

Available error analysis and fixing tools:
{[tool.get("name", "") for tool in tools]}

CRITICAL INSTRUCTIONS:
- First, analyze the error using appropriate tools (generate_error_analysis_report, lsp_get_diagnostics, etc.)
- Then decide on the best fixing strategy based on the analysis results
- Apply fixes using the most suitable tools (lsp_generate_code_fixes, apply_code_fixes_with_diff, etc.)
- You can use multiple tools in sequence to achieve the best results
- Always explain your reasoning for tool selection

Current error context:
- Exit code: {error_context['exit_code']}
- Command: {error_context['command']}
- Working directory: {error_context['working_directory']}"""

    def _create_error_fixing_user_message(self, error_context: dict) -> str:
        """Create initial user message with error information"""
        return f"""I need your help to fix an execution error in iteration {error_context['iteration']}.

ERROR DETAILS:
=============
Exit Code: {error_context['exit_code']}
Command: {error_context['command']}
Working Directory: {error_context['working_directory']}

STDERR:
{error_context['stderr'][:2000]}{'...' if len(error_context['stderr']) > 2000 else ''}

STDOUT:
{error_context['stdout'][:1000]}{'...' if len(error_context['stdout']) > 1000 else ''}

ERROR TRACEBACK:
{error_context['error_traceback'][:2000]}{'...' if len(error_context['error_traceback']) > 2000 else ''}

Please analyze this error intelligently and apply the most appropriate fixes using the available tools.
Start by analyzing the error, then decide on the fixing strategy, and finally implement the fixes."""

    def _initialize_fixing_result(self) -> dict:
        """Initialize result tracking structure"""
        return {
            "status": "in_progress",
            "analysis_report": {},
            "fixes_applied": [],
            "changed_files": [],
            "tools_used": [],
            "agent_reasoning": [],
            "total_iterations": 0,
            "error": None,
        }

    async def _execute_error_fixing_conversation(
        self, client, client_type, system_message, user_message, tools, fixing_result
    ) -> dict:
        """Execute the multi-iteration conversation with agent for error fixing"""
        messages = [{"role": "user", "content": user_message}]
        max_iterations = 50
        agent_iteration = 0
        fixing_completed = False

        while agent_iteration < max_iterations and not fixing_completed:
            agent_iteration += 1
            fixing_result["total_iterations"] = agent_iteration

            self.logger.info(
                f"🤖 Error fixing iteration {agent_iteration}/{max_iterations}"
            )

            # Get agent response
            response = await self._call_llm_with_tools(
                client, client_type, system_message, messages, tools
            )

            # Process response and update conversation
            fixing_completed = await self._process_error_fixing_response(
                response, messages, fixing_result, agent_iteration
            )

        return fixing_result

    async def _process_error_fixing_response(
        self, response, messages, fixing_result, agent_iteration
    ) -> bool:
        """Process agent response and execute tool calls"""
        response_content = response.get("content", "")
        fixing_result["agent_reasoning"].append(
            {
                "iteration": agent_iteration,
                "content": response_content,
                "tool_calls_count": len(response.get("tool_calls", [])),
            }
        )

        self.logger.info(
            f"🤖 Agent iteration {agent_iteration}: {len(response_content)} chars, {len(response.get('tool_calls', []))} tool calls"
        )

        # Add assistant response to conversation
        if response_content:
            messages.append({"role": "assistant", "content": response_content})

        if response.get("tool_calls"):
            # Process tool calls
            tool_results = await self._execute_error_fixing_tool_calls(
                response["tool_calls"], fixing_result, agent_iteration
            )

            # Add tool results to conversation
            if tool_results:
                tool_results_message = "\n\n".join(tool_results)
                messages.append(
                    {
                        "role": "user",
                        "content": f"Tool execution results:\n\n{tool_results_message}",
                    }
                )

            # Check if fixing is completed
            return len(fixing_result["fixes_applied"]) > 0

        else:
            # No tool calls - check if agent is done
            if "fix" in response_content.lower() and (
                "complete" in response_content.lower()
                or "done" in response_content.lower()
            ):
                self.logger.info("🎯 Agent indicates fixing is complete")
                return True
            elif agent_iteration >= 7:  # Near max iterations
                self.logger.warning(
                    "⚠️ Reached near maximum iterations without tool calls"
                )
                return True
            else:
                # Prompt agent to continue
                messages.append(
                    {
                        "role": "user",
                        "content": "Please continue with error analysis and fixing using the available tools, or let me know if you're done.",
                    }
                )
                return False

    async def _execute_error_fixing_tool_calls(
        self, tool_calls, fixing_result, agent_iteration
    ) -> List[str]:
        """Execute tool calls and process results"""
        tool_results_for_conversation = []

        for tool_call in tool_calls:
            tool_name = tool_call["name"]
            tool_input = tool_call["input"]
            fixing_result["tools_used"].append(f"{tool_name}(iter{agent_iteration})")

            self.logger.info(
                f"🔧 Agent iteration {agent_iteration}: Processing tool call: {tool_name}"
            )

            try:
                self.logger.info(self._format_tool_info(tool_name, tool_input))
                tool_result = await self.mcp_revision_agent.call_tool(
                    tool_name, tool_input
                )
                tool_result_content = self._extract_tool_result_content(tool_result)

                # Process specific tool results
                await self._process_specific_tool_result(
                    tool_name, tool_result_content, fixing_result
                )

                # Create summary for conversation
                tool_result_summary = self._create_tool_result_summary(
                    tool_name, tool_result_content
                )
                tool_results_for_conversation.append(tool_result_summary)

            except Exception as e:
                error_msg = f"Tool {tool_name} failed: {str(e)}"
                self.logger.error(f"❌ {error_msg}")
                tool_results_for_conversation.append(f"❌ {error_msg}")

        return tool_results_for_conversation

    async def _process_specific_tool_result(
        self, tool_name, tool_result_content, fixing_result
    ):
        """Process specific tool results and update fixing_result"""
        try:
            if tool_name == "generate_error_analysis_report":
                analysis_data = json.loads(tool_result_content)
                if analysis_data.get("status") == "success":
                    fixing_result["analysis_report"] = analysis_data
                    self.logger.info("📊 Error analysis completed successfully")

            elif tool_name in [
                "lsp_generate_code_fixes",
                "generate_precise_code_fixes",
            ]:
                fix_data = json.loads(tool_result_content)
                if fix_data.get("status") == "success":
                    fixes = fix_data.get("generated_fixes", []) or fix_data.get(
                        "fix_proposals", []
                    )
                    self.logger.info(f"🛠️ Generated {len(fixes)} code fixes")

            elif tool_name == "apply_code_fixes_with_diff":
                apply_data = json.loads(tool_result_content)
                if apply_data.get("status") == "success":
                    successful_applications = apply_data.get(
                        "successful_applications", 0
                    )
                    application_results = apply_data.get("application_results", [])

                    # Track applied fixes and changed files
                    for result in application_results:
                        if result.get("success"):
                            fixing_result["fixes_applied"].append(result)
                            if result.get("file_path"):
                                fixing_result["changed_files"].append(
                                    result["file_path"]
                                )

                    self.logger.info(
                        f"✅ Applied {successful_applications} code fixes successfully"
                    )

        except json.JSONDecodeError:
            self.logger.error(f"❌ Failed to parse {tool_name} result")
        except Exception as e:
            self.logger.error(f"❌ Error processing {tool_name} result: {e}")

    def _create_tool_result_summary(self, tool_name, tool_result_content) -> str:
        """Create a summary of tool result for conversation"""
        summary = f"Tool {tool_name} executed successfully. Result length: {len(tool_result_content)} characters."
        if len(tool_result_content) < 1000:
            summary += f"\nResult: {tool_result_content}"
        else:
            summary += f"\nResult preview: {tool_result_content[:500]}..."
        return summary

    def _finalize_fixing_result(self, fixing_result) -> dict:
        """Finalize the fixing result with appropriate status"""
        if fixing_result["fixes_applied"]:
            fixing_result["status"] = "success"
            self.logger.info(
                f"✅ Intelligent error fixing completed: {len(fixing_result['fixes_applied'])} fixes applied"
            )
        else:
            fixing_result["status"] = "no_fixes_applied"
            fixing_result["error"] = "No fixes were successfully applied"
            self.logger.warning(
                "⚠️ Intelligent error fixing completed but no fixes were applied"
            )

        return fixing_result

    def _create_error_fixing_failure_result(self, error_message: str) -> dict:
        """Create failure result structure"""
        return {
            "status": "error",
            "error": error_message,
            "analysis_report": {},
            "fixes_applied": [],
            "changed_files": [],
            "tools_used": [],
            "agent_reasoning": [],
            "total_iterations": 0,
        }

    async def _execute_intelligent_revision_conversation(
        self, client, client_type, tools, revision_tasks, revision_report_data
    ) -> dict:
        """Execute intelligent multi-round conversation for code revision - process each task independently"""
        try:
            self.logger.info(
                f"🤖 Starting intelligent revision conversation for {len(revision_tasks)} tasks"
            )

            # Initialize overall result tracking
            overall_result = {
                "success": False,
                "total_tasks": len(revision_tasks),
                "completed_tasks": 0,
                "failed_tasks": 0,
                "task_results": [],
                "files_implemented": [],
                "tools_used": [],
                "total_iterations": 0,
                "error": None,
            }

            # Process each task independently
            for task_index, task in enumerate(revision_tasks):
                self.logger.info(
                    f"🎯 Processing Task {task_index + 1}/{len(revision_tasks)}: {task.get('description', 'Unknown task')}"
                )

                # Execute single task conversation
                task_result = await self._execute_single_task_conversation(
                    client,
                    client_type,
                    tools,
                    task,
                    task_index + 1,
                    revision_report_data,
                )

                # Update overall results
                overall_result["task_results"].append(task_result)
                overall_result["total_iterations"] += task_result.get(
                    "total_iterations", 0
                )
                overall_result["tools_used"].extend(task_result.get("tools_used", []))
                overall_result["files_implemented"].extend(
                    task_result.get("files_implemented", [])
                )

                if task_result.get("success", False):
                    overall_result["completed_tasks"] += 1
                    self.logger.info(f"✅ Task {task_index + 1} completed successfully")
                else:
                    overall_result["failed_tasks"] += 1
                    self.logger.warning(
                        f"❌ Task {task_index + 1} failed: {task_result.get('error', 'Unknown error')}"
                    )

                    # Check if we should continue with remaining tasks
                    if task.get("priority") == "high" and not task_result.get(
                        "success"
                    ):
                        self.logger.warning(
                            "⚠️ High priority task failed, but continuing with remaining tasks"
                        )

            # Determine overall success
            overall_result["success"] = overall_result["completed_tasks"] > 0
            completion_rate = (
                overall_result["completed_tasks"] / overall_result["total_tasks"]
            ) * 100

            self.logger.info(
                f"📊 Revision completion: {overall_result['completed_tasks']}/{overall_result['total_tasks']} tasks ({completion_rate:.1f}%)"
            )

            return overall_result

        except Exception as e:
            self.logger.error(f"❌ Intelligent revision conversation failed: {e}")
            return {
                "success": False,
                "total_tasks": len(revision_tasks),
                "completed_tasks": 0,
                "failed_tasks": len(revision_tasks),
                "task_results": [],
                "files_implemented": [],
                "tools_used": [],
                "total_iterations": 0,
                "error": str(e),
            }

    async def _execute_single_task_conversation(
        self, client, client_type, tools, task, task_number, revision_report_data
    ) -> dict:
        """Execute intelligent multi-round conversation for a single revision task"""
        try:
            task_id = task.get("task_id", f"task_{task_number}")
            task_description = task.get("description", "Unknown task")
            task_priority = task.get("priority", "medium")

            self.logger.info(
                f"🎯 Starting conversation for {task_id}: {task_description}"
            )

            # Prepare system message for single task
            from prompts.evaluation_prompts import CODE_REVISE_AGENT_PROMPT

            task_system_message = f"""{CODE_REVISE_AGENT_PROMPT.format(
                repo_path=self.evaluation_state.repo_path,
                docs_path=self.evaluation_state.docs_path,
                memory_path=self.evaluation_state.memory_path
            )}

## CURRENT TASK
Task: {task_id} - {task_description}
Priority: {task_priority}

Task Details: {json.dumps(task, indent=2)}

**IMPORTANT**: Before implementing, review Documentation Path to understand the reproduction plan.

Available tools: {[tool.get("name", "") for tool in tools]}"""

            # Create initial user message for this task
            user_message = f"""Please complete the following revision task:

Task: {task_description}
Priority: {task_priority}
Task ID: {task_id}

{f"Additional Details: {task.get('details', 'No additional details')}" if task.get('details') else ""}

Please analyze what needs to be done for this specific task and implement the necessary changes.
Use the available tools effectively and explain your approach."""

            # Initialize task result tracking
            task_result = {
                "task_id": task_id,
                "task_description": task_description,
                "success": False,
                "files_implemented": [],
                "tools_used": [],
                "agent_reasoning": [],
                "total_iterations": 0,
                "completion_details": None,
                "error": None,
            }

            # Execute conversation loop for this task
            messages = [{"role": "user", "content": user_message}]
            max_iterations = 30  # Reasonable limit per task
            iteration = 0
            task_completed = False

            while iteration < max_iterations and not task_completed:
                iteration += 1
                task_result["total_iterations"] = iteration

                self.logger.info(
                    f"🔄 Task {task_id} iteration {iteration}/{max_iterations}"
                )

                # Get agent response
                response = await self._call_llm_with_tools(
                    client, client_type, task_system_message, messages, tools
                )

                # Process response and check for task completion
                task_completed = await self._process_single_task_response(
                    response, messages, task_result, iteration, task
                )

                # Check termination conditions
                if task_completed:
                    self.logger.info(
                        f"✅ Task {task_id} completed in {iteration} iterations"
                    )
                    task_result["success"] = True
                elif iteration >= max_iterations:
                    self.logger.warning(
                        f"⚠️ Task {task_id} reached maximum iterations ({max_iterations})"
                    )
                    task_result["error"] = (
                        "Reached maximum iterations without completing task"
                    )
                    break

            return task_result

        except Exception as e:
            self.logger.error(
                f"❌ Single task conversation failed for {task.get('task_id', 'unknown')}: {e}"
            )
            return {
                "task_id": task.get("task_id", f"task_{task_number}"),
                "task_description": task.get("description", "Unknown task"),
                "success": False,
                "files_implemented": [],
                "tools_used": [],
                "agent_reasoning": [],
                "total_iterations": 0,
                "completion_details": None,
                "error": str(e),
            }

    async def _process_single_task_response(
        self, response, messages, task_result, iteration, task
    ) -> bool:
        """Process agent response for a single task"""
        try:
            # Extract response content
            response_content = response.get("content", "").strip()
            task_result["agent_reasoning"].append(
                f"Iteration {iteration}: {response_content[:200]}..."
            )

            # Add response to conversation
            messages.append({"role": "assistant", "content": response_content})

            if response.get("tool_calls"):
                # Process tool calls for this task
                tool_results = await self._execute_single_task_tool_calls(
                    response["tool_calls"], task_result, iteration, task
                )

                # Add tool results to conversation
                if tool_results:
                    tool_results_summary = "Tool execution results:\n" + "\n".join(
                        tool_results
                    )
                    messages.append({"role": "user", "content": tool_results_summary})

                # Create continuation prompt for this specific task
                continuation_prompt = self._create_single_task_continuation_prompt(
                    task_result, task, iteration
                )
                if continuation_prompt:
                    messages.append({"role": "user", "content": continuation_prompt})
            else:
                # No tool calls, add encouraging prompt
                messages.append(
                    {
                        "role": "user",
                        "content": f"Please use the available tools to complete this task: {task.get('description', 'Unknown task')}. What specific actions do you need to take?",
                    }
                )

            # Check for task completion indicators
            completion_indicators = [
                "task complete",
                "task finished",
                "task done",
                "implementation complete",
                "changes complete",
                "revision complete",
                "successfully completed",
                "task is now complete",
                "finished implementing",
            ]

            if any(
                indicator in response_content.lower()
                for indicator in completion_indicators
            ):
                self.logger.info("🎯 Task completion indicated by agent response")
                task_result["completion_details"] = "Agent indicated task completion"
                return True

            # Check if reasonable progress has been made
            if (
                len(task_result["files_implemented"]) > 0
                or len(task_result["tools_used"]) >= 2
            ):
                # Agent has made progress, check if they consider it complete
                if iteration >= 5:  # Give more iterations for complex tasks
                    self.logger.info(
                        f"🎯 Task appears complete based on progress after {iteration} iterations"
                    )
                    task_result["completion_details"] = (
                        f"Task completed after {iteration} iterations with substantial progress"
                    )
                    return True

            return False

        except Exception as e:
            self.logger.error(f"❌ Error processing single task response: {e}")
            task_result["agent_reasoning"].append(
                f"Error in iteration {iteration}: {str(e)}"
            )
            return False

    async def _execute_single_task_tool_calls(
        self, tool_calls, task_result, iteration, task
    ) -> List[str]:
        """Execute tool calls for a single task"""
        tool_results_for_conversation = []

        for tool_call in tool_calls:
            tool_name = tool_call["name"]
            tool_input = tool_call["input"]
            task_result["tools_used"].append(
                f"{tool_name}(task:{task.get('task_id', 'unknown')}_iter{iteration})"
            )

            self.logger.info(
                f"🔧 Task {task.get('task_id', 'unknown')} iteration {iteration}: Processing tool call: {tool_name}"
            )

            try:
                self.logger.info(self._format_tool_info(tool_name, tool_input))
                tool_result = await self.mcp_revision_agent.call_tool(
                    tool_name, tool_input
                )
                tool_result_content = self._extract_tool_result_content(tool_result)

                # Process specific tool results for this task
                await self._process_single_task_tool_result(
                    tool_name, tool_result_content, task_result, task
                )

                # Create summary for conversation
                tool_result_summary = self._create_single_task_tool_result_summary(
                    tool_name, tool_result_content, task
                )
                tool_results_for_conversation.append(tool_result_summary)

            except Exception as e:
                error_msg = f"Tool {tool_name} failed for task {task.get('task_id', 'unknown')}: {str(e)}"
                self.logger.error(f"❌ {error_msg}")
                tool_results_for_conversation.append(f"❌ {error_msg}")

        return tool_results_for_conversation

    async def _process_single_task_tool_result(
        self, tool_name, tool_result_content, task_result, task
    ):
        """Process specific tool results for a single task"""
        try:
            # Track files implemented by this task
            if tool_name == "write_multiple_files":
                # Try to extract file information from result
                if "successfully" in tool_result_content.lower():
                    # Parse file names from the result if possible
                    import re

                    file_matches = re.findall(r"[\w/\-\.]+\.py", tool_result_content)
                    if file_matches:
                        task_result["files_implemented"].extend(file_matches)
                        self.logger.info(
                            f"📝 Task {task.get('task_id', 'unknown')} implemented files: {file_matches}"
                        )

            elif tool_name in ["read_multiple_files", "read_file", "list_directory"]:
                # Information gathering tools
                self.logger.info(
                    f"📖 Task {task.get('task_id', 'unknown')} gathered information using {tool_name}"
                )

        except Exception as e:
            self.logger.error(
                f"❌ Error processing tool result for task {task.get('task_id', 'unknown')}: {e}"
            )

    def _create_single_task_continuation_prompt(
        self, task_result, task, iteration
    ) -> str:
        """Create continuation prompt for a single task"""
        task_id = task.get("task_id", "unknown")

        if len(task_result["files_implemented"]) == 0 and iteration > 2:
            return f"You haven't implemented any files yet for task '{task_id}'. Please use write_multiple_files or other implementation tools to complete this specific task."

        if len(task_result["tools_used"]) > 5 and iteration > 6:
            return f"You've used many tools for task '{task_id}'. Please summarize what you've accomplished and if this task is complete."

        return f"Please continue working on task '{task_id}': {task.get('description', 'No description')}. What's your next step?"

    def _create_single_task_tool_result_summary(
        self, tool_name, tool_result_content, task
    ) -> str:
        """Create tool result summary for a single task"""
        try:
            if tool_name == "write_multiple_files":
                if "successfully" in tool_result_content.lower():
                    return f"✅ {tool_name}: Files implemented successfully for task {task.get('task_id', 'unknown')}"
                else:
                    return f"⚠️ {tool_name}: Some issues occurred - {tool_result_content[:100]}..."

            elif tool_name in ["read_multiple_files", "read_file"]:
                return f"📖 {tool_name}: Information gathered for task {task.get('task_id', 'unknown')}"

            elif "analysis" in tool_name.lower():
                return f"🔍 {tool_name}: Analysis completed for task {task.get('task_id', 'unknown')}"

            else:
                return f"🔧 {tool_name}: {tool_result_content[:100]}..."

        except Exception:
            return f"🔧 {tool_name}: Tool executed for task {task.get('task_id', 'unknown')}"

    async def _process_revision_response(
        self, response, messages, revision_result, iteration, revision_tasks
    ) -> bool:
        """Process agent response for revision phase"""
        response_content = response.get("content", "")
        revision_result["agent_reasoning"].append(
            {
                "iteration": iteration,
                "content": response_content,
                "tool_calls_count": len(response.get("tool_calls", [])),
            }
        )

        self.logger.info(
            f"🤖 Revision iteration {iteration}: {len(response_content)} chars, {len(response.get('tool_calls', []))} tool calls"
        )

        # Add assistant response to conversation
        if response_content:
            messages.append({"role": "assistant", "content": response_content})

        if response.get("tool_calls"):
            # Process tool calls
            tool_results = await self._execute_revision_tool_calls(
                response["tool_calls"], revision_result, iteration
            )

            # Add tool results to conversation
            if tool_results:
                tool_results_message = "\n\n".join(tool_results)
                messages.append(
                    {
                        "role": "user",
                        "content": f"Tool execution results:\n\n{tool_results_message}",
                    }
                )

            return False  # Continue conversation after tool calls

        else:
            # No tool calls - check if agent is done
            if any(
                keyword in response_content.lower()
                for keyword in [
                    "revision complete",
                    "revision finished",
                    "all tasks completed",
                    "implementation done",
                ]
            ):
                self.logger.info("🎯 Agent indicates revision is complete")
                return True
            elif iteration >= 11:  # Near max iterations
                self.logger.warning("⚠️ Reached near maximum iterations")
                return True
            else:
                # Prompt agent to continue
                continuation_prompt = self._create_revision_continuation_prompt(
                    revision_result, revision_tasks
                )
                messages.append({"role": "user", "content": continuation_prompt})
                return False

    async def _execute_revision_tool_calls(
        self, tool_calls, revision_result, iteration
    ) -> List[str]:
        """Execute revision tool calls and process results"""
        tool_results_for_conversation = []

        for tool_call in tool_calls:
            tool_name = tool_call["name"]
            tool_input = tool_call["input"]
            revision_result["tools_used"].append(f"{tool_name}(iter{iteration})")

            self.logger.info(
                f"🔧 Revision iteration {iteration}: Processing tool call: {tool_name}"
            )

            try:
                self.logger.info(self._format_tool_info(tool_name, tool_input))
                tool_result = await self.mcp_revision_agent.call_tool(
                    tool_name, tool_input
                )
                tool_result_content = self._extract_tool_result_content(tool_result)

                # Process specific revision tool results
                await self._process_specific_revision_tool_result(
                    tool_name, tool_result_content, revision_result
                )

                # Create summary for conversation
                tool_result_summary = self._create_tool_result_summary(
                    tool_name, tool_result_content
                )
                tool_results_for_conversation.append(tool_result_summary)

            except Exception as e:
                error_msg = f"Tool {tool_name} failed: {str(e)}"
                self.logger.error(f"❌ {error_msg}")
                tool_results_for_conversation.append(f"❌ {error_msg}")

        return tool_results_for_conversation

    async def _process_specific_revision_tool_result(
        self, tool_name, tool_result_content, revision_result
    ):
        """Process specific revision tool results"""
        try:
            if tool_name == "write_multiple_files":
                # Track successful file implementations
                if "successfully" in tool_result_content.lower():
                    revision_result["tasks_processed"] += 1
                    self.logger.info(
                        f"✅ write_multiple_files completed - tasks processed: {revision_result['tasks_processed']}"
                    )

            elif tool_name in ["read_multiple_files", "read_file"]:
                # Reading tools help understand structure
                self.logger.info(f"📖 {tool_name} provided code context")

        except Exception as e:
            self.logger.error(f"❌ Error processing {tool_name} result: {e}")

    def _create_revision_continuation_prompt(
        self, revision_result, revision_tasks
    ) -> str:
        """Create continuation prompt based on current revision state"""
        tasks_remaining = len(revision_tasks) - revision_result["tasks_processed"]

        if tasks_remaining > 0:
            return f"""You have {tasks_remaining} revision tasks remaining. Please continue implementing the required changes using the available tools. Focus on using write_multiple_files for efficient implementation."""
        else:
            return """All revision tasks appear to be processed. Please confirm if your revision work is complete or if you need to use additional tools."""

    def _create_revision_result_from_conversation(
        self, revision_result, revision_tasks
    ):
        """Create CodeRevisionResult from conversation data"""
        completion_rate = (
            (revision_result["tasks_processed"] / len(revision_tasks)) * 100
            if revision_tasks
            else 100.0
        )

        return CodeRevisionResult(
            revision_success=completion_rate >= 75.0,
            tasks_completed=[
                f"task_{i}" for i in range(revision_result["tasks_processed"])
            ],
            tasks_failed=[
                f"task_{i}"
                for i in range(revision_result["tasks_processed"], len(revision_tasks))
            ],
            files_created=revision_result["files_implemented"],
            files_modified=[],
            empty_files_implemented=revision_result["tasks_processed"],
            missing_files_created=0,
            quality_issues_fixed=0,
            revision_issues=[],
            final_project_health="good" if completion_rate >= 75 else "needs_work",
            execution_logs=[
                f"Intelligent revision: {revision_result['total_iterations']} iterations"
            ],
            total_tasks=len(revision_tasks),
            completion_rate=completion_rate,
            batch_operations=[
                {
                    "status": "completed",
                    "batch_size": revision_result["tasks_processed"],
                    "tools_used": revision_result["tools_used"],
                }
            ],
        )

    async def _apply_basic_error_fixes(self, error_text: str, iteration: int):
        """Apply basic fixes for common error patterns when LSP analysis fails"""
        try:
            self.logger.info(f"🔧 Applying basic error fixes (iteration {iteration})")

            # Handle ModuleNotFoundError/ImportError
            if "ModuleNotFoundError" in error_text or "ImportError" in error_text:
                await self._fix_missing_dependencies(error_text)

            # Handle common file/path issues
            elif "FileNotFoundError" in error_text or "No such file" in error_text:
                await self._fix_file_path_issues(error_text)

            # Handle syntax errors
            elif "SyntaxError" in error_text:
                await self._fix_syntax_errors(error_text)

            else:
                self.logger.info("📝 No automatic fix available for this error type")

        except Exception as e:
            self.logger.warning(f"⚠️ Basic error fix failed: {e}")

    async def _fix_missing_dependencies(self, error_text: str):
        """Fix missing dependency errors by installing missing packages"""
        try:
            # Extract missing module name
            match = re.search(r"No module named ['\"]([^'\"]+)['\"]", error_text)
            if match:
                missing_module = match.group(1)
                self.logger.info(
                    f"🔧 Attempting to install missing module: {missing_module}"
                )

                # This would typically interact with the sandbox agent to install packages
                # For now, just log the attempt
                self.logger.info(f"📦 Would install package: {missing_module}")

        except Exception as e:
            self.logger.warning(f"⚠️ Failed to fix missing dependencies: {e}")

    async def _fix_file_path_issues(self, error_text: str):
        """Fix file path related issues"""
        self.logger.info(
            "🔧 Analyzing file path issues - manual intervention may be needed"
        )

    async def _fix_syntax_errors(self, error_text: str):
        """Fix syntax errors"""
        self.logger.info("🔧 Syntax errors detected - manual code review needed")

    async def _synchronize_revision_memory(self, files_modified: List[str]):
        """Synchronize memory agent with revised files"""
        try:
            if self.memory_agent:
                self.logger.info(
                    f"🧠 Synchronizing memory for {len(files_modified)} revised files"
                )
                await self.memory_agent.synchronize_multiple_revised_files(
                    files_modified
                )
        except Exception as e:
            self.logger.warning(f"⚠️ Memory synchronization failed: {e}")

    def _extract_error_type(self, error_traceback: str) -> str:
        """Extract error type from traceback"""
        error_patterns = {
            "ModuleNotFoundError": r"ModuleNotFoundError",
            "ImportError": r"ImportError",
            "AttributeError": r"AttributeError",
            "NameError": r"NameError",
            "SyntaxError": r"SyntaxError",
            "FileNotFoundError": r"FileNotFoundError",
        }

        for error_type, pattern in error_patterns.items():
            if re.search(pattern, error_traceback):
                return error_type

        return "UnknownError"

    def _create_file_batches(
        self, files: List[str], batch_size: int
    ) -> List[List[str]]:
        """Split files into batches of specified size"""
        batches = []
        for i in range(0, len(files), batch_size):
            batch = files[i : i + batch_size]
            batches.append(batch)
        return batches

    def _get_current_implemented_files(self) -> List[str]:
        """Get current list of implemented files from revision state"""
        if self.evaluation_state.code_revision:
            return (
                self.evaluation_state.code_revision.files_created
                + self.evaluation_state.code_revision.files_modified
            )
        return []

    def _prepare_error_fixing_tool_definitions(self) -> List[Dict[str, Any]]:
        """Prepare tool definitions specifically for intelligent error fixing"""
        try:
            from config.mcp_tool_definitions_evaluation import get_evaluation_mcp_tools

            # Get tools from different MCP servers for error fixing
            error_fixing_tools = []

            # Error analysis tools from error-analysis server
            try:
                error_analysis_tools = get_evaluation_mcp_tools("error-analysis")
                error_fixing_tools.extend(error_analysis_tools)
                self.logger.info(
                    f"✅ Added {len(error_analysis_tools)} error analysis tools"
                )
            except Exception as e:
                self.logger.warning(f"⚠️ Could not load error analysis tools: {e}")

            # LSP tools from lsp-tools server
            try:
                lsp_tools = get_evaluation_mcp_tools("lsp-tools")
                error_fixing_tools.extend(lsp_tools)
                self.logger.info(f"✅ Added {len(lsp_tools)} LSP tools")
            except Exception as e:
                self.logger.warning(f"⚠️ Could not load LSP tools: {e}")

            # Sandbox tools from sandbox-tools server
            try:
                sandbox_tools = get_evaluation_mcp_tools("sandbox-tools")
                error_fixing_tools.extend(sandbox_tools)
                self.logger.info(f"✅ Added {len(sandbox_tools)} sandbox tools")
            except Exception as e:
                self.logger.warning(f"⚠️ Could not load sandbox tools: {e}")

            if not error_fixing_tools:
                self.logger.error(
                    "❌ No error fixing tools loaded! Using fallback basic tools"
                )
                # Fallback to basic tools if nothing loaded
                error_fixing_tools = self._get_fallback_error_fixing_tools()

            self.logger.info(
                f"🔧 Prepared {len(error_fixing_tools)} error fixing tools total"
            )
            return error_fixing_tools

        except Exception as e:
            self.logger.error(f"❌ Failed to prepare error fixing tools: {e}")
            # Return fallback tools
            return self._get_fallback_error_fixing_tools()

    def _get_fallback_error_fixing_tools(self) -> List[Dict[str, Any]]:
        """Fallback error fixing tools if MCP tools are not available"""
        return [
            {
                "name": "basic_error_analysis",
                "description": "Basic error analysis for common patterns",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "error_text": {
                            "type": "string",
                            "description": "Error text to analyze",
                        }
                    },
                    "required": ["error_text"],
                },
            },
            {
                "name": "apply_basic_fixes",
                "description": "Apply basic fixes for common error patterns",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "error_type": {
                            "type": "string",
                            "description": "Type of error to fix",
                        },
                        "file_path": {
                            "type": "string",
                            "description": "File path to fix",
                        },
                    },
                    "required": ["error_type", "file_path"],
                },
            },
        ]

    def _prepare_revision_tool_definitions(self) -> List[Dict[str, Any]]:
        """Prepare tool definitions for Code Revise Agent (revision execution with multi-file support)"""
        try:
            from config.mcp_tool_definitions_evaluation import get_evaluation_mcp_tools

            implementation_tools = get_evaluation_mcp_tools("code_implementation")
            revision_tools = get_evaluation_mcp_tools("revision-tools")

            # Log what tools we got from each source
            impl_tool_names = [tool.get("name", "") for tool in implementation_tools]
            revision_tools_names = [tool.get("name", "") for tool in revision_tools]

            self.logger.info(f"📦 Implementation tools loaded: {impl_tool_names}")
            self.logger.info(f"📖 Revision tools loaded: {revision_tools_names}")

            # Combine tools for revision execution with multi-file support
            all_tools = implementation_tools.copy()
            tool_names = [tool.get("name", "") for tool in implementation_tools]

            # Add evaluation tools that are needed for revision context
            for tool in revision_tools:
                tool_name = tool.get("name", "")
                if tool_name not in tool_names and tool_name in [
                    "read_multiple_files",
                    "read_file",
                ]:
                    all_tools.append(tool)
                    tool_names.append(tool_name)

            # get_file_structure should already be in implementation_tools now
            # No need for special handling since we're using evaluation config

            # Log multi-file tools availability with detailed info
            multi_file_tools = []
            write_multiple_found = False
            read_multiple_found = False

            for tool in all_tools:
                tool_name = tool.get("name", "")
                if "multiple" in tool_name or "multi" in tool_name:
                    multi_file_tools.append(tool_name)
                    if tool_name == "write_multiple_files":
                        write_multiple_found = True
                        self.logger.info(
                            "✅ write_multiple_files tool definition found"
                        )
                    if tool_name == "read_multiple_files":
                        read_multiple_found = True
                        self.logger.info("✅ read_multiple_files tool definition found")

            # Critical checks
            if not write_multiple_found:
                self.logger.error(
                    "❌ CRITICAL: write_multiple_files not found in tool definitions!"
                )
                self.logger.error(
                    f"📋 Available tools: {[t.get('name', '') for t in all_tools]}"
                )

            if not read_multiple_found:
                self.logger.warning(
                    "⚠️ read_multiple_files not found in tool definitions"
                )

            self.logger.info(f"Loaded {len(all_tools)} tools for Code Revise Agent")
            self.logger.info(f"📦 Multi-file tools: {multi_file_tools}")
            self.logger.info(
                f"📋 All tool names: {[t.get('name', '') for t in all_tools]}"
            )

            return all_tools

        except Exception as e:
            self.logger.error(f"Error loading revision tools: {e}")
            # Return empty list to allow workflow to continue
            self.logger.warning(
                "⚠️ Continuing with empty tool list - tools will be resolved at runtime"
            )
            return []

    def _extract_files_from_revision_task(self, task):
        """Extract file paths from revision task"""
        # This would use the same logic as in the analyzer agent
        # For brevity, implementing a simplified version
        files = []
        task_id = task.get("task_id", "")
        details = task.get("details", {})

        # Extract files based on task structure
        if task_id == "implement_empty_files":
            completely_empty = details.get("completely_empty", [])
            minimal_content = details.get("minimal_content", [])

            for file_info in completely_empty:
                if isinstance(file_info, dict) and "path" in file_info:
                    files.append(file_info["path"])

            for file_info in minimal_content:
                if isinstance(file_info, dict) and "path" in file_info:
                    files.append(file_info["path"])

        # Add other extraction logic as needed
        return files

    def _parse_file_implementations_robust(
        self, file_implementations_str: str
    ) -> Dict[str, str]:
        """Robust parsing of file implementations with error handling and cleanup"""
        try:
            # First attempt: direct parsing
            if isinstance(file_implementations_str, dict):
                return file_implementations_str

            if isinstance(file_implementations_str, str):
                return json.loads(file_implementations_str)

        except json.JSONDecodeError:
            # Fallback: create minimal implementations
            self.logger.warning("JSON parsing failed, creating minimal implementations")
            return {"temp_file.py": "# Auto-generated minimal implementation\npass\n"}

    def _format_tool_result_for_conversation(self, tool_name: str, tool_result) -> str:
        """Format tool result for conversation continuation"""
        try:
            # Extract content from tool result
            if hasattr(tool_result, "content"):
                content = tool_result.content
                if isinstance(content, list) and len(content) > 0:
                    if hasattr(content[0], "text"):
                        result_text = content[0].text
                    else:
                        result_text = str(content[0])
                else:
                    result_text = str(content)
            else:
                result_text = str(tool_result)

            # Truncate if too long
            if len(result_text) > 1000:
                result_text = result_text[:1000] + "... (truncated)"

            return f"🔧 Tool: {tool_name}\n✅ Result: {result_text}"

        except Exception as e:
            return f"🔧 Tool: {tool_name}\n❌ Result formatting error: {str(e)}"

    def _compile_tool_results_for_conversation(
        self, tool_results: List[str], write_multiple_called: bool, files_count: int
    ) -> str:
        """Compile tool results into conversation response"""
        response_parts = ["🔧 **Tool Execution Results:**"]
        response_parts.extend(tool_results)

        if write_multiple_called:
            response_parts.append(
                f"\n✅ **Files implemented successfully!** All {files_count} files have been created/modified."
            )
        else:
            response_parts.append(
                f"\n⚡ **Next Action Required:** Use write_multiple_files to implement all {files_count} files in one call."
            )

        return "\n\n".join(response_parts)

    async def _generate_multi_file_code_summary_after_batch(
        self, client, client_type, content_map, files_implemented_count
    ):
        """Generate multi-file code summary using Memory Agent after batch write"""
        try:
            if not content_map:
                self.logger.warning("🧠 No content to summarize in batch")
                return

            self.logger.info(
                f"🧠 Generating multi-file code summary for batch: {len(content_map)} files"
            )

            # Get current implemented files from revision state
            current_implemented_files = self._get_current_implemented_files()

            # Use Memory Agent to create multi-file code implementation summary
            summary = await self.memory_agent.create_multi_code_implementation_summary(
                client,
                client_type,
                content_map,
                files_implemented_count,
                current_implemented_files,
            )

            self.logger.info(
                f"✅ Multi-file code summary generated and saved for {len(content_map)} files"
            )

        except Exception as e:
            self.logger.error(f"Failed to generate multi-file code summary: {e}")

    def _make_json_safe(self, obj: Any) -> Any:
        """Recursively convert objects to JSON-serializable structures"""
        try:
            if obj is None or isinstance(obj, (str, int, float, bool)):
                return obj
            if isinstance(obj, dict):
                return {
                    str(self._make_json_safe(k)): self._make_json_safe(v)
                    for k, v in obj.items()
                }
            if isinstance(obj, (list, tuple)):
                return [self._make_json_safe(v) for v in obj]
            if isinstance(obj, set):
                return [self._make_json_safe(v) for v in obj]
            try:
                from dataclasses import is_dataclass, asdict as dc_asdict

                if is_dataclass(obj):
                    return self._make_json_safe(dc_asdict(obj))
            except Exception:
                pass
            return str(obj)
        except Exception:
            return str(obj)

    def _update_evaluation_state_with_lsp_results(
        self,
        error_analysis_reports: List[Dict],
        lsp_fix_applications: List[Dict],
        iterations: int,
        project_running: bool,
        analysis_duration: float,
    ):
        """Update evaluation state with comprehensive LSP results"""
        try:
            from .analyzer_agent import ErrorAnalysisResult

            # Extract comprehensive statistics
            total_suspect_files = sum(
                len(report.get("error_analysis_report", {}).get("suspect_files", []))
                for report in error_analysis_reports
            )
            total_lsp_symbols = sum(
                len(fix.get("lsp_symbols", []))
                for report in error_analysis_reports
                for fix in report.get("error_analysis_report", {}).get(
                    "suspect_files", []
                )
            )
            successful_fixes = len(
                [fix for fix in lsp_fix_applications if fix.get("success")]
            )

            self.evaluation_state.error_analysis = ErrorAnalysisResult(
                analysis_success=project_running or len(error_analysis_reports) > 0,
                error_reports_generated=len(error_analysis_reports),
                suspect_files_identified=total_suspect_files,
                remediation_tasks_created=len(lsp_fix_applications),
                sandbox_executions_completed=iterations,
                critical_errors_found=sum(
                    1
                    for report in error_analysis_reports
                    if "error"
                    in report.get("error_analysis_report", {})
                    .get("traceback_analysis", {})
                    .get("error_type", "")
                    .lower()
                ),
                high_confidence_fixes=len(
                    [
                        fix
                        for fix in lsp_fix_applications
                        if fix.get("confidence", 0) > 0.8
                    ]
                ),
                analysis_duration_seconds=analysis_duration,
                error_types_found=list(
                    set(
                        report.get("error_analysis_report", {})
                        .get("traceback_analysis", {})
                        .get("error_type", "")
                        for report in error_analysis_reports
                    )
                ),
                most_problematic_files=list(
                    set(fix["file_path"] for fix in lsp_fix_applications)
                )[:5],
                remediation_success_rate=100.0
                if project_running
                else (successful_fixes / max(len(lsp_fix_applications), 1)) * 100,
                error_analysis_reports=error_analysis_reports,
                # Additional LSP-specific data
                lsp_enhanced=True,
                lsp_symbols_identified=total_lsp_symbols,
                lsp_fixes_applied=successful_fixes,
            )

        except Exception as e:
            self.logger.warning(
                f"⚠️ Failed to update evaluation state with LSP results: {e}"
            )

    def _format_tool_info(self, tool_name: str, tool_input: dict) -> str:
        """Format tool call information for logging"""
        try:
            # Convert tool_input to string representation
            if isinstance(tool_input, dict):
                # Get a readable string representation, truncate long values
                input_str = ""
                for key, value in tool_input.items():
                    value_str = str(value)
                    if len(value_str) > 50:
                        value_str = value_str[:50] + "..."
                    input_str += f"{key}={value_str}, "
                input_str = input_str.rstrip(", ")
                if len(input_str) > 100:
                    input_str = input_str[:100] + "..."
            else:
                input_str = str(tool_input)[:50] + "..." if len(str(tool_input)) > 50 else str(tool_input)
            
            return f"🔧 [TOOL CALL] {tool_name} | Input: {input_str}"
        except Exception as e:
            return f"🔧 [TOOL CALL] {tool_name} | Input: <formatting error: {str(e)}>"

    # ==================== Placeholder Methods ====================
    # These would be implemented by the main workflow or passed in

    async def _initialize_llm_client(self):
        """Initialize LLM client using the main workflow's method"""
        # This would be passed in from the main workflow
        raise NotImplementedError(
            "LLM client initialization should be passed from main workflow"
        )

    async def _call_llm_with_tools(
        self, client, client_type, system_message, messages, tools
    ):
        """Call LLM with tools using the main workflow's method"""
        # This would be passed in from the main workflow
        raise NotImplementedError(
            "LLM communication should be passed from main workflow"
        )
