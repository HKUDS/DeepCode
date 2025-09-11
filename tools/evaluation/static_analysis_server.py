#!/usr/bin/env python3
"""
Static Analysis MCP Server

This module provides static analysis tools for code quality assessment and formatting.
Contains tools for syntax checking, linting, formatting, and code quality metrics.
"""

import os
import json
import subprocess
import time
import logging
from typing import Dict, Any, List, Optional, Tuple
from dataclasses import dataclass, asdict
from collections import defaultdict

# Import MCP modules
from mcp.server.fastmcp import FastMCP

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Create FastMCP server instance
mcp = FastMCP("static-analysis")


@dataclass
class StaticAnalysisIssue:
    """Individual static analysis issue"""
    file_path: str
    line: int
    column: int
    severity: str  # error, warning, info
    code: str
    message: str
    rule: str
    fixable: bool = False
    

@dataclass
class StaticAnalysisResult:
    """Static analysis results for a file"""
    file_path: str
    language: str
    issues: List[StaticAnalysisIssue]
    formatted: bool = False
    syntax_valid: bool = True
    auto_fixes_applied: List[str] = None
    
    def __post_init__(self):
        if self.auto_fixes_applied is None:
            self.auto_fixes_applied = []


@dataclass  
class RepositoryStaticAnalysis:
    """Complete repository static analysis results"""
    repo_path: str
    analyzed_files: List[StaticAnalysisResult]
    total_files: int
    total_issues: int
    error_count: int
    warning_count: int
    info_count: int
    fixable_issues: int
    auto_fixes_applied: int
    languages_detected: List[str]
    analysis_tools_used: List[str]
    analysis_duration: float


# Static analysis tool configurations
STATIC_ANALYSIS_TOOLS = {
    'python': {
        'formatters': ['black', 'isort'],
        'linters': ['flake8', 'pylint', 'mypy'],
        'syntax_checker': 'python',
        'extensions': ['.py']
    },
    'javascript': {
        'formatters': ['prettier'],
        'linters': ['eslint'],
        'syntax_checker': 'node',
        'extensions': ['.js', '.jsx']
    },
    'typescript': {
        'formatters': ['prettier'],
        'linters': ['eslint', 'tsc'],
        'syntax_checker': 'tsc',
        'extensions': ['.ts', '.tsx']
    },
    'java': {
        'formatters': ['google-java-format'],
        'linters': ['checkstyle', 'spotbugs'],
        'syntax_checker': 'javac',
        'extensions': ['.java']
    },
    'go': {
        'formatters': ['gofmt', 'goimports'],
        'linters': ['golint', 'go vet'],
        'syntax_checker': 'go',
        'extensions': ['.go']
    },
    'rust': {
        'formatters': ['rustfmt'],
        'linters': ['clippy'],
        'syntax_checker': 'rustc',
        'extensions': ['.rs']
    },
    'cpp': {
        'formatters': ['clang-format'],
        'linters': ['cppcheck', 'clang-tidy'],
        'syntax_checker': 'clang++',
        'extensions': ['.cpp', '.cxx', '.cc']
    },
    'c': {
        'formatters': ['clang-format'],
        'linters': ['cppcheck'],
        'syntax_checker': 'clang',
        'extensions': ['.c', '.h']
    }
}


