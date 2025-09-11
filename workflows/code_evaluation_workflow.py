"""
Code Evaluation Workflow - Multi-Agent Collaborative Repository Validation

ENHANCED VERSION: Uses multi-file capabilities for efficient batch processing
"""

import json
import logging
import os
import sys
import time
import yaml
from typing import Dict, Any, Optional, List
from dataclasses import dataclass, asdict
from enum import Enum

# MCP Agent imports
from mcp_agent.agents.agent import Agent

# Local imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from prompts.evaluation_prompts import (
    ORCHESTRATOR_AGENT_PROMPT,
    CODE_ANALYZER_AGENT_PROMPT,
    CODE_REVISE_AGENT_PROMPT,
)
from utils.llm_utils import get_preferred_llm_class, get_default_models

# Import Memory Agent for code summaries
from agents.memory_agent_concise_multi import ConciseMemoryAgent


class EvaluationPhase(Enum):
    """Evaluation workflow phases"""

    INITIALIZED = "initialized"
    ANALYZING = "analyzing"
    REVISING = "revising"
    STATIC_ANALYSIS = "static_analysis"
    ERROR_ANALYSIS = (
        "error_analysis"  # NEW: Phase 4 - Advanced error analysis and remediation
    )
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class CodeAnalysisResult:
    """Code analysis results structure"""

    repo_type: str  # academic/engineering/library/application
    languages: List[str]
    frameworks: List[str]
    dependencies: Dict[str, Any]
    structure_summary: str
    quality_issues: List[str]
    documentation_completeness: float
    reproduction_readiness: Dict[str, Any]
    confidence_score: float


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


@dataclass
class EvaluationState:
    """Shared state across all agents"""

    phase: EvaluationPhase
    repo_path: str
    docs_path: str
    memory_path: str
    workspace_dir: str
    start_time: float
    code_analysis: Optional[CodeAnalysisResult] = None
    code_revision: Optional[CodeRevisionResult] = None
    static_analysis: Optional[StaticAnalysisResult] = (
        None  # Phase 3: Static analysis results
    )
    error_analysis: Optional[ErrorAnalysisResult] = (
        None  # NEW: Phase 4: Error analysis results
    )
    revision_report: Optional[Dict[str, Any]] = None
    all_files_to_implement: List[str] = (
        None  # NEW: Track all files from revision report
    )
    errors: List[str] = None
    warnings: List[str] = None

    def __post_init__(self):
        if self.errors is None:
            self.errors = []
        if self.warnings is None:
            self.warnings = []
        if self.all_files_to_implement is None:
            self.all_files_to_implement = []

    def to_dict(self) -> Dict[str, Any]:
        """Convert state to dictionary for serialization"""
        result = asdict(self)
        result["phase"] = self.phase.value
        return result

    def add_error(self, error: str):
        """Add error to state"""
        self.errors.append(f"[{time.strftime('%H:%M:%S')}] {error}")

    def add_warning(self, warning: str):
        """Add warning to state"""
        self.warnings.append(f"[{time.strftime('%H:%M:%S')}] {warning}")


