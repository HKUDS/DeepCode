#!/usr/bin/env python3
"""
Code Evaluation MCP Server - Simplified Version

This is a simplified version of the original code_evaluation_server.py that only contains
the tools actually used by the workflow agents.

Based on analysis of:
- workflows/agents/revision_agent.py
- workflows/agents/analyzer_agent.py  
- workflows/agents/sandbox_agent.py
- workflows/code_evaluation_workflow_refactored.py
- config/mcp_tool_definitions_index.py

Only includes the MCP tools that are actually called by the workflow.
"""

import os
import json
import sys
import subprocess
import re
import ast
import time
import tempfile
import shutil
import traceback as tb
import networkx as nx
import asyncio
import threading
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple, Set, Union
import logging
from dataclasses import dataclass, asdict
from collections import defaultdict, Counter

# Import MCP modules
from mcp.server.fastmcp import FastMCP

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Create FastMCP server instance
mcp = FastMCP("code-evaluation")

# Import all the data classes and helper functions from the modular files
# (keeping the original structure for compatibility)

@dataclass
class FileInfo:
    """Information about a single file"""
    path: str
    size: int
    lines: int
    language: str
    complexity_score: float = 0.0
    issues: List[str] = None
    
    def __post_init__(self):
        if self.issues is None:
            self.issues = []

@dataclass
class DependencyInfo:
    """Dependency information structure"""
    name: str
    version: Optional[str]
    source: str  # requirements.txt, package.json, etc.
    is_dev: bool = False
    is_optional: bool = False

@dataclass
class RepoStructureInfo:
    """Repository structure analysis results"""
    total_files: int
    total_lines: int
    languages: Dict[str, int]  # language -> file count
    directories: List[str]
    file_details: List[FileInfo]
    main_entry_points: List[str]
    test_files: List[str]
    config_files: List[str]
    documentation_files: List[str]

@dataclass
class CodeQualityAssessment:
    """Code quality assessment results"""
    overall_score: float  # 0-100
    complexity_issues: List[str]
    style_issues: List[str]
    potential_bugs: List[str]
    security_issues: List[str]
    maintainability_score: float
    test_coverage_estimate: float

@dataclass
class DocumentationAssessment:
    """Documentation quality assessment"""
    completeness_score: float  # 0-100
    has_readme: bool
    has_api_docs: bool
    has_examples: bool
    has_installation_guide: bool
    documentation_files_count: int
    missing_documentation: List[str]

# Import tools from specialized modules and re-export them
# This maintains the original API while using the modular structure

# Import all tools from the specialized modules
from evaluation.core_evaluation_server import (
    analyze_repo_structure as _analyze_repo_structure,
    detect_dependencies as _detect_dependencies, 
    assess_code_quality as _assess_code_quality,
    evaluate_documentation as _evaluate_documentation,
    check_reproduction_readiness as _check_reproduction_readiness,
    generate_evaluation_summary as _generate_evaluation_summary
)

# Re-register core evaluation tools
@mcp.tool()
async def analyze_repo_structure(repo_path: str) -> str:
    """Perform comprehensive repository structure analysis."""
    return await _analyze_repo_structure(repo_path)

@mcp.tool()
async def detect_dependencies(repo_path: str) -> str:
    """Detect and analyze project dependencies across multiple languages."""
    return await _detect_dependencies(repo_path)

@mcp.tool()
async def assess_code_quality(repo_path: str) -> str:
    """Assess code quality metrics and identify potential issues."""
    return await _assess_code_quality(repo_path)

@mcp.tool()
async def evaluate_documentation(repo_path: str, docs_path: Optional[str] = None) -> str:
    """Evaluate documentation completeness and quality."""
    return await _evaluate_documentation(repo_path, docs_path)

@mcp.tool()
async def check_reproduction_readiness(repo_path: str, docs_path: Optional[str] = None) -> str:
    """Assess repository readiness for reproduction and validation."""
    return await _check_reproduction_readiness(repo_path, docs_path)

@mcp.tool()
async def generate_evaluation_summary(repo_path: str, docs_path: Optional[str] = None) -> str:
    """Generate comprehensive evaluation summary combining all analysis results."""
    return await _generate_evaluation_summary(repo_path, docs_path)