def check_tool_availability(tool_name: str) -> bool:
    """Check if a static analysis tool is available in the system"""
    try:
        result = subprocess.run(
            [tool_name, '--version'], 
            capture_output=True, 
            text=True, 
            timeout=10
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        try:
            # Try with different version flags
            for flag in ['--version', '-v', 'version', '--help']:
                result = subprocess.run(
                    [tool_name, flag], 
                    capture_output=True, 
                    text=True, 
                    timeout=5
                )
                if result.returncode == 0:
                    return True
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            pass
        return False


def get_available_tools_for_language(language: str) -> Dict[str, List[str]]:
    """Get available static analysis tools for a given language"""
    if language not in STATIC_ANALYSIS_TOOLS:
        return {'formatters': [], 'linters': [], 'syntax_checker': None}
    
    config = STATIC_ANALYSIS_TOOLS[language]
    available = {
        'formatters': [],
        'linters': [],
        'syntax_checker': None
    }
    
    # Check formatters
    for formatter in config['formatters']:
        if check_tool_availability(formatter):
            available['formatters'].append(formatter)
    
    # Check linters
    for linter in config['linters']:
        if check_tool_availability(linter):
            available['linters'].append(linter)
    
    # Check syntax checker
    syntax_checker = config['syntax_checker']
    if syntax_checker and check_tool_availability(syntax_checker):
        available['syntax_checker'] = syntax_checker
    
    return available


def run_command_safe(cmd: List[str], cwd: str = None, timeout: int = 30) -> Dict[str, Any]:
    """Safely run a command and return structured results"""
    try:
        result = subprocess.run(
            cmd,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout
        )
        
        return {
            'success': result.returncode == 0,
            'returncode': result.returncode,
            'stdout': result.stdout,
            'stderr': result.stderr,
            'command': ' '.join(cmd)
        }
    except subprocess.TimeoutExpired:
        return {
            'success': False,
            'returncode': -1,
            'stdout': '',
            'stderr': f'Command timed out after {timeout} seconds',
            'command': ' '.join(cmd)
        }
    except Exception as e:
        return {
            'success': False,
            'returncode': -1,
            'stdout': '',
            'stderr': str(e),
            'command': ' '.join(cmd)
        }


def format_python_file(file_path: str, available_tools: Dict[str, List[str]]) -> List[str]:
    """Format Python file using available tools"""
    fixes_applied = []
    
    # Apply black formatting
    if 'black' in available_tools['formatters']:
        result = run_command_safe(['black', '--quiet', file_path])
        if result['success']:
            fixes_applied.append('black_formatting')
    
    # Apply isort import sorting
    if 'isort' in available_tools['formatters']:
        result = run_command_safe(['isort', '--quiet', file_path])
        if result['success']:
            fixes_applied.append('isort_imports')
    
    return fixes_applied


def lint_python_file(file_path: str, available_tools: Dict[str, List[str]]) -> List[StaticAnalysisIssue]:
    """Lint Python file and return issues"""
    issues = []
    
    # Flake8 linting
    if 'flake8' in available_tools['linters']:
        result = run_command_safe(['flake8', '--format=json', file_path])
        if result['success'] and result['stdout']:
            try:
                flake8_data = json.loads(result['stdout'])
                for item in flake8_data:
                    issues.append(StaticAnalysisIssue(
                        file_path=file_path,
                        line=item.get('line_number', 0),
                        column=item.get('column_number', 0),
                        severity='warning' if item.get('code', '').startswith('W') else 'error',
                        code=item.get('code', ''),
                        message=item.get('text', ''),
                        rule='flake8',
                        fixable=False
                    ))
            except json.JSONDecodeError:
                pass
    
    # Pylint linting (JSON output)
    if 'pylint' in available_tools['linters']:
        result = run_command_safe(['pylint', '--output-format=json', file_path])
        if result['stdout']:
            try:
                pylint_data = json.loads(result['stdout'])
                for item in pylint_data:
                    issues.append(StaticAnalysisIssue(
                        file_path=file_path,
                        line=item.get('line', 0),
                        column=item.get('column', 0),
                        severity=item.get('type', 'warning'),
                        code=item.get('symbol', ''),
                        message=item.get('message', ''),
                        rule='pylint',
                        fixable=False
                    ))
            except json.JSONDecodeError:
                pass
    
    return issues


def check_python_syntax(file_path: str) -> Tuple[bool, List[StaticAnalysisIssue]]:
    """Check Python syntax"""
    import ast
    issues = []
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            source = f.read()
        
        ast.parse(source)
        return True, issues
    except SyntaxError as e:
        issues.append(StaticAnalysisIssue(
            file_path=file_path,
            line=e.lineno or 0,
            column=e.offset or 0,
            severity='error',
            code='SyntaxError',
            message=str(e),
            rule='python_syntax',
            fixable=False
        ))
        return False, issues
    except Exception as e:
        issues.append(StaticAnalysisIssue(
            file_path=file_path,
            line=0,
            column=0,
            severity='error',
            code='ParseError',
            message=str(e),
            rule='python_syntax',
            fixable=False
        ))
        return False, issues


def format_javascript_file(file_path: str, available_tools: Dict[str, List[str]]) -> List[str]:
    """Format JavaScript/TypeScript file using Prettier"""
    fixes_applied = []
    
    if 'prettier' in available_tools['formatters']:
        result = run_command_safe(['prettier', '--write', file_path])
        if result['success']:
            fixes_applied.append('prettier_formatting')
    
    return fixes_applied


def lint_javascript_file(file_path: str, available_tools: Dict[str, List[str]]) -> List[StaticAnalysisIssue]:
    """Lint JavaScript/TypeScript file using ESLint"""
    issues = []
    
    if 'eslint' in available_tools['linters']:
        result = run_command_safe(['eslint', '--format=json', file_path])
        if result['stdout']:
            try:
                eslint_data = json.loads(result['stdout'])
                for file_result in eslint_data:
                    for message in file_result.get('messages', []):
                        issues.append(StaticAnalysisIssue(
                            file_path=file_path,
                            line=message.get('line', 0),
                            column=message.get('column', 0),
                            severity=message.get('severity', 1) == 2 and 'error' or 'warning',
                            code=message.get('ruleId', ''),
                            message=message.get('message', ''),
                            rule='eslint',
                            fixable=message.get('fix') is not None
                        ))
            except json.JSONDecodeError:
                pass
    
    return issues


def analyze_single_file(file_path: str, language: str, repo_path: str) -> StaticAnalysisResult:
    """Perform static analysis on a single file"""
    available_tools = get_available_tools_for_language(language)
    issues = []
    fixes_applied = []
    syntax_valid = True
    formatted = False
    
    abs_file_path = os.path.join(repo_path, file_path)
    
    if not os.path.exists(abs_file_path):
        return StaticAnalysisResult(
            file_path=file_path,
            language=language,
            issues=[StaticAnalysisIssue(
                file_path=file_path,
                line=0,
                column=0,
                severity='error',
                code='FileNotFound',
                message='File does not exist',
                rule='filesystem',
                fixable=False
            )],
            formatted=False,
            syntax_valid=False,
            auto_fixes_applied=[]
        )
    
    # Language-specific processing
    if language == 'python':
        # Check syntax first
        syntax_valid, syntax_issues = check_python_syntax(abs_file_path)
        issues.extend(syntax_issues)
        
        # If syntax is valid, apply formatting and linting
        if syntax_valid:
            fixes_applied = format_python_file(abs_file_path, available_tools)
            formatted = len(fixes_applied) > 0
            lint_issues = lint_python_file(abs_file_path, available_tools)
            issues.extend(lint_issues)
    
    elif language in ['javascript', 'typescript']:
        fixes_applied = format_javascript_file(abs_file_path, available_tools)
        formatted = len(fixes_applied) > 0
        lint_issues = lint_javascript_file(abs_file_path, available_tools)
        issues.extend(lint_issues)
    
    return StaticAnalysisResult(
        file_path=file_path,
        language=language,
        issues=issues,
        formatted=formatted,
        syntax_valid=syntax_valid,
        auto_fixes_applied=fixes_applied
    )


def detect_repository_languages(repo_path: str) -> Dict[str, List[str]]:
    """Detect all programming languages used in a repository"""
    language_files = defaultdict(list)
    
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
        '.rb': 'ruby'
    }
    
    def get_file_language(file_path: str) -> str:
        """Determine programming language from file extension"""
        ext = os.path.splitext(file_path)[1].lower()
        return LANGUAGE_PATTERNS.get(ext, 'unknown')
    
    for root, dirs, files in os.walk(repo_path):
        # Skip hidden directories and common build/cache directories
        dirs[:] = [d for d in dirs if not d.startswith('.') and d not in ['__pycache__', 'node_modules', 'target', 'build', 'dist']]
        
        for file in files:
            if file.startswith('.'):
                continue
                
            file_path = os.path.join(root, file)
            rel_file_path = os.path.relpath(file_path, repo_path)
            language = get_file_language(file)
            
            if language != 'unknown':
                language_files[language].append(rel_file_path)
    
    return dict(language_files)


