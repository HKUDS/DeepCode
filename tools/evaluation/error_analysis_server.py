#!/usr/bin/env python3
"""
Error Analysis MCP Server

This module provides error analysis and remediation tools for code debugging.
Contains tools for parsing tracebacks, analyzing errors, and generating targeted fixes.
"""

import os
import json
import subprocess
import re
import ast
import time
import logging
import networkx as nx
from typing import Dict, Any, List, Optional, Tuple
from dataclasses import dataclass, asdict
from collections import defaultdict

# Import MCP modules
from mcp.server.fastmcp import FastMCP

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Create FastMCP server instance
mcp = FastMCP("error-analysis")


@dataclass
class ErrorLocation:
    """Individual error location from traceback"""
    file_path: str
    function_name: str
    line_number: int
    code_line: str = ""
    confidence: float = 1.0  # How confident we are this is relevant

@dataclass
class TracebackAnalysis:
    """Parsed traceback information"""
    error_type: str
    error_message: str
    error_locations: List[ErrorLocation]
    root_cause_file: Optional[str] = None
    exception_chain: List[str] = None
    
    def __post_init__(self):
        if self.exception_chain is None:
            self.exception_chain = []

@dataclass
class ImportRelationship:
    """Import relationship between files"""
    importer: str
    imported: str
    import_type: str  # "direct", "from", "as"
    symbol: Optional[str] = None
    line_number: Optional[int] = None

@dataclass
class LSPSymbolInfo:
    """LSP symbol information"""
    name: str
    kind: str  # function, class, variable, module
    file_path: str
    line: int
    column: int
    references: List[Tuple[str, int, int]] = None  # (file, line, col)
    definitions: List[Tuple[str, int, int]] = None
    
    def __post_init__(self):
        if self.references is None:
            self.references = []
        if self.definitions is None:
            self.definitions = []

@dataclass
class SuspectFile:
    """Suspect file for error remediation"""
    file_path: str
    confidence_score: float
    reasons: List[str]
    error_context: List[ErrorLocation]
    suggested_focus_areas: List[str]
    related_symbols: List[LSPSymbolInfo] = None
    
    def __post_init__(self):
        if self.related_symbols is None:
            self.related_symbols = []

@dataclass
class ErrorAnalysisReport:
    """Comprehensive error analysis report"""
    traceback_analysis: TracebackAnalysis
    suspect_files: List[SuspectFile]
    import_graph: Dict[str, List[str]]
    call_chain_analysis: Dict[str, List[str]]
    remediation_suggestions: List[str]
    execution_context: Optional[Dict[str, Any]] = None

@dataclass
class SandboxResult:
    """Result from sandbox execution (Phase 4+)"""
    success: bool
    stdout: str
    stderr: str
    exit_code: int
    execution_time: float
    error_traceback: Optional[str] = None
    resource_usage: Optional[Dict[str, Any]] = None


