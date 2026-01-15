"""
CLI Workflow Adapter for Agent Orchestration Engine
CLI工作流适配器 - 智能体编排引擎

This adapter provides CLI-optimized interface to the latest agent orchestration engine,
with enhanced progress reporting, error handling, and CLI-specific optimizations.

Version: 2.1 (Updated to match UI version - Added Requirement Analysis)
Changes:
- Default enable_indexing=False for faster processing (matching UI defaults)
- Mode-aware progress callback with detailed stage mapping
- Chat pipeline now accepts enable_indexing parameter
- Improved error handling and resource management
- Enhanced progress display for different modes (fast/comprehensive/chat)
- NEW: Added requirement analysis workflow support
"""

import os
import sys
from typing import Callable, Dict, Any

# Ensure project root is in sys.path for lazy imports
_current_dir = os.path.dirname(os.path.abspath(__file__))
_project_root = os.path.dirname(os.path.dirname(_current_dir))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from mcp_agent.app import MCPApp


class CLIWorkflowAdapter:
    """
    CLI-optimized workflow adapter for the intelligent agent orchestration engine.

    This adapter provides:
    - Enhanced CLI progress reporting
    - Optimized error handling for CLI environments
    - Streamlined interface for command-line usage
    - Integration with the latest agent orchestration engine
    """

    def __init__(self, cli_interface=None):
        """
        Initialize CLI workflow adapter.

        Args:
            cli_interface: CLI interface instance for progress reporting
        """
        self.cli_interface = cli_interface
        self.app = None
        self.logger = None
        self.context = None

    async def initialize_mcp_app(self) -> Dict[str, Any]:
        """
        Initialize MCP application for CLI usage (improved version matching UI).

        Returns:
            dict: Initialization result
        """
        try:
            if self.cli_interface:
                self.cli_interface.show_spinner(
                    "🚀 Initializing Agent Orchestration Engine", 2.0
                )

            # Initialize MCP application using async context manager (matching UI pattern)
            self.app = MCPApp(name="cli_agent_orchestration")
            self.app_context = self.app.run()
            agent_app = await self.app_context.__aenter__()

            self.logger = agent_app.logger
            self.context = agent_app.context

            # Configure filesystem access
            self.context.config.mcp.servers["filesystem"].args.extend([os.getcwd()])

            if self.cli_interface:
                self.cli_interface.print_status(
                    "🧠 Agent Orchestration Engine initialized successfully", "success"
                )

            return {
                "status": "success",
                "message": "MCP application initialized successfully",
            }

        except Exception as e:
            error_msg = f"Failed to initialize MCP application: {str(e)}"
            if self.cli_interface:
                self.cli_interface.print_status(error_msg, "error")
            return {"status": "error", "message": error_msg}

    async def cleanup_mcp_app(self):
        """
        Clean up MCP application resources.
        """
        if hasattr(self, "app_context"):
            try:
                await self.app_context.__aexit__(None, None, None)
                if self.cli_interface:
                    self.cli_interface.print_status(
                        "🧹 Resources cleaned up successfully", "info"
                    )
            except Exception as e:
                if self.cli_interface:
                    self.cli_interface.print_status(
                        f"⚠️ Cleanup warning: {str(e)}", "warning"
                    )

    def create_cli_progress_callback(self, enable_indexing: bool = True) -> Callable:
        """
        Create CLI-optimized progress callback function with mode-aware stage mapping.

        This matches the UI version's detailed progress mapping logic.

        Args:
            enable_indexing: Whether indexing is enabled (affects stage mapping)

        Returns:
            Callable: Progress callback function
        """

        def progress_callback(progress: int, message: str):
            if self.cli_interface:
                # Mode-aware stage mapping (matching UI version logic)
                if enable_indexing:
                    # Full workflow mapping: Initialize -> Analyze -> Download -> Plan -> References -> Repos -> Index -> Implement
                    if progress <= 5:
                        stage = 0  # Initialize
                    elif progress <= 10:
                        stage = 1  # Analyze
                    elif progress <= 25:
                        stage = 2  # Download
                    elif progress <= 40:
                        stage = 3  # Plan
                    elif progress <= 50:
                        stage = 4  # References
                    elif progress <= 60:
                        stage = 5  # Repos
                    elif progress <= 70:
                        stage = 6  # Index
                    elif progress <= 85:
                        stage = 7  # Implement
                    else:
                        stage = 8  # Complete
                else:
                    # Fast mode mapping: Initialize -> Analyze -> Download -> Plan -> Implement
                    if progress <= 5:
                        stage = 0  # Initialize
                    elif progress <= 10:
                        stage = 1  # Analyze
                    elif progress <= 25:
                        stage = 2  # Download
                    elif progress <= 40:
                        stage = 3  # Plan
                    elif progress <= 85:
                        stage = 4  # Implement (skip References, Repos, Index)
                    else:
                        stage = 4  # Complete

                self.cli_interface.display_processing_stages(stage, enable_indexing)

                # Display status message
                self.cli_interface.print_status(message, "processing")

        return progress_callback

    async def execute_full_pipeline(
        self, input_source: str, enable_indexing: bool = False
    ) -> Dict[str, Any]:
        """
        Execute the complete intelligent multi-agent research orchestration pipeline.

        Updated to match UI version: default enable_indexing=False for faster processing.

        Args:
            input_source: Research input source (file path, URL, or preprocessed analysis)
            enable_indexing: Whether to enable advanced intelligence analysis (default: False)

        Returns:
            dict: Comprehensive pipeline execution result
        """
        try:
            # Import the latest agent orchestration engine
            from workflows.agent_orchestration_engine import (
                execute_multi_agent_research_pipeline,
            )

            # Create CLI progress callback with mode awareness
            progress_callback = self.create_cli_progress_callback(enable_indexing)

            # Display pipeline start
            if self.cli_interface:
                if enable_indexing:
                    mode_msg = "🧠 comprehensive (with indexing)"
                else:
                    mode_msg = "⚡ fast (indexing disabled)"
                self.cli_interface.print_status(
                    f"🚀 Starting {mode_msg} agent orchestration pipeline...",
                    "processing",
                )
                self.cli_interface.display_processing_stages(0, enable_indexing)

            # Execute the pipeline
            result = await execute_multi_agent_research_pipeline(
                input_source=input_source,
                logger=self.logger,
                progress_callback=progress_callback,
                enable_indexing=enable_indexing,
            )

            # Display completion
            if self.cli_interface:
                final_stage = 8 if enable_indexing else 4
                self.cli_interface.display_processing_stages(
                    final_stage, enable_indexing
                )
                self.cli_interface.print_status(
                    "🎉 Agent orchestration pipeline completed successfully!",
                    "complete",
                )

            return {
                "status": "success",
                "result": result,
                "pipeline_mode": "comprehensive" if enable_indexing else "optimized",
            }

        except Exception as e:
            error_msg = f"Pipeline execution failed: {str(e)}"
            if self.cli_interface:
                self.cli_interface.print_status(error_msg, "error")

            return {
                "status": "error",
                "error": error_msg,
                "pipeline_mode": "comprehensive" if enable_indexing else "optimized",
            }

    async def execute_requirement_analysis_workflow(
        self, user_input: str, analysis_mode: str, user_answers: Dict[str, str] = None
    ) -> Dict[str, Any]:
        """
        Execute requirement analysis workflow (NEW: matching UI version).

        This workflow helps users refine their requirements through guided questions
        and intelligent analysis before starting code implementation.

        Args:
            user_input: User's initial requirements or description
            analysis_mode: Analysis mode ("generate_questions" or "summarize_requirements")
            user_answers: Dictionary of user answers to guiding questions (for summarize mode)

        Returns:
            dict: Analysis result with questions or requirement summary
        """
        try:
            # Import the requirement analysis workflow
            from workflows.agent_orchestration_engine import (
                execute_requirement_analysis_workflow,
            )

            # Create CLI progress callback
            def analysis_progress_callback(progress: int, message: str):
                if self.cli_interface:
                    self.cli_interface.print_status(message, "processing")

            # Display workflow start
            if self.cli_interface:
                if analysis_mode == "generate_questions":
                    self.cli_interface.print_status(
                        "🤖 Generating guiding questions for your requirements...",
                        "processing",
                    )
                else:
                    self.cli_interface.print_status(
                        "📄 Analyzing and summarizing your detailed requirements...",
                        "processing",
                    )

            # Execute the requirement analysis workflow
            result = await execute_requirement_analysis_workflow(
                user_input=user_input,
                analysis_mode=analysis_mode,
                user_answers=user_answers,
                logger=self.logger,
                progress_callback=analysis_progress_callback,
            )

            # Display completion
            if self.cli_interface:
                if result["status"] == "success":
                    if analysis_mode == "generate_questions":
                        self.cli_interface.print_status(
                            "✅ Guiding questions generated successfully!", "success"
                        )
                    else:
                        self.cli_interface.print_status(
                            "✅ Requirements analysis completed successfully!",
                            "success",
                        )
                else:
                    self.cli_interface.print_status(
                        f"❌ Analysis failed: {result.get('error', 'Unknown error')}",
                        "error",
                    )

            return result

        except Exception as e:
            error_msg = f"Requirement analysis workflow failed: {str(e)}"
            if self.cli_interface:
                self.cli_interface.print_status(error_msg, "error")

            return {"status": "error", "error": error_msg}

    async def execute_chat_pipeline(
        self, user_input: str, enable_indexing: bool = False
    ) -> Dict[str, Any]:
        """
        Execute the chat-based planning and implementation pipeline.

        Updated to match UI version: accepts enable_indexing parameter.

        Args:
            user_input: User's coding requirements and description
            enable_indexing: Whether to enable indexing for enhanced code understanding (default: False)

        Returns:
            dict: Chat pipeline execution result
        """
        try:
            # Debug: Check sys.path and workflows directory
            import sys
            import os
            print(f"\n[DEBUG] sys.path: {sys.path[:5]}...")  # First 5 entries
            print(f"[DEBUG] _project_root: {_project_root}")
            workflows_path = os.path.join(_project_root, "workflows")
            print(f"[DEBUG] workflows path exists: {os.path.exists(workflows_path)}")
            agent_orch_path = os.path.join(workflows_path, "agent_orchestration_engine.py")
            print(f"[DEBUG] agent_orchestration_engine.py exists: {os.path.exists(agent_orch_path)}")

            # Try to import step by step to see where it fails
            try:
                import workflows
                print(f"[DEBUG] workflows package imported: {workflows.__file__}")
            except Exception as e1:
                print(f"[DEBUG] Failed to import workflows package: {e1}")

            try:
                from workflows import agent_orchestration_engine
                print(f"[DEBUG] agent_orchestration_engine imported: {agent_orchestration_engine.__file__}")
            except Exception as e2:
                print(f"[DEBUG] Failed to import agent_orchestration_engine: {e2}")
                import traceback
                traceback.print_exc()

            # Import the chat-based pipeline
            from workflows.agent_orchestration_engine import (
                execute_chat_based_planning_pipeline,
            )

            # Create CLI progress callback for chat mode
            def chat_progress_callback(progress: int, message: str):
                if self.cli_interface:
                    # Map progress to CLI stages for chat mode (matching UI logic)
                    if progress <= 5:
                        stage = 0  # Initialize
                    elif progress <= 30:
                        stage = 1  # Planning
                    elif progress <= 50:
                        stage = 2  # Setup
                    elif progress <= 70:
                        stage = 3  # Save Plan
                    else:
                        stage = 4  # Implement

                    self.cli_interface.display_processing_stages(stage, chat_mode=True)

                    # Display status message
                    self.cli_interface.print_status(message, "processing")

            # Display pipeline start
            if self.cli_interface:
                indexing_note = (
                    " (with indexing)" if enable_indexing else " (fast mode)"
                )
                self.cli_interface.print_status(
                    f"🚀 Starting chat-based planning pipeline{indexing_note}...",
                    "processing",
                )
                self.cli_interface.display_processing_stages(0, chat_mode=True)

            # Execute the chat pipeline with configurable indexing
            result = await execute_chat_based_planning_pipeline(
                user_input=user_input,
                logger=self.logger,
                progress_callback=chat_progress_callback,
                enable_indexing=enable_indexing,  # Pass through enable_indexing parameter
            )

            # Display completion
            if self.cli_interface:
                self.cli_interface.display_processing_stages(4, chat_mode=True)
                self.cli_interface.print_status(
                    "🎉 Chat-based planning pipeline completed successfully!",
                    "complete",
                )

            return {"status": "success", "result": result, "pipeline_mode": "chat"}

        except Exception as e:
            import traceback
            full_traceback = traceback.format_exc()
            error_msg = f"Chat pipeline execution failed: {str(e)}"
            if self.cli_interface:
                self.cli_interface.print_status(error_msg, "error")
                # Print full traceback for debugging
                print(f"\n{'='*60}\nFull traceback:\n{full_traceback}\n{'='*60}\n")

            return {"status": "error", "error": error_msg, "pipeline_mode": "chat"}

    async def process_input_with_orchestration(
        self, input_source: str, input_type: str, enable_indexing: bool = False
    ) -> Dict[str, Any]:
        """
        Process input using the intelligent agent orchestration engine.

        This is the main CLI interface to the latest agent orchestration capabilities.
        Updated to match UI version: default enable_indexing=False.

        Args:
            input_source: Input source (file path, URL, or chat input)
            input_type: Type of input ('file', 'url', or 'chat')
            enable_indexing: Whether to enable advanced intelligence analysis (default: False)

        Returns:
            dict: Processing result with status and details
        """
        pipeline_result = None

        try:
            # Initialize MCP app
            init_result = await self.initialize_mcp_app()
            if init_result["status"] != "success":
                return init_result

            # Process file:// URLs for traditional file/URL inputs
            if input_source.startswith("file://"):
                file_path = input_source[7:]
                if os.name == "nt" and file_path.startswith("/"):
                    file_path = file_path.lstrip("/")
                input_source = file_path

            # Execute appropriate pipeline based on input type
            if input_type == "chat":
                # Use chat-based planning pipeline for user requirements
                # Pass enable_indexing to chat pipeline as well
                pipeline_result = await self.execute_chat_pipeline(
                    input_source, enable_indexing=enable_indexing
                )
            else:
                # Use traditional multi-agent research pipeline for files/URLs
                pipeline_result = await self.execute_full_pipeline(
                    input_source, enable_indexing=enable_indexing
                )

            return {
                "status": pipeline_result["status"],
                "analysis_result": "Integrated into agent orchestration pipeline",
                "download_result": "Integrated into agent orchestration pipeline",
                "repo_result": pipeline_result.get("result", ""),
                "pipeline_mode": pipeline_result.get("pipeline_mode", "comprehensive"),
                "error": pipeline_result.get("error"),
            }

        except Exception as e:
            error_msg = f"Error during orchestrated processing: {str(e)}"
            if self.cli_interface:
                self.cli_interface.print_status(error_msg, "error")

            return {
                "status": "error",
                "error": error_msg,
                "analysis_result": "",
                "download_result": "",
                "repo_result": "",
                "pipeline_mode": "comprehensive" if enable_indexing else "optimized",
            }

        finally:
            # Clean up resources
            await self.cleanup_mcp_app()
