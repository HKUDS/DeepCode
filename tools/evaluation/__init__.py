"""
Code Evaluation Tools Package

This package contains specialized MCP servers for code evaluation and analysis.

Modules:
- core_evaluation_server: Basic repository analysis tools
- lsp_tools_server: LSP-enhanced analysis tools  
- static_analysis_server: Code quality and formatting tools
- error_analysis_server: Error analysis and remediation tools
- revision_tools_server: Empty file detection and revision reports
- sandbox_tools_server: Sandbox execution and validation tools

Usage:
The tools can be used individually by importing specific modules,
or collectively through the main code_evaluation_server_refactored.py
"""

__version__ = "1.0.0"
__author__ = "DeepCode Evaluation System"

# Re-export commonly used classes and functions
from .core_evaluation_server import (
    FileInfo,
    DependencyInfo,
    RepoStructureInfo,
    CodeQualityAssessment,
    DocumentationAssessment
)

from .error_analysis_server import (
    ErrorLocation,
    TracebackAnalysis,
    ImportRelationship,
    SuspectFile,
    ErrorAnalysisReport
)

from .static_analysis_server import (
    StaticAnalysisIssue,
    StaticAnalysisResult,
    RepositoryStaticAnalysis
)

__all__ = [
    # Data classes
    "FileInfo",
    "DependencyInfo", 
    "RepoStructureInfo",
    "CodeQualityAssessment",
    "DocumentationAssessment",
    "ErrorLocation",
    "TracebackAnalysis",
    "ImportRelationship",
    "SuspectFile",
    "ErrorAnalysisReport",
    "StaticAnalysisIssue",
    "StaticAnalysisResult",
    "RepositoryStaticAnalysis"
]