from evaluation.lsp_tools_server import (
    setup_lsp_servers as _setup_lsp_servers,
    lsp_find_symbol_references as _lsp_find_symbol_references,
    lsp_get_diagnostics as _lsp_get_diagnostics,
    lsp_get_code_actions as _lsp_get_code_actions,
    lsp_generate_code_fixes as _lsp_generate_code_fixes,
    lsp_apply_workspace_edit as _lsp_apply_workspace_edit
)

# Re-register LSP tools
@mcp.tool()
async def setup_lsp_servers(repo_path: str, languages: Optional[List[str]] = None) -> str:
    """Set up LSP servers for the specified repository and languages."""
    return await _setup_lsp_servers(repo_path, languages)

@mcp.tool()
async def lsp_find_symbol_references(repo_path: str, symbol: str, file_path: Optional[str] = None) -> str:
    """Find all references to a symbol using LSP."""
    return await _lsp_find_symbol_references(repo_path, symbol, file_path)

@mcp.tool()
async def lsp_get_diagnostics(repo_path: str, file_path: Optional[str] = None) -> str:
    """Get diagnostic information (errors, warnings) from LSP servers."""
    return await _lsp_get_diagnostics(repo_path, file_path)

@mcp.tool()
async def lsp_get_code_actions(repo_path: str, file_path: str, start_line: int, end_line: int, error_context: Optional[str] = None) -> str:
    """Get available code actions/fixes from LSP servers."""
    return await _lsp_get_code_actions(repo_path, file_path, start_line, end_line, error_context)

@mcp.tool()
async def lsp_generate_code_fixes(repo_path: str, file_path: str, start_line: int, end_line: int, error_context: Optional[str] = None) -> str:
    """Generate code fixes using LSP and LLM integration."""
    return await _lsp_generate_code_fixes(repo_path, file_path, start_line, end_line, error_context)

@mcp.tool()
async def lsp_apply_workspace_edit(repo_path: str, workspace_edit: str) -> str:
    """Apply workspace edit using LSP servers."""
    return await _lsp_apply_workspace_edit(repo_path, workspace_edit)

from evaluation.static_analysis_server import (
    perform_static_analysis as _perform_static_analysis,
    auto_fix_formatting as _auto_fix_formatting,
    generate_static_issues_report as _generate_static_issues_report
)

# Re-register static analysis tools
@mcp.tool()
async def perform_static_analysis(repo_path: str, auto_fix: bool = False, languages: Optional[List[str]] = None) -> str:
    """Perform comprehensive static analysis on the repository."""
    return await _perform_static_analysis(repo_path, auto_fix, languages)

@mcp.tool()
async def auto_fix_formatting(repo_path: str, languages: Optional[List[str]] = None, dry_run: bool = False) -> str:
    """Auto-fix code formatting issues across multiple languages."""
    return await _auto_fix_formatting(repo_path, languages, dry_run)

@mcp.tool()
async def generate_static_issues_report(repo_path: str, severity_filter: Optional[str] = None, language_filter: Optional[str] = None) -> str:
    """Generate comprehensive static analysis issues report."""
    return await _generate_static_issues_report(repo_path, severity_filter, language_filter)

from evaluation.error_analysis_server import (
    parse_error_traceback as _parse_error_traceback,
    analyze_import_dependencies as _analyze_import_dependencies,
    generate_error_analysis_report as _generate_error_analysis_report,
    generate_precise_code_fixes as _generate_precise_code_fixes,
    apply_code_fixes_with_diff as _apply_code_fixes_with_diff
)

# Re-register error analysis tools
@mcp.tool()
async def parse_error_traceback(traceback_text: str, repo_path: str) -> str:
    """Parse Python tracebacks and extract file locations, function names, and error types."""
    return await _parse_error_traceback(traceback_text, repo_path)

@mcp.tool()
async def analyze_import_dependencies(repo_path: str, target_file: Optional[str] = None) -> str:
    """Analyze import dependencies and build dependency graph."""
    return await _analyze_import_dependencies(repo_path, target_file)

@mcp.tool()
async def generate_error_analysis_report(traceback_text: str, repo_path: str, execution_context: Optional[str] = None) -> str:
    """Generate comprehensive error analysis report with suspect files."""
    return await _generate_error_analysis_report(traceback_text, repo_path, execution_context)

