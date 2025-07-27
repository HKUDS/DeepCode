"""
Intelligent Agent Orchestration Workflows for Research-to-Code Automation.

This package provides advanced AI-driven workflow orchestration capabilities
for automated research analysis and code implementation synthesis.
"""

from .agent_orchestration_engine import (
    run_research_analyzer,
    run_resource_processor,
    run_code_analyzer,
    github_repo_download,
    paper_reference_analyzer,
    execute_multi_agent_research_pipeline,
    paper_code_preparation,  # Deprecated, for backward compatibility
)

from .code_implementation_workflow import CodeImplementationWorkflow

__all__ = [
    # Initial workflows
    "run_research_analyzer",
    "run_resource_processor",
    "run_code_analyzer",
    "github_repo_download",
    "paper_reference_analyzer",
    "execute_multi_agent_research_pipeline",  # Main multi-agent pipeline function
    "paper_code_preparation",  # Deprecated, for backward compatibility
    # Code implementation workflows
    "CodeImplementationWorkflow",
]
