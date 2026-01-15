"""
Intelligent Agent Orchestration Workflows for Research-to-Code Automation.

This package provides advanced AI-driven workflow orchestration capabilities
for automated research analysis and code implementation synthesis.

Note: Uses lazy imports via __getattr__ to avoid loading heavy mcp_agent
dependencies at package import time. This prevents import errors when
specific submodules are imported directly.
"""


def __getattr__(name):
    """Lazy loading of submodules to avoid import errors at package level."""
    if name in [
        "run_research_analyzer",
        "run_resource_processor",
        "run_code_analyzer",
        "github_repo_download",
        "paper_reference_analyzer",
        "execute_multi_agent_research_pipeline",
        "execute_chat_based_planning_pipeline",
        "execute_requirement_analysis_workflow",
        "paper_code_preparation",
    ]:
        from . import agent_orchestration_engine
        return getattr(agent_orchestration_engine, name)
    elif name == "CodeImplementationWorkflow":
        from .code_implementation_workflow import CodeImplementationWorkflow
        return CodeImplementationWorkflow
    elif name == "CodeImplementationWorkflowWithIndex":
        from .code_implementation_workflow_index import CodeImplementationWorkflowWithIndex
        return CodeImplementationWorkflowWithIndex
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    # Agent orchestration functions
    "run_research_analyzer",
    "run_resource_processor",
    "run_code_analyzer",
    "github_repo_download",
    "paper_reference_analyzer",
    "execute_multi_agent_research_pipeline",
    "execute_chat_based_planning_pipeline",
    "execute_requirement_analysis_workflow",
    "paper_code_preparation",
    # Code implementation workflows
    "CodeImplementationWorkflow",
    "CodeImplementationWorkflowWithIndex",
]
