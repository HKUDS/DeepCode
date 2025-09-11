"""
Agents Package for Code Implementation and Evaluation Workflows

This package contains specialized agents for different aspects of code implementation and evaluation:
- CodeImplementationAgent: Handles file-by-file code generation
- ConciseMemoryAgent: Manages memory optimization and consistency across phases
- SandboxAgent: Handles isolated project execution and testing
- AnalyzerAgent: Performs comprehensive repository analysis and error detection
- RevisionAgent: Manages multi-file batch code revision and implementation
"""

from .code_implementation_agent import CodeImplementationAgent
from .memory_agent_concise import ConciseMemoryAgent as MemoryAgent
from .sandbox_agent import SandboxAgent, SandboxExecutionResult, SandboxState
from .analyzer_agent import AnalyzerAgent, StaticAnalysisResult, ErrorAnalysisResult
from .revision_agent import RevisionAgent, CodeRevisionResult

__all__ = [
    "CodeImplementationAgent",
    "MemoryAgent",
    "SandboxAgent",
    "SandboxExecutionResult",
    "SandboxState",
    "AnalyzerAgent",
    "StaticAnalysisResult",
    "ErrorAnalysisResult",
    "RevisionAgent",
    "CodeRevisionResult",
]