def parse_python_traceback(traceback_text: str, repo_path: str) -> TracebackAnalysis:
    """
    Parse Python traceback to extract error information and file locations
    
    Args:
        traceback_text: Raw traceback text from stderr
        repo_path: Repository path to filter relevant files
        
    Returns:
        TracebackAnalysis with parsed information
    """
    try:
        lines = traceback_text.strip().split('\n')
        error_locations = []
        error_type = ""
        error_message = ""
        exception_chain = []
        
        repo_path = os.path.abspath(repo_path)
        
        i = 0
        while i < len(lines):
            line = lines[i].strip()
            
            # Look for file references: 'File "/path/to/file.py", line 123, in function_name'
            if line.startswith('File "') and ', line ' in line and ', in ' in line:
                # Extract file path
                file_match = re.match(r'File "([^"]+)", line (\d+), in (.+)', line)
                if file_match:
                    file_path = file_match.group(1)
                    line_num = int(file_match.group(2))
                    func_name = file_match.group(3)
                    
                    # Get the code line if available (next line)
                    code_line = ""
                    if i + 1 < len(lines):
                        next_line = lines[i + 1].strip()
                        if not next_line.startswith('File "') and not next_line.startswith('Traceback'):
                            code_line = next_line
                    
                    # Calculate confidence based on whether file is in repo
                    confidence = 1.0 if file_path.startswith(repo_path) else 0.3
                    
                    error_locations.append(ErrorLocation(
                        file_path=file_path,
                        function_name=func_name,
                        line_number=line_num,
                        code_line=code_line,
                        confidence=confidence
                    ))
            
            # Look for exception type and message (usually last line)
            elif ':' in line and not line.startswith('File ') and not line.startswith('Traceback'):
                if error_type == "":  # First exception found (deepest)
                    parts = line.split(':', 1)
                    if len(parts) == 2:
                        error_type = parts[0].strip()
                        error_message = parts[1].strip()
                exception_chain.append(line)
            
            i += 1
        
        # Determine root cause file (last file in repo with highest confidence)
        root_cause_file = None
        for loc in reversed(error_locations):
            if loc.confidence >= 0.8:
                root_cause_file = loc.file_path
                break
        
        return TracebackAnalysis(
            error_type=error_type,
            error_message=error_message,
            error_locations=error_locations,
            root_cause_file=root_cause_file,
            exception_chain=exception_chain
        )
        
    except Exception as e:
        logger.error(f"Traceback parsing failed: {e}")
        return TracebackAnalysis(
            error_type="ParseError",
            error_message=f"Failed to parse traceback: {str(e)}",
            error_locations=[],
            root_cause_file=None,
            exception_chain=[]
        )


def build_import_graph(repo_path: str, language: str = "python") -> Dict[str, List[ImportRelationship]]:
    """
    Build import graph for the repository
    
    Args:
        repo_path: Repository path
        language: Programming language (currently supports "python")
        
    Returns:
        Dictionary mapping file paths to their import relationships
    """
    import_graph = {}
    
    try:
        if language.lower() == "python":
            # Find all Python files
            python_files = []
            for root, dirs, files in os.walk(repo_path):
                # Skip common non-source directories
                dirs[:] = [d for d in dirs if d not in {'.git', '__pycache__', '.pytest_cache', 'node_modules'}]
                for file in files:
                    if file.endswith('.py'):
                        python_files.append(os.path.join(root, file))
            
            # Parse imports for each file
            for file_path in python_files:
                try:
                    with open(file_path, 'r', encoding='utf-8') as f:
                        tree = ast.parse(f.read())
                    
                    imports = []
                    for node in ast.walk(tree):
                        if isinstance(node, ast.Import):
                            for alias in node.names:
                                imports.append(ImportRelationship(
                                    importer=file_path,
                                    imported=alias.name,
                                    import_type="direct",
                                    symbol=alias.asname,
                                    line_number=node.lineno
                                ))
                        elif isinstance(node, ast.ImportFrom):
                            module = node.module or ""
                            for alias in node.names:
                                imports.append(ImportRelationship(
                                    importer=file_path,
                                    imported=module,
                                    import_type="from",
                                    symbol=alias.name,
                                    line_number=node.lineno
                                ))
                    
                    import_graph[file_path] = imports
                    
                except Exception as e:
                    logger.warning(f"Failed to parse imports for {file_path}: {e}")
                    import_graph[file_path] = []
        
        return import_graph
        
    except Exception as e:
        logger.error(f"Import graph building failed: {e}")
        return {}


