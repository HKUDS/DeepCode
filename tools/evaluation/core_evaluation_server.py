#!/usr/bin/env python3
"""
Core Code Evaluation MCP Server

This module provides basic repository analysis and evaluation tools.
Contains the fundamental tools for repository structure analysis and basic quality assessment.
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
mcp = FastMCP("core-evaluation")


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


# Language detection patterns
LANGUAGE_PATTERNS = {
    '.py': 'python',
    '.js': 'javascript', 
    '.ts': 'typescript',
    '.java': 'java',
    '.cpp': 'cpp',
    '.c': 'c',
    '.h': 'c',
    '.hpp': 'cpp',
    '.cs': 'csharp',
    '.go': 'go',
    '.rs': 'rust',
    '.php': 'php',
    '.rb': 'ruby',
    '.swift': 'swift',
    '.kt': 'kotlin',
    '.scala': 'scala',
    '.r': 'r',
    '.m': 'matlab',
    '.sh': 'shell',
    '.sql': 'sql',
    '.md': 'markdown',
    '.yml': 'yaml',
    '.yaml': 'yaml',
    '.json': 'json',
    '.xml': 'xml',
    '.html': 'html',
    '.css': 'css'
}

# Configuration and dependency files
CONFIG_FILES = [
    'requirements.txt', 'setup.py', 'setup.cfg', 'pyproject.toml', 'Pipfile',
    'package.json', 'package-lock.json', 'yarn.lock',
    'pom.xml', 'build.gradle', 'Cargo.toml',
    'Makefile', 'CMakeLists.txt', 'configure.ac',
    'Dockerfile', 'docker-compose.yml',
    '.gitignore', '.env', '.env.example'
]

# Documentation file patterns
DOC_PATTERNS = [
    r'README.*',
    r'INSTALL.*', 
    r'CHANGELOG.*',
    r'CONTRIBUTING.*',
    r'LICENSE.*',
    r'docs?/.*',
    r'documentation/.*',
    r'.*\.md$',
    r'.*\.rst$',
    r'.*\.txt$'
]


def get_file_language(file_path: str) -> str:
    """Determine programming language from file extension"""
    ext = Path(file_path).suffix.lower()
    return LANGUAGE_PATTERNS.get(ext, 'unknown')


def count_lines_in_file(file_path: str) -> int:
    """Count non-empty lines in a file"""
    try:
        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
            return len([line for line in f if line.strip()])
    except Exception:
        return 0


def calculate_complexity_score(file_path: str, language: str) -> float:
    """Calculate basic complexity score for a file"""
    try:
        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
            content = f.read()
            
        complexity = 0
        
        if language == 'python':
            # Count control structures, functions, classes
            complexity += len(re.findall(r'\b(if|elif|while|for|try|except|with)\b', content))
            complexity += len(re.findall(r'\bdef\s+\w+', content))
            complexity += len(re.findall(r'\bclass\s+\w+', content))
            
        elif language in ['javascript', 'typescript']:
            complexity += len(re.findall(r'\b(if|else|while|for|switch|try|catch|function)\b', content))
            complexity += len(re.findall(r'=>', content))
            
        elif language == 'java':
            complexity += len(re.findall(r'\b(if|else|while|for|switch|try|catch|public|private|protected)\b', content))
            complexity += len(re.findall(r'\bclass\s+\w+', content))
            
        # Normalize by file size
        lines = len(content.split('\n'))
        return min(complexity / max(lines, 1) * 100, 100)
        
    except Exception:
        return 0


def detect_issues_in_file(file_path: str, language: str) -> List[str]:
    """Detect potential issues in a file"""
    issues = []
    
    try:
        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
            content = f.read()
            
        # Common issues across languages
        if len(content.split('\n')) > 1000:
            issues.append("File is very large (>1000 lines)")
            
        if 'TODO' in content.upper() or 'FIXME' in content.upper():
            issues.append("Contains TODO/FIXME comments")
            
        if language == 'python':
            # Python-specific checks
            if 'import *' in content:
                issues.append("Uses wildcard imports")
            if re.search(r'except:', content):
                issues.append("Uses bare except clauses")
            if 'eval(' in content or 'exec(' in content:
                issues.append("Uses potentially dangerous eval/exec")
                
        elif language in ['javascript', 'typescript']:
            # JavaScript/TypeScript-specific checks
            if 'eval(' in content:
                issues.append("Uses dangerous eval function")
            if re.search(r'var\s+\w+', content):
                issues.append("Uses var instead of let/const")
                
    except Exception as e:
        issues.append(f"Error analyzing file: {str(e)}")
        
    return issues


def find_entry_points(repo_path: str) -> List[str]:
    """Find main entry points in the repository"""
    entry_points = []
    
    common_entry_files = [
        'main.py', '__main__.py', 'app.py', 'run.py', 'start.py',
        'index.js', 'main.js', 'app.js', 'server.js',
        'Main.java', 'Application.java',
        'main.cpp', 'main.c'
    ]
    
    for root, dirs, files in os.walk(repo_path):
        for file in files:
            file_path = os.path.join(root, file)
            rel_path = os.path.relpath(file_path, repo_path)
            
            # Check for common entry point names
            if file in common_entry_files:
                entry_points.append(rel_path)
                continue
                
            # Check for executable scripts
            if file.endswith('.py'):
                try:
                    with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                        first_line = f.readline()
                        if first_line.startswith('#!') or 'if __name__ == "__main__"' in f.read():
                            entry_points.append(rel_path)
                except Exception:
                    pass
    
    return entry_points


def find_test_files(repo_path: str) -> List[str]:
    """Find test files in the repository"""
    test_files = []
    
    test_patterns = [
        r'test_.*\.py$',
        r'.*_test\.py$', 
        r'tests?/.*\.py$',
        r'.*\.test\.js$',
        r'.*\.spec\.js$',
        r'test/.*\.js$',
        r'.*Test\.java$',
        r'.*Tests\.java$'
    ]
    
    for root, dirs, files in os.walk(repo_path):
        for file in files:
            file_path = os.path.join(root, file)
            rel_path = os.path.relpath(file_path, repo_path)
            
            for pattern in test_patterns:
                if re.match(pattern, rel_path, re.IGNORECASE):
                    test_files.append(rel_path)
                    break
    
    return test_files


def find_documentation_files(repo_path: str) -> List[str]:
    """Find documentation files in the repository"""
    doc_files = []
    
    for root, dirs, files in os.walk(repo_path):
        for file in files:
            file_path = os.path.join(root, file)
            rel_path = os.path.relpath(file_path, repo_path)
            
            for pattern in DOC_PATTERNS:
                if re.match(pattern, rel_path, re.IGNORECASE):
                    doc_files.append(rel_path)
                    break
    
    return doc_files


def parse_python_dependencies(repo_path: str) -> List[DependencyInfo]:
    """Parse Python dependencies from various files"""
    deps = []
    
    # requirements.txt
    req_file = os.path.join(repo_path, 'requirements.txt')
    if os.path.exists(req_file):
        try:
            with open(req_file, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith('#'):
                        # Parse package==version or package>=version
                        match = re.match(r'^([a-zA-Z0-9\-_]+)([><=!]+.*)?', line)
                        if match:
                            name = match.group(1)
                            version = match.group(2) if match.group(2) else None
                            deps.append(DependencyInfo(name, version, 'requirements.txt'))
        except Exception as e:
            logger.warning(f"Error parsing requirements.txt: {e}")
    
    # setup.py
    setup_file = os.path.join(repo_path, 'setup.py')
    if os.path.exists(setup_file):
        try:
            with open(setup_file, 'r', encoding='utf-8') as f:
                content = f.read()
                # Look for install_requires
                install_requires_match = re.search(r'install_requires\s*=\s*\[(.*?)\]', content, re.DOTALL)
                if install_requires_match:
                    requires_str = install_requires_match.group(1)
                    # Extract quoted strings
                    for match in re.finditer(r'[\'"]([^\'"]+)[\'"]', requires_str):
                        dep_str = match.group(1)
                        dep_match = re.match(r'^([a-zA-Z0-9\-_]+)([><=!]+.*)?', dep_str)
                        if dep_match:
                            name = dep_match.group(1)
                            version = dep_match.group(2) if dep_match.group(2) else None
                            deps.append(DependencyInfo(name, version, 'setup.py'))
        except Exception as e:
            logger.warning(f"Error parsing setup.py: {e}")
    
    return deps


def parse_javascript_dependencies(repo_path: str) -> List[DependencyInfo]:
    """Parse JavaScript dependencies from package.json"""
    deps = []
    
    package_file = os.path.join(repo_path, 'package.json')
    if os.path.exists(package_file):
        try:
            with open(package_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
                
            # Parse dependencies
            for dep_name, version in data.get('dependencies', {}).items():
                deps.append(DependencyInfo(dep_name, version, 'package.json', is_dev=False))
                
            # Parse devDependencies  
            for dep_name, version in data.get('devDependencies', {}).items():
                deps.append(DependencyInfo(dep_name, version, 'package.json', is_dev=True))
                
        except Exception as e:
            logger.warning(f"Error parsing package.json: {e}")
    
    return deps


@mcp.tool()
async def analyze_repo_structure(repo_path: str) -> str:
    """
    Perform comprehensive repository structure analysis.
    
    Args:
        repo_path: Path to the repository to analyze
        
    Returns:
        JSON string with detailed repository structure information
    """
    try:
        if not os.path.exists(repo_path):
            return json.dumps({
                "status": "error",
                "message": f"Repository path does not exist: {repo_path}"
            })
        
        logger.info(f"Analyzing repository structure: {repo_path}")
        
        # Initialize counters and collections
        total_files = 0
        total_lines = 0
        languages = Counter()
        file_details = []
        directories = []
        
        # Walk through repository
        for root, dirs, files in os.walk(repo_path):
            # Skip hidden directories and common build/cache directories
            dirs[:] = [d for d in dirs if not d.startswith('.') and d not in ['__pycache__', 'node_modules', 'target', 'build', 'dist']]
            
            rel_root = os.path.relpath(root, repo_path)
            if rel_root != '.':
                directories.append(rel_root)
            
            for file in files:
                if file.startswith('.'):
                    continue
                    
                file_path = os.path.join(root, file)
                rel_file_path = os.path.relpath(file_path, repo_path)
                
                # Get file info
                file_size = os.path.getsize(file_path)
                file_lines = count_lines_in_file(file_path)
                language = get_file_language(file)
                
                # Calculate complexity and find issues
                complexity = calculate_complexity_score(file_path, language)
                issues = detect_issues_in_file(file_path, language)
                
                file_info = FileInfo(
                    path=rel_file_path,
                    size=file_size,
                    lines=file_lines,
                    language=language,
                    complexity_score=complexity,
                    issues=issues
                )
                
                file_details.append(file_info)
                total_files += 1
                total_lines += file_lines
                languages[language] += 1
        
        # Find special files
        entry_points = find_entry_points(repo_path)
        test_files = find_test_files(repo_path)
        doc_files = find_documentation_files(repo_path)
        config_files = [f for f in CONFIG_FILES if os.path.exists(os.path.join(repo_path, f))]
        
        # Create structure info
        structure_info = RepoStructureInfo(
            total_files=total_files,
            total_lines=total_lines,
            languages=dict(languages),
            directories=sorted(directories),
            file_details=file_details,
            main_entry_points=entry_points,
            test_files=test_files,
            config_files=config_files,
            documentation_files=doc_files
        )
        
        result = {
            "status": "success",
            "repo_path": repo_path,
            "analysis": asdict(structure_info),
            "summary": {
                "primary_language": max(languages.items(), key=lambda x: x[1])[0] if languages else "unknown",
                "file_count": total_files,
                "line_count": total_lines,
                "language_count": len(languages),
                "has_tests": len(test_files) > 0,
                "has_documentation": len(doc_files) > 0,
                "complexity_average": sum(f.complexity_score for f in file_details) / max(len(file_details), 1)
            }
        }
        
        return json.dumps(result, indent=2, ensure_ascii=False)
        
    except Exception as e:
        logger.error(f"Repository structure analysis failed: {e}")
        return json.dumps({
            "status": "error", 
            "message": f"Analysis failed: {str(e)}"
        })


@mcp.tool()
async def detect_dependencies(repo_path: str) -> str:
    """
    Detect and analyze project dependencies across multiple languages.
    
    Args:
        repo_path: Path to the repository
        
    Returns:
        JSON string with dependency information
    """
    try:
        if not os.path.exists(repo_path):
            return json.dumps({
                "status": "error",
                "message": f"Repository path does not exist: {repo_path}"
            })
        
        logger.info(f"Detecting dependencies in: {repo_path}")
        
        all_dependencies = []
        
        # Parse Python dependencies
        python_deps = parse_python_dependencies(repo_path)
        all_dependencies.extend(python_deps)
        
        # Parse JavaScript dependencies
        js_deps = parse_javascript_dependencies(repo_path)
        all_dependencies.extend(js_deps)
        
        # Group dependencies by source
        deps_by_source = defaultdict(list)
        for dep in all_dependencies:
            deps_by_source[dep.source].append(asdict(dep))
        
        # Analyze dependency characteristics
        total_deps = len(all_dependencies)
        dev_deps = len([d for d in all_dependencies if d.is_dev])
        versioned_deps = len([d for d in all_dependencies if d.version])
        
        result = {
            "status": "success",
            "repo_path": repo_path,
            "dependencies": {
                "total_count": total_deps,
                "dev_dependencies": dev_deps,
                "production_dependencies": total_deps - dev_deps,
                "versioned_dependencies": versioned_deps,
                "by_source": dict(deps_by_source),
                "all_dependencies": [asdict(dep) for dep in all_dependencies]
            },
            "analysis": {
                "has_requirements": os.path.exists(os.path.join(repo_path, 'requirements.txt')),
                "has_setup_py": os.path.exists(os.path.join(repo_path, 'setup.py')),
                "has_package_json": os.path.exists(os.path.join(repo_path, 'package.json')),
                "dependency_management_score": min(100, (versioned_deps / max(total_deps, 1)) * 100),
                "potential_issues": []
            }
        }
        
        # Add potential issues
        if total_deps == 0:
            result["analysis"]["potential_issues"].append("No dependencies detected")
        if versioned_deps < total_deps * 0.8:
            result["analysis"]["potential_issues"].append("Many dependencies without version constraints")
        
        return json.dumps(result, indent=2, ensure_ascii=False)
        
    except Exception as e:
        logger.error(f"Dependency detection failed: {e}")
        return json.dumps({
            "status": "error",
            "message": f"Dependency detection failed: {str(e)}"
        })


@mcp.tool()
async def assess_code_quality(repo_path: str) -> str:
    """
    Assess code quality metrics and identify potential issues.
    
    Args:
        repo_path: Path to the repository
        
    Returns:
        JSON string with code quality assessment
    """
    try:
        if not os.path.exists(repo_path):
            return json.dumps({
                "status": "error",
                "message": f"Repository path does not exist: {repo_path}"
            })
        
        logger.info(f"Assessing code quality in: {repo_path}")
        
        # First get repository structure to analyze files
        structure_result = await analyze_repo_structure(repo_path)
        structure_data = json.loads(structure_result)
        
        if structure_data["status"] != "success":
            return structure_result
        
        file_details = structure_data["analysis"]["file_details"]
        
        # Aggregate quality metrics
        complexity_issues = []
        style_issues = []
        potential_bugs = []
        security_issues = []
        
        total_complexity = 0
        total_files = 0
        
        for file_info in file_details:
            if file_info["language"] in ['python', 'javascript', 'typescript', 'java', 'cpp']:
                total_complexity += file_info["complexity_score"]
                total_files += 1
                
                # Categorize issues
                for issue in file_info["issues"]:
                    if "large" in issue.lower() or "complex" in issue.lower():
                        complexity_issues.append(f"{file_info['path']}: {issue}")
                    elif "eval" in issue.lower() or "dangerous" in issue.lower():
                        security_issues.append(f"{file_info['path']}: {issue}")
                    elif "TODO" in issue or "FIXME" in issue:
                        potential_bugs.append(f"{file_info['path']}: {issue}")
                    else:
                        style_issues.append(f"{file_info['path']}: {issue}")
        
        # Calculate scores
        avg_complexity = total_complexity / max(total_files, 1)
        complexity_score = max(0, 100 - avg_complexity)
        
        # Test coverage estimate
        test_files = structure_data["analysis"]["test_files"]
        code_files = [f for f in file_details if f["language"] in ['python', 'javascript', 'java']]
        test_coverage_estimate = min(100, (len(test_files) / max(len(code_files), 1)) * 100)
        
        # Overall maintainability score
        issue_penalty = min(50, len(complexity_issues + style_issues + potential_bugs) * 2)
        maintainability_score = max(0, 100 - issue_penalty)
        
        # Overall score
        overall_score = (complexity_score + maintainability_score + test_coverage_estimate) / 3
        
        assessment = CodeQualityAssessment(
            overall_score=overall_score,
            complexity_issues=complexity_issues,
            style_issues=style_issues,
            potential_bugs=potential_bugs,
            security_issues=security_issues,
            maintainability_score=maintainability_score,
            test_coverage_estimate=test_coverage_estimate
        )
        
        result = {
            "status": "success",
            "repo_path": repo_path,
            "assessment": asdict(assessment),
            "recommendations": []
        }
        
        # Generate recommendations
        if overall_score < 70:
            result["recommendations"].append("Consider refactoring complex files")
        if test_coverage_estimate < 50:
            result["recommendations"].append("Add more comprehensive tests")
        if len(security_issues) > 0:
            result["recommendations"].append("Address security vulnerabilities")
        if len(potential_bugs) > 5:
            result["recommendations"].append("Resolve TODO and FIXME comments")
        
        return json.dumps(result, indent=2, ensure_ascii=False)
        
    except Exception as e:
        logger.error(f"Code quality assessment failed: {e}")
        return json.dumps({
            "status": "error",
            "message": f"Code quality assessment failed: {str(e)}"
        })


@mcp.tool()
async def evaluate_documentation(repo_path: str, docs_path: Optional[str] = None) -> str:
    """
    Evaluate documentation completeness and quality.
    
    Args:
        repo_path: Path to the repository
        docs_path: Optional path to external documentation
        
    Returns:
        JSON string with documentation evaluation
    """
    try:
        if not os.path.exists(repo_path):
            return json.dumps({
                "status": "error",
                "message": f"Repository path does not exist: {repo_path}"
            })
        
        logger.info(f"Evaluating documentation in: {repo_path}")
        
        # Check for standard documentation files
        has_readme = any(os.path.exists(os.path.join(repo_path, f"README.{ext}")) 
                        for ext in ['md', 'rst', 'txt'])
        
        has_license = any(os.path.exists(os.path.join(repo_path, f"LICENSE{ext}")) 
                         for ext in ['', '.txt', '.md'])
        
        has_changelog = any(os.path.exists(os.path.join(repo_path, f"CHANGELOG{ext}"))
                           for ext in ['', '.txt', '.md'])
        
        has_contributing = any(os.path.exists(os.path.join(repo_path, f"CONTRIBUTING{ext}"))
                              for ext in ['', '.txt', '.md'])
        
        # Check for docs directory
        docs_dir = os.path.join(repo_path, 'docs')
        has_docs_dir = os.path.exists(docs_dir) and os.path.isdir(docs_dir)
        
        # Count documentation files
        doc_files = find_documentation_files(repo_path)
        documentation_files_count = len(doc_files)
        
        # Check for API documentation (common patterns)
        has_api_docs = any('api' in f.lower() or 'reference' in f.lower() for f in doc_files)
        
        # Check for examples
        has_examples = any('example' in f.lower() or 'demo' in f.lower() or 'sample' in f.lower() 
                          for f in doc_files)
        
        # Check for installation guide
        has_installation_guide = has_readme  # README usually contains installation
        if not has_installation_guide:
            has_installation_guide = any('install' in f.lower() or 'setup' in f.lower() 
                                        for f in doc_files)
        
        # Check external documentation
        external_docs_score = 0
        if docs_path and os.path.exists(docs_path):
            external_docs_score = 30  # Bonus for external documentation
            
        # Calculate completeness score
        doc_checklist = [
            has_readme,
            has_license, 
            has_api_docs,
            has_examples,
            has_installation_guide,
            has_docs_dir,
            documentation_files_count > 3
        ]
        
        completeness_score = (sum(doc_checklist) / len(doc_checklist)) * 100 + external_docs_score
        completeness_score = min(100, completeness_score)
        
        # Identify missing documentation
        missing_documentation = []
        if not has_readme:
            missing_documentation.append("README file")
        if not has_license:
            missing_documentation.append("LICENSE file")
        if not has_api_docs:
            missing_documentation.append("API documentation")
        if not has_examples:
            missing_documentation.append("Usage examples")
        if not has_installation_guide:
            missing_documentation.append("Installation guide")
        if not has_contributing:
            missing_documentation.append("Contributing guidelines")
        
        assessment = DocumentationAssessment(
            completeness_score=completeness_score,
            has_readme=has_readme,
            has_api_docs=has_api_docs,
            has_examples=has_examples,
            has_installation_guide=has_installation_guide,
            documentation_files_count=documentation_files_count,
            missing_documentation=missing_documentation
        )
        
        result = {
            "status": "success",
            "repo_path": repo_path,
            "external_docs_path": docs_path,
            "assessment": asdict(assessment),
            "found_documentation_files": doc_files[:10],  # Limit for readability
            "recommendations": []
        }
        
        # Generate recommendations
        if completeness_score < 60:
            result["recommendations"].append("Add comprehensive documentation")
        if not has_readme:
            result["recommendations"].append("Create a detailed README file")
        if not has_examples:
            result["recommendations"].append("Add usage examples and tutorials")
        if not has_api_docs:
            result["recommendations"].append("Document API and function signatures")
        
        return json.dumps(result, indent=2, ensure_ascii=False)
        
    except Exception as e:
        logger.error(f"Documentation evaluation failed: {e}")
        return json.dumps({
            "status": "error",
            "message": f"Documentation evaluation failed: {str(e)}"
        })


@mcp.tool()
async def check_reproduction_readiness(repo_path: str, docs_path: Optional[str] = None) -> str:
    """
    Assess repository readiness for reproduction and validation.
    
    Args:
        repo_path: Path to the repository
        docs_path: Optional path to reproduction documentation
        
    Returns:
        JSON string with reproduction readiness assessment
    """
    try:
        if not os.path.exists(repo_path):
            return json.dumps({
                "status": "error",
                "message": f"Repository path does not exist: {repo_path}"
            })
        
        logger.info(f"Checking reproduction readiness: {repo_path}")
        
        # Get previous analysis results
        structure_result = await analyze_repo_structure(repo_path)
        structure_data = json.loads(structure_result)
        
        deps_result = await detect_dependencies(repo_path)
        deps_data = json.loads(deps_result)
        
        docs_result = await evaluate_documentation(repo_path, docs_path)
        docs_data = json.loads(docs_result)
        
        if any(data["status"] != "success" for data in [structure_data, deps_data, docs_data]):
            return json.dumps({
                "status": "error",
                "message": "Failed to complete preliminary analysis"
            })
        
        # Assess reproduction factors
        readiness_factors = {}
        
        # 1. Code completeness (entry points, main files)
        entry_points = structure_data["analysis"]["main_entry_points"]
        readiness_factors["has_entry_points"] = len(entry_points) > 0
        
        # 2. Dependency management
        has_deps_file = deps_data["analysis"]["has_requirements"] or deps_data["analysis"]["has_package_json"]
        readiness_factors["has_dependency_management"] = has_deps_file
        
        # 3. Documentation quality
        doc_score = docs_data["assessment"]["completeness_score"]
        readiness_factors["adequate_documentation"] = doc_score > 60
        
        # 4. Test availability
        test_files = structure_data["analysis"]["test_files"]
        readiness_factors["has_tests"] = len(test_files) > 0
        
        # 5. Configuration files
        config_files = structure_data["analysis"]["config_files"]
        readiness_factors["has_configuration"] = len(config_files) > 0
        
        # 6. External reproduction guide
        readiness_factors["has_reproduction_guide"] = docs_path is not None and os.path.exists(docs_path)
        
        # Calculate overall readiness score
        readiness_score = (sum(readiness_factors.values()) / len(readiness_factors)) * 100
        
        # Identify blocking issues
        blocking_issues = []
        if not readiness_factors["has_entry_points"]:
            blocking_issues.append("No clear entry points found")
        if not readiness_factors["has_dependency_management"]:
            blocking_issues.append("No dependency management files found")
        if not readiness_factors["adequate_documentation"]:
            blocking_issues.append("Insufficient documentation")
        
        # Determine readiness level
        if readiness_score >= 80:
            readiness_level = "high"
        elif readiness_score >= 60:
            readiness_level = "medium"
        else:
            readiness_level = "low"
        
        result = {
            "status": "success",
            "repo_path": repo_path,
            "reproduction_guide_path": docs_path,
            "readiness_assessment": {
                "overall_score": readiness_score,
                "readiness_level": readiness_level,
                "factors": readiness_factors,
                "blocking_issues": blocking_issues,
                "entry_points_found": entry_points,
                "test_files_count": len(test_files),
                "dependency_files_found": [f for f in config_files if 'requirements' in f or 'package' in f]
            },
            "recommendations": []
        }
        
        # Generate recommendations
        if readiness_score < 80:
            result["recommendations"].append("Improve overall reproduction readiness")
        if not readiness_factors["has_entry_points"]:
            result["recommendations"].append("Add clear entry points or main files")
        if not readiness_factors["has_dependency_management"]:
            result["recommendations"].append("Add dependency management files")
        if not readiness_factors["has_tests"]:
            result["recommendations"].append("Add test files for validation")
        if not readiness_factors["has_reproduction_guide"]:
            result["recommendations"].append("Provide detailed reproduction documentation")
        
        return json.dumps(result, indent=2, ensure_ascii=False)
        
    except Exception as e:
        logger.error(f"Reproduction readiness check failed: {e}")
        return json.dumps({
            "status": "error",
            "message": f"Reproduction readiness check failed: {str(e)}"
        })


@mcp.tool()
async def generate_evaluation_summary(repo_path: str, docs_path: Optional[str] = None, conversation_data: Optional[Union[str, dict]] = None) -> str:
    """
    Generate comprehensive evaluation summary from conversation analysis results.
    
    Args:
        repo_path: Path to the repository
        docs_path: Optional path to reproduction documentation
        conversation_data: JSON string containing analysis results from conversation
        
    Returns:
        JSON string with complete evaluation summary
    """
    try:
        if not os.path.exists(repo_path):
            return json.dumps({
                "status": "error",
                "message": f"Repository path does not exist: {repo_path}"
            })
        
        logger.info(f"Generating evaluation summary for: {repo_path}")
        
        # Parse conversation data if provided
        analyses = {}
        if conversation_data:
            try:
                # Handle both string and dict types
                if isinstance(conversation_data, str):
                    conv_data = json.loads(conversation_data)
                    logger.info("📊 Parsed JSON string conversation_data")
                elif isinstance(conversation_data, dict):
                    conv_data = conversation_data
                    logger.info("📊 Using dict conversation_data directly")
                else:
                    raise ValueError(f"Unsupported conversation_data type: {type(conversation_data)}")
                
                analyses = {
                    "structure": conv_data.get("analyze_repo_structure"),
                    "dependencies": conv_data.get("detect_dependencies"),
                    "quality": conv_data.get("assess_code_quality"),
                    "documentation": conv_data.get("evaluate_documentation"),
                    "reproduction_readiness": conv_data.get("check_reproduction_readiness")
                }
                logger.info("📊 Using analysis results from conversation")
            except (json.JSONDecodeError, ValueError) as e:
                logger.warning(f"⚠️ Failed to parse conversation data: {e}, running fresh analysis")
        
        # Run missing analyses if not available from conversation
        missing_analyses = []
        
        if not analyses.get("structure"):
            logger.info("🔍 Running analyze_repo_structure analysis")
            structure_result = await analyze_repo_structure(repo_path)
            analyses["structure"] = json.loads(structure_result)
        
        if not analyses.get("dependencies"):
            logger.info("🔍 Running detect_dependencies analysis")
            deps_result = await detect_dependencies(repo_path)
            analyses["dependencies"] = json.loads(deps_result)
        
        if not analyses.get("quality"):
            logger.info("🔍 Running assess_code_quality analysis")
            quality_result = await assess_code_quality(repo_path)
            analyses["quality"] = json.loads(quality_result)
        
        if not analyses.get("documentation"):
            logger.info("🔍 Running evaluate_documentation analysis")
            docs_result = await evaluate_documentation(repo_path, docs_path)
            analyses["documentation"] = json.loads(docs_result)
        
        if not analyses.get("reproduction_readiness"):
            logger.info("🔍 Running check_reproduction_readiness analysis")
            readiness_result = await check_reproduction_readiness(repo_path, docs_path)
            analyses["reproduction_readiness"] = json.loads(readiness_result)
        
        # Validate analyses - handle cases where data might be None
        for name, analysis in analyses.items():
            if not analysis:
                analyses[name] = {"status": "error", "message": f"No data available for {name}"}
            elif not isinstance(analysis, dict):
                try:
                    analyses[name] = json.loads(analysis) if isinstance(analysis, str) else analysis
                except json.JSONDecodeError as e:
                    analyses[name] = {"status": "error", "message": f"Failed to parse {name} results: {e}"}
        
        # Extract key metrics
        metrics = {}
        if analyses["structure"]["status"] == "success":
            metrics["total_files"] = analyses["structure"]["analysis"]["total_files"]
            metrics["total_lines"] = analyses["structure"]["analysis"]["total_lines"]
            metrics["primary_language"] = analyses["structure"]["summary"]["primary_language"]
            
        if analyses["quality"]["status"] == "success":
            metrics["code_quality_score"] = analyses["quality"]["assessment"]["overall_score"]
            
        if analyses["documentation"]["status"] == "success":
            metrics["documentation_score"] = analyses["documentation"]["assessment"]["completeness_score"]
            
        if analyses["reproduction_readiness"]["status"] == "success":
            metrics["reproduction_readiness_score"] = analyses["reproduction_readiness"]["readiness_assessment"]["overall_score"]
        
        # Calculate overall assessment
        scores = [
            metrics.get("code_quality_score", 0),
            metrics.get("documentation_score", 0),
            metrics.get("reproduction_readiness_score", 0)
        ]
        overall_score = sum(scores) / len(scores) if scores else 0
        
        # Determine overall assessment
        if overall_score >= 80:
            overall_assessment = "excellent"
        elif overall_score >= 70:
            overall_assessment = "good"
        elif overall_score >= 60:
            overall_assessment = "adequate"
        else:
            overall_assessment = "needs_improvement"
        
        # Collect all recommendations
        all_recommendations = []
        for analysis in analyses.values():
            if analysis["status"] == "success" and "recommendations" in analysis:
                all_recommendations.extend(analysis["recommendations"])
        
        # Remove duplicates
        unique_recommendations = list(dict.fromkeys(all_recommendations))
        
        result = {
            "status": "success",
            "evaluation_timestamp": time.strftime('%Y-%m-%d %H:%M:%S'),
            "repository": {
                "path": repo_path,
                "documentation_path": docs_path
            },
            "overall_assessment": {
                "score": overall_score,
                "level": overall_assessment,
                "summary": f"Repository shows {overall_assessment} quality with {overall_score:.1f}% overall score"
            },
            "key_metrics": metrics,
            "detailed_analyses": analyses,
            "recommendations": unique_recommendations[:10],  # Top 10 recommendations
            "evaluation_summary": {
                "strengths": [],
                "weaknesses": [],
                "critical_issues": []
            }
        }
        
        # Identify strengths and weaknesses
        if metrics.get("code_quality_score", 0) > 70:
            result["evaluation_summary"]["strengths"].append("Good code quality")
        else:
            result["evaluation_summary"]["weaknesses"].append("Code quality needs improvement")
            
        if metrics.get("documentation_score", 0) > 70:
            result["evaluation_summary"]["strengths"].append("Adequate documentation")
        else:
            result["evaluation_summary"]["weaknesses"].append("Documentation is insufficient")
            
        if metrics.get("reproduction_readiness_score", 0) > 70:
            result["evaluation_summary"]["strengths"].append("Ready for reproduction")
        else:
            result["evaluation_summary"]["critical_issues"].append("Not ready for reproduction")
        
        return json.dumps(result, indent=2, ensure_ascii=False)
        
    except Exception as e:
        logger.error(f"Evaluation summary generation failed: {e}")
        return json.dumps({
            "status": "error",
            "message": f"Evaluation summary generation failed: {str(e)}"
        })


# Run the server
if __name__ == "__main__":
    mcp.run()
