"""
CLI-specific Workflow Adapters
CLI专用工作流适配器

This module provides CLI-optimized versions of workflow components that are
specifically adapted for command-line interface usage patterns.
"""

from .cli_workflow_adapter import CLIWorkflowAdapter

__all__ = ["CLIWorkflowAdapter"]