def identify_suspect_files(
    traceback_analysis: TracebackAnalysis,
    import_graph: Dict[str, List[ImportRelationship]],
    repo_path: str
) -> List[SuspectFile]:
    """
    Identify suspect files for error remediation based on error analysis
    
    Args:
        traceback_analysis: Parsed traceback information
        import_graph: Import relationships between files
        repo_path: Repository path
        
    Returns:
        List of suspect files with confidence scores and remediation context
    """
    suspect_files = []
    file_scores = defaultdict(float)
    file_reasons = defaultdict(list)
    file_contexts = defaultdict(list)
    
    try:
        # 1. Direct files from traceback (highest priority)
        for location in traceback_analysis.error_locations:
            if location.file_path.startswith(repo_path):
                file_scores[location.file_path] += location.confidence * 10
                file_reasons[location.file_path].append(f"Direct error location: {location.function_name} line {location.line_number}")
                file_contexts[location.file_path].append(location)
        
        # 2. Files that import error-causing files
        error_files = {loc.file_path for loc in traceback_analysis.error_locations 
                      if loc.file_path.startswith(repo_path)}
        
        for file_path, imports in import_graph.items():
            for import_rel in imports:
                # Check if this file imports any error-causing modules
                for error_file in error_files:
                    if (import_rel.imported in error_file or 
                        any(part in import_rel.imported for part in error_file.split('/')[-1].split('.'))):
                        file_scores[file_path] += 3.0
                        file_reasons[file_path].append(f"Imports error-related module: {import_rel.imported}")
        
        # 3. Files imported by error-causing files (dependencies)
        for error_file in error_files:
            if error_file in import_graph:
                for import_rel in import_graph[error_file]:
                    # Try to resolve import to actual file
                    potential_files = []
                    if import_rel.import_type == "from":
                        # Look for files matching the import pattern
                        for file_path in import_graph.keys():
                            if import_rel.imported in file_path:
                                potential_files.append(file_path)
                    
                    for potential_file in potential_files:
                        file_scores[potential_file] += 2.0
                        file_reasons[potential_file].append(f"Imported by error file: {error_file}")
        
        # 4. Create SuspectFile objects
        for file_path, score in file_scores.items():
            if score > 0.5:  # Threshold for inclusion
                # Normalize score to 0-1 range
                normalized_score = min(score / 20.0, 1.0)
                
                # Generate focus areas based on error context
                focus_areas = []
                if file_contexts[file_path]:
                    for context in file_contexts[file_path]:
                        focus_areas.append(f"Function: {context.function_name} around line {context.line_number}")
                
                if not focus_areas:
                    focus_areas = ["Review import statements and function definitions"]
                
                suspect_files.append(SuspectFile(
                    file_path=file_path,
                    confidence_score=normalized_score,
                    reasons=file_reasons[file_path],
                    error_context=file_contexts[file_path],
                    suggested_focus_areas=focus_areas
                ))
        
        # Sort by confidence score (highest first)
        suspect_files.sort(key=lambda x: x.confidence_score, reverse=True)
        
        return suspect_files
        
    except Exception as e:
        logger.error(f"Suspect file identification failed: {e}")
        return []


@mcp.tool()
async def parse_error_traceback(traceback_text: str, repo_path: str) -> str:
    """
    Parse error traceback to extract structured error information
    
    Args:
        traceback_text: Raw traceback/error text from execution
        repo_path: Repository path for context
        
    Returns:
        JSON string with parsed traceback analysis
    """
    try:
        analysis = parse_python_traceback(traceback_text, repo_path)
        
        result = {
            "status": "success",
            "analysis": {
                "error_type": analysis.error_type,
                "error_message": analysis.error_message,
                "root_cause_file": analysis.root_cause_file,
                "error_locations": [
                    {
                        "file_path": loc.file_path,
                        "function_name": loc.function_name,
                        "line_number": loc.line_number,
                        "code_line": loc.code_line,
                        "confidence": loc.confidence
                    }
                    for loc in analysis.error_locations
                ],
                "exception_chain": analysis.exception_chain
            },
            "summary": {
                "total_locations": len(analysis.error_locations),
                "repo_files_involved": len([loc for loc in analysis.error_locations 
                                          if loc.file_path.startswith(repo_path)]),
                "highest_confidence_file": analysis.root_cause_file
            }
        }
        
        logger.info(f"Parsed traceback: {analysis.error_type} with {len(analysis.error_locations)} locations")
        
        return json.dumps(result, indent=2, ensure_ascii=False)
        
    except Exception as e:
        logger.error(f"Traceback parsing failed: {e}")
        return json.dumps({
            "status": "error",
            "message": f"Traceback parsing failed: {str(e)}"
        })


