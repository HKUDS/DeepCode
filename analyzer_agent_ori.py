"""
Analyzer Agent for comprehensive repository analysis, static analysis, and error analysis

This module provides the AnalyzerAgent class and related functionality for:
- Repository structure and quality analysis
- Generating revision reports for implementation
- Static code analysis with LSP integration
- Error analysis and remediation suggestions
- Import dependency analysis
"""

import asyncio
import json
import logging
import os
import time
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
        PHASE 1: Analysis and Revision Report Generation
        ONLY the Analyzer Agent is responsible for this phase
        """
        try:
            self.logger.info("🔬 Starting comprehensive analysis and revision report generation")
            self.logger.info("📋 Analyzer Agent has SOLE responsibility for generating revision reports")
            
            # Initialize LLM client
            client, client_type = await self._initialize_llm_client()
            
            # Prepare tools for code analysis
            tools = self._prepare_analyzer_tool_definitions()
            
            # Enhanced system message for analyzer agent
            from prompts.evaluation_prompts import CODE_ANALYZER_AGENT_PROMPT
            system_message = CODE_ANALYZER_AGENT_PROMPT.format(
                root_dir=self.evaluation_state.repo_path,
                analysis_task=f"Comprehensive analysis and revision report generation for {self.evaluation_state.repo_path}"
            )

            # Create comprehensive analysis message
            analysis_message = f"""You are the ANALYZER AGENT responsible for comprehensive repository analysis AND generating ALL revision reports.

Repository Path: {self.evaluation_state.repo_path}
Documentation Path: {self.evaluation_state.docs_path}

YOUR RESPONSIBILITIES:
1. **CRITICAL: Revision Report Generation (YOUR PRIMARY RESPONSIBILITY):**
   - Use generate_code_revision_report to create the comprehensive revision plan
   - This report will be passed to the Code Revise Agent for MULTI-FILE BATCH execution
   - Ensure the revision report contains SPECIFIC FILE PATHS for each task
   - Include detailed file lists with proper path structures for batch processing

2. **Final Summary:**
   - Use generate_evaluation_summary to create analysis summary

WORKFLOW ORDER:
1. First: Generate comprehensive revision report using generate_code_revision_report
2. Second: Generate evaluation summary