@mcp.tool()
async def perform_static_analysis(repo_path: str, auto_fix: bool = True, languages: Optional[List[str]] = None) -> str:
    """
    Perform comprehensive static analysis on repository with automatic fixes.
    
    Args:
        repo_path: Path to the repository to analyze
        auto_fix: Whether to automatically apply formatting fixes
        languages: Optional list of languages to analyze (if None, auto-detect all)
        
    Returns:
        JSON string with complete static analysis results
    """
    try:
        if not os.path.exists(repo_path):
            return json.dumps({
                "status": "error",
                "message": f"Repository path does not exist: {repo_path}"
            })
        
        logger.info(f"Starting static analysis: {repo_path}")
        start_time = time.time()
        
        # Detect repository languages and files
        if languages is None:
            language_files = detect_repository_languages(repo_path)
            languages_detected = list(language_files.keys())
        else:
            language_files = {}
            languages_detected = languages
            for language in languages:
                language_files[language] = []
                
                # Find files for specified languages
                for root, dirs, files in os.walk(repo_path):
                    dirs[:] = [d for d in dirs if not d.startswith('.') and d not in ['__pycache__', 'node_modules', 'target', 'build', 'dist']]
                    
                    for file in files:
                        if file.startswith('.'):
                            continue
                            
                        file_path = os.path.join(root, file)
                        rel_file_path = os.path.relpath(file_path, repo_path)
                        from pathlib import Path
                        ext = Path(file_path).suffix.lower()
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
                            '.rb': 'ruby'
                        }
                        file_language = LANGUAGE_PATTERNS.get(ext, 'unknown')
                        
                        if file_language == language:
                            language_files[language].append(rel_file_path)
        
        logger.info(f"Detected languages: {languages_detected}")
        
        # Check available tools for each language
        analysis_tools_used = []
        for language in languages_detected:
            available_tools = get_available_tools_for_language(language)
            if available_tools['formatters'] or available_tools['linters']:
                analysis_tools_used.extend(available_tools['formatters'])
                analysis_tools_used.extend(available_tools['linters'])
                if available_tools['syntax_checker']:
                    analysis_tools_used.append(available_tools['syntax_checker'])
        
        # Remove duplicates
        analysis_tools_used = list(set(analysis_tools_used))
        logger.info(f"Available analysis tools: {analysis_tools_used}")
        
        # Analyze all files
        analyzed_files = []
        total_issues = 0
        error_count = 0
        warning_count = 0
        info_count = 0
        fixable_issues = 0
        auto_fixes_applied = 0
        
        for language, files in language_files.items():
            logger.info(f"Analyzing {len(files)} {language} files")
            
            for file_path in files:
                try:
                    # Perform analysis on individual file
                    result = analyze_single_file(file_path, language, repo_path)
                    analyzed_files.append(result)
                    
                    # Count issues and statistics
                    total_issues += len(result.issues)
                    auto_fixes_applied += len(result.auto_fixes_applied)
                    
                    for issue in result.issues:
                        if issue.severity == 'error':
                            error_count += 1
                        elif issue.severity == 'warning':
                            warning_count += 1
                        else:
                            info_count += 1
                        
                        if issue.fixable:
                            fixable_issues += 1
                            
                except Exception as e:
                    logger.error(f"Failed to analyze file {file_path}: {e}")
                    # Add error result for failed analysis
                    analyzed_files.append(StaticAnalysisResult(
                        file_path=file_path,
                        language=language,
                        issues=[StaticAnalysisIssue(
                            file_path=file_path,
                            line=0,
                            column=0,
                            severity='error',
                            code='AnalysisError',
                            message=str(e),
                            rule='analyzer',
                            fixable=False
                        )],
                        formatted=False,
                        syntax_valid=False,
                        auto_fixes_applied=[]
                    ))
                    error_count += 1
                    total_issues += 1
        
        # Calculate analysis duration
        analysis_duration = time.time() - start_time
        
        # Create complete repository analysis
        repo_analysis = RepositoryStaticAnalysis(
            repo_path=repo_path,
            analyzed_files=analyzed_files,
            total_files=len(analyzed_files),
            total_issues=total_issues,
            error_count=error_count,
            warning_count=warning_count,
            info_count=info_count,
            fixable_issues=fixable_issues,
            auto_fixes_applied=auto_fixes_applied,
            languages_detected=languages_detected,
            analysis_tools_used=analysis_tools_used,
            analysis_duration=analysis_duration
        )
        
        # Convert to JSON-safe format
        result = {
            "status": "success",
            "repo_path": repo_path,
            "analysis": asdict(repo_analysis),
            "summary": {
                "total_files_analyzed": len(analyzed_files),
                "languages_detected": len(languages_detected),
                "total_issues_found": total_issues,
                "auto_fixes_applied": auto_fixes_applied,
                "analysis_duration_seconds": analysis_duration,
                "issues_by_severity": {
                    "errors": error_count,
                    "warnings": warning_count,
                    "info": info_count
                },
                "tools_used": analysis_tools_used,
                "analysis_successful": error_count == 0 or (error_count < total_issues * 0.1)  # Less than 10% errors
            }
        }
        
        logger.info(f"Static analysis completed in {analysis_duration:.2f}s")
        logger.info(f"Analyzed {len(analyzed_files)} files, found {total_issues} issues")
        logger.info(f"Applied {auto_fixes_applied} automatic fixes")
        
        return json.dumps(result, indent=2, ensure_ascii=False)
        
    except Exception as e:
        logger.error(f"Static analysis failed: {e}")
        return json.dumps({
            "status": "error",
            "message": f"Static analysis failed: {str(e)}"
        })


