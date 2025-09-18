"""
Analyzer Agent for comprehensive repository analysis, static analysis, and error analysis

This module provides the AnalyzerAgent class and related functionality for:
- Repository structure and quality analysis
- Generating revision reports for implementation
- Static code analysis with LSP integration
- Error analysis and remediation suggestions
- Import dependency analysis
"""

import json
from typing import Dict, Any, Optional, List
from dataclasses import dataclass


@dataclass
class StaticAnalysisResult:
    """Static analysis results structure"""

    analysis_success: bool
    total_files_analyzed: int
    languages_detected: List[str]
    total_issues_found: int
    auto_fixes_applied: int
    analysis_duration_seconds: float
    issues_by_severity: Dict[str, int]
    tools_used: List[str]
    syntax_errors_found: int
    formatting_fixes_applied: int
    most_problematic_files: List[str]
    static_analysis_report: Optional[Dict[str, Any]] = None


@dataclass
class ErrorAnalysisResult:
    """Error analysis results structure for Phase 4"""

    analysis_success: bool
    error_reports_generated: int
    suspect_files_identified: int
    remediation_tasks_created: int
    sandbox_executions_completed: int
    critical_errors_found: int
    high_confidence_fixes: int
    analysis_duration_seconds: float
    error_types_found: List[str]
    most_problematic_files: List[str]
    remediation_success_rate: float
    error_analysis_reports: List[Dict[str, Any]] = None

    def __post_init__(self):
        if self.error_analysis_reports is None:
            self.error_analysis_reports = []