@mcp.tool()
async def analyze_import_dependencies(repo_path: str, target_file: Optional[str] = None) -> str:
    """
    Analyze import dependencies and build import graph
    
    Args:
        repo_path: Repository path
        target_file: Optional specific file to analyze (if None, analyzes all files)
        
    Returns:
        JSON string with import graph and dependency analysis
    """
    try:
        import_graph = build_import_graph(repo_path, "python")
        
        # Build networkx graph for analysis
        G = nx.DiGraph()
        
        # Add edges for imports
        for importer, imports in import_graph.items():
            for import_rel in imports:
                G.add_edge(importer, import_rel.imported)
        
        # Calculate metrics
        all_files = list(import_graph.keys())
        
        if target_file:
            # Focus on specific file
            target_imports = import_graph.get(target_file, [])
            target_dependents = [f for f, imports in import_graph.items() 
                               if any(imp.imported in target_file for imp in imports)]
            
            result = {
                "status": "success",
                "target_file": target_file,
                "direct_imports": [
                    {
                        "imported": imp.imported,
                        "import_type": imp.import_type,
                        "symbol": imp.symbol,
                        "line_number": imp.line_number
                    }
                    for imp in target_imports
                ],
                "dependent_files": target_dependents,
                "impact_analysis": {
                    "files_depending_on_target": len(target_dependents),
                    "files_imported_by_target": len(target_imports),
                    "potential_impact_radius": len(target_dependents) + len(target_imports)
                }
            }
        else:
            # Overall repository analysis
            total_imports = sum(len(imports) for imports in import_graph.values())
            
            # Find most connected files
            import_counts = {f: len(imports) for f, imports in import_graph.items()}
            dependent_counts = defaultdict(int)
            for f, imports in import_graph.items():
                for imp in imports:
                    dependent_counts[imp.imported] += 1
            
            most_importing = sorted(import_counts.items(), key=lambda x: x[1], reverse=True)[:5]
            most_depended_on = sorted(dependent_counts.items(), key=lambda x: x[1], reverse=True)[:5]
            
            result = {
                "status": "success",
                "repository_analysis": {
                    "total_files": len(all_files),
                    "total_import_relationships": total_imports,
                    "most_importing_files": [{"file": f, "import_count": c} for f, c in most_importing],
                    "most_depended_on_modules": [{"module": m, "dependent_count": c} for m, c in most_depended_on],
                },
                "import_graph": {
                    file_path: [
                        {
                            "imported": imp.imported,
                            "import_type": imp.import_type,
                            "symbol": imp.symbol,
                            "line_number": imp.line_number
                        }
                        for imp in imports
                    ]
                    for file_path, imports in import_graph.items()
                }
            }
        
        logger.info(f"Import analysis completed: {len(all_files)} files, {sum(len(imports) for imports in import_graph.values())} imports")
        
        return json.dumps(result, indent=2, ensure_ascii=False)
        
    except Exception as e:
        logger.error(f"Import dependency analysis failed: {e}")
        return json.dumps({
            "status": "error",
            "message": f"Import dependency analysis failed: {str(e)}"
        })