@mcp.tool()
async def auto_fix_formatting(repo_path: str, languages: Optional[List[str]] = None, dry_run: bool = False) -> str:
    """
    Automatically fix formatting issues in repository files.
    
    Args:
        repo_path: Path to the repository
        languages: Optional list of languages to format (if None, auto-detect all)
        dry_run: If True, only report what would be fixed without making changes
        
    Returns:
        JSON string with formatting results
    """
    try:
        if not os.path.exists(repo_path):
            return json.dumps({
                "status": "error",
                "message": f"Repository path does not exist: {repo_path}"
            })
        
        logger.info(f"Starting auto-formatting: {repo_path} (dry_run={dry_run})")
        start_time = time.time()
        
        # Detect repository languages and files
        if languages is None:
            language_files = detect_repository_languages(repo_path)
            languages_detected = list(language_files.keys())
        else:
            language_files = {}
            languages_detected = languages
            for language in languages:
                language_files[language] = []
                
                # Find files for specified languages
                for root, dirs, files in os.walk(repo_path):
                    dirs[:] = [d for d in dirs if not d.startswith('.') and d not in ['__pycache__', 'node_modules', 'target', 'build', 'dist']]
                    
                    for file in files:
                        if file.startswith('.'):
                            continue
                            
                        file_path = os.path.join(root, file)
                        rel_file_path = os.path.relpath(file_path, repo_path)
                        from pathlib import Path
                        ext = Path(file_path).suffix.lower()
                        LANGUAGE_PATTERNS = {
                            '.py': 'python',
                            '.js': 'javascript', 
                            '.ts': 'typescript',
                            '.java': 'java',
                            '.go': 'go',
                            '.rs': 'rust',
                            '.cpp': 'cpp',
                            '.c': 'c'
                        }
                        file_language = LANGUAGE_PATTERNS.get(ext, 'unknown')
                        
                        if file_language == language:
                            language_files[language].append(rel_file_path)
        
        formatting_results = []
        total_files_processed = 0
        total_files_formatted = 0
        
        for language, files in language_files.items():
            available_tools = get_available_tools_for_language(language)
            
            if not available_tools['formatters']:
                logger.info(f"No formatters available for {language}")
                continue
                
            logger.info(f"Formatting {len(files)} {language} files with tools: {available_tools['formatters']}")
            
            for file_path in files:
                abs_file_path = os.path.join(repo_path, file_path)
                
                if not os.path.exists(abs_file_path):
                    continue
                
                total_files_processed += 1
                fixes_applied = []
                
                if not dry_run:
                    # Apply actual formatting
                    if language == 'python':
                        fixes_applied = format_python_file(abs_file_path, available_tools)
                    elif language in ['javascript', 'typescript']:
                        fixes_applied = format_javascript_file(abs_file_path, available_tools)
                else:
                    # Dry run - just report available formatters
                    fixes_applied = [f"would_apply_{formatter}" for formatter in available_tools['formatters']]
                
                if fixes_applied:
                    total_files_formatted += 1
                    formatting_results.append({
                        "file_path": file_path,
                        "language": language,
                        "fixes_applied": fixes_applied,
                        "tools_used": available_tools['formatters']
                    })
        
        duration = time.time() - start_time
        
        result = {
            "status": "success",
            "repo_path": repo_path,
            "dry_run": dry_run,
            "formatting_results": {
                "total_files_processed": total_files_processed,
                "total_files_formatted": total_files_formatted,
                "languages_processed": languages_detected,
                "files_formatted": formatting_results,
                "duration_seconds": duration
            },
            "summary": {
                "success_rate": (total_files_formatted / max(total_files_processed, 1)) * 100,
                "languages_with_formatters": len([lang for lang in languages_detected 
                                                 if get_available_tools_for_language(lang)['formatters']]),
                "action_taken": "Would format" if dry_run else "Formatted"
            }
        }
        
        logger.info(f"Auto-formatting completed: {total_files_formatted}/{total_files_processed} files formatted")
        
        return json.dumps(result, indent=2, ensure_ascii=False)
        
    except Exception as e:
        logger.error(f"Auto-formatting failed: {e}")
        return json.dumps({
            "status": "error",
            "message": f"Auto-formatting failed: {str(e)}"
        })


