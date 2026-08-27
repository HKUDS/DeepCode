"""
Intelligent Agent Orchestration Workflows for Research-to-Code Automation.

This package provides advanced AI-driven workflow orchestration capabilities
for automated research analysis and code implementation synthesis.
"""

from .agent_orchestration_engine import (
    acquire_input_artifact,
    execute_multi_agent_research_pipeline,
    github_repo_download,
    paper_code_preparation,  # Deprecated, for backward compatibility
    paper_reference_analyzer,
    run_code_analyzer,
)
from .code_implementation_workflow import CodeImplementationWorkflow

__all__ = [
    # Initial workflows
    "acquire_input_artifact",
    "run_code_analyzer",
    "github_repo_download",
    "paper_reference_analyzer",
    "execute_multi_agent_research_pipeline",  # Main multi-agent pipeline function
    "paper_code_preparation",  # Deprecated, for backward compatibility
    # Code implementation workflows
    "CodeImplementationWorkflow",
]