@mcp.tool()
async def generate_error_analysis_report(
    traceback_text: str, 
    repo_path: str, 
    execution_context: Optional[str] = None
) -> str:
    """
    Generate comprehensive error analysis report with suspect files and remediation suggestions
    
    Args:
        traceback_text: Raw error traceback from execution
        repo_path: Repository path
        execution_context: Optional context about what was being executed
        
    Returns:
        JSON string with comprehensive error analysis and remediation plan
    """
    try:
        # 1. Parse traceback
        traceback_analysis = parse_python_traceback(traceback_text, repo_path)
        
        # 2. Build import graph
        import_graph = build_import_graph(repo_path, "python")
        
        # 3. Identify suspect files
        suspect_files = identify_suspect_files(traceback_analysis, import_graph, repo_path)
        
        # 4. Generate remediation suggestions
        remediation_suggestions = []
        
        if traceback_analysis.root_cause_file:
            remediation_suggestions.append(f"Start by examining the root cause file: {traceback_analysis.root_cause_file}")
        
        if suspect_files:
            top_suspect = suspect_files[0]
            remediation_suggestions.append(f"High priority: Review {top_suspect.file_path} (confidence: {top_suspect.confidence_score:.2f})")
            
            for reason in top_suspect.reasons[:2]:  # Top 2 reasons
                remediation_suggestions.append(f"Focus area: {reason}")
        
        if traceback_analysis.error_type:
            if "ImportError" in traceback_analysis.error_type or "ModuleNotFoundError" in traceback_analysis.error_type:
                remediation_suggestions.append("Check import statements and module availability")
            elif "AttributeError" in traceback_analysis.error_type:
                remediation_suggestions.append("Verify object attributes and method availability")
            elif "TypeError" in traceback_analysis.error_type:
                remediation_suggestions.append("Check function signatures and argument types")
            elif "NameError" in traceback_analysis.error_type:
                remediation_suggestions.append("Check variable definitions and scope")
        
        # 5. Build call chain analysis (simplified)
        call_chain = {}
        for location in traceback_analysis.error_locations:
            if location.file_path not in call_chain:
                call_chain[location.file_path] = []
            call_chain[location.file_path].append(location.function_name)
        
        # 6. Create comprehensive report  
        result = {
            "status": "success",
            "lsp_enhanced": False,
            "fallback_method": "AST-based",
            "error_analysis_report": {
                "traceback_analysis": {
                    "error_type": traceback_analysis.error_type,
                    "error_message": traceback_analysis.error_message,
                    "root_cause_file": traceback_analysis.root_cause_file,
                    "error_locations_count": len(traceback_analysis.error_locations),
                    "repo_files_involved": len([loc for loc in traceback_analysis.error_locations 
                                              if loc.file_path.startswith(repo_path)])
                },
                "suspect_files": [
                    {
                        "file_path": sf.file_path,
                        "confidence_score": sf.confidence_score,
                        "reasons": sf.reasons,
                        "suggested_focus_areas": sf.suggested_focus_areas,
                        "error_context": [
                            {
                                "function_name": ctx.function_name,
                                "line_number": ctx.line_number,
                                "code_line": ctx.code_line
                            }
                            for ctx in sf.error_context
                        ]
                    }
                    for sf in suspect_files[:10]  # Top 10 suspects
                ],
                "call_chain_analysis": call_chain,
                "remediation_suggestions": remediation_suggestions,
                "execution_context": execution_context
            },
            "summary": {
                "total_suspect_files": len(suspect_files),
                "high_confidence_suspects": len([sf for sf in suspect_files if sf.confidence_score > 0.7]),
                "error_classification": traceback_analysis.error_type,
                "remediation_priority": "high" if suspect_files and suspect_files[0].confidence_score > 0.8 else "medium"
            }
        }
        
        logger.info(f"Generated error analysis report: {len(suspect_files)} suspect files identified")
        
        return json.dumps(result, indent=2, ensure_ascii=False)
        
    except Exception as e:
        logger.error(f"Error analysis report generation failed: {e}")
        return json.dumps({
            "status": "error",
            "message": f"Error analysis report generation failed: {str(e)}"
        })