class AnalyzerAgent:
    """
    Analyzer Agent for comprehensive repository analysis and error detection

    Handles:
    - Repository structure analysis
    - Code quality assessment
    - Static analysis with LSP integration
    - Error analysis and remediation planning
    - Revision report generation
    """

    def __init__(self, logger, evaluation_state, mcp_analyzer_agent, config):
        self.logger = logger
        self.evaluation_state = evaluation_state
        self.mcp_analyzer_agent = mcp_analyzer_agent  # The actual MCP agent
        self.config = config

    async def run_analysis_and_generate_revision_reports(self):
        """
        PHASE 1: Intelligent Multi-Round Analysis and Revision Report Generation
        Uses iterative Agent conversation to achieve comprehensive analysis
        """
        try:
            self.logger.info(
                "🔬 Starting intelligent multi-round analysis and revision report generation"
            )
            self.logger.info(
                "🤖 Analyzer Agent will use iterative conversation to achieve comprehensive analysis"
            )

            # Initialize LLM client and tools
            client, client_type = await self._initialize_llm_client()
            tools = self._prepare_analyzer_tool_definitions()

            # Execute intelligent analysis conversation
            analysis_result = await self._execute_intelligent_analysis_conversation(
                client, client_type, tools
            )

            if analysis_result["success"]:
                self.logger.info(
                    "✅ Intelligent analysis conversation completed successfully"
                )
                return True
            else:
                self.logger.error(
                    f"❌ Intelligent analysis conversation failed: {analysis_result.get('error', 'Unknown error')}"
                )
                return False

        except Exception as e:
            self.logger.error(f"❌ Intelligent analysis phase failed: {e}")
            self.evaluation_state.add_error(f"Analysis phase failed: {e}")
            return False

    async def _execute_intelligent_analysis_conversation(
        self, client, client_type, tools
    ) -> dict:
        """
        Execute intelligent multi-round conversation for comprehensive analysis
        Similar to _execute_intelligent_error_fixing but focused on analysis and reporting
        """
        try:
            self.logger.info("🤖 Starting intelligent analysis conversation")

            # Prepare system message for intelligent analysis
            from prompts.evaluation_prompts import CODE_ANALYZER_AGENT_PROMPT

            analysis_system_message = f"""{CODE_ANALYZER_AGENT_PROMPT.format(
                root_dir=self.evaluation_state.repo_path,
                analysis_task="Intelligent multi-round repository analysis and revision report generation"
            )}

INTELLIGENT ANALYSIS MODE:
You are now in intelligent analysis mode. Your task is to:

1. ANALYZE the repository comprehensively using multiple tools
2. DECIDE which analysis tools to use based on initial findings
3. GENERATE comprehensive revision reports based on your analysis

Available analysis tools:
{[tool.get("name", "") for tool in tools]}

CRITICAL INSTRUCTIONS:
- Start with basic repository analysis (analyze_repo_structure, detect_dependencies)
- Based on findings, use appropriate specialized tools (assess_code_quality, evaluate_documentation, etc.)
- Generate revision reports using generate_code_revision_report
- Create final summary using generate_evaluation_summary
- Use multiple tools in sequence to build comprehensive understanding
- Always explain your reasoning for tool selection

TERMINATION CONDITIONS:
- You have completed generate_code_revision_report successfully
- You have created generate_evaluation_summary
- You indicate analysis is "complete" or "finished" in your response
- Maximum 10 iterations reached

Repository to analyze: {self.evaluation_state.repo_path}
Documentation: {self.evaluation_state.docs_path}"""

            # Create initial user message
            user_message = f"""Please analyze this repository comprehensively and generate revision reports.

Repository Path: {self.evaluation_state.repo_path}
Documentation Path: {self.evaluation_state.docs_path}

Start with basic repository analysis, then use your judgment to select the most appropriate analysis tools.
Your goal is to generate a comprehensive revision report that will guide the Code Revise Agent.

Begin your analysis now."""

            # Initialize result tracking
            analysis_result = {
                "success": False,
                "revision_report_generated": False,
                "evaluation_summary_generated": False,
                "tools_used": [],
                "agent_reasoning": [],
                "total_iterations": 0,
                "analysis_data": {},
                "error": None,
            }

            # Execute conversation loop
            messages = [{"role": "user", "content": user_message}]
            max_iterations = 20  # Reasonable limit for analysis
            iteration = 0
            analysis_completed = False

            while iteration < max_iterations and not analysis_completed:
                iteration += 1
                analysis_result["total_iterations"] = iteration

                self.logger.info(f"🤖 Analysis iteration {iteration}/{max_iterations}")

                # Get agent response
                response = await self._call_llm_with_tools(
                    client, client_type, analysis_system_message, messages, tools
                )

                # Process response and check for completion
                analysis_completed = await self._process_analysis_response(
                    response, messages, analysis_result, iteration
                )

                # Check termination conditions
                if (
                    analysis_result["revision_report_generated"]
                    and analysis_result["evaluation_summary_generated"]
                ):
                    self.logger.info(
                        "🎯 All required reports generated - analysis complete"
                    )
                    analysis_completed = True
                    analysis_result["success"] = True

            # Final validation
            if not analysis_result["success"]:
                if iteration >= max_iterations:
                    analysis_result["error"] = (
                        f"Reached maximum iterations ({max_iterations}) without completing analysis"
                    )
                else:
                    analysis_result["error"] = (
                        "Analysis terminated without generating required reports"
                    )

            return analysis_result

        except Exception as e:
            self.logger.error(f"❌ Intelligent analysis conversation failed: {e}")
            return {
                "success": False,
                "error": str(e),
                "revision_report_generated": False,
                "evaluation_summary_generated": False,
                "tools_used": [],
                "agent_reasoning": [],
                "total_iterations": 0,
                "analysis_data": {},
            }

    async def _process_analysis_response(
        self, response, messages, analysis_result, iteration
    ) -> bool:
        """Process agent response for analysis phase"""
        response_content = response.get("content", "")
        analysis_result["agent_reasoning"].append(
            {
                "iteration": iteration,
                "content": response_content,
                "tool_calls_count": len(response.get("tool_calls", [])),
            }
        )

        self.logger.info(
            f"🤖 Analysis iteration {iteration}: {len(response_content)} chars, {len(response.get('tool_calls', []))} tool calls"
        )

        # Add assistant response to conversation
        if response_content:
            messages.append({"role": "assistant", "content": response_content})

        if response.get("tool_calls"):
            # Process tool calls
            tool_results = await self._execute_analysis_tool_calls(
                response["tool_calls"], analysis_result, iteration
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
                    "analysis complete",
                    "analysis finished",
                    "reports generated",
                    "analysis done",
                ]
            ):
                self.logger.info("🎯 Agent indicates analysis is complete")
                return True
            elif iteration >= 9:  # Near max iterations
                self.logger.warning("⚠️ Reached near maximum iterations")
                return True
            else:
                # Prompt agent to continue
                continuation_prompt = self._create_analysis_continuation_prompt(
                    analysis_result
                )
                messages.append({"role": "user", "content": continuation_prompt})
                return False

    async def _execute_analysis_tool_calls(
        self, tool_calls, analysis_result, iteration
    ) -> List[str]:
        """Execute analysis tool calls and process results"""
        tool_results_for_conversation = []

        for tool_call in tool_calls:
            tool_name = tool_call["name"]
            tool_input = tool_call["input"]
            analysis_result["tools_used"].append(f"{tool_name}(iter{iteration})")

            self.logger.info(
                f"🔧 Analysis iteration {iteration}: Processing tool call: {tool_name}"
            )

            try:
                # Enhanced tool input for summary tools
                if tool_name in [
                    "generate_code_revision_report",
                    "generate_evaluation_summary",
                ]:
                    # Check if LLM provided conversation_data and ensure it's a string
                    if (
                        "conversation_data" in tool_input
                        and tool_input["conversation_data"]
                    ):
                        # If LLM provided a dict, convert it to JSON string
                        if isinstance(tool_input["conversation_data"], dict):
                            tool_input["conversation_data"] = json.dumps(
                                tool_input["conversation_data"]
                            )
                            self.logger.info(
                                "🔄 Converted LLM-provided conversation_data from dict to JSON string"
                            )
                        self.logger.info(
                            f"📊 {tool_name} using LLM-provided conversation_data"
                        )
                    else:
                        # Add our own conversation data if LLM didn't provide it
                        conversation_data = json.dumps(analysis_result["analysis_data"])
                        tool_input["conversation_data"] = conversation_data
                        self.logger.info(
                            f"🔄 Enhanced {tool_name} with conversation data from {len(analysis_result['analysis_data'])} previous analyses"
                        )

                self.logger.info(self._format_tool_info(tool_name, tool_input))
                tool_result = await self.mcp_analyzer_agent.call_tool(
                    tool_name, tool_input
                )
                tool_result_content = self._extract_tool_result_content(tool_result)

                # Process specific analysis tool results
                await self._process_specific_analysis_tool_result(
                    tool_name, tool_result_content, analysis_result
                )

                # Create summary for conversation
                tool_result_summary = self._create_analysis_tool_result_summary(
                    tool_name, tool_result_content
                )
                tool_results_for_conversation.append(tool_result_summary)

            except Exception as e:
                error_msg = f"Tool {tool_name} failed: {str(e)}"
                self.logger.error(f"❌ {error_msg}")
                tool_results_for_conversation.append(f"❌ {error_msg}")

        return tool_results_for_conversation

    async def _process_specific_analysis_tool_result(
        self, tool_name, tool_result_content, analysis_result
    ):
        """Process specific analysis tool results with enhanced error handling"""
        try:
            # Enhanced JSON parsing with debugging
            self.logger.info(
                f"🔍 Processing {tool_name} result (length: {len(tool_result_content)})"
            )

            # Try to parse JSON with better error handling
            try:
                if isinstance(tool_result_content, str):
                    # Clean the content first
                    cleaned_content = tool_result_content.strip()
                    if cleaned_content.startswith("```json"):
                        cleaned_content = (
                            cleaned_content.replace("```json", "")
                            .replace("```", "")
                            .strip()
                        )

                    parsed_data = json.loads(cleaned_content)
                else:
                    parsed_data = tool_result_content

            except json.JSONDecodeError as e:
                self.logger.error(f"❌ JSON parsing failed for {tool_name}: {e}")
                self.logger.error(
                    f"Raw content preview: {tool_result_content[:200]}..."
                )
                # Store raw content for debugging
                analysis_result["analysis_data"][f"{tool_name}_raw"] = (
                    tool_result_content[:500]
                )
                return

            # Process parsed data
            if tool_name == "generate_code_revision_report":
                if (
                    isinstance(parsed_data, dict)
                    and parsed_data.get("status") == "success"
                ):
                    self.evaluation_state.revision_report = parsed_data
                    analysis_result["revision_report_generated"] = True
                    analysis_result["analysis_data"]["revision_report"] = parsed_data
                    self.logger.info("📋 ✅ Revision report generated successfully")
                else:
                    self.logger.warning(
                        f"⚠️ Revision report status: {parsed_data.get('status', 'unknown')}"
                    )

            elif tool_name == "generate_evaluation_summary":
                if (
                    isinstance(parsed_data, dict)
                    and parsed_data.get("status") == "success"
                ):
                    self.evaluation_state.code_analysis = (
                        self._convert_to_code_analysis_result(parsed_data)
                    )
                    analysis_result["evaluation_summary_generated"] = True
                    analysis_result["analysis_data"]["evaluation_summary"] = parsed_data
                    self.logger.info("📊 ✅ Evaluation summary generated successfully")
                else:
                    self.logger.warning(
                        f"⚠️ Evaluation summary status: {parsed_data.get('status', 'unknown')}"
                    )

            elif tool_name in [
                "analyze_repo_structure",
                "detect_dependencies",
                "assess_code_quality",
                "evaluate_documentation",
                "detect_empty_files",
                "detect_missing_files",
            ]:
                if (
                    isinstance(parsed_data, dict)
                    and parsed_data.get("status") == "success"
                ):
                    analysis_result["analysis_data"][tool_name] = parsed_data
                    self.logger.info(f"📊 ✅ {tool_name} completed successfully")
                else:
                    self.logger.warning(
                        f"⚠️ {tool_name} status: {parsed_data.get('status', 'unknown')}"
                    )
                    analysis_result["analysis_data"][tool_name] = (
                        parsed_data  # Store anyway for debugging
                    )

        except Exception as e:
            self.logger.error(f"❌ Error processing {tool_name} result: {e}")
            # Store error info for debugging
            analysis_result["analysis_data"][f"{tool_name}_error"] = str(e)

    def _create_analysis_continuation_prompt(self, analysis_result) -> str:
        """Create continuation prompt based on current analysis state"""
        if not analysis_result["revision_report_generated"]:
            return """You haven't generated the revision report yet. Please use generate_code_revision_report to create the comprehensive revision plan that the Code Revise Agent will need."""

        elif not analysis_result["evaluation_summary_generated"]:
            return """Great! You've generated the revision report. Now please use generate_evaluation_summary to create the final evaluation summary."""

        else:
            return """Both revision report and evaluation summary have been generated. Please confirm if your analysis is complete or if you need to use additional tools."""

    def _create_analysis_tool_result_summary(
        self, tool_name, tool_result_content
    ) -> str:
        """Create summary of analysis tool result for conversation"""
        summary = f"Tool {tool_name} executed successfully. Result length: {len(tool_result_content)} characters."
        if len(tool_result_content) < 1000:
            summary += f"\nResult: {tool_result_content}"
        else:
            summary += f"\nResult preview: {tool_result_content[:500]}..."
        return summary

    def _prepare_analyzer_tool_definitions(self) -> List[Dict[str, Any]]:
        """Prepare tool definitions for Analyzer Agent"""
        try:
            from config.mcp_tool_definitions_evaluation import get_evaluation_mcp_tools

            # Get tools for comprehensive analysis
            analyzer_tools = []

            # Core evaluation tools
            try:
                core_tools = get_evaluation_mcp_tools("core-evaluation")
                analyzer_tools.extend(core_tools)
                self.logger.info(f"✅ Added {len(core_tools)} core evaluation tools")
            except Exception as e:
                self.logger.warning(f"⚠️ Could not load core evaluation tools: {e}")

            # Revision tools for report generation
            try:
                revision_tools = get_evaluation_mcp_tools("revision-tools")
                analyzer_tools.extend(revision_tools)
                self.logger.info(f"✅ Added {len(revision_tools)} revision tools")
            except Exception as e:
                self.logger.warning(f"⚠️ Could not load revision tools: {e}")

            # Static analysis tools
            try:
                static_tools = get_evaluation_mcp_tools("static-analysis")
                analyzer_tools.extend(static_tools)
                self.logger.info(f"✅ Added {len(static_tools)} static analysis tools")
            except Exception as e:
                self.logger.warning(f"⚠️ Could not load static analysis tools: {e}")

            if not analyzer_tools:
                self.logger.error("❌ No analyzer tools loaded! Using fallback")
                # Fallback basic tools
                analyzer_tools = [
                    {
                        "name": "analyze_repo_structure",
                        "description": "Analyze repository structure",
                        "input_schema": {
                            "type": "object",
                            "properties": {"repo_path": {"type": "string"}},
                            "required": ["repo_path"],
                        },
                    }
                ]

            self.logger.info(f"🔧 Prepared {len(analyzer_tools)} analyzer tools")
            return analyzer_tools

        except Exception as e:
            self.logger.error(f"❌ Failed to prepare analyzer tools: {e}")
            return []

    def _prepare_static_analysis_enhancement_tools(self) -> List[Dict[str, Any]]:
        """Prepare tool definitions specifically for static analysis enhancement tasks (README + Executable)"""
        try:
            from config.mcp_tool_definitions_evaluation import get_evaluation_mcp_tools

            # Get tools for static analysis enhancement tasks
            enhancement_tools = []

            # Code implementation tools for file operations and execution
            try:
                implementation_tools = get_evaluation_mcp_tools("code_implementation")
                enhancement_tools.extend(implementation_tools)
                self.logger.info(
                    f"✅ Added {len(implementation_tools)} code implementation tools for enhancement tasks"
                )
            except Exception as e:
                self.logger.warning(f"⚠️ Could not load code implementation tools: {e}")

            if not enhancement_tools:
                self.logger.error("❌ No enhancement tools loaded! Using fallback")
                # Fallback basic tools for README and script generation
                enhancement_tools = [
                    {
                        "name": "read_file",
                        "description": "Read file content",
                        "input_schema": {
                            "type": "object",
                            "properties": {"file_path": {"type": "string"}},
                            "required": ["file_path"],
                        },
                    },
                    {
                        "name": "write_file",
                        "description": "Write file content",
                        "input_schema": {
                            "type": "object",
                            "properties": {
                                "file_path": {"type": "string"},
                                "content": {"type": "string"},
                            },
                            "required": ["file_path", "content"],
                        },
                    },
                    {
                        "name": "get_file_structure",
                        "description": "Get directory structure",
                        "input_schema": {
                            "type": "object",
                            "properties": {"directory": {"type": "string"}},
                            "required": [],
                        },
                    },
                ]

            self.logger.info(
                f"🔧 Prepared {len(enhancement_tools)} static analysis enhancement tools"
            )
            self.logger.info(
                "📋 Tool categories: static-analysis + code_implementation"
            )

            # Log available tool names for debugging
            tool_names = [tool.get("name", "unknown") for tool in enhancement_tools]
            self.logger.info(f"🛠️ Available tools: {tool_names}")

            return enhancement_tools

        except Exception as e:
            self.logger.error(
                f"❌ Failed to prepare static analysis enhancement tools: {e}"
            )
            return []

    def _extract_tool_result_content(self, tool_result) -> str:
        """Extract content from MCP tool result"""
        try:
            # Handle TextContent objects from MCP
            if hasattr(tool_result, "content"):
                content = tool_result.content
                # If content is a list of TextContent objects
                if isinstance(content, list) and len(content) > 0:
                    if hasattr(content[0], "text"):
                        return content[0].text  # Extract text from TextContent
                    else:
                        return str(content[0])
                elif hasattr(content, "text"):
                    return content.text
                else:
                    return str(content)
            elif isinstance(tool_result, dict):
                return str(tool_result.get("content", tool_result))
            else:
                return str(tool_result)
        except Exception as e:
            self.logger.warning(f"⚠️ Failed to extract tool result content: {e}")
            return str(tool_result)

    async def run_static_analysis_phase(self) -> bool:
        """
        PHASE 3: Enhanced Static Analysis and Comprehensive Preliminary Error Fixes
        Uses the Analyzer Agent and LSP to perform comprehensive error detection and fixes
        """
        try:
            self.logger.info(
                "🔍 Starting enhanced static analysis phase with comprehensive error handling"
            )

            # Step 1: Set up LSP servers for comprehensive analysis
            self.logger.info(
                "🔧 Setting up LSP servers for comprehensive error analysis"
            )
            tool_name = "setup_lsp_servers"
            tool_input = {"repo_path": self.evaluation_state.repo_path}
            self.logger.info(self._format_tool_info(tool_name, tool_input))
            lsp_setup_result = await self.mcp_analyzer_agent.call_tool(
                tool_name, tool_input
            )

            lsp_setup_content = self._extract_tool_result_content(lsp_setup_result)
            lsp_setup_data = self._safe_parse_json(lsp_setup_content, "LSP setup")

            if lsp_setup_data.get("status") == "success":
                self.logger.info(
                    "✅ LSP servers set up successfully for error analysis"
                )
            else:
                self.logger.warning(
                    "⚠️ LSP setup had issues, continuing with basic analysis"
                )

            # Step 2: Apply automatic formatting fixes (optimized - no redundant error detection)
            self.logger.info("🎨 Step 2: Applying automatic formatting fixes")
            tool_name_format = "auto_fix_formatting"
            tool_input_format = {
                "repo_path": self.evaluation_state.repo_path,
                "languages": None,  # Auto-detect all languages
                "dry_run": False,  # Apply actual fixes
            }
            self.logger.info(self._format_tool_info(tool_name_format, tool_input_format))
            format_result = await self.mcp_analyzer_agent.call_tool(
                tool_name_format, tool_input_format
            )

            format_content = self._extract_tool_result_content(format_result)
            format_data = self._safe_parse_json(format_content, "Auto-formatting")

            formatting_fixes_applied = 0
            if isinstance(format_data, dict) and format_data.get("status") == "success":
                format_results = format_data.get("formatting_results", {})
                files_formatted = format_results.get("total_files_formatted", 0)
                formatting_fixes_applied = files_formatted

                if files_formatted > 0:
                    self.logger.info(
                        f"✅ Formatting applied to {files_formatted} files"
                    )
                else:
                    self.logger.info("ℹ️ No formatting fixes needed")
            else:
                self.logger.warning("⚠️ Automatic formatting had issues, continuing")

            # Step 3: Comprehensive LSP diagnostic analysis and error fixing
            self.logger.info(
                "🔍 Step 3: Running comprehensive LSP diagnostics analysis"
            )
            tool_name_diag = "lsp_get_diagnostics"
            tool_input_diag = {
                "repo_path": self.evaluation_state.repo_path,
                "file_path": None,  # Analyze all files
            }
            self.logger.info(self._format_tool_info(tool_name_diag, tool_input_diag))
            lsp_diagnostics_result = await self.mcp_analyzer_agent.call_tool(
                tool_name_diag, tool_input_diag
            )

            diagnostics_content = self._extract_tool_result_content(
                lsp_diagnostics_result
            )
            diagnostics_data = self._safe_parse_json(
                diagnostics_content, "LSP diagnostics"
            )

            diagnostics_found = 0
            error_files = []
            llm_fixes_applied = 0

            if diagnostics_data.get("status") == "success":
                diagnostics_found = diagnostics_data.get("diagnostics_found", 0)
                error_files = diagnostics_data.get("files_with_errors", [])

                self.logger.info(
                    f"📊 LSP diagnostics found {diagnostics_found} issues in {len(error_files)} files"
                )

                # Step 4: Use LLM to fix critical errors identified by LSP
                if diagnostics_found > 0 and error_files:
                    self.logger.info(
                        "🤖 Step 4: Using LLM to fix critical errors identified by LSP"
                    )

                    for error_file in error_files[
                        :5
                    ]:  # Limit to top 5 most problematic files
                        file_path = error_file.get("file_path", "")
                        error_count = error_file.get("error_count", 0)

                        if error_count > 0 and file_path:
                            self.logger.info(
                                f"🔧 Attempting LLM-based fixes for {file_path} ({error_count} errors)"
                            )

                            # Generate targeted code fixes using LSP
                            tool_name_lsp_fix = "lsp_generate_code_fixes"
                            tool_input_lsp_fix = {
                                "repo_path": self.evaluation_state.repo_path,
                                "file_path": file_path,
                                "start_line": 1,
                                "end_line": -1,  # Entire file
                                "error_context": f"Fix {error_count} LSP diagnostic errors",
                            }
                            self.logger.info(self._format_tool_info(tool_name_lsp_fix, tool_input_lsp_fix))
                            fix_result = await self.mcp_analyzer_agent.call_tool(
                                tool_name_lsp_fix, tool_input_lsp_fix
                            )

                            fix_content = self._extract_tool_result_content(fix_result)
                            fix_data = self._safe_parse_json(
                                fix_content, "LLM code fixes"
                            )

                            if fix_data.get("status") == "success":
                                self.logger.info(
                                    f"✅ LLM generated fixes for {file_path}"
                                )

                                # Apply the workspace edit if available
                                workspace_edit = fix_data.get("workspace_edit")
                                if workspace_edit:
                                    tool_name_apply = "lsp_apply_workspace_edit"
                                    tool_input_apply = {
                                        "repo_path": self.evaluation_state.repo_path,
                                        "workspace_edit": json.dumps(
                                            workspace_edit
                                        ),
                                    }
                                    self.logger.info(self._format_tool_info(tool_name_apply, tool_input_apply))
                                    apply_result = await self.mcp_analyzer_agent.call_tool(
                                        tool_name_apply, tool_input_apply
                                    )

                                    apply_content = self._extract_tool_result_content(
                                        apply_result
                                    )
                                    apply_data = self._safe_parse_json(
                                        apply_content, "Workspace edit"
                                    )

                                    if apply_data.get("status") == "success":
                                        self.logger.info(
                                            f"✅ Applied workspace edit to {file_path}"
                                        )
                                        llm_fixes_applied += 1
                                    else:
                                        self.logger.warning(
                                            f"⚠️ Failed to apply workspace edit to {file_path}"
                                        )
                            else:
                                self.logger.warning(
                                    f"⚠️ LLM failed to generate fixes for {file_path}"
                                )
                else:
                    self.logger.info(
                        "✅ No critical errors requiring LLM intervention found"
                    )
            else:
                self.logger.warning(
                    "⚠️ LSP diagnostics analysis failed, falling back to basic static analysis"
                )
                # Fallback: Use basic static analysis if LSP completely fails
                tool_name_basic = "perform_static_analysis"
                tool_input_basic = {
                    "repo_path": self.evaluation_state.repo_path,
                    "auto_fix": True,
                    "languages": None,
                }
                self.logger.info(self._format_tool_info(tool_name_basic, tool_input_basic))
                basic_analysis_result = await self.mcp_analyzer_agent.call_tool(
                    tool_name_basic, tool_input_basic
                )

                basic_content = self._extract_tool_result_content(basic_analysis_result)
                basic_data = self._safe_parse_json(
                    basic_content, "Basic static analysis"
                )

                if basic_data.get("status") == "success":
                    summary = basic_data.get("summary", {})
                    diagnostics_found = summary.get("total_issues_found", 0)
                    llm_fixes_applied = summary.get("auto_fixes_applied", 0)
                    self.logger.info(
                        f"✅ Fallback static analysis found {diagnostics_found} issues, applied {llm_fixes_applied} fixes"
                    )

            # Step 5: Final validation of fixes
            self.logger.info("✅ Step 5: Final validation of preliminary error fixes")
            tool_name_final = "lsp_get_diagnostics"
            tool_input_final = {"repo_path": self.evaluation_state.repo_path, "file_path": None}
            self.logger.info(self._format_tool_info(tool_name_final, tool_input_final))
            final_validation_result = await self.mcp_analyzer_agent.call_tool(
                tool_name_final, tool_input_final
            )

            final_content = self._extract_tool_result_content(final_validation_result)
            final_data = self._safe_parse_json(final_content, "Final validation")

            remaining_errors = diagnostics_found  # Default fallback
            if final_data.get("status") == "success":
                remaining_errors = final_data.get("diagnostics_found", 0)
                self.logger.info(
                    f"📊 Final validation: {remaining_errors} errors remaining after comprehensive fixes"
                )

                if remaining_errors == 0:
                    self.logger.info("🎉 All preliminary errors successfully resolved!")
                elif remaining_errors < diagnostics_found:
                    self.logger.info(
                        f"✅ Reduced errors from {diagnostics_found} to {remaining_errors}"
                    )
                else:
                    self.logger.warning(
                        f"⚠️ {remaining_errors} errors still need attention in Phase 4"
                    )

            # Create optimized StaticAnalysisResult (LSP-based + formatting)
            self.evaluation_state.static_analysis = StaticAnalysisResult(
                analysis_success=True,
                total_files_analyzed=len(error_files) if error_files else 1,
                languages_detected=list(lsp_setup_data.get("lsp_servers", {}).keys())
                if lsp_setup_data
                else [],
                total_issues_found=diagnostics_found,
                auto_fixes_applied=formatting_fixes_applied + llm_fixes_applied,
                analysis_duration_seconds=0.0,  # Would need to track timing
                issues_by_severity={
                    "errors": remaining_errors,
                    "warnings": 0,
                    "info": 0,
                },
                tools_used=["LSP", "auto_fix_formatting"],
                syntax_errors_found=remaining_errors,
                formatting_fixes_applied=formatting_fixes_applied,
                most_problematic_files=[
                    f.get("file_path", "") for f in error_files[:5]
                ],
                static_analysis_report={
                    "lsp_based": True,
                    "optimized": True,
                    "formatting_results": format_data,
                    "lsp_diagnostics": diagnostics_data,
                    "final_validation": final_data,
                },
            )

            self.logger.info("✅ Optimized static analysis completed:")
            self.logger.info(
                f"   📁 Files analyzed: {len(error_files) if error_files else 1}"
            )
            self.logger.info(
                f"   🔧 Languages detected: {len(lsp_setup_data.get('lsp_servers', {}).keys()) if lsp_setup_data else 0}"
            )
            self.logger.info(f"   ⚠️ Issues found: {diagnostics_found}")
            self.logger.info(f"   🎨 Formatting fixes: {formatting_fixes_applied}")
            self.logger.info(f"   🤖 LLM fixes applied: {llm_fixes_applied}")
            self.logger.info(f"   📊 Final error count: {remaining_errors}")
            self.logger.info(
                "   🛠️ Tools used: LSP diagnostics + auto-formatting (optimized)"
            )

            # Initialize LLM client and tools for additional tasks
            client, client_type = await self._initialize_llm_client()
            tools = self._prepare_static_analysis_enhancement_tools()

            # TASK 1: README Accuracy Check and Enhancement
            self.logger.info("📚 Task 1: README Accuracy Check and Enhancement")
            readme_task_success = await self._execute_readme_enhancement_task(
                client, client_type, tools
            )

            if readme_task_success:
                self.logger.info("✅ README enhancement task completed successfully")
            else:
                self.logger.warning("⚠️ README enhancement task failed but continuing")

            # TASK 2: Executable Script Generation and Testing
            self.logger.info("🚀 Task 2: Executable Script Generation and Testing")
            executable_task_success = await self._execute_executable_generation_task(
                client, client_type, tools
            )

            if executable_task_success:
                self.logger.info("✅ Executable generation task completed successfully")
            else:
                self.logger.warning(
                    "⚠️ Executable generation task failed but continuing"
                )

            # Update static analysis result with additional task information
            self.evaluation_state.static_analysis.static_analysis_report.update(
                {
                    "readme_enhancement_completed": readme_task_success,
                    "executable_generation_completed": executable_task_success,
                    "additional_tasks_executed": True,
                }
            )

            return True

        except Exception as e:
            self.logger.error(f"❌ Static analysis phase failed: {e}")
            self.evaluation_state.add_error(f"Static analysis phase failed: {e}")

            # Create minimal static analysis result for exception
            self.evaluation_state.static_analysis = StaticAnalysisResult(
                analysis_success=False,
                total_files_analyzed=0,
                languages_detected=[],
                total_issues_found=0,
                auto_fixes_applied=0,
                analysis_duration_seconds=0.0,
                issues_by_severity={},
                tools_used=[],
                syntax_errors_found=0,
                formatting_fixes_applied=0,
                most_problematic_files=[],
                static_analysis_report={"status": "error", "message": str(e)},
            )
            return False

    async def _execute_readme_enhancement_task(
        self, client, client_type, tools
    ) -> bool:
        """
        TASK 1: README Accuracy Check and Enhancement
        Uses intelligent multi-round conversation to analyze and improve README documentation
        """
        try:
            self.logger.info("📚 Starting README accuracy check and enhancement task")

            # Prepare system message for README enhancement
            from prompts.evaluation_prompts import CODE_ANALYZER_AGENT_PROMPT

            readme_system_message = f"""{CODE_ANALYZER_AGENT_PROMPT.format(
                root_dir=self.evaluation_state.repo_path,
                analysis_task="README accuracy verification and update"
            )}

# ROLE AND OBJECTIVE
You are a README Accuracy Verifier and Enhancer. Your primary objective is to:
1. Locate and analyze existing README files in the repository
2. Verify that all commands, paths, and instructions are accurate and functional
3. Enhance the README content with missing critical information if needed
4. Only make changes when improvements are genuinely required

## AVAILABLE TOOLS
Use only these code_implementation tools: {[tool.get("name", "") for tool in tools]}

## STEP-BY-STEP METHODOLOGY

### Phase 1: README Discovery
**Think step by step:**
1. **Primary Discovery**: Call `get_file_structure` with directory="." to scan the entire repository
2. **Parse Results**: Look for files matching these patterns (case-insensitive): README.md, README.MD, README.rst, README.txt, readme.md, readme.txt
3. **Fallback Discovery**: If get_file_structure fails or returns no README files after 2 attempts, use `execute_bash` with commands like:
   - `find . -name "README*" -type f`
   - `find . -name "readme*" -type f`
   - `ls -la` (to check root directory)
4. **Path Capture**: Record the exact "path" for each README candidate as returned by the tools

### Phase 2: Content Analysis
**For each README found, systematically verify:**
1. **Installation Commands**: Check if referenced scripts, package managers, or setup files exist
2. **Execution Commands**: Verify that entry points, main scripts, or executable files are present
3. **Dependencies**: Cross-reference with requirements.txt, package.json, setup.py, or similar files
4. **File Paths**: Ensure all referenced files and directories actually exist
5. **Configuration**: Validate that mentioned config files, environment variables, or settings are accurate

### Phase 3: Enhancement Assessment
**Determine if README needs improvement by checking for:**
- Missing installation instructions for detected package managers
- Absent usage examples when main entry points exist
- Incomplete dependency information
- Outdated or broken command references
- Missing project description or purpose
- Lack of basic usage documentation

### Phase 4: Implementation
**Only if improvements are needed:**
1. **Preserve Valid Content**: Keep all accurate and useful existing information
2. **Enhance Missing Sections**: Add only genuinely helpful information based on actual code structure
3. **Fix Inaccuracies**: Correct any commands, paths, or instructions that don't match reality
4. **Write Back**: Use `write_file` with the exact path discovered in Phase 1

## CRITICAL GUIDELINES

### Path Handling (ESSENTIAL):
- Use README paths EXACTLY as discovered from tools
- Never construct absolute paths or guess directories
- The workspace root equals the repository root
- If multiple READMEs exist, prioritize: repo-root README.* > shallowest path

### Quality Standards:
- Only make changes that add genuine value
- Preserve the original tone and style when possible
- Ensure all suggested commands actually work
- Test command accuracy by checking file existence

### Decision Framework:
Ask yourself before making changes:
1. "Does this README accurately reflect the current codebase?"
2. "Are there missing critical instructions that would help users?"
3. "Do all commands and paths actually work?"
4. "Would these changes genuinely improve user experience?"

## SUCCESS CRITERIA
- All commands in README are verified as functional
- Critical missing information has been added (if any)
- No unnecessary changes made to already-accurate content
- README provides clear, actionable guidance for users

## REPOSITORY CONTEXT
Repository: {self.evaluation_state.repo_path}
Focus: Create a README that accurately reflects the real codebase and provides genuine value to users.

Remember: Think through each step methodically. Only improve what genuinely needs improvement."""

            # Create initial user message
            user_message = f"""Verify README accuracy for this repository and update it if needed.

Repository: {self.evaluation_state.repo_path}

Tasks:
- Find and read README file(s)
- Check every command/path/instruction against actual files
- Cross-check dependencies with requirements.txt if present
- Produce and write corrected README.md

Start by listing detected README files and the checks you will perform."""

            # Initialize result tracking
            readme_result = {
                "success": False,
                "readme_files_found": [],
                "code_files_analyzed": [],
                "inaccuracies_found": [],
                "enhancements_made": [],
                "tools_used": [],
                "agent_reasoning": [],
                "total_iterations": 0,
                "error": None,
            }

            # Execute conversation loop
            messages = [{"role": "user", "content": user_message}]
            max_iterations = 50
            iteration = 0
            task_completed = False

            while iteration < max_iterations and not task_completed:
                iteration += 1
                readme_result["total_iterations"] = iteration

                self.logger.info(
                    f"📚 README task iteration {iteration}/{max_iterations}"
                )

                # Get agent response
                response = await self._call_llm_with_tools(
                    client, client_type, readme_system_message, messages, tools
                )

                # Process response and check for completion
                task_completed = await self._process_readme_enhancement_response(
                    response, messages, readme_result, iteration
                )

                # Check termination conditions
                if task_completed:
                    self.logger.info("🎯 README enhancement task completed")
                    readme_result["success"] = True
                    break

            # Final validation
            if not readme_result["success"]:
                if iteration >= max_iterations:
                    readme_result["error"] = (
                        f"Reached maximum iterations ({max_iterations}) without completing README enhancement"
                    )
                    self.logger.warning("⚠️ README task reached max iterations")
                else:
                    readme_result["error"] = (
                        "README enhancement terminated without completion"
                    )

            # Log final results
            self.logger.info("📚 README Enhancement Results:")
            self.logger.info(
                f"   📄 README files found: {len(readme_result['readme_files_found'])}"
            )
            self.logger.info(
                f"   🔍 Code files analyzed: {len(readme_result['code_files_analyzed'])}"
            )
            self.logger.info(
                f"   ⚠️ Inaccuracies found: {len(readme_result['inaccuracies_found'])}"
            )
            self.logger.info(
                f"   ✨ Enhancements made: {len(readme_result['enhancements_made'])}"
            )
            self.logger.info(
                f"   🔄 Total iterations: {readme_result['total_iterations']}"
            )

            return readme_result["success"]

        except Exception as e:
            self.logger.error(f"❌ README enhancement task failed: {e}")
            return False

    async def _process_readme_enhancement_response(
        self, response, messages, readme_result, iteration
    ) -> bool:
        """Process agent response for README enhancement task"""
        try:
            response_content = response.get("content", "").strip()
            readme_result["agent_reasoning"].append(
                f"Iteration {iteration}: {response_content[:200]}..."
            )

            # Add assistant response to conversation
            if response_content:
                messages.append({"role": "assistant", "content": response_content})

            if response.get("tool_calls"):
                # Process tool calls
                tool_results = await self._execute_readme_tool_calls(
                    response["tool_calls"], readme_result, iteration
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

                # Create continuation prompt
                continuation_prompt = self._create_readme_continuation_prompt(
                    readme_result, iteration
                )
                if continuation_prompt:
                    messages.append({"role": "user", "content": continuation_prompt})

                return False  # Continue conversation after tool calls

            else:
                # No tool calls - check if agent is done
                completion_indicators = [
                    "readme enhancement complete",
                    "readme updated successfully",
                    "documentation enhanced",
                    "task complete",
                    "enhancement finished",
                    "readme improvement done",
                    "documentation complete",
                ]

                if any(
                    indicator in response_content.lower()
                    for indicator in completion_indicators
                ):
                    self.logger.info(
                        "🎯 Agent indicates README enhancement is complete"
                    )
                    return True
                elif iteration >= 10:  # Near max iterations
                    self.logger.warning("⚠️ README task reached near maximum iterations")
                    return True
                else:
                    # Prompt agent to continue
                    messages.append(
                        {
                            "role": "user",
                            "content": "Please continue with README analysis and enhancement, or let me know if the task is complete.",
                        }
                    )
                    return False

        except Exception as e:
            self.logger.error(f"❌ Error processing README enhancement response: {e}")
            readme_result["agent_reasoning"].append(
                f"Error in iteration {iteration}: {str(e)}"
            )
            return False

    async def _execute_readme_tool_calls(
        self, tool_calls, readme_result, iteration
    ) -> List[str]:
        """Execute tool calls for README enhancement task"""
        tool_results_for_conversation = []

        for tool_call in tool_calls:
            tool_name = tool_call["name"]
            tool_input = tool_call["input"]
            readme_result["tools_used"].append(f"{tool_name}(iter{iteration})")

            self.logger.info(
                f"📚 README iteration {iteration}: Processing tool call: {tool_name}"
            )

            try:
                self.logger.info(self._format_tool_info(tool_name, tool_input))
                tool_result = await self.mcp_analyzer_agent.call_tool(
                    tool_name, tool_input
                )
                tool_result_content = self._extract_tool_result_content(tool_result)

                # Process specific README tool results
                await self._process_readme_specific_tool_result(
                    tool_name, tool_result_content, readme_result
                )

                # Create summary for conversation
                tool_result_summary = self._create_readme_tool_result_summary(
                    tool_name, tool_result_content
                )
                tool_results_for_conversation.append(tool_result_summary)

            except Exception as e:
                error_msg = f"Tool {tool_name} failed: {str(e)}"
                self.logger.error(f"❌ {error_msg}")
                tool_results_for_conversation.append(f"❌ {error_msg}")

        return tool_results_for_conversation

    async def _process_readme_specific_tool_result(
        self, tool_name, tool_result_content, readme_result
    ):
        """Process specific tool results for README enhancement"""
        try:
            if tool_name in ["read_file", "read_multiple_files"]:
                # Track README files found
                if "readme" in tool_name.lower() or "README" in tool_result_content:
                    readme_result["readme_files_found"].append(tool_name)

            elif tool_name == "list_directory":
                # Track code files for analysis
                readme_result["code_files_analyzed"].append("directory_structure")

            elif tool_name == "write_file":
                # Track enhancements made
                if "readme" in str(tool_result_content).lower():
                    readme_result["enhancements_made"].append("README file updated")
                    self.logger.info("✨ README file has been enhanced")

        except Exception as e:
            self.logger.error(f"❌ Error processing README tool result: {e}")

    def _create_readme_continuation_prompt(self, readme_result, iteration) -> str:
        """Create continuation prompt for README enhancement task"""
        if len(readme_result["readme_files_found"]) == 0 and iteration > 2:
            return "You haven't found any README files yet. Please search for README.md, README.txt, README.rst, or similar documentation files in the repository."

        if len(readme_result["code_files_analyzed"]) == 0 and iteration > 4:
            return "You should analyze some code files to understand the project structure and functionality. This will help you identify inaccuracies in the README."

        if len(readme_result["enhancements_made"]) == 0 and iteration > 8:
            return "You haven't made any README enhancements yet. Please create or update the README file based on your analysis."

        return "Please continue with README analysis and enhancement. What's your next step?"

    def _create_readme_tool_result_summary(self, tool_name, tool_result_content) -> str:
        """Create tool result summary for README enhancement task"""
        try:
            if tool_name in ["read_file", "read_multiple_files"]:
                return f"📖 {tool_name}: File content analyzed ({len(tool_result_content)} characters)"
            elif tool_name == "write_file":
                return f"✍️ {tool_name}: File written successfully"
            elif tool_name == "list_directory":
                return f"📁 {tool_name}: Directory structure obtained"
            else:
                return f"🔧 {tool_name}: {tool_result_content[:100]}..."
        except Exception:
            return f"🔧 {tool_name}: Tool executed successfully"

    async def _execute_executable_generation_task(
        self, client, client_type, tools
    ) -> bool:
        """
        TASK 2: Executable Script Generation and Testing
        Uses intelligent multi-round conversation to create executable scripts and test data
        """
        try:
            self.logger.info(
                "🚀 Starting executable script generation and testing task"
            )

            # Prepare system message for executable generation
            from prompts.evaluation_prompts import CODE_ANALYZER_AGENT_PROMPT

            executable_system_message = f"""{CODE_ANALYZER_AGENT_PROMPT.format(
                root_dir=self.evaluation_state.repo_path,
                analysis_task="Executable script generation and testing"
            )}

# ROLE AND OBJECTIVE
You are an Executable Script Generation and Enhancement Specialist. Your primary mission is to:
1. **Analyze** the repository comprehensively to understand its true functionality and structure
2. **Understand** file relationships and dependencies to create meaningful execution paths
3. **Create or enhance** executable scripts that demonstrate the project's capabilities effectively
4. **Only make changes** when genuine improvements to executability are needed

## AVAILABLE TOOLS
Use these code_implementation tools strategically: {[tool.get("name", "") for tool in tools]}

## STEP-BY-STEP METHODOLOGY

### Phase 1: Deep Repository Analysis
**Think step by step:**
1. **Primary Structure Discovery**: Use `get_file_structure` to map the complete repository structure
2. **Code Relationship Analysis**: Use `read_multiple_files` to understand connections between key files
3. **Dependency Investigation**: Read requirements.txt, setup.py, package.json, or similar files to understand dependencies
4. **Entry Point Detection**: Look for main execution files (main.py, app.py, run.py, __main__.py, etc.)
5. **Configuration Analysis**: Identify config files and their impact on execution

### Phase 2: Execution Path Understanding
**For each potential execution path, systematically analyze:**
1. **Input Requirements**: What data, parameters, or configurations does it need?
2. **Processing Flow**: How does the code flow from input to output?
3. **Output Generation**: What does the code produce and where?
4. **Error Scenarios**: What can go wrong and how to handle it?
5. **Dependencies**: What external libraries or files are required?

### Phase 3: Script Assessment and Enhancement
**Determine if executable scripts need improvement by checking:**
- Are there missing entry points that would help users run the project?
- Do existing scripts handle common execution scenarios properly?
- Are there missing demonstration or testing scripts?
- Do scripts lack proper error handling or user guidance?
- Are there opportunities to create wrapper scripts for complex workflows?
- Would utility scripts improve the user experience significantly?

### Phase 4: Implementation (Only When Beneficial)
**Only if genuine improvements are needed:**
1. **Preserve Existing Scripts**: Keep all functional existing executable files unchanged unless they have clear issues
2. **Enhance Strategically**: Only add scripts that provide genuine value based on the project's actual structure
3. **Create Meaningful Examples**: Generate test data or examples that actually work with the codebase
4. **Document Clearly**: Ensure new scripts have clear usage instructions

## CRITICAL GUIDELINES

### Analysis-First Approach:
- **Read Before Writing**: Thoroughly understand the existing codebase before creating anything new
- **Respect Existing Architecture**: Work with the project's existing patterns and structure
- **Understand File Relationships**: Use `read_multiple_files` to understand how components interact
- **Trace Execution Flows**: Follow imports and function calls to understand the actual execution paths

### Enhancement Strategy:
- **Value-Driven Changes**: Only create scripts that solve real usability problems
- **Preserve Working Code**: Never modify existing functional entry points unless clearly broken
- **Complementary Addition**: Add scripts that complement existing functionality
- **Test-Driven Approach**: If creating test data, ensure it works with the actual code

### Quality Standards:
- **Functional Verification**: Test that new scripts actually work with the existing codebase
- **Error Handling**: Include proper error checking and meaningful error messages
- **Documentation**: Provide clear usage instructions and examples
- **Dependency Awareness**: Ensure scripts handle missing dependencies gracefully

### Decision Framework:
**Before creating or modifying executable scripts, ask:**
1. "Does this repository actually need additional executable scripts?"
2. "Are there existing entry points that already serve this purpose?"
3. "Would this script provide genuine value to users of this project?"
4. "Can I verify that this script actually works with the existing codebase?"
5. "Am I respecting the project's existing architecture and patterns?"

## SUCCESS CRITERIA
- Repository structure and execution flows are thoroughly understood
- File relationships and dependencies are mapped and documented
- Only genuinely valuable executable enhancements are created
- All new scripts are tested and functional with the existing codebase
- Existing functional scripts are preserved and respected
- Clear documentation is provided for any new executable components

## REPOSITORY CONTEXT
Repository: {self.evaluation_state.repo_path}
Focus: Understand the repository deeply, then create or enhance executable scripts only when they provide genuine value to users.

Remember: **Analysis first, enhancement second.** Understand before you create. Respect the existing codebase structure and only add value where it's genuinely needed."""

            # Create initial user message
            user_message = f"""Please create executable scripts and test data for this repository.

Repository Path: {self.evaluation_state.repo_path}

Your task is to:
1. Analyze the repository structure and understand the main functionality
2. Identify entry points and execution paths
3. Create executable scripts that can run the entire project
4. Generate test data if the project needs input data
5. Ensure the scripts are robust and well-documented

Start by examining the repository structure, main files, and understanding what this project does."""

            # Initialize result tracking
            executable_result = {
                "success": False,
                "entry_points_found": [],
                "scripts_created": [],
                "test_data_generated": [],
                "configurations_analyzed": [],
                "tools_used": [],
                "agent_reasoning": [],
                "total_iterations": 0,
                "error": None,
            }

            # Execute conversation loop
            messages = [{"role": "user", "content": user_message}]
            max_iterations = 60
            iteration = 0
            task_completed = False

            while iteration < max_iterations and not task_completed:
                iteration += 1
                executable_result["total_iterations"] = iteration

                self.logger.info(
                    f"🚀 Executable task iteration {iteration}/{max_iterations}"
                )

                # Get agent response
                response = await self._call_llm_with_tools(
                    client, client_type, executable_system_message, messages, tools
                )

                # Process response and check for completion
                task_completed = await self._process_executable_generation_response(
                    response, messages, executable_result, iteration
                )

                # Check termination conditions
                if task_completed:
                    self.logger.info("🎯 Executable generation task completed")
                    executable_result["success"] = True
                    break

            # Final validation
            if not executable_result["success"]:
                if iteration >= max_iterations:
                    executable_result["error"] = (
                        f"Reached maximum iterations ({max_iterations}) without completing executable generation"
                    )
                    self.logger.warning("⚠️ Executable task reached max iterations")
                else:
                    executable_result["error"] = (
                        "Executable generation terminated without completion"
                    )

            # Log final results
            self.logger.info("🚀 Executable Generation Results:")
            self.logger.info(
                f"   🎯 Entry points found: {len(executable_result['entry_points_found'])}"
            )
            self.logger.info(
                f"   📜 Scripts created: {len(executable_result['scripts_created'])}"
            )
            self.logger.info(
                f"   📊 Test data generated: {len(executable_result['test_data_generated'])}"
            )
            self.logger.info(
                f"   ⚙️ Configurations analyzed: {len(executable_result['configurations_analyzed'])}"
            )
            self.logger.info(
                f"   🔄 Total iterations: {executable_result['total_iterations']}"
            )

            return executable_result["success"]

        except Exception as e:
            self.logger.error(f"❌ Executable generation task failed: {e}")
            return False

    async def _process_executable_generation_response(
        self, response, messages, executable_result, iteration
    ) -> bool:
        """Process agent response for executable generation task"""
        try:
            response_content = response.get("content", "").strip()
            executable_result["agent_reasoning"].append(
                f"Iteration {iteration}: {response_content[:200]}..."
            )

            # Add assistant response to conversation
            if response_content:
                messages.append({"role": "assistant", "content": response_content})

            if response.get("tool_calls"):
                # Process tool calls
                tool_results = await self._execute_executable_tool_calls(
                    response["tool_calls"], executable_result, iteration
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

                # Create continuation prompt
                continuation_prompt = self._create_executable_continuation_prompt(
                    executable_result, iteration
                )
                if continuation_prompt:
                    messages.append({"role": "user", "content": continuation_prompt})

                return False  # Continue conversation after tool calls

            else:
                # No tool calls - check if agent is done
                completion_indicators = [
                    "executable scripts complete",
                    "scripts created successfully",
                    "generation complete",
                    "task complete",
                    "scripts finished",
                    "execution scripts ready",
                    "test data generated",
                    "scripts working",
                ]

                if any(
                    indicator in response_content.lower()
                    for indicator in completion_indicators
                ):
                    self.logger.info(
                        "🎯 Agent indicates executable generation is complete"
                    )
                    return True
                elif iteration >= 13:  # Near max iterations
                    self.logger.warning(
                        "⚠️ Executable task reached near maximum iterations"
                    )
                    return True
                else:
                    # Prompt agent to continue
                    messages.append(
                        {
                            "role": "user",
                            "content": "Please continue with script generation and testing, or let me know if the task is complete.",
                        }
                    )
                    return False

        except Exception as e:
            self.logger.error(
                f"❌ Error processing executable generation response: {e}"
            )
            executable_result["agent_reasoning"].append(
                f"Error in iteration {iteration}: {str(e)}"
            )
            return False

    async def _execute_executable_tool_calls(
        self, tool_calls, executable_result, iteration
    ) -> List[str]:
        """Execute tool calls for executable generation task"""
        tool_results_for_conversation = []

        for tool_call in tool_calls:
            tool_name = tool_call["name"]
            tool_input = tool_call["input"]
            executable_result["tools_used"].append(f"{tool_name}(iter{iteration})")

            self.logger.info(
                f"🚀 Executable iteration {iteration}: Processing tool call: {tool_name}"
            )

            try:
                self.logger.info(self._format_tool_info(tool_name, tool_input))
                tool_result = await self.mcp_analyzer_agent.call_tool(
                    tool_name, tool_input
                )
                tool_result_content = self._extract_tool_result_content(tool_result)

                # Process specific executable tool results
                await self._process_executable_specific_tool_result(
                    tool_name, tool_result_content, executable_result
                )

                # Create summary for conversation
                tool_result_summary = self._create_executable_tool_result_summary(
                    tool_name, tool_result_content
                )
                tool_results_for_conversation.append(tool_result_summary)

            except Exception as e:
                error_msg = f"Tool {tool_name} failed: {str(e)}"
                self.logger.error(f"❌ {error_msg}")
                tool_results_for_conversation.append(f"❌ {error_msg}")

        return tool_results_for_conversation

    async def _process_executable_specific_tool_result(
        self, tool_name, tool_result_content, executable_result
    ):
        """Process specific tool results for executable generation"""
        try:
            if tool_name in ["read_file", "read_multiple_files"]:
                # Check for entry points
                if any(
                    keyword in tool_result_content.lower()
                    for keyword in ["if __name__", "main(", "def main", "app.run"]
                ):
                    executable_result["entry_points_found"].append(tool_name)

            elif tool_name == "write_file":
                # Track scripts created
                if any(
                    keyword in str(tool_result_content).lower()
                    for keyword in ["run.py", "demo.py", "test", "execute"]
                ):
                    executable_result["scripts_created"].append(
                        "Executable script created"
                    )
                    self.logger.info("📜 Executable script has been created")

                # Track test data generated
                if any(
                    keyword in str(tool_result_content).lower()
                    for keyword in ["test_data", "sample", "demo_data"]
                ):
                    executable_result["test_data_generated"].append(
                        "Test data file created"
                    )
                    self.logger.info("📊 Test data has been generated")

            elif tool_name in ["list_directory", "analyze_repo_structure"]:
                # Track configurations analyzed
                executable_result["configurations_analyzed"].append(
                    "Repository structure analyzed"
                )

        except Exception as e:
            self.logger.error(f"❌ Error processing executable tool result: {e}")

    def _create_executable_continuation_prompt(
        self, executable_result, iteration
    ) -> str:
        """Create continuation prompt for executable generation task"""
        if len(executable_result["entry_points_found"]) == 0 and iteration > 3:
            return "You should analyze the code files to find main entry points (main.py, __main__.py, functions with if __name__ == '__main__', etc.)."

        if len(executable_result["scripts_created"]) == 0 and iteration > 8:
            return "You haven't created any executable scripts yet. Please create scripts like run.py, demo.py, or test_runner.py that can execute the project."

        if len(executable_result["configurations_analyzed"]) == 0 and iteration > 5:
            return "You should analyze configuration files, requirements.txt, and understand the project setup to create proper executable scripts."

        return "Please continue with executable script generation and testing. What's your next step?"

    def _create_executable_tool_result_summary(
        self, tool_name, tool_result_content
    ) -> str:
        """Create tool result summary for executable generation task"""
        try:
            if tool_name in ["read_file", "read_multiple_files"]:
                return f"📖 {tool_name}: Code analyzed ({len(tool_result_content)} characters)"
            elif tool_name == "write_file":
                return f"📜 {tool_name}: Script/data file created successfully"
            elif tool_name == "list_directory":
                return f"📁 {tool_name}: Repository structure analyzed"
            else:
                return f"🔧 {tool_name}: {tool_result_content[:100]}..."
        except Exception:
            return f"🔧 {tool_name}: Tool executed successfully"

    def _format_tool_info(self, tool_name: str, tool_input: dict) -> str:
        """Format tool call information for logging"""
        try:
            # Convert tool_input to string representation
            if isinstance(tool_input, dict):
                # Get a readable string representation, truncate long values
                input_str = ""
                for key, value in tool_input.items():
                    value_str = str(value)
                    if len(value_str) > 100:
                        value_str = value_str[:100] + "..."
                    input_str += f"{key}={value_str}, "
                input_str = input_str.rstrip(", ")
                if len(input_str) > 100:
                    input_str = input_str[:100] + "..."
            else:
                input_str = str(tool_input)[:50] + "..." if len(str(tool_input)) > 50 else str(tool_input)
            
            return f"🔧 [TOOL CALL] {tool_name} | Input: {input_str}"
        except Exception as e:
            return f"🔧 [TOOL CALL] {tool_name} | Input: <formatting error: {str(e)}>"

    def _safe_parse_json(self, content, context_name="unknown"):
        """Safely parse JSON content"""
        self.logger.debug(f"{context_name} content type: {type(content)}")

        if isinstance(content, (dict, list)):
            self.logger.debug(f"{context_name}: Content is already parsed JSON")
            return content

        if isinstance(content, str):
            try:
                parsed = json.loads(content)
                self.logger.debug(f"{context_name}: Successfully parsed JSON string")
                return parsed
            except json.JSONDecodeError as e:
                self.logger.error(f"{context_name}: Failed to parse JSON string: {e}")
                return {"status": "error", "message": f"JSON parsing failed: {e}"}

        try:
            str_content = str(content)
            parsed = json.loads(str_content)
            self.logger.debug(
                f"{context_name}: Successfully parsed after string conversion"
            )
            return parsed
        except (json.JSONDecodeError, TypeError) as e:
            self.logger.error(
                f"{context_name}: Failed to parse after string conversion: {e}"
            )
            return {"status": "error", "message": f"Parsing failed: {e}"}

    def _convert_to_code_analysis_result(self, summary_data):
        """Convert evaluation summary to CodeAnalysisResult"""
        try:
            from workflows.code_evaluation_workflow_refactored import CodeAnalysisResult

            # Extract key metrics from summary
            metrics = summary_data.get("key_metrics", {})
            overall_assessment = summary_data.get("overall_assessment", {})

            return CodeAnalysisResult(
                repo_type="academic",  # Default for research projects
                languages=[metrics.get("primary_language", "unknown")],
                frameworks=[],
                dependencies={},
                structure_summary=f"Repository with {metrics.get('total_files', 0)} files",
                quality_issues=[],
                documentation_completeness=metrics.get("documentation_score", 0)
                / 100.0,
                reproduction_readiness={
                    "score": metrics.get("reproduction_readiness_score", 0)
                },
                confidence_score=overall_assessment.get("score", 0) / 100.0,
            )
        except Exception as e:
            self.logger.error(f"Failed to convert evaluation summary: {e}")
            return None