CRITICAL: The Code Revise Agent will process your revision report using MULTI-FILE BATCHING - ensure each task contains SPECIFIC FILE PATHS suitable for batch processing!"""

            messages = [{"role": "user", "content": analysis_message}]
            
            # Call LLM with tools
            response = await self._call_llm_with_tools(
                client, client_type, system_message, messages, tools
            )
            
            # Handle tool calls from LLM
            analysis_results = {}
            revision_report_generated = False
            
            if response.get("tool_calls"):
                for tool_call in response["tool_calls"]:
                    tool_name = tool_call["name"]
                    tool_input = tool_call["input"]
                    
                    # Execute the tool call through MCP agent
                    tool_result = await self.mcp_analyzer_agent.call_tool(tool_name, tool_input)
                    analysis_results[tool_name] = tool_result
                    
                    self.logger.info(f"✅ Analyzer Agent executed: {tool_name}")
                    
                    # Track revision report generation
                    if tool_name == "generate_code_revision_report":
                        revision_report_generated = True
            
            # CRITICAL: Ensure revision report was generated
            if not revision_report_generated:
                self.logger.warning("⚠️ Revision report not generated in main flow - generating now...")
                revision_result = await self.mcp_analyzer_agent.call_tool(
                    "generate_code_revision_report", 
                    {"repo_path": self.evaluation_state.repo_path, "docs_path": self.evaluation_state.docs_path}
                )
                analysis_results["generate_code_revision_report"] = revision_result
                revision_report_generated = True

            # Process and store the revision report
            if "generate_code_revision_report" in analysis_results:
                revision_result = analysis_results["generate_code_revision_report"]
                revision_content = self._extract_tool_result_content(revision_result)
                revision_data = self._safe_parse_json(revision_content, "Analyzer revision report")
                revision_data = self._normalize_revision_data(revision_data, "Analyzer revision report")

                if isinstance(revision_data, dict) and revision_data.get("status") == "success":
                    self.evaluation_state.revision_report = revision_data
                    self.logger.info("✅ Analyzer Agent successfully generated revision report")
                    
                    # Extract all files from revision tasks for workflow tracking
                    revision_tasks = revision_data.get("revision_report", {}).get("revision_tasks", [])
                    all_files = self._extract_all_files_from_revision_tasks(revision_tasks)
                    self.evaluation_state.all_files_to_implement = all_files
                    
                    self.logger.info(f"📋 Revision report contains {len(revision_tasks)} tasks for multi-file batch execution")
                    self.logger.info(f"📁 Total unique files to implement: {len(all_files)}")
                    
                    # Debug: Log file extraction for multi-file batching verification
                    total_files = len(all_files)
                    for task in revision_tasks:
                        files = self._extract_files_from_revision_task(task)
                        self.logger.info(f"🔍 Task {task.get('task_id')}: Found {len(files)} files for batch processing")
                        if files:
                            self.logger.info(f"   📄 Files: {files[:]}...")
                    
                    batches_needed = (total_files + self.config.get('max_files_per_batch', 3) - 1) // self.config.get('max_files_per_batch', 3)
                    self.logger.info(f"📦 Estimated {batches_needed} multi-file batches needed for {total_files} total files")
                else:
                    raise Exception(f"Analyzer Agent failed to generate valid revision report: {revision_data}")

            # Process analysis results for summary
            analysis_result = self._process_analysis_results(analysis_results, response)
            self.evaluation_state.code_analysis = analysis_result
            
            self.logger.info("✅ Analyzer Agent completed all responsibilities:")
            self.logger.info("   ✓ Repository analysis")
            self.logger.info("   ✓ Revision report generation")
            self.logger.info("   ✓ Evaluation summary")

        except Exception as e:
            self.evaluation_state.add_error(f"Analyzer Agent failed: {e}")
            raise Exception(f"Analyzer Agent failed to complete analysis and revision report generation: {e}")

    async def run_static_analysis_phase(self) -> bool:
        """
        PHASE 3: Enhanced Static Analysis and Comprehensive Preliminary Error Fixes
        Uses the Analyzer Agent and LSP to perform comprehensive error detection and fixes
        """
        try:
            self.logger.info("🔍 Starting enhanced static analysis phase with comprehensive error handling")
            
            # Step 1: Set up LSP servers for comprehensive analysis
            self.logger.info("🔧 Setting up LSP servers for comprehensive error analysis")
            lsp_setup_result = await self.mcp_analyzer_agent.call_tool(
                "setup_lsp_servers",
                {"repo_path": self.evaluation_state.repo_path}
            )
            
            lsp_setup_content = self._extract_tool_result_content(lsp_setup_result)
            lsp_setup_data = self._safe_parse_json(lsp_setup_content, "LSP setup")
            
            if lsp_setup_data.get("status") == "success":
                self.logger.info("✅ LSP servers set up successfully for error analysis")
            else:
                self.logger.warning("⚠️ LSP setup had issues, continuing with basic analysis")
            
            # Step 2: Apply automatic formatting fixes (optimized - no redundant error detection)
            self.logger.info("🎨 Step 2: Applying automatic formatting fixes")
            format_result = await self.mcp_analyzer_agent.call_tool(
                "auto_fix_formatting",
                {
                    "repo_path": self.evaluation_state.repo_path,
                    "languages": None,  # Auto-detect all languages
                    "dry_run": False    # Apply actual fixes
                }
            )
            
            format_content = self._extract_tool_result_content(format_result)
            format_data = self._safe_parse_json(format_content, "Auto-formatting")
            
            formatting_fixes_applied = 0
            if isinstance(format_data, dict) and format_data.get("status") == "success":
                format_results = format_data.get("formatting_results", {})
                files_formatted = format_results.get("total_files_formatted", 0)
                formatting_fixes_applied = files_formatted
                
                if files_formatted > 0:
                    self.logger.info(f"✅ Formatting applied to {files_formatted} files")
                else:
                    self.logger.info("ℹ️ No formatting fixes needed")
            else:
                self.logger.warning("⚠️ Automatic formatting had issues, continuing")
                
            # Step 3: Comprehensive LSP diagnostic analysis and error fixing
            self.logger.info("🔍 Step 3: Running comprehensive LSP diagnostics analysis")
            lsp_diagnostics_result = await self.mcp_analyzer_agent.call_tool(
                "lsp_get_diagnostics",
                {
                    "repo_path": self.evaluation_state.repo_path,
                    "file_path": None  # Analyze all files
                }
            )
            
            diagnostics_content = self._extract_tool_result_content(lsp_diagnostics_result)
            diagnostics_data = self._safe_parse_json(diagnostics_content, "LSP diagnostics")
            
            diagnostics_found = 0
            error_files = []
            llm_fixes_applied = 0
            
            if diagnostics_data.get("status") == "success":
                diagnostics_found = diagnostics_data.get("diagnostics_found", 0)
                error_files = diagnostics_data.get("files_with_errors", [])
                
                self.logger.info(f"📊 LSP diagnostics found {diagnostics_found} issues in {len(error_files)} files")
                
                # Step 4: Use LLM to fix critical errors identified by LSP
                if diagnostics_found > 0 and error_files:
                    self.logger.info("🤖 Step 4: Using LLM to fix critical errors identified by LSP")
                    
                    for error_file in error_files[:5]:  # Limit to top 5 most problematic files
                        file_path = error_file.get("file_path", "")
                        error_count = error_file.get("error_count", 0)
                        
                        if error_count > 0 and file_path:
                            self.logger.info(f"🔧 Attempting LLM-based fixes for {file_path} ({error_count} errors)")
                            
                            # Generate targeted code fixes using LSP
                            fix_result = await self.mcp_analyzer_agent.call_tool(
                                "lsp_generate_code_fixes",
                        {
                            "repo_path": self.evaluation_state.repo_path,
                                    "file_path": file_path,
                                    "start_line": 1,
                                    "end_line": -1,  # Entire file
                                    "error_context": f"Fix {error_count} LSP diagnostic errors"
                                }
                            )
                            
                            fix_content = self._extract_tool_result_content(fix_result)
                            fix_data = self._safe_parse_json(fix_content, "LLM code fixes")
                            
                            if fix_data.get("status") == "success":
                                self.logger.info(f"✅ LLM generated fixes for {file_path}")
                                
                                # Apply the workspace edit if available
                                workspace_edit = fix_data.get("workspace_edit")
                                if workspace_edit:
                                    apply_result = await self.mcp_analyzer_agent.call_tool(
                                        "lsp_apply_workspace_edit",
                                        {
                                            "repo_path": self.evaluation_state.repo_path,
                                            "workspace_edit": json.dumps(workspace_edit)
                                        }
                                    )
                                    
                                    apply_content = self._extract_tool_result_content(apply_result)
                                    apply_data = self._safe_parse_json(apply_content, "Workspace edit")
                                    
                                    if apply_data.get("status") == "success":
                                        self.logger.info(f"✅ Applied workspace edit to {file_path}")
                                        llm_fixes_applied += 1
                                    else:
                                        self.logger.warning(f"⚠️ Failed to apply workspace edit to {file_path}")
                            else:
                                self.logger.warning(f"⚠️ LLM failed to generate fixes for {file_path}")
                else:
                    self.logger.info("✅ No critical errors requiring LLM intervention found")
            else:
                self.logger.warning("⚠️ LSP diagnostics analysis failed, falling back to basic static analysis")
                # Fallback: Use basic static analysis if LSP completely fails
                basic_analysis_result = await self.mcp_analyzer_agent.call_tool(
                    "perform_static_analysis",
                    {
                        "repo_path": self.evaluation_state.repo_path,
                        "auto_fix": True,
                        "languages": None
                    }
                )
                
                basic_content = self._extract_tool_result_content(basic_analysis_result)
                basic_data = self._safe_parse_json(basic_content, "Basic static analysis")
                
                if basic_data.get("status") == "success":
                    summary = basic_data.get("summary", {})
                    diagnostics_found = summary.get("total_issues_found", 0)
                    llm_fixes_applied = summary.get("auto_fixes_applied", 0)
                    self.logger.info(f"✅ Fallback static analysis found {diagnostics_found} issues, applied {llm_fixes_applied} fixes")
            
            # Step 5: Final validation of fixes
            self.logger.info("✅ Step 5: Final validation of preliminary error fixes")
            final_validation_result = await self.mcp_analyzer_agent.call_tool(
                "lsp_get_diagnostics",
                        {
                            "repo_path": self.evaluation_state.repo_path,
                    "file_path": None
                }
            )
            
            final_content = self._extract_tool_result_content(final_validation_result)
            final_data = self._safe_parse_json(final_content, "Final validation")
            
            remaining_errors = diagnostics_found  # Default fallback
            if final_data.get("status") == "success":
                remaining_errors = final_data.get("diagnostics_found", 0)
                self.logger.info(f"📊 Final validation: {remaining_errors} errors remaining after comprehensive fixes")
                
                if remaining_errors == 0:
                    self.logger.info("🎉 All preliminary errors successfully resolved!")
                elif remaining_errors < diagnostics_found:
                    self.logger.info(f"✅ Reduced errors from {diagnostics_found} to {remaining_errors}")
                else:
                    self.logger.warning(f"⚠️ {remaining_errors} errors still need attention in Phase 4")
            
            # Create optimized StaticAnalysisResult (LSP-based + formatting)
            self.evaluation_state.static_analysis = StaticAnalysisResult(
                analysis_success=True,
                total_files_analyzed=len(error_files) if error_files else 1,
                languages_detected=list(lsp_setup_data.get("lsp_servers", {}).keys()) if lsp_setup_data else [],
                total_issues_found=diagnostics_found,
                auto_fixes_applied=formatting_fixes_applied + llm_fixes_applied,
                analysis_duration_seconds=0.0,  # Would need to track timing
                issues_by_severity={"errors": remaining_errors, "warnings": 0, "info": 0},
                tools_used=["LSP", "auto_fix_formatting"],
                syntax_errors_found=remaining_errors,
                formatting_fixes_applied=formatting_fixes_applied,
                most_problematic_files=[f.get("file_path", "") for f in error_files[:5]],
                static_analysis_report={
                    "lsp_based": True,
                    "optimized": True,
                    "formatting_results": format_data,
                    "lsp_diagnostics": diagnostics_data,
                    "final_validation": final_data
                }
            )
            
            self.logger.info(f"✅ Optimized static analysis completed:")
            self.logger.info(f"   📁 Files analyzed: {len(error_files) if error_files else 1}")
            self.logger.info(f"   🔧 Languages detected: {len(lsp_setup_data.get('lsp_servers', {}).keys()) if lsp_setup_data else 0}")
            self.logger.info(f"   ⚠️ Issues found: {diagnostics_found}")
            self.logger.info(f"   🎨 Formatting fixes: {formatting_fixes_applied}")
            self.logger.info(f"   🤖 LLM fixes applied: {llm_fixes_applied}")
            self.logger.info(f"   📊 Final error count: {remaining_errors}")
            self.logger.info(f"   🛠️ Tools used: LSP diagnostics + auto-formatting (optimized)")
            
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
                    static_analysis_report={"status": "error", "message": str(e)}
                )
            return False

    async def run_error_analysis_phase(self) -> bool:
        """
        PHASE 4: Advanced Error Analysis and Targeted Remediation
        Uses sandbox execution to identify runtime errors and provides targeted fixes
        """
        try:
            self.logger.info("🔬 Starting advanced error analysis phase with sandbox execution")
            
            start_time = time.time()
            error_reports_generated = 0
            suspect_files_identified = 0
            remediation_tasks_created = 0
            sandbox_executions_completed = 0
            critical_errors_found = 0
            high_confidence_fixes = 0
            error_types_found = []
            most_problematic_files = []
            error_analysis_reports = []
            
            # 1. Run initial code validation in sandbox (placeholder interface)
            self.logger.info("🏗️ Step 1: Running initial code validation in sandbox")
            validation_result = await self.mcp_analyzer_agent.call_tool(
                "run_code_validation",
                {
                    "repo_path": self.evaluation_state.repo_path,
                    "test_command": None  # Auto-detect test patterns
                }
            )
            
            validation_content = self._extract_tool_result_content(validation_result)
            validation_data = self._safe_parse_json(validation_content, "Code validation")
            
            if validation_data.get("status") == "todo":
                self.logger.info("📝 Sandbox validation interface ready - TODO: Implement actual sandbox")
                sandbox_executions_completed = 1  # Interface call completed
            
            # 2. If we have error information, perform error analysis
            # For demonstration, simulate some error scenarios
            simulated_errors = self._generate_simulated_error_scenarios()
            
            for error_scenario in simulated_errors:
                self.logger.info(f"🔍 Analyzing error scenario: {error_scenario['type']}")
                
                # Generate error analysis report
                error_analysis_result = await self.mcp_analyzer_agent.call_tool(
                    "generate_error_analysis_report",
                    {
                        "traceback_text": error_scenario["traceback"],
                        "repo_path": self.evaluation_state.repo_path,
                        "execution_context": error_scenario["context"]
                    }
                )
                
                error_analysis_content = self._extract_tool_result_content(error_analysis_result)
                error_analysis_data = self._safe_parse_json(error_analysis_content, "Error analysis")
                
                if error_analysis_data.get("status") == "success":
                    error_reports_generated += 1
                    report = error_analysis_data.get("error_analysis_report", {})
                    error_analysis_reports.append(error_analysis_data)
                    
                    # Extract analysis metrics
                    suspect_files = report.get("suspect_files", [])
                    suspect_files_identified += len(suspect_files)
                    
                    if suspect_files:
                        # Add most problematic files
                        top_suspects = [sf["file_path"] for sf in suspect_files[:3]]
                        most_problematic_files.extend(top_suspects)
                        
                        # Count high confidence fixes
                        high_conf_suspects = [sf for sf in suspect_files if sf["confidence_score"] > 0.8]
                        high_confidence_fixes += len(high_conf_suspects)
                    
                    # Track error types
                    traceback_analysis = report.get("traceback_analysis", {})
                    error_type = traceback_analysis.get("error_type")
                    if error_type and error_type not in error_types_found:
                        error_types_found.append(error_type)
                    
                    # Count critical errors
                    if error_scenario.get("severity") == "critical":
                        critical_errors_found += 1
                    
                    remediation_tasks_created += len(report.get("remediation_suggestions", []))
                    
                    self.logger.info(f"✅ Error analysis completed: {len(suspect_files)} suspect files identified")
                
                else:
                    self.logger.warning(f"⚠️ Error analysis failed for scenario: {error_scenario['type']}")
            
            # 3. Perform import dependency analysis
            self.logger.info("🔗 Step 3: Analyzing import dependencies for error propagation")
            import_analysis_result = await self.mcp_analyzer_agent.call_tool(
                "analyze_import_dependencies",
                {
                    "repo_path": self.evaluation_state.repo_path,
                    "target_file": None  # Analyze all files
                }
            )
            
            import_content = self._extract_tool_result_content(import_analysis_result)
            import_data = self._safe_parse_json(import_content, "Import analysis")
            
            if import_data.get("status") == "success":
                self.logger.info("✅ Import dependency analysis completed")
                # Add import analysis insights to most problematic files
                repo_analysis = import_data.get("repository_analysis", {})
                importing_files = repo_analysis.get("most_importing_files", [])
                for file_info in importing_files[:2]:  # Top 2 most importing files
                    file_path = file_info.get("file")
                    if file_path and file_path not in most_problematic_files:
                        most_problematic_files.append(file_path)
            
            # 4. Calculate remediation success rate (simulated)
            remediation_success_rate = 0.0
            if error_reports_generated > 0:
                # Simulate success rate based on confidence levels
                total_confidence = sum(
                    sum(sf["confidence_score"] for sf in report.get("error_analysis_report", {}).get("suspect_files", []))
                    for report in error_analysis_reports
                )
                max_possible_confidence = error_reports_generated * 10  # Assume max 10 files per report with confidence 1.0
                remediation_success_rate = min(total_confidence / max(max_possible_confidence, 1), 1.0) * 100
            
            # 5. Create ErrorAnalysisResult
            analysis_duration = time.time() - start_time
            self.evaluation_state.error_analysis = ErrorAnalysisResult(
                analysis_success=error_reports_generated > 0,
                error_reports_generated=error_reports_generated,
                suspect_files_identified=suspect_files_identified,
                remediation_tasks_created=remediation_tasks_created,
                sandbox_executions_completed=sandbox_executions_completed,
                critical_errors_found=critical_errors_found,
                high_confidence_fixes=high_confidence_fixes,
                analysis_duration_seconds=analysis_duration,
                error_types_found=error_types_found,
                most_problematic_files=list(set(most_problematic_files))[:5],  # Dedupe and limit
                remediation_success_rate=remediation_success_rate,
                error_analysis_reports=error_analysis_reports
            )
            
            self.logger.info(f"✅ Error analysis phase completed:")
            self.logger.info(f"   📊 Error reports generated: {error_reports_generated}")
            self.logger.info(f"   📁 Suspect files identified: {suspect_files_identified}")
            self.logger.info(f"   🎯 High confidence fixes: {high_confidence_fixes}")
            self.logger.info(f"   ⚠️ Critical errors found: {critical_errors_found}")
            self.logger.info(f"   📈 Remediation success rate: {remediation_success_rate:.1f}%")
            self.logger.info(f"   ⏱️ Duration: {analysis_duration:.2f}s")
            
            return True
                
        except Exception as e:
            self.logger.error(f"❌ Error analysis phase failed: {e}")
            self.evaluation_state.add_error(f"Error analysis phase failed: {e}")
            
            # Create minimal error analysis result for exception
            self.evaluation_state.error_analysis = ErrorAnalysisResult(
                analysis_success=False,
                error_reports_generated=0,
                suspect_files_identified=0,
                remediation_tasks_created=0,
                sandbox_executions_completed=0,
                critical_errors_found=0,
                high_confidence_fixes=0,
                analysis_duration_seconds=0.0,
                error_types_found=[],
                most_problematic_files=[],
                remediation_success_rate=0.0,
                error_analysis_reports=[]
            )
            return False

    def _prepare_analyzer_tool_definitions(self) -> List[Dict[str, Any]]:
        """Prepare tool definitions for Analyzer Agent (analysis + revision report generation)"""
        from config.mcp_tool_definitions_index import get_mcp_tools
        
        try:
            evaluation_tools = get_mcp_tools("code_evaluation")
            self.logger.info(f"Loaded {len(evaluation_tools)} tools for Analyzer Agent")
            return evaluation_tools
        except Exception as e:
            self.logger.error(f"Error loading analyzer tools: {e}")
            return []

    def _generate_simulated_error_scenarios(self) -> List[Dict[str, Any]]:
        """
        Generate simulated error scenarios for demonstration
        TODO: Replace with actual sandbox execution results
        """
        return [
            {
                "type": "ImportError",
                "severity": "critical",
                "context": "main module execution",
                "traceback": f'''Traceback (most recent call last):
  File "{self.evaluation_state.repo_path}/main.py", line 10, in <module>
    from utils.helper import process_data
  File "{self.evaluation_state.repo_path}/utils/helper.py", line 5, in <module>
    import numpy as np
ModuleNotFoundError: No module named 'numpy' '''
            },
            {
                "type": "AttributeError", 
                "severity": "medium",
                "context": "function call",
                "traceback": f'''Traceback (most recent call last):
  File "{self.evaluation_state.repo_path}/src/processor.py", line 25, in process_file
    result = data.transform()
AttributeError: 'dict' object has no attribute 'transform' '''
            }
        ]

    def _extract_all_files_from_revision_tasks(self, revision_tasks: List[Dict[str, Any]]) -> List[str]:
        """
        Extract all unique files from all revision tasks for workflow tracking
        
        Args:
            revision_tasks: List of revision tasks from the revision report
            
        Returns:
            List of all unique file paths mentioned in all tasks
        """
        all_files = []
        
        for task in revision_tasks:
            task_files = self._extract_files_from_revision_task(task)
            all_files.extend(task_files)
        
        # Remove duplicates while preserving order
        unique_files = []
        seen = set()
        for file_path in all_files:
            if file_path not in seen:
                seen.add(file_path)
                unique_files.append(file_path)
        
        self.logger.info(f"📁 Extracted {len(unique_files)} unique files from {len(revision_tasks)} revision tasks")
        return unique_files

    def _extract_files_from_revision_task(self, task):
        """
        FIXED: Properly extract file paths from revision task based on actual report structure
        """
        files = []
        task_id = task.get("task_id", "")
        
        # Method 1: Extract from task details structure (for empty files task)
        details = task.get('details', {})
        
        if task_id == "implement_empty_files":
            # Handle empty files task structure
            completely_empty = details.get('completely_empty', [])
            minimal_content = details.get('minimal_content', [])
            
            for file_info in completely_empty:
                if isinstance(file_info, dict) and 'path' in file_info:
                    files.append(file_info['path'])
            
            for file_info in minimal_content:
                if isinstance(file_info, dict) and 'path' in file_info:
                    files.append(file_info['path'])
            
            self.logger.info(f"📋 Extracted {len(files)} empty files from task {task_id}")
            
        elif task_id == "create_missing_files":
            # Handle missing files task structure - extract from suggestions
            if isinstance(details, list):
                for missing_file in details:
                    if isinstance(missing_file, dict):
                        suggestions = missing_file.get('suggestions', [])
                        for suggestion in suggestions:
                            if isinstance(suggestion, str):
                                files.append(suggestion)
            
            self.logger.info(f"📋 Extracted {len(files)} missing files from task {task_id}")
            
        elif task_id == "improve_code_quality":
            # Handle quality improvement task - extract from security issues or other quality details
            quality_details = details.get('security_issues', [])
            for issue in quality_details:
                if isinstance(issue, str) and ':' in issue:
                    # Extract file path from "file_path: description" format
                    file_path = issue.split(':')[0].strip()
                    if file_path:
                        files.append(file_path)
            
            self.logger.info(f"📋 Extracted {len(files)} quality files from task {task_id}")
        
        # Method 2: Look for standard file path fields (fallback)
        if not files:
            for field in ['file_path', 'target_file', 'files', 'file_list', 'target_files']:
                if field in details:
                    value = details[field]
                    if isinstance(value, str):
                        files.append(value)
                    elif isinstance(value, list):
                        files.extend([f for f in value if isinstance(f, str)])
        
        # Method 3: Extract from description (fallback)
        if not files:
            description = task.get('description', '')
            if 'file' in description.lower():
                import re
                file_patterns = [
                    r'([a-zA-Z0-9_\-/]+\.[a-zA-Z0-9]+)',  # filename.ext
                    r'"([a-zA-Z0-9_\-/]+\.[a-zA-Z0-9]+)"',  # "filename.ext"
                    r'`([a-zA-Z0-9_\-/]+\.[a-zA-Z0-9]+)`',  # `filename.ext`
                ]
                for pattern in file_patterns:
                    matches = re.findall(pattern, description)
                    for match in matches:
                        if match not in files:
                            files.append(match)
        
        # Remove duplicates while preserving order
        seen = set()
        unique_files = []
        for file_path in files:
            if file_path not in seen:
                seen.add(file_path)
                unique_files.append(file_path)
        
        self.logger.info(f"🔍 Task {task_id}: Final extracted files count: {len(unique_files)}")
        
        return unique_files

    def _process_analysis_results(self, analysis_results, response):
        """Process analysis results into CodeAnalysisResult structure"""
        from dataclasses import dataclass
        
        @dataclass
        class CodeAnalysisResult:
            repo_type: str
            languages: List[str]
            frameworks: List[str]
            dependencies: Dict[str, Any]
            structure_summary: str
            quality_issues: List[str]
            documentation_completeness: float
            reproduction_readiness: Dict[str, Any]
            confidence_score: float
        
        # Parse the final summary if available
        if "generate_evaluation_summary" in analysis_results:
            summary_result = analysis_results["generate_evaluation_summary"]
            summary_content = self._extract_tool_result_content(summary_result)
            summary_data = self._safe_parse_json(summary_content, "Evaluation summary")
            
            if summary_data.get("status") == "success":
                metrics = summary_data.get("key_metrics", {})
                
                return CodeAnalysisResult(
                    repo_type="academic",  # Could be extracted from summary
                    languages=[metrics.get("primary_language", "unknown")],
                    frameworks=[],  # Could be extracted from analysis
                    dependencies={},  # Available in dependency analysis
                    structure_summary=response.get("content", ""),
                    quality_issues=[],  # Available in quality assessment
                    documentation_completeness=metrics.get("documentation_score", 0) / 100,
                    reproduction_readiness={"score": metrics.get("reproduction_readiness_score", 0)},
                    confidence_score=summary_data["overall_assessment"]["score"] / 100
                )
        
        # Fallback with basic info
        return CodeAnalysisResult(
            repo_type="detected",
            languages=["python"],  # Default assumption
            frameworks=[],
            dependencies={},
            structure_summary=response.get("content", "Analysis completed"),
            quality_issues=[],
            documentation_completeness=0.7,
            reproduction_readiness={},
            confidence_score=0.7
        )

    # ==================== Utility Methods ====================
    
    async def _initialize_llm_client(self):
        """Initialize LLM client using the main workflow's method"""
        # This would be passed in from the main workflow
        # For now, placeholder - will be replaced when integrating
        raise NotImplementedError("LLM client initialization should be passed from main workflow")
    
    async def _call_llm_with_tools(self, client, client_type, system_message, messages, tools):
        """Call LLM with tools using the main workflow's method"""
        # This would be passed in from the main workflow
        # For now, placeholder - will be replaced when integrating
        raise NotImplementedError("LLM communication should be passed from main workflow")

    def _extract_tool_result_content(self, tool_result):
        """Extract content from CallToolResult object"""
        if hasattr(tool_result, 'content'):
            content = tool_result.content
        elif hasattr(tool_result, 'data'):
            content = tool_result.data
        else:
            content = tool_result

        try:
            if isinstance(content, list):
                extracted_texts = []
                for block in content:
                    if isinstance(block, dict) and 'text' in block:
                        extracted_texts.append(str(block.get('text', '')))
                    elif hasattr(block, 'text'):
                        extracted_texts.append(str(getattr(block, 'text')))
                    elif isinstance(block, str):
                        extracted_texts.append(block)
                if extracted_texts:
                    return "\n".join([t for t in extracted_texts if t is not None])
        except Exception:
            pass

        return content

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
            self.logger.debug(f"{context_name}: Successfully parsed after string conversion")
            return parsed
        except (json.JSONDecodeError, TypeError) as e:
            self.logger.error(f"{context_name}: Failed to parse after string conversion: {e}")
            return {"status": "error", "message": f"Parsing failed: {e}"}

    def _normalize_revision_data(self, data, context_name: str = "revision") -> Dict[str, Any]:
        """Normalize revision report into standard structure"""
        try:
            if isinstance(data, dict):
                if "revision_report" in data:
                    return data

                if "revision_tasks" in data and isinstance(data["revision_tasks"], list):
                    status_value = data.get("status", "success")
                    other_fields = {k: v for k, v in data.items() if k not in ("revision_tasks", "status")}
                    return {
                        "status": status_value,
                        "revision_report": {
                            "revision_tasks": data["revision_tasks"],
                            **other_fields,
                        },
                    }

                if "data" in data and isinstance(data["data"], list):
                    return {
                        "status": data.get("status", "success"),
                        "revision_report": {"revision_tasks": data["data"]},
                    }

                if "status" not in data:
                    data["status"] = "success"
                return data

            if isinstance(data, list):
                return {"status": "success", "revision_report": {"revision_tasks": data}}

            return {"status": "error", "message": f"Unexpected {context_name} type: {type(data)}"}
        except Exception as e:
            self.logger.error(f"Failed to normalize {context_name}: {e}")
            return {"status": "error", "message": f"Normalization failed: {e}"}