class CodeEvaluationWorkflow:
    """
    Multi-Agent Code Evaluation Workflow Manager

    ENHANCED: Uses multi-file capabilities for efficient batch processing
    """

    def __init__(
        self, config_path: str = "mcp_agent.secrets.yaml", max_files_per_batch: int = 3
    ):
        """Initialize workflow with configuration"""
        self.config_path = config_path
        self.api_config = self._load_api_config()
        self.default_models = get_default_models("mcp_agent.config.yaml")
        self.logger = self._create_logger()
        self.max_files_per_batch = max_files_per_batch

        # Agent instances
        self.orchestrator = None
        self.code_analyzer = None
        self.code_revise = None

        # Memory agent for iterative implementation
        self.memory_agent = None

        # Shared state
        self.evaluation_state = None

    def _load_api_config(self) -> Dict[str, Any]:
        """Load API configuration from YAML file"""
        try:
            with open(self.config_path, "r", encoding="utf-8") as f:
                return yaml.safe_load(f)
        except Exception as e:
            raise Exception(f"Failed to load API config: {e}")

    def _create_logger(self) -> logging.Logger:
        """Create and configure logger"""
        logger = logging.getLogger(__name__)
        logger.setLevel(logging.INFO)

        # Check if handlers already exist to avoid duplicates
        if not logger.handlers:
            # Create console handler
            console_handler = logging.StreamHandler()
            console_handler.setLevel(logging.INFO)

            # Create simple formatter (just the message, no timestamp)
            formatter = logging.Formatter("%(message)s")
            console_handler.setFormatter(formatter)

            # Add handler to logger
            logger.addHandler(console_handler)

        return logger

    async def run_evaluation(
        self,
        repo_path: str,
        docs_path: str,
        memory_path: str,
        workspace_dir: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Run complete evaluation workflow with iterative code implementation using multi-file capabilities

        Args:
            repo_path: Path to repository to evaluate
            docs_path: Path to reproduction documentation
            memory_path: Path to memory file for code summaries
            workspace_dir: Working directory for evaluation (auto-created if None)

        Returns:
            Comprehensive evaluation results
        """
        try:
            # Initialize workspace and state
            if workspace_dir is None:
                workspace_dir = os.path.join(
                    os.path.abspath(repo_path), ".evaluation", f"run_{int(time.time())}"
                )

            os.makedirs(workspace_dir, exist_ok=True)
            self.logger.info(f"🎯 Workspace: {workspace_dir}")

            self.evaluation_state = EvaluationState(
                phase=EvaluationPhase.INITIALIZED,
                repo_path=os.path.abspath(repo_path),
                docs_path=os.path.abspath(docs_path),
                memory_path=os.path.abspath(memory_path),
                workspace_dir=workspace_dir,
                start_time=time.time(),
            )

            self.logger.info("=" * 80)
            self.logger.info("🚀 STARTING ENHANCED MULTI-FILE CODE EVALUATION WORKFLOW")
            self.logger.info("=" * 80)
            self.logger.info(f"📂 Repository: {repo_path}")
            self.logger.info(f"📄 Documentation: {docs_path}")
            self.logger.info(f"🧠 Memory Path: {memory_path}")
            self.logger.info(f"🎯 Workspace: {workspace_dir}")
            self.logger.info(f"📦 Max files per batch: {self.max_files_per_batch}")
            self.logger.info("=" * 80)

            # Initialize agents (including memory agent with multi-file support)
            await self._initialize_agents()

            # PHASE 1: Analysis and Revision Report Generation (ANALYZER AGENT ONLY)
            self.logger.info(
                "🔍 Phase 1: Repository Analysis & Revision Report Generation"
            )
            self.logger.info("📋 Analyzer Agent will generate ALL revision reports")
            self.evaluation_state.phase = EvaluationPhase.ANALYZING
            await self._run_analysis_and_generate_revision_reports()

            # Verify that revision reports were generated
            if not self.evaluation_state.revision_report:
                raise Exception(
                    "Analyzer Agent failed to generate revision reports - cannot proceed to revision phase"
                )

            self.logger.info(
                "✅ Analysis phase completed - revision reports generated by Analyzer Agent"
            )

            # PHASE 2: Iterative Multi-File Code Revision Execution (CODE REVISE AGENT + MEMORY AGENT)
            self.logger.info("🔧 Phase 2: Enhanced Multi-File Code Revision Execution")
            self.logger.info(
                "⚙️ Code Revise Agent will execute ALL revision tasks with multi-file batching"
            )
            self.logger.info(
                "🧠 Memory Agent will manage multi-file code summaries after each batch"
            )
            self.evaluation_state.phase = EvaluationPhase.REVISING

            # Execute iterative revision with multi-file memory management
            revision_completed = (
                await self._run_iterative_multi_file_revision_execution()
            )

            if not revision_completed:
                self.logger.error(
                    "❌ Multi-file code revision phase failed to complete properly"
                )
                raise Exception(
                    "Multi-file code revision phase did not complete successfully"
                )

            self.logger.info(
                "✅ Enhanced multi-file revision phase completed - all tasks executed by Code Revise Agent"
            )

            # PHASE 3: Static Analysis and Automatic Fixes
            self.logger.info("🔍 Phase 3: Static Analysis and Code Quality Fixes")
            self.logger.info(
                "🛠️ Analyzer Agent will perform static analysis and apply automatic fixes"
            )
            self.evaluation_state.phase = EvaluationPhase.STATIC_ANALYSIS

            static_analysis_completed = await self._run_static_analysis_phase()

            if not static_analysis_completed:
                self.logger.warning(
                    "⚠️ Static analysis phase failed but continuing to error analysis"
                )

            self.logger.info("✅ Static analysis phase completed")

            # PHASE 4: Advanced Error Analysis and Remediation
            self.logger.info(
                "🔬 Phase 4: Advanced Error Analysis and Targeted Remediation"
            )
            self.logger.info(
                "🎯 Analyzer Agent will perform error analysis, sandbox execution, and targeted fixes"
            )
            self.evaluation_state.phase = EvaluationPhase.ERROR_ANALYSIS

            error_analysis_completed = await self._run_error_analysis_phase()

            if not error_analysis_completed:
                self.logger.warning(
                    "⚠️ Error analysis phase failed but continuing to final evaluation"
                )

            self.logger.info("✅ Error analysis phase completed")

            # PHASE 5: Final Evaluation
            self.logger.info("📊 Phase 5: Final Evaluation")
            self.evaluation_state.phase = EvaluationPhase.COMPLETED
            results = await self._generate_final_report()

            self.logger.info(
                "✅ Enhanced multi-file evaluation workflow with static analysis and error analysis completed successfully"
            )
            return results

        except Exception as e:
            self.logger.error(f"❌ Evaluation workflow failed: {e}")
            if self.evaluation_state:
                self.evaluation_state.phase = EvaluationPhase.FAILED
                self.evaluation_state.add_error(str(e))

            return {
                "status": "error",
                "message": str(e),
                "repo_path": repo_path,
                "docs_path": docs_path,
            }
        finally:
            await self._cleanup_agents()

    async def _initialize_agents(self):
        """Initialize all agents with MCP connections"""
        try:
            # Initialize Orchestrator Agent
            self.orchestrator = Agent(
                name="EvaluationOrchestrator",
                instruction=ORCHESTRATOR_AGENT_PROMPT,
                server_names=["code-evaluation"],
            )
            await self.orchestrator.__aenter__()
            await self.orchestrator.attach_llm(
                get_preferred_llm_class(self.config_path)
            )

            # Initialize Code Analyzer Agent (RESPONSIBLE FOR ALL ANALYSIS AND REVISION REPORTS)
            self.logger.info(
                "🔬 Initializing Code Analyzer Agent - will handle ALL revision report generation"
            )
            self.code_analyzer = Agent(
                name="CodeAnalyzer",
                instruction=CODE_ANALYZER_AGENT_PROMPT,
                server_names=["code-evaluation", "filesystem"],
            )
            await self.code_analyzer.__aenter__()
            await self.code_analyzer.attach_llm(
                get_preferred_llm_class(self.config_path)
            )

            # Initialize Code Revise Agent (RESPONSIBLE FOR ALL REVISION EXECUTION WITH MULTI-FILE SUPPORT)
            self.logger.info(
                "⚙️ Initializing Code Revise Agent - will handle ALL revision task execution with multi-file capabilities"
            )
            self.code_revise = Agent(
                name="CodeRevise",
                instruction=CODE_REVISE_AGENT_PROMPT,
                server_names=["code-implementation", "code-evaluation"],
            )
            await self.code_revise.__aenter__()
            await self.code_revise.attach_llm(get_preferred_llm_class(self.config_path))

            # Test Code Revise Agent connectivity with improved error handling
            try:
                # Test basic connectivity with a simple tool call
                test_result = await self.code_revise.call_tool(
                    "get_operation_history", {"last_n": 1}
                )
                self.logger.info("✅ Code Revise Agent connectivity test successful")

                # Test multi-file tools availability more safely
                await self._test_multi_file_tools_availability()

            except Exception as e:
                self.logger.error(f"❌ Code Revise Agent connectivity test failed: {e}")
                # Don't fail initialization, just log the issue
                self.logger.warning(
                    "⚠️ Continuing without full connectivity verification"
                )

            # Initialize Memory Agent for iterative multi-file implementation tracking
            self.logger.info(
                "🧠 Initializing Memory Agent - will manage multi-file code summaries and iteration"
            )
            await self._initialize_memory_agent()

            self.logger.info(
                "🤖 All agents initialized successfully with multi-file capabilities"
            )

        except Exception as e:
            self.logger.error(f"Failed to initialize agents: {e}")
            raise

    async def _test_multi_file_tools_availability(self):
        """Test availability of multi-file tools with safe error handling"""
        try:
            # Test write_multiple_files availability
            try:
                # Try calling with invalid input to see if tool exists
                await self.code_revise.call_tool(
                    "write_multiple_files",
                    {
                        "file_implementations": "{}"  # Empty but valid JSON
                    },
                )
                self.logger.info("📦 write_multiple_files tool available")
            except Exception as e:
                # Tool might exist but fail due to empty input - check error message
                if "write_multiple_files" in str(e) or "file_implementations" in str(e):
                    self.logger.info(
                        "📦 write_multiple_files tool available (validated via error response)"
                    )
                else:
                    self.logger.warning(
                        f"📦 write_multiple_files tool may not be available: {e}"
                    )

            # Test read_multiple_files availability
            try:
                await self.code_revise.call_tool(
                    "read_multiple_files",
                    {
                        "file_requests": "[]"  # Empty but valid JSON array
                    },
                )
                self.logger.info("📖 read_multiple_files tool available")
            except Exception as e:
                if "read_multiple_files" in str(e) or "file_requests" in str(e):
                    self.logger.info(
                        "📖 read_multiple_files tool available (validated via error response)"
                    )
                else:
                    self.logger.warning(
                        f"📖 read_multiple_files tool may not be available: {e}"
                    )

        except Exception as e:
            self.logger.warning(f"Multi-file tools availability test failed: {e}")
            self.logger.info(
                "📦 Will attempt to use tools during execution and handle errors gracefully"
            )

    async def _initialize_memory_agent(self):
        """Initialize Memory Agent for iterative multi-file code implementation"""
        try:
            # Read initial plan content
            with open(self.evaluation_state.docs_path, "r", encoding="utf-8") as f:
                initial_plan_content = f.read()

            memory_path = os.path.dirname(self.evaluation_state.memory_path)
            # Initialize memory agent with multi-file support
            self.memory_agent = ConciseMemoryAgent(
                initial_plan_content=initial_plan_content,
                logger=self.logger,
                target_directory=memory_path,
                default_models=self.default_models,
                max_files_per_batch=self.max_files_per_batch,
            )

            self.logger.info(
                f"✅ Memory Agent initialized with multi-file support (max {self.max_files_per_batch} files per batch)"
            )

        except Exception as e:
            self.logger.error(f"Failed to initialize Memory Agent: {e}")
            raise

            # TODO: The prompt is not good, need to be improved

    # YOUR RESPONSIBILITIES (ANALYZER AGENT ONLY):
    # 1. **Repository Analysis Tasks:**
    #    - Use detect_empty_files to identify empty files needing implementation
    #    - Use detect_missing_files to find missing essential files
    #    - Use analyze_repo_structure to get overall repository structure
    #    - Use detect_dependencies to identify project dependencies
    #    - Use assess_code_quality to evaluate code quality metrics
    #    - Use evaluate_documentation to assess documentation completeness
    #    - Use check_reproduction_readiness to determine readiness for reproduction

    async def _run_analysis_and_generate_revision_reports(self):
        """
        PHASE 1: Analysis and Revision Report Generation
        ONLY the Analyzer Agent is responsible for this phase
        """
        try:
            self.logger.info(
                "🔬 Starting comprehensive analysis and revision report generation"
            )
            self.logger.info(
                "📋 Analyzer Agent has SOLE responsibility for generating revision reports"
            )

            # Initialize LLM client
            client, client_type = await self._initialize_llm_client()

            # Prepare tools for code analysis
            tools = self._prepare_analyzer_tool_definitions()

            # Enhanced system message for analyzer agent
            system_message = CODE_ANALYZER_AGENT_PROMPT.format(
                root_dir=self.evaluation_state.repo_path,
                analysis_task=f"Comprehensive analysis and revision report generation for {self.evaluation_state.repo_path}",
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
                    tool_result = await self.code_analyzer.call_tool(
                        tool_name, tool_input
                    )
                    analysis_results[tool_name] = tool_result

                    self.logger.info(f"✅ Analyzer Agent executed: {tool_name}")

                    # Track revision report generation
                    if tool_name == "generate_code_revision_report":
                        revision_report_generated = True

            # CRITICAL: Ensure revision report was generated
            if not revision_report_generated:
                self.logger.warning(
                    "⚠️ Revision report not generated in main flow - generating now..."
                )
                revision_result = await self.code_analyzer.call_tool(
                    "generate_code_revision_report",
                    {
                        "repo_path": self.evaluation_state.repo_path,
                        "docs_path": self.evaluation_state.docs_path,
                    },
                )
                analysis_results["generate_code_revision_report"] = revision_result
                revision_report_generated = True

            # Process and store the revision report
            if "generate_code_revision_report" in analysis_results:
                revision_result = analysis_results["generate_code_revision_report"]
                revision_content = self._extract_tool_result_content(revision_result)
                revision_data = self._safe_parse_json(
                    revision_content, "Analyzer revision report"
                )
                revision_data = self._normalize_revision_data(
                    revision_data, "Analyzer revision report"
                )

                if (
                    isinstance(revision_data, dict)
                    and revision_data.get("status") == "success"
                ):
                    self.evaluation_state.revision_report = revision_data
                    self.logger.info(
                        "✅ Analyzer Agent successfully generated revision report"
                    )

                    # Extract all files from revision tasks for workflow tracking
                    revision_tasks = revision_data.get("revision_report", {}).get(
                        "revision_tasks", []
                    )
                    all_files = self._extract_all_files_from_revision_tasks(
                        revision_tasks
                    )
                    self.evaluation_state.all_files_to_implement = all_files

                    self.logger.info(
                        f"📋 Revision report contains {len(revision_tasks)} tasks for multi-file batch execution"
                    )
                    self.logger.info(
                        f"📁 Total unique files to implement: {len(all_files)}"
                    )

                    # Debug: Log file extraction for multi-file batching verification
                    total_files = len(all_files)
                    for task in revision_tasks:
                        files = self._extract_files_from_revision_task(task)
                        self.logger.info(
                            f"🔍 Task {task.get('task_id')}: Found {len(files)} files for batch processing"
                        )
                        if files:
                            self.logger.info(f"   📄 Files: {files[:]}...")

                    batches_needed = (
                        total_files + self.max_files_per_batch - 1
                    ) // self.max_files_per_batch
                    self.logger.info(
                        f"📦 Estimated {batches_needed} multi-file batches needed for {total_files} total files"
                    )
                else:
                    raise Exception(
                        f"Analyzer Agent failed to generate valid revision report: {revision_data}"
                    )

            # Process analysis results for summary
            analysis_result = self._process_analysis_results(analysis_results, response)
            self.evaluation_state.code_analysis = analysis_result

            self.logger.info("✅ Analyzer Agent completed all responsibilities:")
            self.logger.info("   ✓ Repository analysis")
            self.logger.info("   ✓ Revision report generation")
            self.logger.info("   ✓ Evaluation summary")

        except Exception as e:
            self.evaluation_state.add_error(f"Analyzer Agent failed: {e}")
            raise Exception(
                f"Analyzer Agent failed to complete analysis and revision report generation: {e}"
            )

    def _extract_all_files_from_revision_tasks(
        self, revision_tasks: List[Dict[str, Any]]
    ) -> List[str]:
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

        self.logger.info(
            f"📁 Extracted {len(unique_files)} unique files from {len(revision_tasks)} revision tasks"
        )
        return unique_files

    async def _run_static_analysis_phase(self) -> bool:
        """
        PHASE 3: Static Analysis and Automatic Code Quality Fixes
        Uses the Analyzer Agent to perform comprehensive static analysis and apply automatic fixes
        """
        try:
            self.logger.info("🔍 Starting static analysis phase with automatic fixes")

            # Use the Analyzer Agent to perform static analysis with automatic fixes
            self.logger.info(
                "🛠️ Running comprehensive static analysis with automatic formatting fixes"
            )
            static_analysis_result = await self.code_analyzer.call_tool(
                "perform_static_analysis",
                {
                    "repo_path": self.evaluation_state.repo_path,
                    "auto_fix": True,  # Enable automatic fixes
                    "languages": None,  # Auto-detect all languages
                },
            )

            # Parse static analysis results
            static_content = self._extract_tool_result_content(static_analysis_result)
            static_data = self._safe_parse_json(static_content, "Static analysis")

            if isinstance(static_data, dict) and static_data.get("status") == "success":
                analysis = static_data.get("analysis", {})
                summary = static_data.get("summary", {})

                # Create StaticAnalysisResult
                self.evaluation_state.static_analysis = StaticAnalysisResult(
                    analysis_success=True,
                    total_files_analyzed=summary.get("total_files_analyzed", 0),
                    languages_detected=analysis.get("languages_detected", []),
                    total_issues_found=summary.get("total_issues_found", 0),
                    auto_fixes_applied=summary.get("auto_fixes_applied", 0),
                    analysis_duration_seconds=summary.get(
                        "analysis_duration_seconds", 0.0
                    ),
                    issues_by_severity=summary.get("issues_by_severity", {}),
                    tools_used=summary.get("tools_used", []),
                    syntax_errors_found=summary.get("issues_by_severity", {}).get(
                        "errors", 0
                    ),
                    formatting_fixes_applied=summary.get("auto_fixes_applied", 0),
                    most_problematic_files=[],
                    static_analysis_report=static_data,
                )

                self.logger.info("✅ Static analysis completed:")
                self.logger.info(
                    f"   📁 Files analyzed: {summary.get('total_files_analyzed', 0)}"
                )
                self.logger.info(
                    f"   🔧 Languages detected: {len(analysis.get('languages_detected', []))}"
                )
                self.logger.info(
                    f"   ⚠️ Issues found: {summary.get('total_issues_found', 0)}"
                )
                self.logger.info(
                    f"   🔨 Auto-fixes applied: {summary.get('auto_fixes_applied', 0)}"
                )
                self.logger.info(
                    f"   ⏱️ Duration: {summary.get('analysis_duration_seconds', 0.0):.2f}s"
                )
                self.logger.info(
                    f"   🛠️ Tools used: {', '.join(summary.get('tools_used', []))}"
                )

                # Generate detailed issues report if issues were found
                if summary.get("total_issues_found", 0) > 0:
                    self.logger.info(
                        "📊 Generating detailed static analysis issues report"
                    )
                    issues_report_result = await self.code_analyzer.call_tool(
                        "generate_static_issues_report",
                        {
                            "repo_path": self.evaluation_state.repo_path,
                            "severity_filter": None,  # Include all severities
                            "language_filter": None,  # Include all languages
                        },
                    )

                    issues_content = self._extract_tool_result_content(
                        issues_report_result
                    )
                    issues_data = self._safe_parse_json(issues_content, "Issues report")

                    if (
                        isinstance(issues_data, dict)
                        and issues_data.get("status") == "success"
                    ):
                        # Update most problematic files
                        problematic_files = issues_data.get(
                            "most_problematic_files", []
                        )
                        self.evaluation_state.static_analysis.most_problematic_files = [
                            f["file_path"] for f in problematic_files[:5]
                        ]

                        self.logger.info(
                            f"📋 Issues report generated: {len(problematic_files)} problematic files identified"
                        )

                        # Log most problematic files
                        if problematic_files:
                            self.logger.info("🔍 Most problematic files:")
                            for i, file_info in enumerate(problematic_files[:5], 1):
                                self.logger.info(
                                    f"   {i}. {file_info['file_path']} ({file_info['issue_count']} issues)"
                                )
                    else:
                        self.logger.warning(
                            "⚠️ Failed to generate detailed issues report"
                        )

                # Apply additional automatic formatting if tools are available
                if summary.get("auto_fixes_applied", 0) < summary.get(
                    "total_issues_found", 0
                ):
                    self.logger.info(
                        "🔧 Attempting additional automatic formatting fixes"
                    )
                    format_result = await self.code_analyzer.call_tool(
                        "auto_fix_formatting",
                        {
                            "repo_path": self.evaluation_state.repo_path,
                            "languages": None,  # Auto-detect all languages
                            "dry_run": False,  # Apply actual fixes
                        },
                    )

                    format_content = self._extract_tool_result_content(format_result)
                    format_data = self._safe_parse_json(
                        format_content, "Auto-formatting"
                    )

                    if (
                        isinstance(format_data, dict)
                        and format_data.get("status") == "success"
                    ):
                        format_results = format_data.get("formatting_results", {})
                        files_formatted = format_results.get("total_files_formatted", 0)

                        if files_formatted > 0:
                            self.evaluation_state.static_analysis.formatting_fixes_applied += files_formatted
                            self.logger.info(
                                f"✅ Additional formatting applied to {files_formatted} files"
                            )
                        else:
                            self.logger.info("ℹ️ No additional formatting fixes needed")
                    else:
                        self.logger.warning("⚠️ Additional formatting failed")

                return True

            else:
                self.logger.error(f"❌ Static analysis failed: {static_data}")
                # Create minimal static analysis result for failed analysis
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
                    static_analysis_report=static_data,
                )
                return False

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

    async def _run_error_analysis_phase(self) -> bool:
        """
        PHASE 4: Advanced Error Analysis and Targeted Remediation
        Uses sandbox execution to identify runtime errors and provides targeted fixes
        """
        try:
            self.logger.info(
                "🔬 Starting advanced error analysis phase with sandbox execution"
            )

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
            validation_result = await self.code_analyzer.call_tool(
                "run_code_validation",
                {
                    "repo_path": self.evaluation_state.repo_path,
                    "test_command": None,  # Auto-detect test patterns
                },
            )

            validation_content = self._extract_tool_result_content(validation_result)
            validation_data = self._safe_parse_json(
                validation_content, "Code validation"
            )

            if validation_data.get("status") == "todo":
                self.logger.info(
                    "📝 Sandbox validation interface ready - TODO: Implement actual sandbox"
                )
                sandbox_executions_completed = 1  # Interface call completed

            # 2. If we have error information, perform error analysis
            # For demonstration, simulate some error scenarios
            simulated_errors = self._generate_simulated_error_scenarios()

            for error_scenario in simulated_errors:
                self.logger.info(
                    f"🔍 Analyzing error scenario: {error_scenario['type']}"
                )

                # Generate error analysis report
                error_analysis_result = await self.code_analyzer.call_tool(
                    "generate_error_analysis_report",
                    {
                        "traceback_text": error_scenario["traceback"],
                        "repo_path": self.evaluation_state.repo_path,
                        "execution_context": error_scenario["context"],
                    },
                )

                error_analysis_content = self._extract_tool_result_content(
                    error_analysis_result
                )
                error_analysis_data = self._safe_parse_json(
                    error_analysis_content, "Error analysis"
                )

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
                        high_conf_suspects = [
                            sf for sf in suspect_files if sf["confidence_score"] > 0.8
                        ]
                        high_confidence_fixes += len(high_conf_suspects)

                    # Track error types
                    traceback_analysis = report.get("traceback_analysis", {})
                    error_type = traceback_analysis.get("error_type")
                    if error_type and error_type not in error_types_found:
                        error_types_found.append(error_type)

                    # Count critical errors
                    if error_scenario.get("severity") == "critical":
                        critical_errors_found += 1

                    remediation_tasks_created += len(
                        report.get("remediation_suggestions", [])
                    )

                    self.logger.info(
                        f"✅ Error analysis completed: {len(suspect_files)} suspect files identified"
                    )

                else:
                    self.logger.warning(
                        f"⚠️ Error analysis failed for scenario: {error_scenario['type']}"
                    )

            # 3. Perform import dependency analysis
            self.logger.info(
                "🔗 Step 3: Analyzing import dependencies for error propagation"
            )
            import_analysis_result = await self.code_analyzer.call_tool(
                "analyze_import_dependencies",
                {
                    "repo_path": self.evaluation_state.repo_path,
                    "target_file": None,  # Analyze all files
                },
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
                    sum(
                        sf["confidence_score"]
                        for sf in report.get("error_analysis_report", {}).get(
                            "suspect_files", []
                        )
                    )
                    for report in error_analysis_reports
                )
                max_possible_confidence = (
                    error_reports_generated * 10
                )  # Assume max 10 files per report with confidence 1.0
                remediation_success_rate = (
                    min(total_confidence / max(max_possible_confidence, 1), 1.0) * 100
                )

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
                most_problematic_files=list(set(most_problematic_files))[
                    :5
                ],  # Dedupe and limit
                remediation_success_rate=remediation_success_rate,
                error_analysis_reports=error_analysis_reports,
            )

            self.logger.info("✅ Error analysis phase completed:")
            self.logger.info(
                f"   📊 Error reports generated: {error_reports_generated}"
            )
            self.logger.info(
                f"   📁 Suspect files identified: {suspect_files_identified}"
            )
            self.logger.info(f"   🎯 High confidence fixes: {high_confidence_fixes}")
            self.logger.info(f"   ⚠️ Critical errors found: {critical_errors_found}")
            self.logger.info(
                f"   📈 Remediation success rate: {remediation_success_rate:.1f}%"
            )
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
                error_analysis_reports=[],
            )
            return False

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
                "traceback": f"""Traceback (most recent call last):
  File "{self.evaluation_state.repo_path}/main.py", line 10, in <module>
    from utils.helper import process_data
  File "{self.evaluation_state.repo_path}/utils/helper.py", line 5, in <module>
    import numpy as np
ModuleNotFoundError: No module named 'numpy' """,
            },
            {
                "type": "AttributeError",
                "severity": "medium",
                "context": "function call",
                "traceback": f"""Traceback (most recent call last):
  File "{self.evaluation_state.repo_path}/src/processor.py", line 25, in process_file
    result = data.transform()
AttributeError: 'dict' object has no attribute 'transform' """,
            },
        ]

    async def _run_iterative_multi_file_revision_execution(self) -> bool:
        """
        PHASE 2: Enhanced Multi-File Code Revision Execution
        Code Revise Agent + Memory Agent work together for multi-file batch implementation
        """
        try:
            self.logger.info("⚙️ Starting enhanced multi-file revision execution phase")
            self.logger.info(
                "🔧 Code Revise Agent + Memory Agent will execute revision tasks with multi-file batching"
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
                f"🔧 Starting multi-file batch execution of {len(revision_tasks)} revision tasks"
            )

            # Set up workspace for the Code Revise Agent
            try:
                workspace_setup_result = await self.code_revise.call_tool(
                    "set_workspace", {"workspace_path": self.evaluation_state.repo_path}
                )
                self.logger.info(
                    f"✅ Code Revise Agent workspace configured: {self.evaluation_state.repo_path}"
                )
            except Exception as e:
                self.logger.warning(
                    f"Failed to set workspace for Code Revise Agent: {e}"
                )

            # Initialize LLM client for code revision
            client, client_type = await self._initialize_llm_client()
            tools = self._prepare_revision_tool_definitions()

            # Execute all revision tasks with multi-file batching
            revision_results = await self._execute_multi_file_revision_tasks(
                client, client_type, tools, revision_tasks, revision_report_data
            )

            # Store revision results
            self.evaluation_state.code_revision = revision_results

            self.logger.info("✅ Multi-file revision execution completed:")
            self.logger.info(
                f"   📊 Tasks completed: {len(revision_results.tasks_completed)}/{revision_results.total_tasks}"
            )
            self.logger.info(
                f"   📊 Completion rate: {revision_results.completion_rate:.1f}%"
            )
            self.logger.info(
                f"   📊 Files created: {len(revision_results.files_created)}"
            )
            self.logger.info(
                f"   📊 Files modified: {len(revision_results.files_modified)}"
            )
            self.logger.info(
                f"   📦 Batch operations: {len(revision_results.batch_operations)}"
            )

            return revision_results.revision_success

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
                    f"❌ No files found in task {task_id} after extraction - skipping task"
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

            # Use the new revision-specific message creation
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
                            self.logger.info(f"🔧 Executing tool: {tool_name}")
                            tool_result = await self.code_revise.call_tool(
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

    def _extract_files_from_revision_task(self, task):
        """
        FIXED: Properly extract file paths from revision task based on actual report structure
        """
        files = []
        task_id = task.get("task_id", "")

        # Method 1: Extract from task details structure (for empty files task)
        details = task.get("details", {})

        if task_id == "implement_empty_files":
            # Handle empty files task structure
            completely_empty = details.get("completely_empty", [])
            minimal_content = details.get("minimal_content", [])

            for file_info in completely_empty:
                if isinstance(file_info, dict) and "path" in file_info:
                    files.append(file_info["path"])

            for file_info in minimal_content:
                if isinstance(file_info, dict) and "path" in file_info:
                    files.append(file_info["path"])

            self.logger.info(
                f"📋 Extracted {len(files)} empty files from task {task_id}"
            )

        elif task_id == "create_missing_files":
            # Handle missing files task structure - extract from suggestions
            if isinstance(details, list):
                for missing_file in details:
                    if isinstance(missing_file, dict):
                        suggestions = missing_file.get("suggestions", [])
                        for suggestion in suggestions:
                            if isinstance(suggestion, str):
                                files.append(suggestion)

            self.logger.info(
                f"📋 Extracted {len(files)} missing files from task {task_id}"
            )

        elif task_id == "improve_code_quality":
            # Handle quality improvement task - extract from security issues or other quality details
            quality_details = details.get("security_issues", [])
            for issue in quality_details:
                if isinstance(issue, str) and ":" in issue:
                    # Extract file path from "file_path: description" format
                    file_path = issue.split(":")[0].strip()
                    if file_path:
                        files.append(file_path)

            self.logger.info(
                f"📋 Extracted {len(files)} quality files from task {task_id}"
            )

        # Method 2: Look for standard file path fields (fallback)
        if not files:
            for field in [
                "file_path",
                "target_file",
                "files",
                "file_list",
                "target_files",
            ]:
                if field in details:
                    value = details[field]
                    if isinstance(value, str):
                        files.append(value)
                    elif isinstance(value, list):
                        files.extend([f for f in value if isinstance(f, str)])

        # Method 3: Extract from description (fallback)
        if not files:
            description = task.get("description", "")
            if "file" in description.lower():
                import re

                file_patterns = [
                    r"([a-zA-Z0-9_\-/]+\.[a-zA-Z0-9]+)",  # filename.ext
                    r'"([a-zA-Z0-9_\-/]+\.[a-zA-Z0-9]+)"',  # "filename.ext"
                    r"`([a-zA-Z0-9_\-/]+\.[a-zA-Z0-9]+)`",  # `filename.ext`
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

        self.logger.info(
            f"🔍 Task {task_id}: Final extracted files count: {len(unique_files)}"
        )

        return unique_files

    def _process_analysis_results(self, analysis_results, response):
        """Process analysis results into CodeAnalysisResult structure"""
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
                    documentation_completeness=metrics.get("documentation_score", 0)
                    / 100,
                    reproduction_readiness={
                        "score": metrics.get("reproduction_readiness_score", 0)
                    },
                    confidence_score=summary_data["overall_assessment"]["score"] / 100,
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
            confidence_score=0.7,
        )

    # ==================== Tool Definitions ====================

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

    def _prepare_revision_tool_definitions(self) -> List[Dict[str, Any]]:
        """Prepare tool definitions for Code Revise Agent (revision execution with multi-file support)"""
        try:
            from config.mcp_tool_definitions_index import get_mcp_tools

            implementation_tools = get_mcp_tools("code_implementation")
            evaluation_tools = get_mcp_tools("code_evaluation")

            # Log what tools we got from each source
            impl_tool_names = [tool.get("name", "") for tool in implementation_tools]
            eval_tool_names = [tool.get("name", "") for tool in evaluation_tools]

            self.logger.info(f"📦 Implementation tools loaded: {impl_tool_names}")
            self.logger.info(f"📖 Evaluation tools loaded: {eval_tool_names}")

            # Combine tools for revision execution with multi-file support
            all_tools = implementation_tools.copy()
            tool_names = [tool.get("name", "") for tool in implementation_tools]

            # Add evaluation tools that are needed for revision context
            for tool in evaluation_tools:
                tool_name = tool.get("name", "")
                if tool_name not in tool_names and tool_name in [
                    "read_multiple_files",
                    "read_file",
                    "get_file_structure",
                ]:
                    all_tools.append(tool)
                    tool_names.append(tool_name)

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

    # ==================== LLM Communication Methods ====================

    async def _initialize_llm_client(self):
        """Initialize LLM client (Anthropic or OpenAI) based on API key availability"""
        # Check which API has available key and try that first
        anthropic_key = self.api_config.get("anthropic", {}).get("api_key", "")
        openai_key = self.api_config.get("openai", {}).get("api_key", "")

        # Try Anthropic API first if key is available
        if anthropic_key and anthropic_key.strip():
            try:
                from anthropic import AsyncAnthropic

                client = AsyncAnthropic(api_key=anthropic_key)
                # Test connection with default model from config
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

        # Try OpenAI API if Anthropic failed or key not available
        if openai_key and openai_key.strip():
            try:
                from openai import AsyncOpenAI

                # Handle custom base_url if specified
                openai_config = self.api_config.get("openai", {})
                base_url = openai_config.get("base_url")

                if base_url:
                    client = AsyncOpenAI(api_key=openai_key, base_url=base_url)
                else:
                    client = AsyncOpenAI(api_key=openai_key)

                # Test connection with default model from config
                try:
                    await client.chat.completions.create(
                        model=self.default_models["openai"],
                        max_tokens=20,
                        messages=[{"role": "user", "content": "test"}],
                    )
                except Exception as e:
                    if "max_tokens" in str(e) and "max_completion_tokens" in str(e):
                        # Retry with max_completion_tokens for models that require it
                        await client.chat.completions.create(
                            model=self.default_models["openai"],
                            max_completion_tokens=20,
                            messages=[{"role": "user", "content": "test"}],
                        )
                    else:
                        raise
                self.logger.info(
                    f"Using OpenAI API with model: {self.default_models['openai']}"
                )
                if base_url:
                    self.logger.info(f"Using custom base URL: {base_url}")
                return client, "openai"
            except Exception as e:
                self.logger.warning(f"OpenAI API unavailable: {e}")

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
                {"role": "user", "content": "Please continue with analysis"}
            ]

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
                tool_calls.append(
                    {"id": block.id, "name": block.name, "input": block.input}
                )

        return {"content": content, "tool_calls": tool_calls}

    async def _call_openai_with_tools(
        self, client, system_message, messages, tools, max_tokens
    ):
        """Call OpenAI API"""
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

        try:
            response = await client.chat.completions.create(
                model=self.default_models["openai"],
                messages=openai_messages,
                tools=openai_tools if openai_tools else None,
                max_tokens=max_tokens,
                temperature=0.2,
            )
        except Exception as e:
            if "max_tokens" in str(e) and "max_completion_tokens" in str(e):
                # Retry with max_completion_tokens for models that require it
                response = await client.chat.completions.create(
                    model=self.default_models["openai"],
                    messages=openai_messages,
                    tools=openai_tools if openai_tools else None,
                    max_completion_tokens=max_tokens,
                )
            else:
                raise

        message = response.choices[0].message
        content = message.content or ""

        tool_calls = []
        if message.tool_calls:
            for tool_call in message.tool_calls:
                tool_calls.append(
                    {
                        "id": tool_call.id,
                        "name": tool_call.function.name,
                        "input": json.loads(tool_call.function.arguments),
                    }
                )

        return {"content": content, "tool_calls": tool_calls}

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

    # ==================== Utility Methods ====================

    def _extract_tool_result_content(self, tool_result):
        """Extract content from CallToolResult object"""
        if hasattr(tool_result, "content"):
            content = tool_result.content
        elif hasattr(tool_result, "data"):
            content = tool_result.data
        else:
            content = tool_result

        try:
            if isinstance(content, list):
                extracted_texts = []
                for block in content:
                    if isinstance(block, dict) and "text" in block:
                        extracted_texts.append(str(block.get("text", "")))
                    elif hasattr(block, "text"):
                        extracted_texts.append(str(getattr(block, "text")))
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
            self.logger.debug(
                f"{context_name}: Successfully parsed after string conversion"
            )
            return parsed
        except (json.JSONDecodeError, TypeError) as e:
            self.logger.error(
                f"{context_name}: Failed to parse after string conversion: {e}"
            )
            return {"status": "error", "message": f"Parsing failed: {e}"}

    def _normalize_revision_data(
        self, data, context_name: str = "revision"
    ) -> Dict[str, Any]:
        """Normalize revision report into standard structure"""
        try:
            if isinstance(data, dict):
                if "revision_report" in data:
                    return data

                if "revision_tasks" in data and isinstance(
                    data["revision_tasks"], list
                ):
                    status_value = data.get("status", "success")
                    other_fields = {
                        k: v
                        for k, v in data.items()
                        if k not in ("revision_tasks", "status")
                    }
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
                return {
                    "status": "success",
                    "revision_report": {"revision_tasks": data},
                }

            return {
                "status": "error",
                "message": f"Unexpected {context_name} type: {type(data)}",
            }
        except Exception as e:
            self.logger.error(f"Failed to normalize {context_name}: {e}")
            return {"status": "error", "message": f"Normalization failed: {e}"}

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

    def _clean_json_string(self, json_str: str) -> str:
        """Clean JSON string to handle control characters and formatting issues"""
        try:
            # Remove literal newlines and tabs within string values
            # This handles cases where LLM puts actual newlines instead of \n

            # First, let's try to fix common JSON issues
            cleaned = json_str

            # Fix unescaped newlines in string values
            # Pattern: find content between quotes that contains literal newlines
            import re

            # Method 1: Replace literal newlines with \n in string values
            def fix_string_newlines(match):
                content = match.group(1)
                # Replace literal newlines with escaped newlines
                content = content.replace("\n", "\\n")
                content = content.replace("\r", "\\r")
                content = content.replace("\t", "\\t")
                return f'"{content}"'

            # Find all string values and fix them
            # This regex finds quoted strings that may contain newlines
            string_pattern = r'"([^"]*(?:\\.[^"]*)*)"'
            cleaned = re.sub(
                string_pattern, fix_string_newlines, cleaned, flags=re.DOTALL
            )

            # Method 2: Alternative approach - handle multiline strings specifically
            if '"""' in cleaned:
                # Handle triple-quoted strings that LLM might generate
                def fix_triple_quotes(match):
                    content = match.group(1)
                    # Escape the content properly
                    content = content.replace("\\", "\\\\")
                    content = content.replace("\n", "\\n")
                    content = content.replace("\r", "\\r")
                    content = content.replace("\t", "\\t")
                    content = content.replace('"', '\\"')
                    return f'"{content}"'

                # Replace triple-quoted content
                triple_quote_pattern = r'"""(.*?)"""'
                cleaned = re.sub(
                    triple_quote_pattern, fix_triple_quotes, cleaned, flags=re.DOTALL
                )

            return cleaned

        except Exception as e:
            self.logger.error(f"Failed to clean JSON string: {e}")
            return json_str

    def _parse_file_implementations_robust(
        self, file_implementations_str: str
    ) -> Dict[str, str]:
        """Robust parsing of file implementations with error handling and cleanup"""
        try:
            # First attempt: direct parsing
            try:
                if isinstance(file_implementations_str, dict):
                    return file_implementations_str

                if isinstance(file_implementations_str, str):
                    return json.loads(file_implementations_str)

            except json.JSONDecodeError as e:
                self.logger.warning(f"Initial JSON parse failed: {e}")
                self.logger.info("Attempting to clean and re-parse JSON...")

                # Second attempt: clean and parse
                cleaned_json = self._clean_json_string(file_implementations_str)
                try:
                    return json.loads(cleaned_json)
                except json.JSONDecodeError as e2:
                    self.logger.warning(f"Cleaned JSON parse failed: {e2}")

                    # Third attempt: Extract file content using regex
                    self.logger.info("Attempting regex extraction as fallback...")
                    return self._extract_files_from_malformed_json(
                        file_implementations_str
                    )

        except Exception as e:
            self.logger.error(f"All parsing methods failed: {e}")
            raise

    def _extract_files_from_malformed_json(self, content: str) -> Dict[str, str]:
        """Extract file content from malformed JSON using regex as last resort"""
        import re

        files = {}

        # Pattern to match file entries: "filepath": "content"
        # This handles various quote styles and content
        pattern = r'"([^"]+\.py)":\s*"((?:[^"\\]|\\.)*)(?:",|\s*})'

        matches = re.findall(pattern, content, re.DOTALL)

        for file_path, file_content in matches:
            # Unescape the content
            unescaped_content = file_content.replace('\\"', '"')
            unescaped_content = unescaped_content.replace("\\n", "\n")
            unescaped_content = unescaped_content.replace("\\t", "\t")
            unescaped_content = unescaped_content.replace("\\r", "\r")
            unescaped_content = unescaped_content.replace("\\\\", "\\")

            files[file_path] = unescaped_content
            self.logger.info(f"📄 Extracted file via regex: {file_path}")

        if not files:
            # Even more aggressive extraction - look for any file patterns
            file_pattern = r"([a-zA-Z0-9_/]+\.py)"
            potential_files = re.findall(file_pattern, content)

            # Create minimal implementations for found files
            for file_path in potential_files:
                if file_path not in files:
                    files[file_path] = (
                        f"# Implementation for {file_path}\n# Auto-generated due to parsing error\npass\n"
                    )
                    self.logger.warning(
                        f"⚠️ Created minimal implementation for: {file_path}"
                    )

        return files

    async def _generate_final_report(self) -> Dict[str, Any]:
        """Generate comprehensive evaluation report with multi-file batch details"""
        try:
            evaluation_time = time.time() - self.evaluation_state.start_time

            # Get memory agent statistics for final report
            memory_stats = None
            if self.memory_agent:
                all_files = self.evaluation_state.all_files_to_implement
                implemented_files = self._get_current_implemented_files()

                memory_stats = self.memory_agent.get_memory_statistics(
                    all_files=all_files, implemented_files=implemented_files
                )

            # Analyze batch operations
            batch_analysis = {}
            if (
                self.evaluation_state.code_revision
                and self.evaluation_state.code_revision.batch_operations
            ):
                batch_ops = self.evaluation_state.code_revision.batch_operations
                batch_analysis = {
                    "total_batches": len(batch_ops),
                    "successful_batches": len(
                        [b for b in batch_ops if b.get("status") == "completed"]
                    ),
                    "failed_batches": len(
                        [b for b in batch_ops if b.get("status") == "failed"]
                    ),
                    "average_batch_size": sum(b.get("batch_size", 0) for b in batch_ops)
                    / len(batch_ops)
                    if batch_ops
                    else 0,
                    "tools_used": list(
                        set(
                            [
                                tool
                                for b in batch_ops
                                for tool in b.get("tools_used", [])
                            ]
                        )
                    ),
                }

            report = {
                "status": "success",
                "evaluation_id": f"eval_{int(self.evaluation_state.start_time)}",
                "repository": {
                    "path": self.evaluation_state.repo_path,
                    "documentation": self.evaluation_state.docs_path,
                    "memory_file": self.evaluation_state.memory_path,
                },
                "timing": {
                    "total_time_seconds": evaluation_time,
                    "started_at": time.strftime(
                        "%Y-%m-%d %H:%M:%S",
                        time.localtime(self.evaluation_state.start_time),
                    ),
                    "completed_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                },
                "phase_results": {
                    "code_analysis": asdict(self.evaluation_state.code_analysis)
                    if self.evaluation_state.code_analysis
                    else None,
                    "code_revision": asdict(self.evaluation_state.code_revision)
                    if self.evaluation_state.code_revision
                    else None,
                    "static_analysis": asdict(self.evaluation_state.static_analysis)
                    if self.evaluation_state.static_analysis
                    else None,
                    "error_analysis": asdict(self.evaluation_state.error_analysis)
                    if self.evaluation_state.error_analysis
                    else None,
                    "revision_report": self.evaluation_state.revision_report,
                },
                "multi_file_implementation": {
                    "max_files_per_batch": self.max_files_per_batch,
                    "memory_agent_enabled": self.memory_agent is not None,
                    "memory_statistics": memory_stats,
                    "batch_analysis": batch_analysis,
                    "completion_rate": self.evaluation_state.code_revision.completion_rate
                    if self.evaluation_state.code_revision
                    else 0.0,
                    "total_tasks": self.evaluation_state.code_revision.total_tasks
                    if self.evaluation_state.code_revision
                    else 0,
                    "tasks_completed": len(
                        self.evaluation_state.code_revision.tasks_completed
                    )
                    if self.evaluation_state.code_revision
                    else 0,
                    "tasks_failed": len(
                        self.evaluation_state.code_revision.tasks_failed
                    )
                    if self.evaluation_state.code_revision
                    else 0,
                    "multi_file_batching_enabled": True,
                    "all_files_count": len(
                        self.evaluation_state.all_files_to_implement
                    ),
                    "files_implemented_count": len(
                        self._get_current_implemented_files()
                    ),
                },
                "overall_assessment": {
                    "implementation_completeness": "high"
                    if self.evaluation_state.code_revision
                    and self.evaluation_state.code_revision.revision_success
                    else "low",
                    "code_quality": "good"
                    if self.evaluation_state.code_analysis
                    and self.evaluation_state.code_analysis.confidence_score > 0.7
                    else "needs_improvement",
                    "documentation_quality": "adequate"
                    if self.evaluation_state.code_analysis
                    and self.evaluation_state.code_analysis.documentation_completeness
                    > 0.6
                    else "insufficient",
                    "project_health": self.evaluation_state.code_revision.final_project_health
                    if self.evaluation_state.code_revision
                    else "unknown",
                    "multi_file_success": self.evaluation_state.code_revision.completion_rate
                    >= 75.0
                    if self.evaluation_state.code_revision
                    else False,
                    "batch_efficiency": batch_analysis.get("successful_batches", 0)
                    / max(batch_analysis.get("total_batches", 1), 1)
                    * 100,
                    "static_analysis_success": self.evaluation_state.static_analysis.analysis_success
                    if self.evaluation_state.static_analysis
                    else False,
                    "syntax_quality": "good"
                    if self.evaluation_state.static_analysis
                    and self.evaluation_state.static_analysis.syntax_errors_found == 0
                    else "needs_fixing",
                    "formatting_quality": "good"
                    if self.evaluation_state.static_analysis
                    and self.evaluation_state.static_analysis.formatting_fixes_applied
                    > 0
                    else "unchanged",
                    "error_analysis_success": self.evaluation_state.error_analysis.analysis_success
                    if self.evaluation_state.error_analysis
                    else False,
                    "remediation_quality": "excellent"
                    if self.evaluation_state.error_analysis
                    and self.evaluation_state.error_analysis.remediation_success_rate
                    > 80
                    else "good"
                    if self.evaluation_state.error_analysis
                    and self.evaluation_state.error_analysis.remediation_success_rate
                    > 60
                    else "needs_improvement",
                    "runtime_stability": "stable"
                    if self.evaluation_state.error_analysis
                    and self.evaluation_state.error_analysis.critical_errors_found == 0
                    else "unstable",
                },
                "issues_found": {
                    "errors": self.evaluation_state.errors,
                    "warnings": self.evaluation_state.warnings,
                },
                "recommendations": [],
            }

            # Generate dynamic recommendations based on multi-file batch results
            if self.evaluation_state.code_revision:
                revision = self.evaluation_state.code_revision
                if revision.completion_rate >= 90:
                    report["recommendations"].append(
                        f"Excellent completion rate: {revision.completion_rate:.1f}% of tasks completed successfully with multi-file batching"
                    )
                elif revision.completion_rate >= 75:
                    report["recommendations"].append(
                        f"Good completion rate: {revision.completion_rate:.1f}% of tasks completed successfully"
                    )
                else:
                    report["recommendations"].append(
                        f"Review failed tasks: {len(revision.tasks_failed)} tasks need attention"
                    )

                if batch_analysis.get("total_batches", 0) > 0:
                    batch_success_rate = (
                        batch_analysis.get("successful_batches", 0)
                        / batch_analysis.get("total_batches", 1)
                        * 100
                    )
                    report["recommendations"].append(
                        f"Multi-file batch success rate: {batch_success_rate:.1f}% ({batch_analysis.get('successful_batches', 0)}/{batch_analysis.get('total_batches', 0)} batches)"
                    )

                    if batch_analysis.get("average_batch_size", 0) > 0:
                        report["recommendations"].append(
                            f"Average batch size: {batch_analysis.get('average_batch_size', 0):.1f} files per batch"
                        )

                if revision.empty_files_implemented > 0:
                    report["recommendations"].append(
                        f"Successfully implemented {revision.empty_files_implemented} empty files using multi-file batching"
                    )
                if revision.missing_files_created > 0:
                    report["recommendations"].append(
                        f"Successfully created {revision.missing_files_created} missing files"
                    )
                if revision.quality_issues_fixed > 0:
                    report["recommendations"].append(
                        f"Fixed {revision.quality_issues_fixed} code quality issues"
                    )
                if revision.revision_issues:
                    report["recommendations"].append(
                        f"Address {len(revision.revision_issues)} remaining issues"
                    )
            else:
                report["recommendations"].extend(
                    [
                        "Consider using multi-file batch processing for efficiency",
                        "Improve documentation coverage",
                        "Add dependency version pinning",
                    ]
                )

            # Add static analysis recommendations
            if self.evaluation_state.static_analysis:
                static = self.evaluation_state.static_analysis
                if static.analysis_success:
                    if static.total_issues_found > 0:
                        report["recommendations"].append(
                            f"Static analysis found {static.total_issues_found} code quality issues"
                        )
                        if static.syntax_errors_found > 0:
                            report["recommendations"].append(
                                f"Fix {static.syntax_errors_found} syntax errors identified"
                            )
                    if static.auto_fixes_applied > 0:
                        report["recommendations"].append(
                            f"Successfully applied {static.auto_fixes_applied} automatic formatting fixes"
                        )
                    if static.formatting_fixes_applied > 0:
                        report["recommendations"].append(
                            f"Improved code formatting in {static.formatting_fixes_applied} files"
                        )
                    if static.most_problematic_files:
                        report["recommendations"].append(
                            f"Review most problematic files: {', '.join(static.most_problematic_files[:3])}"
                        )
                    if len(static.tools_used) > 0:
                        report["recommendations"].append(
                            f"Static analysis tools used: {', '.join(static.tools_used[:5])}"
                        )
                else:
                    report["recommendations"].append(
                        "Static analysis failed - consider manual code review"
                    )

            # Add error analysis recommendations (Phase 4)
            if self.evaluation_state.error_analysis:
                error_analysis = self.evaluation_state.error_analysis
                if error_analysis.analysis_success:
                    if error_analysis.error_reports_generated > 0:
                        report["recommendations"].append(
                            f"Error analysis generated {error_analysis.error_reports_generated} detailed reports"
                        )

                    if error_analysis.suspect_files_identified > 0:
                        report["recommendations"].append(
                            f"Identified {error_analysis.suspect_files_identified} suspect files for targeted remediation"
                        )

                    if error_analysis.critical_errors_found > 0:
                        report["recommendations"].append(
                            f"URGENT: Address {error_analysis.critical_errors_found} critical runtime errors"
                        )

                    if error_analysis.high_confidence_fixes > 0:
                        report["recommendations"].append(
                            f"High confidence: {error_analysis.high_confidence_fixes} files have clear remediation paths"
                        )

                    if error_analysis.most_problematic_files:
                        report["recommendations"].append(
                            f"Focus on error-prone files: {', '.join(error_analysis.most_problematic_files[:3])}"
                        )

                    if error_analysis.error_types_found:
                        report["recommendations"].append(
                            f"Error types to address: {', '.join(error_analysis.error_types_found[:3])}"
                        )

                    if error_analysis.remediation_success_rate > 80:
                        report["recommendations"].append(
                            f"Excellent remediation prospects: {error_analysis.remediation_success_rate:.1f}% success rate"
                        )
                    elif error_analysis.remediation_success_rate > 60:
                        report["recommendations"].append(
                            f"Good remediation prospects: {error_analysis.remediation_success_rate:.1f}% success rate"
                        )
                    else:
                        report["recommendations"].append(
                            f"Challenging remediation ahead: {error_analysis.remediation_success_rate:.1f}% success rate - consider architectural review"
                        )

                    if error_analysis.sandbox_executions_completed > 0:
                        report["recommendations"].append(
                            f"Sandbox interface ready: {error_analysis.sandbox_executions_completed} execution(s) completed (TODO: Implement actual sandbox)"
                        )

                else:
                    report["recommendations"].append(
                        "Error analysis failed - consider manual error identification and remediation"
                    )

            # Save report
            repo_parent_dir = os.path.dirname(self.evaluation_state.repo_path)
            report_path = os.path.join(
                repo_parent_dir,
                "evaluation_report_multi_file_static_error_analysis.json",
            )
            safe_report = self._make_json_safe(report)
            with open(report_path, "w", encoding="utf-8") as f:
                json.dump(safe_report, f, indent=2, ensure_ascii=False)

            self.logger.info(
                f"📊 Multi-file evaluation report with static and error analysis saved to: {report_path}"
            )

            return safe_report

        except Exception as e:
            self.logger.error(f"Failed to generate final report: {e}")
            raise

    async def _cleanup_agents(self):
        """Clean up agent resources"""
        self.logger.info("🧹 Starting agent cleanup process...")

        agents = [
            ("Orchestrator", self.orchestrator),
            ("Code Analyzer", self.code_analyzer),
            ("Code Revise", self.code_revise),
        ]

        for agent_name, agent in agents:
            if agent:
                try:
                    self.logger.info(f"🧹 Cleaning up {agent_name} agent...")
                    await agent.__aexit__(None, None, None)
                    self.logger.info(f"✅ {agent_name} agent cleaned up successfully")
                except Exception as e:
                    self.logger.warning(f"❌ Error cleaning up {agent_name} agent: {e}")

        # Clear agent references
        self.orchestrator = None
        self.code_analyzer = None
        self.code_revise = None

        # Memory agent doesn't need async cleanup
        if self.memory_agent:
            self.logger.info("🧹 Memory Agent: No cleanup needed")

        self.logger.info("✅ All agents successfully cleaned up")


# Entry point for testing
async def main(repo_path=None, docs_path=None, memory_path=None, max_files_per_batch=3):
    """
    Run the enhanced multi-file evaluation workflow

    Args:
        repo_path: Path to repository to evaluate
        docs_path: Path to reproduction documentation
        memory_path: Path to memory file for code summaries
        max_files_per_batch: Maximum files per batch for multi-file processing
    """
    workflow = CodeEvaluationWorkflow(max_files_per_batch=max_files_per_batch)

    # Use provided paths or default example paths
    if repo_path is None:
        repo_path = "/Users/lizongwei/Reasearch/DeepCode_Base/DeepCode_eval_init/deepcode_lab/papers/1/generate_code"
    if docs_path is None:
        docs_path = "/Users/lizongwei/Reasearch/DeepCode_Base/DeepCode_eval_init/deepcode_lab/papers/1/initial_plan.txt"
    if memory_path is None:
        memory_path = "/Users/lizongwei/Reasearch/DeepCode_Base/DeepCode_eval_init/deepcode_lab/papers/1/implement_code_summary.md"

    print("🚀 Starting Code Evaluation Workflow")
    print(f"📂 Repository: {repo_path}")
    print(f"📄 Documentation: {docs_path}")
    print(f"🧠 Memory Path: {memory_path}")
    print(f"📦 Max files per batch: {max_files_per_batch}")
    print("=" * 80)

    results = await workflow.run_evaluation(repo_path, docs_path, memory_path)
    print(json.dumps(results, indent=2))
    return results


def run_evaluation(repo_path, docs_path, memory_path, max_files_per_batch=3):
    """
    Synchronous wrapper for running evaluation workflow

    Args:
        repo_path: Path to repository to evaluate
        docs_path: Path to reproduction documentation
        memory_path: Path to memory file for code summaries
        max_files_per_batch: Maximum files per batch for multi-file processing

    Returns:
        Evaluation results dictionary
    """
    import asyncio

    return asyncio.run(main(repo_path, docs_path, memory_path, max_files_per_batch))


if __name__ == "__main__":
    import sys
    import asyncio

    # Parse command line arguments
    if len(sys.argv) >= 4:
        repo_path = sys.argv[1]
        docs_path = sys.argv[2]
        memory_path = sys.argv[3]
        max_files_per_batch = int(sys.argv[4]) if len(sys.argv) > 4 else 3

        print("📋 Using command line arguments:")
        print(f"   Repository: {repo_path}")
        print(f"   Documentation: {docs_path}")
        print(f"   Memory: {memory_path}")
        print(f"   Max files per batch: {max_files_per_batch}")

        asyncio.run(main(repo_path, docs_path, memory_path, max_files_per_batch))
    else:
        print(
            "📋 Usage: python code_evaluation_workflow.py <repo_path> <docs_path> <memory_path> [max_files_per_batch]"
        )
        print("📋 Or run without arguments to use default example paths")
        print()
        asyncio.run(main())
