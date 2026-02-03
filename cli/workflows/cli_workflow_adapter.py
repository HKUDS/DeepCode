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
from datetime import datetime
from typing import Any, Callable, Dict

import httpx
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

    # Maximum length for result strings to avoid building giant strings
    MAX_RESULT_LENGTH = 1000

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

    @staticmethod
    def _truncate_result(result: str, max_length: int = MAX_RESULT_LENGTH) -> str:
        """
        Truncate result string at source to avoid building giant strings.

        Args:
            result: Result string to truncate
            max_length: Maximum length (default: 1000)

        Returns:
            Truncated string with ellipsis if needed
        """
        if not result:
            return ""
        if len(result) <= max_length:
            return result
        return result[:max_length] + "..."

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

    def create_cli_progress_callback(self) -> Callable:
        """Create CLI-optimized progress callback function.

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

    async def _send_pipeline_webhook_notification(
        self,
        input_source: str,
        enable_indexing: bool,
        pipeline_mode: str,
        result: Dict[str, Any],
    ) -> None:
        """Send an optional webhook notification when a pipeline run completes.

        The webhook URL is configured via the ``PIPELINE_WEBHOOK_URL`` environment
        variable. If it is not set, this method is a no-op.
        """
        webhook_url = os.getenv("PIPELINE_WEBHOOK_URL")
        if not webhook_url:
            return

        payload: Dict[str, Any] = {
            "event": "pipeline.completed",
            "status": result.get("status", "unknown"),
            "pipeline_mode": pipeline_mode,
            "input_source": input_source,
            "enable_indexing": enable_indexing,
            "timestamp": datetime.utcnow().isoformat() + "Z",
        }
        if "error" in result:
            payload["error"] = result["error"]

        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                await client.post(webhook_url, json=payload)
        except Exception as exc:  # Webhook failures must not break the CLI flow
            if self.logger:
                self.logger.warning(
                    f"Webhook notification failed for {webhook_url}: {exc}"
                )

    async def execute_full_pipeline(
        self, input_source: str, enable_indexing: bool = False
    ) -> Dict[str, Any]:
        """Execute the complete intelligent multi-agent research orchestration pipeline.

        Updated to match UI version: default enable_indexing=False for faster processing.

        Args:
            input_source: Research input source (file path, URL, or preprocessed analysis)
            enable_indexing: Whether to enable advanced intelligence analysis (default: False)

        Returns:
            dict: Comprehensive pipeline execution result
        """
        pipeline_mode = "comprehensive" if enable_indexing else "optimized"
        response: Dict[str, Any]
        try:
            # Import the latest agent orchestration engine
            from workflows.agent_orchestration_engine import (
                execute_multi_agent_research_pipeline,
            )

            # Create CLI progress callback with mode awareness
            progress_callback = self.create_cli_progress_callback(enable_indexing)

            # Display pipeline start
            if self.cli_interface:
                self.cli_interface.print_status(
                    f"🚀 Starting {pipeline_mode} agent orchestration pipeline...",
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

            response = {
                "status": "success",
                "result": result,
                "pipeline_mode": pipeline_mode,
            }
        except Exception as e:
            error_msg = f"Pipeline execution failed: {str(e)}"
            if self.cli_interface:
                self.cli_interface.print_status(error_msg, "error")

            response = {
                "status": "error",
                "error": error_msg,
                "pipeline_mode": pipeline_mode,
            }

        # Notify external systems (if configured) that the pipeline has completed
        await self._send_pipeline_webhook_notification(
            input_source=input_source,
            enable_indexing=enable_indexing,
            pipeline_mode=pipeline_mode,
            result=response,
        )

        return response

    async def execute_chat_pipeline(self, user_input: str) -> Dict[str, Any]:
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
            error_msg = f"Chat pipeline execution failed: {str(e)}"
            if self.cli_interface:
                self.cli_interface.print_status(error_msg, "error")

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

            # Truncate repo_result at source to avoid building giant strings
            raw_result = pipeline_result.get("result", "")
            truncated_result = self._truncate_result(raw_result)

            return {
                "status": pipeline_result["status"],
                "analysis_result": "Integrated into agent orchestration pipeline",
                "download_result": "Integrated into agent orchestration pipeline",
                "repo_result": truncated_result,
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