@mcp.tool()
async def generate_static_issues_report(repo_path: str, severity_filter: Optional[str] = None, language_filter: Optional[str] = None) -> str:
    """
    Generate structured JSON report of static analysis issues.
    
    Args:
        repo_path: Path to the repository
        severity_filter: Optional filter by severity (error, warning, info)
        language_filter: Optional filter by programming language
        
    Returns:
        JSON string with structured issues report
    """
    try:
        if not os.path.exists(repo_path):
            return json.dumps({
                "status": "error",
                "message": f"Repository path does not exist: {repo_path}"
            })
        
        logger.info(f"Generating static issues report: {repo_path}")
        
        # First run static analysis to get current issues
        analysis_result = await perform_static_analysis(repo_path, auto_fix=False)
        analysis_data = json.loads(analysis_result)
        
        if analysis_data["status"] != "success":
            return analysis_result
        
        # Extract issues from analysis
        all_issues = []
        analyzed_files = analysis_data["analysis"]["analyzed_files"]
        
        for file_result in analyzed_files:
            file_path = file_result["file_path"]
            language = file_result["language"]
            
            # Apply language filter
            if language_filter and language != language_filter:
                continue
            
            for issue_data in file_result["issues"]:
                # Apply severity filter
                if severity_filter and issue_data["severity"] != severity_filter:
                    continue
                
                issue = StaticAnalysisIssue(
                    file_path=file_path,
                    line=issue_data["line"],
                    column=issue_data["column"],
                    severity=issue_data["severity"],
                    code=issue_data["code"],
                    message=issue_data["message"],
                    rule=issue_data["rule"],
                    fixable=issue_data["fixable"]
                )
                all_issues.append(issue)
        
        # Group issues by different criteria
        issues_by_file = defaultdict(list)
        issues_by_severity = defaultdict(list)
        issues_by_language = defaultdict(list)
        issues_by_rule = defaultdict(list)
        
        for issue in all_issues:
            issues_by_file[issue.file_path].append(issue)
            issues_by_severity[issue.severity].append(issue)
            # Get language from file
            from pathlib import Path
            ext = Path(issue.file_path).suffix.lower()
            LANGUAGE_PATTERNS = {'.py': 'python', '.js': 'javascript', '.ts': 'typescript'}
            language = LANGUAGE_PATTERNS.get(ext, 'unknown')
            issues_by_language[language].append(issue)
            issues_by_rule[issue.rule].append(issue)
        
        # Calculate statistics
        total_issues = len(all_issues)
        fixable_issues = len([issue for issue in all_issues if issue.fixable])
        unique_files_with_issues = len(issues_by_file)
        unique_rules_triggered = len(issues_by_rule)
        
        # Create structured report
        result = {
            "status": "success",
            "repo_path": repo_path,
            "filters_applied": {
                "severity_filter": severity_filter,
                "language_filter": language_filter
            },
            "issues_summary": {
                "total_issues": total_issues,
                "fixable_issues": fixable_issues,
                "files_with_issues": unique_files_with_issues,
                "unique_rules_triggered": unique_rules_triggered,
                "severity_breakdown": {
                    "errors": len(issues_by_severity["error"]),
                    "warnings": len(issues_by_severity["warning"]),
                    "info": len(issues_by_severity["info"])
                },
                "language_breakdown": {lang: len(issues) for lang, issues in issues_by_language.items()},
                "fixability_rate": (fixable_issues / max(total_issues, 1)) * 100
            },
            "issues_by_file": {
                file_path: [asdict(issue) for issue in file_issues]
                for file_path, file_issues in issues_by_file.items()
            },
            "issues_by_severity": {
                severity: [asdict(issue) for issue in severity_issues]
                for severity, severity_issues in issues_by_severity.items()
            },
            "issues_by_rule": {
                rule: {
                    "count": len(rule_issues),
                    "issues": [asdict(issue) for issue in rule_issues[:5]]  # Limit to first 5 for readability
                }
                for rule, rule_issues in issues_by_rule.items()
            },
            "most_problematic_files": [
                {
                    "file_path": file_path,
                    "issue_count": len(file_issues),
                    "severity_breakdown": {
                        "errors": len([i for i in file_issues if i.severity == "error"]),
                        "warnings": len([i for i in file_issues if i.severity == "warning"]),
                        "info": len([i for i in file_issues if i.severity == "info"])
                    }
                }
                for file_path, file_issues in sorted(issues_by_file.items(), 
                                                   key=lambda x: len(x[1]), reverse=True)[:10]
            ],
            "recommendations": []
        }
        
        # Generate recommendations
        if total_issues == 0:
            result["recommendations"].append("No static analysis issues found - code quality looks good!")
        else:
            if fixable_issues > 0:
                result["recommendations"].append(f"Consider running auto-formatting to fix {fixable_issues} automatically fixable issues")
            
            if len(issues_by_severity["error"]) > 0:
                result["recommendations"].append(f"Address {len(issues_by_severity['error'])} critical errors first")
            
            if unique_files_with_issues > 5:
                result["recommendations"].append(f"Focus on the {min(5, unique_files_with_issues)} most problematic files first")
            
            # Most common rule recommendations
            common_rules = sorted(issues_by_rule.items(), key=lambda x: len(x[1]), reverse=True)[:3]
            for rule, rule_issues in common_rules:
                result["recommendations"].append(f"Rule '{rule}' triggered {len(rule_issues)} times - consider reviewing this pattern")
        
        logger.info(f"Generated issues report: {total_issues} issues across {unique_files_with_issues} files")
        
        return json.dumps(result, indent=2, ensure_ascii=False)
        
    except Exception as e:
        logger.error(f"Issues report generation failed: {e}")
        return json.dumps({
            "status": "error",
            "message": f"Issues report generation failed: {str(e)}"
        })


# Run the server
if __name__ == "__main__":
    mcp.run()
