"""
Code Evaluation Workflow - Multi-Agent Collaborative Repository Validation

REFACTORED VERSION: Clean main orchestrator using separated agent modules
"""

import asyncio
import json
import logging
import os
import time
import yaml
from typing import Dict, Any, Optional, List
from dataclasses import dataclass, asdict
from enum import Enum

# MCP Agent imports
from mcp_agent.agents.agent import Agent

# Local imports
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from prompts.evaluation_prompts import (
    ORCHESTRATOR_AGENT_PROMPT,
    CODE_ANALYZER_AGENT_PROMPT,
    CODE_REVISE_AGENT_PROMPT,
)
from utils.llm_utils import get_preferred_llm_class, get_default_models

# Import Memory Agent for code summaries
from workflows.agents.memory_agent_concise_multi import ConciseMemoryAgent

# Import the specialized agents
from workflows.agents.sandbox_agent import (
    SandboxAgent,
)
from workflows.agents.analyzer_agent import (
    AnalyzerAgent,
    StaticAnalysisResult,
    ErrorAnalysisResult,
)
from workflows.agents.revision_agent import RevisionAgent, CodeRevisionResult

# Import Checkpoint Manager
from workflows.checkpoint_manager import CheckpointManager


class EvaluationPhase(Enum):
    """Evaluation workflow phases"""

    INITIALIZED = "initialized"
    ANALYZING = "analyzing"
    REVISING = "revising"
    STATIC_ANALYSIS = "static_analysis"
    ERROR_ANALYSIS = "error_analysis"
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
    static_analysis: Optional[StaticAnalysisResult] = None
    error_analysis: Optional[ErrorAnalysisResult] = None
    revision_report: Optional[Dict[str, Any]] = None
    all_files_to_implement: List[str] = None
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

    REFACTORED: Clean orchestrator using separated agent modules
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

        # Configuration for agents
        self.config = {
            "max_files_per_batch": max_files_per_batch,
            "api_config": self.api_config,
            "default_models": self.default_models,
        }

        # MCP Agent instances
        self.orchestrator = None
        self.code_analyzer_mcp = None
        self.code_revise_mcp = None

        # Specialized agent instances
        self.analyzer_agent = None
        self.revision_agent = None
        self.sandbox_agent = None
        self.memory_agent = None
        
        # Checkpoint manager (initialized when repo_path is known)
        self.checkpoint_manager = None

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
        resume_from_checkpoint: bool = True,
        force_restart: bool = False,
    ) -> Dict[str, Any]:
        """
        Run complete evaluation workflow using specialized agents with checkpoint support

        Args:
            repo_path: Path to repository to evaluate
            docs_path: Path to reproduction documentation
            memory_path: Path to memory file for code summaries
            workspace_dir: Working directory for evaluation (auto-created if None)
            resume_from_checkpoint: Whether to attempt resuming from checkpoint
            force_restart: Force restart from beginning, ignoring checkpoints

        Returns:
            Comprehensive evaluation results
        """
        try:
            # Initialize checkpoint manager
            self.checkpoint_manager = CheckpointManager(repo_path, self.logger)
            
            # Check for existing checkpoint and determine resume strategy
            resume_phase = None
            if resume_from_checkpoint and not force_restart:
                checkpoint_data = self.checkpoint_manager.load_checkpoint()
                if checkpoint_data:
                    self.evaluation_state, agent_states, metadata = checkpoint_data
                    recommendation = self.checkpoint_manager.get_resume_recommendation()
                    if recommendation:
                        resume_phase, reason = recommendation
                        self.logger.info(f"🔄 CHECKPOINT RECOVERY ENABLED")
                        self.logger.info(f"   Resuming from: {resume_phase.value}")
                        self.logger.info(f"   Reason: {reason}")
                        self.logger.info(f"   Last checkpoint: {metadata.timestamp}")
                    
                    # Update workspace_dir if needed
                    if workspace_dir is None:
                        workspace_dir = self.evaluation_state.workspace_dir
                else:
                    self.logger.info("🆕 No valid checkpoint found, starting fresh")
            
            # Initialize workspace and state (if not resumed from checkpoint)
            if not resume_phase:
                if workspace_dir is None:
                    # Place .evaluation alongside the repository directory (same level as repo_path)
                    repo_abs = os.path.abspath(repo_path)
                    parent_dir = os.path.dirname(repo_abs)
                    repo_name = os.path.basename(repo_abs)
                    workspace_dir = os.path.join(
                        parent_dir, f".{repo_name}.evaluation", f"run_{int(time.time())}"
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
            self.logger.info(
                "🚀 STARTING REFACTORED MULTI-AGENT CODE EVALUATION WORKFLOW"
            )
            self.logger.info("=" * 80)
            self.logger.info(f"📂 Repository: {repo_path}")
            self.logger.info(f"📄 Documentation: {docs_path}")
            self.logger.info(f"🧠 Memory Path: {memory_path}")
            self.logger.info(f"🎯 Workspace: {workspace_dir}")
            self.logger.info(f"📦 Max files per batch: {self.max_files_per_batch}")
            self.logger.info("=" * 80)

            # Initialize all agents
            await self._initialize_agents()

            # Execute phases based on resume point
            if not resume_phase or resume_phase == EvaluationPhase.ANALYZING:
                # PHASE 1: Analysis and Revision Report Generation (ANALYZER AGENT)
                self.logger.info("🔍 Phase 1: Repository Analysis & Revision Report Generation")
                self.checkpoint_manager.log_phase_start(EvaluationPhase.ANALYZING)
                self.evaluation_state.phase = EvaluationPhase.ANALYZING
                await self.analyzer_agent.run_analysis_and_generate_revision_reports()

                # Verify that revision reports were generated
                if not self.evaluation_state.revision_report:
                    raise Exception(
                        "Analyzer Agent failed to generate revision reports - cannot proceed to revision phase"
                    )

                self.logger.info("✅ Analysis phase completed - revision reports generated by Analyzer Agent")
                # Save checkpoint after phase 1
                self.checkpoint_manager.save_checkpoint(self.evaluation_state, EvaluationPhase.ANALYZING)

            if not resume_phase or resume_phase in [EvaluationPhase.ANALYZING, EvaluationPhase.REVISING]:
                # PHASE 2: Multi-File Code Revision Execution (REVISION AGENT)
                self.logger.info("🔧 Phase 2: Enhanced Multi-File Code Revision Execution")
                self.checkpoint_manager.log_phase_start(EvaluationPhase.REVISING)
                self.evaluation_state.phase = EvaluationPhase.REVISING

                revision_completed = (
                    await self.revision_agent.run_iterative_multi_file_revision_execution()
                )

                if not revision_completed:
                    self.logger.error("No files need to be revised")

                self.logger.info("✅ Enhanced multi-file revision phase completed")
                # Save checkpoint after phase 2
                self.checkpoint_manager.save_checkpoint(self.evaluation_state, EvaluationPhase.REVISING)

            if not resume_phase or resume_phase in [EvaluationPhase.ANALYZING, EvaluationPhase.REVISING, EvaluationPhase.STATIC_ANALYSIS]:
                # PHASE 3: Static Analysis and Automatic Fixes (ANALYZER AGENT)
                self.logger.info("🔍 Phase 3: Static Analysis and Code Quality Fixes")
                self.checkpoint_manager.log_phase_start(EvaluationPhase.STATIC_ANALYSIS)
                self.evaluation_state.phase = EvaluationPhase.STATIC_ANALYSIS

                static_analysis_completed = (
                    await self.analyzer_agent.run_static_analysis_phase()
                )

                if not static_analysis_completed:
                    self.logger.warning(
                        "⚠️ Static analysis phase failed but continuing to error analysis"
                    )

                self.logger.info("✅ Static analysis phase completed")
                # Save checkpoint after phase 3
                self.checkpoint_manager.save_checkpoint(self.evaluation_state, EvaluationPhase.STATIC_ANALYSIS)

            # SANDBOX SETUP: Create sandbox environment (if needed)
            if not hasattr(self, 'sandbox_agent') or self.sandbox_agent is None:
                self.logger.info("🏗️ Sandbox Setup: Creating isolated environment for project execution")
                sandbox_setup_completed = await self._setup_sandbox_environment()

                if not sandbox_setup_completed:
                    self.logger.warning("⚠️ Sandbox setup failed but continuing with limited Phase 4")

                self.logger.info("✅ Sandbox setup completed")

            if not resume_phase or resume_phase in [EvaluationPhase.ANALYZING, EvaluationPhase.REVISING, EvaluationPhase.STATIC_ANALYSIS, EvaluationPhase.ERROR_ANALYSIS]:
                # PHASE 4: Iterative Error Analysis and Remediation (REVISION AGENT + SANDBOX AGENT)
                self.logger.info("🔬 Phase 4: Iterative Error Analysis and Targeted Remediation with Sandbox")
                self.checkpoint_manager.log_phase_start(EvaluationPhase.ERROR_ANALYSIS)
                self.evaluation_state.phase = EvaluationPhase.ERROR_ANALYSIS

                error_analysis_completed = (
                    await self.revision_agent.run_iterative_error_analysis_phase(
                        self.sandbox_agent
                    )
                )

                if not error_analysis_completed:
                    self.logger.warning(
                        "⚠️ Iterative error analysis phase failed but continuing to final evaluation"
                    )

                self.logger.info("✅ Iterative error analysis phase completed")
                # Save checkpoint after phase 4
                self.checkpoint_manager.save_checkpoint(self.evaluation_state, EvaluationPhase.ERROR_ANALYSIS)

            # PHASE 5: Final Evaluation
            self.logger.info("📊 Phase 5: Final Evaluation")
            self.evaluation_state.phase = EvaluationPhase.COMPLETED
            results = await self._generate_final_report()

            # Save final checkpoint
            self.checkpoint_manager.save_checkpoint(self.evaluation_state, EvaluationPhase.COMPLETED)

            self.logger.info("✅ Refactored multi-agent evaluation workflow completed successfully")
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
    
    def get_checkpoint_summary(self) -> Dict[str, Any]:
        """Get summary of checkpoint status"""
        if self.checkpoint_manager:
            return self.checkpoint_manager.get_checkpoint_summary()
        return {"status": "checkpoint_manager_not_initialized"}
    
    def clear_checkpoints(self):
        """Clear all checkpoints for this repository"""
        if self.checkpoint_manager:
            self.checkpoint_manager.clear_checkpoints()
            self.logger.info("🗑️ All checkpoints cleared")
        else:
            self.logger.warning("⚠️ Checkpoint manager not initialized")

    async def _initialize_agents(self):
        """Initialize all MCP agents and specialized agents"""
        try:
            # Initialize MCP Agents first
            await self._initialize_mcp_agents()

            # Initialize Memory Agent
            await self._initialize_memory_agent()

            # Initialize Specialized Agents
            self.analyzer_agent = AnalyzerAgent(
                logger=self.logger,
                evaluation_state=self.evaluation_state,
                mcp_analyzer_agent=self.code_analyzer_mcp,
                config=self.config,
            )

            self.revision_agent = RevisionAgent(
                logger=self.logger,
                evaluation_state=self.evaluation_state,
                mcp_revision_agent=self.code_revise_mcp,
                memory_agent=self.memory_agent,
                config=self.config,
            )

            # Patch the LLM communication methods into the agents
            self.analyzer_agent._initialize_llm_client = self._initialize_llm_client
            self.analyzer_agent._call_llm_with_tools = self._call_llm_with_tools
            self.revision_agent._initialize_llm_client = self._initialize_llm_client
            self.revision_agent._call_llm_with_tools = self._call_llm_with_tools

            self.logger.info(
                "🤖 All agents initialized successfully with specialized architecture"
            )

        except Exception as e:
            self.logger.error(f"Failed to initialize agents: {e}")
            raise

    async def _initialize_mcp_agents(self):
        """Initialize MCP agents"""
        # Initialize Orchestrator Agent
        self.orchestrator = Agent(
            name="EvaluationOrchestrator",
            instruction=ORCHESTRATOR_AGENT_PROMPT,
            server_names=[
                "core-evaluation",
                "lsp-tools",
                "static-analysis",
                "error-analysis",
                "revision-tools",
                "sandbox-tools",
            ],
        )
        await self.orchestrator.__aenter__()
        await self.orchestrator.attach_llm(get_preferred_llm_class(self.config_path))

        # Initialize Code Analyzer Agent
        self.logger.info("🔬 Initializing Code Analyzer MCP Agent")
        self.code_analyzer_mcp = Agent(
            name="CodeAnalyzer",
            instruction=CODE_ANALYZER_AGENT_PROMPT,
            server_names=[
                "core-evaluation",
                "lsp-tools",
                "static-analysis",
                "error-analysis",
                "revision-tools",
                "sandbox-tools",
                "code-implementation",
                "filesystem",
            ],
        )
        await self.code_analyzer_mcp.__aenter__()
        await self.code_analyzer_mcp.attach_llm(
            get_preferred_llm_class(self.config_path)
        )

        # Initialize Code Revise Agent
        self.logger.info("⚙️ Initializing Code Revise MCP Agent")
        self.code_revise_mcp = Agent(
            name="CodeRevise",
            instruction=CODE_REVISE_AGENT_PROMPT,
            server_names=[
                "code-implementation",
                "core-evaluation",
                "lsp-tools",
                "static-analysis",
                "error-analysis",
                "revision-tools",
                "sandbox-tools",
            ],
        )
        await self.code_revise_mcp.__aenter__()
        await self.code_revise_mcp.attach_llm(get_preferred_llm_class(self.config_path))

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

    async def _setup_sandbox_environment(self) -> bool:
        """Setup sandbox environment for isolated project execution"""
        try:
            self.logger.info("🏗️ Setting up sandbox environment for project execution")

            # Initialize sandbox agent
            self.sandbox_agent = SandboxAgent(
                self.logger, self.evaluation_state.workspace_dir
            )

            # Extract project name from repo path
            repo_name = os.path.basename(self.evaluation_state.repo_path)
            if not repo_name:
                repo_name = "generate_code"

            # Create sandbox environment
            self.logger.info(f"📁 Creating sandbox for project: {repo_name}")
            sandbox_state = self.sandbox_agent.create_sandbox_environment(
                self.evaluation_state.repo_path, repo_name
            )

            # Setup environment
            self.logger.info(
                f"🔧 Setting up {sandbox_state.project_language} environment"
            )
            env_setup_success = self.sandbox_agent.setup_environment()

            if env_setup_success:
                self.logger.info("✅ Environment setup completed successfully")
            else:
                self.logger.warning(
                    "⚠️ Environment setup failed, continuing with basic setup"
                )

            # Analyze README and extract execution commands
            self.logger.info("📖 Analyzing README for execution commands")
            execution_commands = (
                self.sandbox_agent.analyze_readme_and_execution_commands()
            )

            self.logger.info("🎯 Sandbox environment ready:")
            self.logger.info(f"   📂 Sandbox path: {sandbox_state.sandbox_path}")
            self.logger.info(
                f"   🎯 Main code directory: {sandbox_state.main_code_directory}"
            )
            self.logger.info(
                f"   🔤 Project language: {sandbox_state.project_language}"
            )
            self.logger.info(f"   🏃 Execution commands: {execution_commands}")
            self.logger.info(
                f"   🔧 Environment setup: {sandbox_state.environment_setup}"
            )
            self.logger.info(
                f"   📦 Dependencies installed: {sandbox_state.dependencies_installed}"
            )

            return True

        except Exception as e:
            self.logger.error(f"❌ Sandbox setup failed: {e}")
            return False

    async def _generate_final_report(self) -> Dict[str, Any]:
        """Generate comprehensive evaluation report"""
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
                "recommendations": self._generate_recommendations(),
            }

            # Save report
            repo_parent_dir = os.path.dirname(self.evaluation_state.repo_path)
            report_path = os.path.join(
                repo_parent_dir, "evaluation_report_refactored.json"
            )
            safe_report = self._make_json_safe(report)
            with open(report_path, "w", encoding="utf-8") as f:
                json.dump(safe_report, f, indent=2, ensure_ascii=False)

            self.logger.info(f"📊 Refactored evaluation report saved to: {report_path}")

            return safe_report

        except Exception as e:
            self.logger.error(f"Failed to generate final report: {e}")
            raise

    def _generate_recommendations(self) -> List[str]:
        """Generate dynamic recommendations based on results"""
        recommendations = []

        if self.evaluation_state.code_revision:
            revision = self.evaluation_state.code_revision
            if revision.completion_rate >= 90:
                recommendations.append(
                    f"Excellent completion rate: {revision.completion_rate:.1f}% of tasks completed successfully"
                )
            elif revision.completion_rate >= 75:
                recommendations.append(
                    f"Good completion rate: {revision.completion_rate:.1f}% of tasks completed successfully"
                )
            else:
                recommendations.append(
                    f"Review failed tasks: {len(revision.tasks_failed)} tasks need attention"
                )

        if self.evaluation_state.static_analysis:
            static = self.evaluation_state.static_analysis
            if static.analysis_success and static.total_issues_found > 0:
                recommendations.append(
                    f"Static analysis found {static.total_issues_found} code quality issues"
                )

        if self.evaluation_state.error_analysis:
            error_analysis = self.evaluation_state.error_analysis
            if (
                error_analysis.analysis_success
                and error_analysis.critical_errors_found > 0
            ):
                recommendations.append(
                    f"URGENT: Address {error_analysis.critical_errors_found} critical runtime errors"
                )

        return recommendations

    def _get_current_implemented_files(self) -> List[str]:
        """Get current list of implemented files from revision state"""
        if self.evaluation_state.code_revision:
            return (
                self.evaluation_state.code_revision.files_created
                + self.evaluation_state.code_revision.files_modified
            )
        return []

    def _make_json_safe(self, obj) -> Any:
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

                # Test connection
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

    async def _cleanup_agents(self):
        """Clean up agent resources"""
        self.logger.info("🧹 Starting agent cleanup process...")

        agents = [
            ("Orchestrator", self.orchestrator),
            ("Code Analyzer MCP", self.code_analyzer_mcp),
            ("Code Revise MCP", self.code_revise_mcp),
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
        self.code_analyzer_mcp = None
        self.code_revise_mcp = None
        self.analyzer_agent = None
        self.revision_agent = None

        # Other agents don't need async cleanup
        if self.memory_agent:
            self.logger.info("🧹 Memory Agent: No cleanup needed")

        if self.sandbox_agent:
            self.logger.info("🧹 Sandbox Agent: No cleanup needed")

        self.logger.info("✅ All agents successfully cleaned up")


# Entry point for testing
async def main(repo_path=None, docs_path=None, memory_path=None, max_files_per_batch=3, resume_from_checkpoint=True, force_restart=False, show_checkpoint_status=False, clear_checkpoints=False):
    """
    Run the refactored multi-agent evaluation workflow with checkpoint support

    Args:
        repo_path: Path to repository to evaluate
        docs_path: Path to reproduction documentation
        memory_path: Path to memory file for code summaries
        max_files_per_batch: Maximum files per batch for multi-file processing
        resume_from_checkpoint: Whether to attempt resuming from checkpoint
        force_restart: Force restart from beginning, ignoring checkpoints
        show_checkpoint_status: Show checkpoint status and exit
        clear_checkpoints: Clear all checkpoints and exit
    """
    workflow = CodeEvaluationWorkflow(max_files_per_batch=max_files_per_batch)

    # Use provided paths or default example paths
    if repo_path is None:
        repo_path = "d:/Project_codes/DeepCode_docker/deepcode_lab/papers/4/generate_code"
    if docs_path is None:
        docs_path = "d:/Project_codes/DeepCode_docker/deepcode_lab/papers/4/initial_plan.txt"
    if memory_path is None:
        memory_path = "d:/Project_codes/DeepCode_docker/deepcode_lab/papers/4/generate_code/implement_code_summary.md"

    # Initialize checkpoint manager for utility functions
    from workflows.checkpoint_manager import CheckpointManager
    checkpoint_manager = CheckpointManager(repo_path)

    # Handle checkpoint utility commands
    if clear_checkpoints:
        checkpoint_manager.clear_checkpoints()
        print("✅ Checkpoints cleared successfully")
        return {"status": "checkpoints_cleared"}

    if show_checkpoint_status:
        summary = checkpoint_manager.get_checkpoint_summary()
        print("📊 Checkpoint Status:")
        print(json.dumps(summary, indent=2))
        return summary

    print("🚀 Starting Refactored Code Evaluation Workflow")
    print(f"📂 Repository: {repo_path}")
    print(f"📄 Documentation: {docs_path}")
    print(f"🧠 Memory Path: {memory_path}")
    print(f"📦 Max files per batch: {max_files_per_batch}")
    print(f"🔄 Resume from checkpoint: {resume_from_checkpoint}")
    print(f"🔄 Force restart: {force_restart}")
    print("=" * 80)

    results = await workflow.run_evaluation(
        repo_path, docs_path, memory_path, 
        resume_from_checkpoint=resume_from_checkpoint, 
        force_restart=force_restart
    )
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
            "📋 Usage: python code_evaluation_workflow_refactored.py <repo_path> <docs_path> <memory_path> [max_files_per_batch]"
        )
        print("📋 Or run without arguments to use default example paths")
        print()
        asyncio.run(main())