@mcp.tool()
async def generate_precise_code_fixes(error_analysis_report: str, target_files: Optional[List[str]] = None, fix_strategy: str = "targeted") -> str:
    """Generate precise code fixes based on error analysis."""
    return await _generate_precise_code_fixes(error_analysis_report, target_files, fix_strategy)

@mcp.tool()
async def apply_code_fixes_with_diff(fixes_json: str, repo_path: str, dry_run: bool = False) -> str:
    """Apply code fixes with diff generation and backup."""
    return await _apply_code_fixes_with_diff(fixes_json, repo_path, dry_run)

from evaluation.revision_tools_server import (
    detect_empty_files as _detect_empty_files,
    detect_missing_files as _detect_missing_files,
    generate_code_revision_report as _generate_code_revision_report
)

# Re-register tools with this server's MCP instance
@mcp.tool()
async def detect_empty_files(repo_path: str) -> str:
    """Detect empty files in the repository that may need implementation."""
    return await _detect_empty_files(repo_path)

@mcp.tool()
async def detect_missing_files(repo_path: str) -> str:
    """Detect missing essential files based on project type and existing structure."""
    return await _detect_missing_files(repo_path)

@mcp.tool()
async def generate_code_revision_report(repo_path: str, docs_path: Optional[str] = None) -> str:
    """Generate comprehensive code revision report combining empty files, missing files, and quality analysis."""
    return await _generate_code_revision_report(repo_path, docs_path)

from evaluation.sandbox_tools_server import (
    execute_in_sandbox as _execute_in_sandbox,
    run_code_validation as _run_code_validation
)

# Re-register sandbox tools
@mcp.tool()
async def execute_in_sandbox(repo_path: str, command: str, timeout: int = 30) -> str:
    """Execute commands in a sandboxed environment."""
    return await _execute_in_sandbox(repo_path, command, timeout)

@mcp.tool()
async def run_code_validation(repo_path: str, test_command: Optional[str] = None) -> str:
    """Run code validation tests in sandbox environment."""
    return await _run_code_validation(repo_path, test_command)

# Register all imported tools with the main server
all_tools = [
    # Core evaluation tools (used by analyzer_agent.py)
    analyze_repo_structure,
    detect_dependencies,
    assess_code_quality,
    evaluate_documentation,
    check_reproduction_readiness,
    generate_evaluation_summary,
    
    # LSP tools (used by revision_agent.py and analyzer_agent.py)
    setup_lsp_servers,
    lsp_find_symbol_references,
    lsp_get_diagnostics,
    lsp_get_code_actions,
    lsp_generate_code_fixes,
    lsp_apply_workspace_edit,
    
    # Static analysis tools (used by analyzer_agent.py)
    perform_static_analysis,
    auto_fix_formatting,
    generate_static_issues_report,
    
    # Error analysis tools (used by revision_agent.py and analyzer_agent.py)
    parse_error_traceback,
    analyze_import_dependencies,
    generate_error_analysis_report,
    generate_precise_code_fixes,
    apply_code_fixes_with_diff,
    
    # Revision tools (used by analyzer_agent.py)
    detect_empty_files,
    detect_missing_files,
    generate_code_revision_report,
    
    # Sandbox tools (used by analyzer_agent.py)
    execute_in_sandbox,
    run_code_validation
]

# Note: FastMCP doesn't expose _tools attribute, so we can't register tools this way
# The tools are already registered via @mcp.tool() decorators in the imported modules

logger.info(f"🎯 Registered {len(all_tools)} tools from specialized modules")

logger.info("📋 Tool Categories:")
logger.info("   📊 Core Evaluation: 6 tools")
logger.info("   🔧 LSP Tools: 6 tools") 
logger.info("   🎨 Static Analysis: 3 tools")
logger.info("   🔍 Error Analysis: 5 tools")
logger.info("   📝 Revision Tools: 3 tools")
logger.info("   🏗️ Sandbox Tools: 2 tools")

# Run the server
if __name__ == "__main__":
    logger.info("🚀 Starting Code Evaluation MCP Server (Simplified & Refactored)")
    logger.info("=" * 60)
    mcp.run()