@mcp.tool()
async def generate_precise_code_fixes(
    error_analysis_report: str,
    target_files: Optional[List[str]] = None,
    fix_strategy: str = "targeted"
) -> str:
    """
    Generate precise code fixes based on error analysis report
    
    Args:
        error_analysis_report: JSON string containing error analysis results
        target_files: Optional list of specific files to target
        fix_strategy: Strategy for fixes ("targeted", "comprehensive", "conservative")
        
    Returns:
        JSON string with generated code fixes in structured format
    """
    try:
        # Parse the error analysis report
        report_data = json.loads(error_analysis_report) if isinstance(error_analysis_report, str) else error_analysis_report
        
        if report_data.get("status") != "success":
            return json.dumps({
                "status": "error",
                "message": "Invalid error analysis report provided"
            })
        
        error_report = report_data.get("error_analysis_report", {})
        suspect_files = error_report.get("suspect_files", [])
        traceback_analysis = error_report.get("traceback_analysis", {})
        
        # Filter suspect files if target_files specified
        if target_files:
            suspect_files = [sf for sf in suspect_files if sf["file_path"] in target_files]
        
        generated_fixes = []
        
        for suspect_file in suspect_files:
            file_path = suspect_file["file_path"]
            confidence_score = suspect_file["confidence_score"]
            reasons = suspect_file["reasons"]
            error_context = suspect_file["error_context"]
            
            # Generate fixes based on confidence and strategy
            if confidence_score >= 0.8 or fix_strategy == "comprehensive":
                # High confidence fixes
                for context in error_context:
                    function_name = context["function_name"]
                    line_number = context["line_number"]
                    code_line = context.get("code_line", "")
                    
                    # Generate specific fix based on error type
                    error_type = traceback_analysis.get("error_type", "")
                    
                    if "ImportError" in error_type or "ModuleNotFoundError" in error_type:
                        fix = {
                            "file_path": file_path,
                            "start_line": line_number,
                            "end_line": line_number,
                            "fix_type": "import_fix",
                            "description": f"Fix import issue in {function_name}",
                            "suggested_fix": f"# TODO: Fix import - {code_line}",
                            "confidence": confidence_score,
                            "original_line": code_line
                        }
                        generated_fixes.append(fix)
                    
                    elif "AttributeError" in error_type:
                        fix = {
                            "file_path": file_path,
                            "start_line": line_number,
                            "end_line": line_number,
                            "fix_type": "attribute_fix",
                            "description": f"Fix attribute error in {function_name}",
                            "suggested_fix": f"# TODO: Fix attribute access - {code_line}",
                            "confidence": confidence_score,
                            "original_line": code_line
                        }
                        generated_fixes.append(fix)
                    
                    elif "TypeError" in error_type:
                        fix = {
                            "file_path": file_path,
                            "start_line": line_number,
                            "end_line": line_number,
                            "fix_type": "type_fix",
                            "description": f"Fix type error in {function_name}",
                            "suggested_fix": f"# TODO: Fix type mismatch - {code_line}",
                            "confidence": confidence_score,
                            "original_line": code_line
                        }
                        generated_fixes.append(fix)
            
            elif confidence_score >= 0.6 and fix_strategy != "conservative":
                # Medium confidence fixes - more generic
                fix = {
                    "file_path": file_path,
                    "start_line": error_context[0]["line_number"] if error_context else 1,
                    "end_line": error_context[0]["line_number"] if error_context else 1,
                    "fix_type": "general_review",
                    "description": f"Review file {file_path} - {', '.join(reasons[:2])}",
                    "suggested_fix": f"# TODO: Review this file - {', '.join(reasons[:1])}",
                    "confidence": confidence_score,
                    "original_line": error_context[0].get("code_line", "") if error_context else ""
                }
                generated_fixes.append(fix)
        
        # Sort fixes by confidence score (highest first)
        generated_fixes.sort(key=lambda x: x["confidence"], reverse=True)
        
        result = {
            "status": "success",
            "fix_strategy": fix_strategy,
            "total_fixes_generated": len(generated_fixes),
            "high_confidence_fixes": len([f for f in generated_fixes if f["confidence"] >= 0.8]),
            "medium_confidence_fixes": len([f for f in generated_fixes if 0.6 <= f["confidence"] < 0.8]),
            "generated_fixes": generated_fixes,
            "summary": {
                "files_targeted": len(set(f["file_path"] for f in generated_fixes)),
                "fix_types": list(set(f["fix_type"] for f in generated_fixes)),
                "average_confidence": sum(f["confidence"] for f in generated_fixes) / len(generated_fixes) if generated_fixes else 0
            }
        }
        
        logger.info(f"Generated {len(generated_fixes)} precise code fixes with strategy '{fix_strategy}'")
        
        return json.dumps(result, indent=2, ensure_ascii=False)
        
    except Exception as e:
        logger.error(f"Precise code fix generation failed: {e}")
        return json.dumps({
            "status": "error",
            "message": f"Precise code fix generation failed: {str(e)}"
        })


def generate_code_diff(original_content: str, modified_content: str, file_path: str) -> str:
    """
    Generate a unified diff between original and modified content
    
    Args:
        original_content: Original file content
        modified_content: Modified file content
        file_path: File path for diff header
        
    Returns:
        Unified diff string
    """
    import difflib
    
    try:
        original_lines = original_content.splitlines(keepends=True)
        modified_lines = modified_content.splitlines(keepends=True)
        
        diff = difflib.unified_diff(
            original_lines,
            modified_lines,
            fromfile=f"a/{file_path}",
            tofile=f"b/{file_path}",
            lineterm=""
        )
        
        return "".join(diff)
        
    except Exception as e:
        logger.error(f"Failed to generate diff for {file_path}: {e}")
        return f"Error generating diff: {str(e)}"


def apply_precise_code_change(file_path: str, change: Dict[str, Any]) -> Dict[str, Any]:
    """
    Apply a precise code change to a file
    
    Args:
        file_path: Path to the file to modify
        change: Change specification with start_line, end_line, new_content
        
    Returns:
        Result dictionary with success status and details
    """
    try:
        # Read current file content
        if not os.path.exists(file_path):
            return {
                "success": False,
                "error": f"File not found: {file_path}"
            }
        
        with open(file_path, 'r', encoding='utf-8') as f:
            lines = f.readlines()
        
        # Validate line numbers
        start_line = change.get("start_line", 1)
        end_line = change.get("end_line", start_line)
        
        if start_line < 1 or start_line > len(lines):
            return {
                "success": False,
                "error": f"Invalid start_line {start_line} for file with {len(lines)} lines"
            }
        
        if end_line < start_line or end_line > len(lines):
            return {
                "success": False,
                "error": f"Invalid end_line {end_line} for file with {len(lines)} lines"
            }
        
        # Store original content for diff
        original_content = ''.join(lines)
        
        # Apply the change
        new_content = change.get("new_content", "")
        if not new_content.endswith('\n') and new_content:
            new_content += '\n'
        
        # Replace the specified lines
        new_lines = (
            lines[:start_line-1] +  # Lines before change
            [new_content] +         # New content
            lines[end_line:]        # Lines after change
        )
        
        modified_content = ''.join(new_lines)
        
        # Generate diff
        diff = generate_code_diff(original_content, modified_content, file_path)
        
        # Write the modified content
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(modified_content)
        
        return {
            "success": True,
            "file_path": file_path,
            "lines_changed": end_line - start_line + 1,
            "diff": diff,
            "change_type": change.get("change_type", "replace"),
            "original_lines": end_line - start_line + 1,
            "new_lines": len(new_content.split('\n')) - 1 if new_content else 0
        }
        
    except Exception as e:
        logger.error(f"Failed to apply code change to {file_path}: {e}")
        return {
            "success": False,
            "error": str(e)
        }


@mcp.tool()
async def apply_code_fixes_with_diff(
    fixes_json: str,
    repo_path: str,
    dry_run: bool = False
) -> str:
    """
    Apply code fixes with diff generation and validation
    
    Args:
        fixes_json: JSON string containing fixes from generate_precise_code_fixes
        repo_path: Repository path for validation
        dry_run: If True, only generate diffs without applying changes
        
    Returns:
        JSON string with application results and diffs
    """
    try:
        # Parse fixes
        fixes_data = json.loads(fixes_json) if isinstance(fixes_json, str) else fixes_json
        
        if fixes_data.get("status") != "success":
            return json.dumps({
                "status": "error",
                "message": "Invalid fixes data provided"
            })
        
        fixes = fixes_data.get("generated_fixes", [])
        
        if not fixes:
            return json.dumps({
                "status": "success",
                "message": "No fixes to apply",
                "results": []
            })
        
        application_results = []
        successful_applications = 0
        failed_applications = 0
        
        for fix in fixes:
            file_path = fix["file_path"]
            
            # Ensure file path is within repo
            full_file_path = os.path.join(repo_path, file_path) if not os.path.isabs(file_path) else file_path
            
            if not full_file_path.startswith(repo_path):
                result = {
                    "fix": fix,
                    "success": False,
                    "error": "File path outside repository",
                    "diff": None
                }
                application_results.append(result)
                failed_applications += 1
                continue
            
            # Create change object for application
            change = {
                "start_line": fix["start_line"],
                "end_line": fix["end_line"],
                "new_content": fix["suggested_fix"],
                "change_type": "replace"
            }
            
            if dry_run:
                # Generate diff without applying
                try:
                    if os.path.exists(full_file_path):
                        with open(full_file_path, 'r', encoding='utf-8') as f:
                            original_content = f.read()
                        
                        # Simulate the change
                        lines = original_content.splitlines()
                        if fix["start_line"] <= len(lines):
                            new_lines = (
                                lines[:fix["start_line"]-1] +
                                [fix["suggested_fix"]] +
                                lines[fix["end_line"]:]
                            )
                            modified_content = '\n'.join(new_lines)
                            diff = generate_code_diff(original_content, modified_content, file_path)
                        else:
                            diff = "Error: Line number out of range"
                    else:
                        diff = "Error: File not found"
                    
                    result = {
                        "fix": fix,
                        "success": True,
                        "dry_run": True,
                        "diff": diff,
                        "file_path": full_file_path
                    }
                    application_results.append(result)
                    successful_applications += 1
                    
                except Exception as e:
                    result = {
                        "fix": fix,
                        "success": False,
                        "error": str(e),
                        "diff": None
                    }
                    application_results.append(result)
                    failed_applications += 1
            else:
                # Apply the change
                change_result = apply_precise_code_change(full_file_path, change)
                
                result = {
                    "fix": fix,
                    "success": change_result["success"],
                    "diff": change_result.get("diff"),
                    "file_path": full_file_path,
                    "lines_changed": change_result.get("lines_changed"),
                    "error": change_result.get("error")
                }
                application_results.append(result)
                
                if change_result["success"]:
                    successful_applications += 1
                else:
                    failed_applications += 1
        
        result = {
            "status": "success",
            "dry_run": dry_run,
            "total_fixes": len(fixes),
            "successful_applications": successful_applications,
            "failed_applications": failed_applications,
            "success_rate": successful_applications / len(fixes) * 100 if fixes else 0,
            "application_results": application_results,
            "summary": {
                "files_modified": len(set(r["file_path"] for r in application_results if r["success"])),
                "total_diffs_generated": len([r for r in application_results if r.get("diff")]),
                "fix_types_applied": list(set(r["fix"]["fix_type"] for r in application_results if r["success"]))
            }
        }
        
        logger.info(f"Applied {successful_applications}/{len(fixes)} code fixes (dry_run: {dry_run})")
        
        return json.dumps(result, indent=2, ensure_ascii=False)
        
    except Exception as e:
        logger.error(f"Code fix application failed: {e}")
        return json.dumps({
            "status": "error",
            "message": f"Code fix application failed: {str(e)}"
        })


# Run the server
if __name__ == "__main__":
    mcp.run()
