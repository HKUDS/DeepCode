#!/usr/bin/env python3
"""
Code Evaluation MCP Server

This MCP server provides comprehensive code analysis and evaluation tools
for repository-level validation and assessment.

Core Features:
1. Repository structure analysis and mapping
2. Dependency detection and validation  
3. Code quality assessment and metrics
4. Documentation completeness evaluation
5. Reproduction readiness assessment
6. Multi-language project support

Tools Provided:
- analyze_repo_structure: Deep repository structure analysis
- detect_dependencies: Intelligent dependency detection across languages
- assess_code_quality: Code quality metrics and issue identification
- evaluate_documentation: Documentation completeness and quality assessment
- check_reproduction_readiness: Assess readiness for reproduction
- generate_evaluation_summary: Comprehensive evaluation report generation

Usage:
python tools/code_evaluation_server.py
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


# ===== PHASE 4: ADVANCED ERROR ANALYSIS DATACLASSES =====

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


# ===== LSP CLIENT INFRASTRUCTURE (Phase 4+) =====

@dataclass
class LSPSymbol:
    """LSP symbol information"""
    name: str
    kind: int  # SymbolKind from LSP
    location: Dict[str, Any]  # LSP Location
    container_name: Optional[str] = None
    detail: Optional[str] = None
    
@dataclass
class LSPDiagnostic:
    """LSP diagnostic information"""
    range: Dict[str, Any]  # LSP Range
    severity: int  # DiagnosticSeverity
    code: Optional[str] = None
    message: str = ""
    source: Optional[str] = None
    
@dataclass
class LSPCodeAction:
    """LSP code action"""
    title: str
    kind: Optional[str] = None
    diagnostics: Optional[List[LSPDiagnostic]] = None
    edit: Optional[Dict[str, Any]] = None
    command: Optional[Dict[str, Any]] = None

@dataclass
class LSPReference:
    """LSP reference location"""
    uri: str
    range: Dict[str, Any]  # LSP Range
    
class LSPClient:
    """LSP client for communicating with language servers"""
    
    def __init__(self, server_command: List[str], workspace_root: str):
        self.server_command = server_command
        self.workspace_root = os.path.abspath(workspace_root)
        self.process = None
        self.request_id = 0
        self.response_futures = {}
        self.initialized = False
        self.logger = logging.getLogger(__name__ + ".LSPClient")
        
    async def start(self):
        """Start the LSP server"""
        try:
            self.process = await asyncio.create_subprocess_exec(
                *self.server_command,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            
            # Start reading responses
            asyncio.create_task(self._read_responses())
            
            # Initialize the server
            await self._initialize()
            
            self.logger.info(f"LSP server started: {' '.join(self.server_command)}")
            
        except Exception as e:
            self.logger.error(f"Failed to start LSP server: {e}")
            raise
    
    async def stop(self):
        """Stop the LSP server"""
        if self.process:
            try:
                await self._send_notification("exit")
                self.process.terminate()
                await self.process.wait()
            except Exception as e:
                self.logger.error(f"Error stopping LSP server: {e}")
    
    async def _initialize(self):
        """Initialize the LSP server"""
        init_params = {
            "processId": os.getpid(),
            "rootUri": f"file://{self.workspace_root}",
            "capabilities": {
                "textDocument": {
                    "publishDiagnostics": {"relatedInformation": True},
                    "synchronization": {"didSave": True},
                    "completion": {"completionItem": {"snippetSupport": True}},
                    "definition": {"linkSupport": True},
                    "references": {"context": True},
                    "documentSymbol": {"hierarchicalDocumentSymbolSupport": True},
                    "codeAction": {"codeActionLiteralSupport": True},
                    "rename": {"prepareSupport": True}
                },
                "workspace": {
                    "workspaceFolders": True,
                    "symbol": {"symbolKind": {"valueSet": list(range(1, 27))}},
                    "executeCommand": {}
                }
            }
        }
        
        response = await self._send_request("initialize", init_params)
        if response.get("error"):
            raise Exception(f"LSP initialization failed: {response['error']}")
            
        await self._send_notification("initialized", {})
        self.initialized = True
        
    async def _send_request(self, method: str, params: Dict[str, Any]) -> Dict[str, Any]:
        """Send an LSP request and wait for response"""
        self.request_id += 1
        request = {
            "jsonrpc": "2.0",
            "id": self.request_id,
            "method": method,
            "params": params
        }
        
        # Create future for response
        future = asyncio.Future()
        self.response_futures[self.request_id] = future
        
        # Send request
        message = json.dumps(request) + "\n"
        content_length = len(message.encode())
        full_message = f"Content-Length: {content_length}\r\n\r\n{message}"
        
        self.process.stdin.write(full_message.encode())
        await self.process.stdin.drain()
        
        # Wait for response
        try:
            response = await asyncio.wait_for(future, timeout=30.0)
            return response
        except asyncio.TimeoutError:
            self.logger.error(f"LSP request timeout: {method}")
            return {"error": "Request timeout"}
    
    async def _send_notification(self, method: str, params: Dict[str, Any] = None):
        """Send an LSP notification (no response expected)"""
        notification = {
            "jsonrpc": "2.0",
            "method": method,
        }
        if params is not None:
            notification["params"] = params
            
        message = json.dumps(notification) + "\n"
        content_length = len(message.encode())
        full_message = f"Content-Length: {content_length}\r\n\r\n{message}"
        
        self.process.stdin.write(full_message.encode())
        await self.process.stdin.drain()
        
    async def _read_responses(self):
        """Read responses from LSP server"""
        buffer = b""
        
        while self.process.returncode is None:
            try:
                data = await self.process.stdout.read(4096)
                if not data:
                    break
                    
                buffer += data
                
                while b"\r\n\r\n" in buffer:
                    header_end = buffer.find(b"\r\n\r\n")
                    header = buffer[:header_end].decode()
                    
                    content_length = 0
                    for line in header.split("\r\n"):
                        if line.startswith("Content-Length:"):
                            content_length = int(line.split(":")[1].strip())
                            break
                    
                    if len(buffer) >= header_end + 4 + content_length:
                        content = buffer[header_end + 4:header_end + 4 + content_length]
                        buffer = buffer[header_end + 4 + content_length:]
                        
                        try:
                            message = json.loads(content.decode())
                            await self._handle_message(message)
                        except json.JSONDecodeError as e:
                            self.logger.error(f"Failed to parse LSP message: {e}")
                    else:
                        break
                        
            except Exception as e:
                self.logger.error(f"Error reading LSP responses: {e}")
                break
                
    async def _handle_message(self, message: Dict[str, Any]):
        """Handle incoming LSP message"""
        if "id" in message:
            # Response to our request
            request_id = message["id"]
            if request_id in self.response_futures:
                future = self.response_futures.pop(request_id)
                if not future.done():
                    future.set_result(message)
        else:
            # Notification from server
            method = message.get("method")
            if method == "textDocument/publishDiagnostics":
                # Handle diagnostics
                pass
    
    async def open_document(self, file_path: str) -> bool:
        """Open a document in the LSP server"""
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()
            
            params = {
                "textDocument": {
                    "uri": f"file://{os.path.abspath(file_path)}",
                    "languageId": self._get_language_id(file_path),
                    "version": 1,
                    "text": content
                }
            }
            
            await self._send_notification("textDocument/didOpen", params)
            return True
            
        except Exception as e:
            self.logger.error(f"Failed to open document {file_path}: {e}")
            return False
    
    async def get_diagnostics(self, file_path: str) -> List[LSPDiagnostic]:
        """Get diagnostics for a file"""
        # Diagnostics are sent via notifications, we'd need to store them
        # For now, return empty list
        return []
    
    async def get_symbols(self, file_path: str) -> List[LSPSymbol]:
        """Get document symbols"""
        try:
            params = {
                "textDocument": {
                    "uri": f"file://{os.path.abspath(file_path)}"
                }
            }
            
            response = await self._send_request("textDocument/documentSymbol", params)
            
            if response.get("error"):
                self.logger.error(f"Get symbols failed: {response['error']}")
                return []
            
            symbols = []
            for symbol_data in response.get("result", []):
                symbol = LSPSymbol(
                    name=symbol_data.get("name", ""),
                    kind=symbol_data.get("kind", 0),
                    location=symbol_data.get("location", {}),
                    container_name=symbol_data.get("containerName"),
                    detail=symbol_data.get("detail")
                )
                symbols.append(symbol)
            
            return symbols
            
        except Exception as e:
            self.logger.error(f"Failed to get symbols for {file_path}: {e}")
            return []
    
    async def find_references(self, file_path: str, line: int, character: int) -> List[LSPReference]:
        """Find references to symbol at position"""
        try:
            params = {
                "textDocument": {
                    "uri": f"file://{os.path.abspath(file_path)}"
                },
                "position": {
                    "line": line,
                    "character": character
                },
                "context": {
                    "includeDeclaration": True
                }
            }
            
            response = await self._send_request("textDocument/references", params)
            
            if response.get("error"):
                self.logger.error(f"Find references failed: {response['error']}")
                return []
            
            references = []
            for ref_data in response.get("result", []):
                reference = LSPReference(
                    uri=ref_data.get("uri", ""),
                    range=ref_data.get("range", {})
                )
                references.append(reference)
            
            return references
            
        except Exception as e:
            self.logger.error(f"Failed to find references: {e}")
            return []
    
    async def get_code_actions(self, file_path: str, range_data: Dict[str, Any], diagnostics: List[LSPDiagnostic] = None) -> List[LSPCodeAction]:
        """Get code actions for a range"""
        try:
            params = {
                "textDocument": {
                    "uri": f"file://{os.path.abspath(file_path)}"
                },
                "range": range_data,
                "context": {
                    "diagnostics": [asdict(d) for d in (diagnostics or [])]
                }
            }
            
            response = await self._send_request("textDocument/codeAction", params)
            
            if response.get("error"):
                self.logger.error(f"Get code actions failed: {response['error']}")
                return []
            
            actions = []
            for action_data in response.get("result", []):
                if isinstance(action_data, dict):
                    action = LSPCodeAction(
                        title=action_data.get("title", ""),
                        kind=action_data.get("kind"),
                        edit=action_data.get("edit"),
                        command=action_data.get("command")
                    )
                    actions.append(action)
            
            return actions
            
        except Exception as e:
            self.logger.error(f"Failed to get code actions: {e}")
            return []
    
    def _get_language_id(self, file_path: str) -> str:
        """Get LSP language ID for file"""
        ext = os.path.splitext(file_path)[1].lower()
        language_map = {
            '.py': 'python',
            '.js': 'javascript',
            '.ts': 'typescript',
            '.jsx': 'javascriptreact',
            '.tsx': 'typescriptreact',
            '.java': 'java',
            '.cpp': 'cpp',
            '.c': 'c',
            '.h': 'c',
            '.hpp': 'cpp',
            '.rs': 'rust',
            '.go': 'go',
            '.php': 'php',
            '.rb': 'ruby',
            '.cs': 'csharp',
            '.vb': 'vb',
            '.sql': 'sql',
            '.json': 'json',
            '.xml': 'xml',
            '.html': 'html',
            '.css': 'css',
            '.scss': 'scss',
            '.less': 'less',
            '.md': 'markdown',
            '.yml': 'yaml',
            '.yaml': 'yaml'
        }
        return language_map.get(ext, 'plaintext')


class LSPManager:
    """Manager for multiple LSP servers"""
    
    def __init__(self, workspace_root: str):
        self.workspace_root = workspace_root
        self.clients = {}
        self.logger = logging.getLogger(__name__ + ".LSPManager")
        
    async def start_server(self, language: str, command: List[str]) -> bool:
        """Start LSP server for a language"""
        try:
            if language in self.clients:
                return True
                
            client = LSPClient(command, self.workspace_root)
            await client.start()
            self.clients[language] = client
            
            self.logger.info(f"Started LSP server for {language}")
            return True
            
        except Exception as e:
            self.logger.error(f"Failed to start LSP server for {language}: {e}")
            return False
    
    async def stop_all(self):
        """Stop all LSP servers"""
        for language, client in self.clients.items():
            try:
                await client.stop()
                self.logger.info(f"Stopped LSP server for {language}")
            except Exception as e:
                self.logger.error(f"Error stopping LSP server for {language}: {e}")
        
        self.clients.clear()
    
    def get_client(self, language: str) -> Optional[LSPClient]:
        """Get LSP client for language"""
        return self.clients.get(language)
    
    async def setup_for_repository(self, repo_path: str) -> Dict[str, bool]:
        """Automatically set up LSP servers for detected languages in repository"""
        languages_detected = detect_repository_languages(repo_path)
        setup_results = {}
        
        # Language server commands
        server_commands = {
            'python': ['python', '-m', 'pylsp'],  # python-lsp-server
            'javascript': ['typescript-language-server', '--stdio'],
            'typescript': ['typescript-language-server', '--stdio'],
            'java': ['jdtls'],  # Eclipse JDT Language Server
            'cpp': ['clangd'],
            'c': ['clangd'],
            'rust': ['rust-analyzer'],
            'go': ['gopls'],
            'php': ['intelephense', '--stdio'],
            'ruby': ['solargraph', 'stdio'],
            'csharp': ['omnisharp', '--lsp']
        }
        
        for language, files in languages_detected.items():
            if language in server_commands:
                success = await self.start_server(language, server_commands[language])
                setup_results[language] = success
                
                if success:
                    # Open some representative files
                    client = self.get_client(language)
                    for rel_file_path in files[:5]:  # Open first 5 files
                        # Convert relative path to absolute path
                        abs_file_path = os.path.join(repo_path, rel_file_path)
                        if os.path.exists(abs_file_path):
                            await client.open_document(abs_file_path)
                        else:
                            self.logger.warning(f"Skipping non-existent file: {abs_file_path}")
            else:
                setup_results[language] = False
                self.logger.warning(f"No LSP server configured for {language}")
        
        return setup_results


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


# ==================== Static Analysis Helper Functions ====================

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


def detect_repository_languages(repo_path: str) -> Dict[str, List[str]]:
    """Detect all programming languages used in a repository"""
    language_files = defaultdict(list)
    
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


# ==================== Language-Specific Static Analysis Functions ====================

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


def format_java_file(file_path: str, available_tools: Dict[str, List[str]]) -> List[str]:
    """Format Java file using Google Java Format"""
    fixes_applied = []
    
    if 'google-java-format' in available_tools['formatters']:
        result = run_command_safe(['google-java-format', '--replace', file_path])
        if result['success']:
            fixes_applied.append('google_java_format')
    
    return fixes_applied


def format_go_file(file_path: str, available_tools: Dict[str, List[str]]) -> List[str]:
    """Format Go file using gofmt and goimports"""
    fixes_applied = []
    
    if 'gofmt' in available_tools['formatters']:
        result = run_command_safe(['gofmt', '-w', file_path])
        if result['success']:
            fixes_applied.append('gofmt')
    
    if 'goimports' in available_tools['formatters']:
        result = run_command_safe(['goimports', '-w', file_path])
        if result['success']:
            fixes_applied.append('goimports')
    
    return fixes_applied


def format_rust_file(file_path: str, available_tools: Dict[str, List[str]]) -> List[str]:
    """Format Rust file using rustfmt"""
    fixes_applied = []
    
    if 'rustfmt' in available_tools['formatters']:
        result = run_command_safe(['rustfmt', file_path])
        if result['success']:
            fixes_applied.append('rustfmt')
    
    return fixes_applied


def format_cpp_file(file_path: str, available_tools: Dict[str, List[str]]) -> List[str]:
    """Format C/C++ file using clang-format"""
    fixes_applied = []
    
    if 'clang-format' in available_tools['formatters']:
        result = run_command_safe(['clang-format', '-i', file_path])
        if result['success']:
            fixes_applied.append('clang_format')
    
    return fixes_applied


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
    
    elif language == 'java':
        fixes_applied = format_java_file(abs_file_path, available_tools)
        formatted = len(fixes_applied) > 0
    
    elif language == 'go':
        fixes_applied = format_go_file(abs_file_path, available_tools)
        formatted = len(fixes_applied) > 0
    
    elif language == 'rust':
        fixes_applied = format_rust_file(abs_file_path, available_tools)
        formatted = len(fixes_applied) > 0
    
    elif language in ['cpp', 'c']:
        fixes_applied = format_cpp_file(abs_file_path, available_tools)
        formatted = len(fixes_applied) > 0
    
    return StaticAnalysisResult(
        file_path=file_path,
        language=language,
        issues=issues,
        formatted=formatted,
        syntax_valid=syntax_valid,
        auto_fixes_applied=fixes_applied
    )


# ==================== MCP Tool Definitions ====================

@mcp.tool()
async def detect_empty_files(repo_path: str) -> str:
    """
    Detect empty files in the repository that may need implementation.
    
    Args:
        repo_path: Path to the repository to analyze
        
    Returns:
        JSON string with empty files information
    """
    try:
        if not os.path.exists(repo_path):
            return json.dumps({
                "status": "error",
                "message": f"Repository path does not exist: {repo_path}"
            })
        
        logger.info(f"Detecting empty files in: {repo_path}")
        
        empty_files = []
        potentially_empty_files = []
        
        for root, dirs, files in os.walk(repo_path):
            # Skip hidden directories and common build/cache directories
            dirs[:] = [d for d in dirs if not d.startswith('.') and d not in ['__pycache__', 'node_modules', 'target', 'build', 'dist']]
            
            for file in files:
                if file.startswith('.'):
                    continue
                    
                file_path = os.path.join(root, file)
                rel_file_path = os.path.relpath(file_path, repo_path)
                
                try:
                    file_size = os.path.getsize(file_path)
                    
                    # Check if file is completely empty
                    if file_size == 0:
                        empty_files.append({
                            "path": rel_file_path,
                            "size": 0,
                            "type": "completely_empty"
                        })
                    else:
                        # Check if file has only whitespace/comments
                        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                            content = f.read().strip()
                            
                        # Check for files with only comments or minimal content
                        if len(content) < 50:  # Very small files
                            lines = content.split('\n')
                            non_comment_lines = []
                            
                            for line in lines:
                                line = line.strip()
                                if line and not line.startswith('#') and not line.startswith('//') and not line.startswith('/*'):
                                    non_comment_lines.append(line)
                            
                            if len(non_comment_lines) <= 2:  # Only imports or very minimal code
                                potentially_empty_files.append({
                                    "path": rel_file_path,
                                    "size": file_size,
                                    "lines": len(lines),
                                    "non_comment_lines": len(non_comment_lines),
                                    "type": "minimal_content",
                                    "content_preview": content[:100] + "..." if len(content) > 100 else content
                                })
                                
                except Exception as e:
                    logger.warning(f"Error analyzing file {file_path}: {e}")
                    continue
        
        result = {
            "status": "success",
            "repo_path": repo_path,
            "empty_files": {
                "completely_empty": empty_files,
                "minimal_content": potentially_empty_files,
                "total_empty": len(empty_files),
                "total_minimal": len(potentially_empty_files)
            },
            "needs_implementation": len(empty_files) > 0 or len(potentially_empty_files) > 0,
            "recommendations": []
        }
        
        # Generate recommendations
        if len(empty_files) > 0:
            result["recommendations"].append(f"Implement {len(empty_files)} completely empty files")
        if len(potentially_empty_files) > 0:
            result["recommendations"].append(f"Complete implementation for {len(potentially_empty_files)} files with minimal content")
        
        return json.dumps(result, indent=2, ensure_ascii=False)
        
    except Exception as e:
        logger.error(f"Empty file detection failed: {e}")
        return json.dumps({
            "status": "error",
            "message": f"Empty file detection failed: {str(e)}"
        })


@mcp.tool()
async def detect_missing_files(repo_path: str) -> str:
    """
    Detect missing essential files based on project type and existing structure.
    
    Args:
        repo_path: Path to the repository to analyze
        
    Returns:
        JSON string with missing files analysis
    """
    try:
        if not os.path.exists(repo_path):
            return json.dumps({
                "status": "error",
                "message": f"Repository path does not exist: {repo_path}"
            })
        
        logger.info(f"Detecting missing files in: {repo_path}")
        
        # Get existing files and structure
        existing_files = []
        directories = []
        for root, dirs, files in os.walk(repo_path):
            dirs[:] = [d for d in dirs if not d.startswith('.') and d not in ['__pycache__', 'node_modules', 'target', 'build', 'dist']]
            for file in files:
                if not file.startswith('.'):
                    rel_path = os.path.relpath(os.path.join(root, file), repo_path)
                    existing_files.append(rel_path)
            for dir_name in dirs:
                rel_dir = os.path.relpath(os.path.join(root, dir_name), repo_path)
                directories.append(rel_dir)
        
        # Detect project type and characteristics
        project_info = _analyze_project_type(existing_files, directories)
        
        # Check for missing files based on project type
        missing_files = []
        
        # 1. Entry point analysis
        _check_entry_point_files(existing_files, project_info, missing_files)
        
        # 2. Dependencies file
        _check_dependency_files(existing_files, project_info, missing_files)
        
        # 3. Test files
        _check_test_files(existing_files, project_info, missing_files)
        
        # 4. Documentation
        _check_documentation_files(existing_files, project_info, missing_files)
        
        # 5. Configuration files
        _check_configuration_files(existing_files, project_info, missing_files)
        
        # 6. Project structure files
        _check_structure_files(existing_files, directories, project_info, missing_files)
        
        # Calculate completeness
        high_priority_missing = [f for f in missing_files if f["priority"] == "high"]
        completeness_score = max(0, 100 - (len(high_priority_missing) * 25 + len([f for f in missing_files if f["priority"] == "medium"]) * 15 + len([f for f in missing_files if f["priority"] == "low"]) * 5))
        
        result = {
            "status": "success",
            "repo_path": repo_path,
            "project_type": project_info["type"],
            "project_characteristics": project_info["characteristics"],
            "missing_files": missing_files,
            "analysis": {
                "total_missing": len(missing_files),
                "high_priority_missing": len(high_priority_missing),
                "completeness_score": completeness_score,
                "project_status": "incomplete" if high_priority_missing else "needs_improvement" if missing_files else "complete"
            },
            "existing_files_count": len(existing_files),
            "recommendations": _generate_recommendations(missing_files, project_info)
        }
        
        return json.dumps(result, indent=2, ensure_ascii=False)
        
    except Exception as e:
        logger.error(f"Missing file detection failed: {e}")
        return json.dumps({
            "status": "error",
            "message": f"Missing file detection failed: {str(e)}"
        })

def _analyze_project_type(existing_files, directories):
    """Analyze project type based on existing files and structure."""
    file_extensions = {}
    for file in existing_files:
        ext = os.path.splitext(file)[1].lower()
        file_extensions[ext] = file_extensions.get(ext, 0) + 1
    
    characteristics = []
    project_type = "unknown"
    
    # Python project detection
    if '.py' in file_extensions:
        characteristics.append("python")
        
        # Check for specific Python project types
        if any('setup.py' in f or 'pyproject.toml' in f for f in existing_files):
            characteristics.append("package")
            project_type = "python_package"
        elif any('app.py' in f or 'flask' in f.lower() or 'django' in f.lower() for f in existing_files):
            characteristics.append("web_app")
            project_type = "python_web_app"
        elif any('__init__.py' in f for f in existing_files):
            characteristics.append("module")
            project_type = "python_module"
        else:
            project_type = "python_script"
    
    # JavaScript/Node.js project
    elif '.js' in file_extensions or 'package.json' in existing_files:
        characteristics.append("javascript")
        if 'package.json' in existing_files:
            characteristics.append("node")
            project_type = "node_project"
        else:
            project_type = "javascript_project"
    
    # Other language detection
    elif '.java' in file_extensions:
        characteristics.append("java")
        project_type = "java_project"
    elif '.go' in file_extensions:
        characteristics.append("go")
        project_type = "go_project"
    elif '.rs' in file_extensions:
        characteristics.append("rust")
        project_type = "rust_project"
    
    # Check for additional characteristics
    if any('test' in d.lower() for d in directories):
        characteristics.append("has_test_directory")
    if any('doc' in d.lower() for d in directories):
        characteristics.append("has_docs_directory")
    if any('src' in d.lower() for d in directories):
        characteristics.append("has_src_directory")
    
    return {
        "type": project_type,
        "characteristics": characteristics,
        "file_extensions": file_extensions,
        "primary_language": max(file_extensions.keys(), key=file_extensions.get) if file_extensions else None
    }

def _check_entry_point_files(existing_files, project_info, missing_files):
    """Check for appropriate entry point files based on project type."""
    if project_info["type"] == "python_package":
        # For packages, check for __main__.py or setup.py entry points
        has_main = any('__main__.py' in f for f in existing_files)
        has_setup = any('setup.py' in f or 'pyproject.toml' in f for f in existing_files)
        if not has_main and not has_setup:
            missing_files.append({
                "type": "entry_point",
                "description": "Package entry point",
                "suggestions": ["__main__.py"],
                "priority": "medium",
                "reason": "Python package should have __main__.py for direct execution"
            })
    
    elif project_info["type"] == "python_web_app":
        # For web apps, prefer app.py
        web_entry_patterns = ['app.py', 'main.py', 'run.py', 'wsgi.py']
        has_web_entry = any(any(pattern in f for pattern in web_entry_patterns) for f in existing_files)
        if not has_web_entry:
            missing_files.append({
                "type": "entry_point",
                "description": "Web application entry point",
                "suggestions": ["app.py"],
                "priority": "high",
                "reason": "Web application needs an entry point file"
            })
    
    elif project_info["type"] == "python_script":
        # For scripts, check for main.py or similar
        script_patterns = ['main.py', 'run.py', '__main__.py']
        has_script_entry = any(any(pattern in f for pattern in script_patterns) for f in existing_files)
        if not has_script_entry and len([f for f in existing_files if f.endswith('.py')]) > 1:
            missing_files.append({
                "type": "entry_point",
                "description": "Script entry point",
                "suggestions": ["main.py"],
                "priority": "medium",
                "reason": "Multi-file Python project should have a clear entry point"
            })
    
    elif project_info["type"] == "node_project":
        # For Node.js, check package.json for main field or common entry files
        entry_patterns = ['index.js', 'main.js', 'app.js', 'server.js']
        has_node_entry = any(any(pattern in f for pattern in entry_patterns) for f in existing_files)
        if not has_node_entry:
            missing_files.append({
                "type": "entry_point",
                "description": "Node.js entry point",
                "suggestions": ["index.js"],
                "priority": "high",
                "reason": "Node.js project needs an entry point file"
            })

def _check_dependency_files(existing_files, project_info, missing_files):
    """Check for dependency management files."""
    if "python" in project_info["characteristics"]:
        python_dep_patterns = ['requirements.txt', 'setup.py', 'pyproject.toml', 'Pipfile', 'poetry.lock']
        has_python_deps = any(any(pattern in f for pattern in python_dep_patterns) for f in existing_files)
        if not has_python_deps:
            suggestion = "setup.py" if project_info["type"] == "python_package" else "requirements.txt"
            missing_files.append({
                "type": "dependencies",
                "description": "Python dependencies file",
                "suggestions": [suggestion],
                "priority": "high",
                "reason": "Python project needs dependency management"
            })
    
    elif "javascript" in project_info["characteristics"]:
        js_dep_patterns = ['package.json', 'yarn.lock', 'package-lock.json']
        has_js_deps = any(any(pattern in f for pattern in js_dep_patterns) for f in existing_files)
        if not has_js_deps:
            missing_files.append({
                "type": "dependencies",
                "description": "JavaScript dependencies file",
                "suggestions": ["package.json"],
                "priority": "high",
                "reason": "JavaScript project needs package.json for dependency management"
            })

def _check_test_files(existing_files, project_info, missing_files):
    """Check for test files based on project type."""
    test_patterns = ['test_', '_test.', 'tests/', 'test.py', '.test.js', '.spec.js']
    has_tests = any(any(pattern in f for pattern in test_patterns) for f in existing_files)
    
    if not has_tests:
        # Only suggest tests for projects with multiple files or packages
        file_count = len([f for f in existing_files if f.endswith(('.py', '.js', '.java', '.go', '.rs'))])
        if file_count > 2 or project_info["type"] in ["python_package", "node_project"]:
            test_suggestion = "tests/" if project_info["type"] == "python_package" else "test_main.py"
            missing_files.append({
                "type": "tests",
                "description": "Test files",
                "suggestions": [test_suggestion],
                "priority": "medium",
                "reason": "Project should include tests for validation"
            })

def _check_documentation_files(existing_files, project_info, missing_files):
    """Check for documentation files."""
    readme_patterns = ['README.md', 'README.txt', 'README.rst', 'readme.md']
    has_readme = any(any(pattern.lower() in f.lower() for pattern in readme_patterns) for f in existing_files)
    
    if not has_readme:
        missing_files.append({
            "type": "documentation",
            "description": "README file",
            "suggestions": ["README.md"],
            "priority": "high",
            "reason": "Project needs documentation for users and contributors"
        })

def _check_configuration_files(existing_files, project_info, missing_files):
    """Check for configuration files based on project complexity."""
    if project_info["type"] == "python_web_app":
        config_patterns = ['config.py', 'settings.py', '.env', 'config.json']
        has_config = any(any(pattern in f for pattern in config_patterns) for f in existing_files)
        if not has_config:
            missing_files.append({
                "type": "configuration",
                "description": "Configuration file",
                "suggestions": ["config.py", ".env"],
                "priority": "medium",
                "reason": "Web application should have configuration management"
            })

def _check_structure_files(existing_files, directories, project_info, missing_files):
    """Check for proper project structure files."""
    if "python" in project_info["characteristics"] and "module" in project_info["characteristics"]:
        # Check for __init__.py files in directories
        python_dirs = [d for d in directories if not any(exclude in d for exclude in ['test', 'doc', '__pycache__'])]
        missing_init_dirs = []
        
        for dir_path in python_dirs:
            init_file = os.path.join(dir_path, '__init__.py')
            if not any(init_file in f for f in existing_files):
                missing_init_dirs.append(dir_path)
        
        if missing_init_dirs:
            missing_files.append({
                "type": "structure",
                "description": "Python package __init__.py files",
                "suggestions": [f"{d}/__init__.py" for d in missing_init_dirs[:3]],
                "priority": "low",
                "reason": "Python directories should have __init__.py to be importable as modules"
            })

def _generate_recommendations(missing_files, project_info):
    """Generate prioritized recommendations."""
    recommendations = []
    
    # Priority order: high -> medium -> low
    for priority in ["high", "medium", "low"]:
        priority_files = [f for f in missing_files if f["priority"] == priority]
        for missing in priority_files:
            if missing["suggestions"]:
                recommendations.append({
                    "action": f"Create {missing['description']}",
                    "file": missing["suggestions"][0],
                    "priority": priority,
                    "reason": missing["reason"]
                })
    
    return recommendations


@mcp.tool()
async def generate_code_revision_report(repo_path: str, docs_path: Optional[str] = None) -> str:
    """
    Generate comprehensive code revision report combining empty files, missing files, and quality analysis.
    
    Args:
        repo_path: Path to the repository to analyze
        docs_path: Optional path to documentation
        
    Returns:
        JSON string with comprehensive revision report
    """
    try:
        if not os.path.exists(repo_path):
            return json.dumps({
                "status": "error",
                "message": f"Repository path does not exist: {repo_path}"
            })
        
        logger.info(f"Generating code revision report for: {repo_path}")
        
        # Run all analysis tools
        empty_files_result = await detect_empty_files(repo_path)
        missing_files_result = await detect_missing_files(repo_path)
        quality_result = await assess_code_quality(repo_path)
        
        # Parse results
        empty_files_data = json.loads(empty_files_result)
        missing_files_data = json.loads(missing_files_result)
        quality_data = json.loads(quality_result)
        
        if any(data["status"] != "success" for data in [empty_files_data, missing_files_data, quality_data]):
            return json.dumps({
                "status": "error",
                "message": "Failed to complete analysis for revision report"
            })
        
        # Compile revision tasks
        revision_tasks = []
        
        # Task 1: Implement empty files
        if empty_files_data["needs_implementation"]:
            empty_task = {
                "task_id": "implement_empty_files",
                "priority": "high",
                "description": "Implement empty and minimal content files",
                "details": {
                    "completely_empty": empty_files_data["empty_files"]["completely_empty"],
                    "minimal_content": empty_files_data["empty_files"]["minimal_content"]
                },
                "action_required": "Use write_multiple_files to implement",
                "estimated_files": empty_files_data["empty_files"]["total_empty"] + empty_files_data["empty_files"]["total_minimal"]
            }
            revision_tasks.append(empty_task)
        
        # Task 2: Create missing files
        if missing_files_data["missing_files"]:
            missing_task = {
                "task_id": "create_missing_files",
                "priority": "high",
                "description": "Create missing essential files",
                "details": missing_files_data["missing_files"],
                "action_required": "Create missing files with appropriate content",
                "estimated_files": len(missing_files_data["missing_files"])
            }
            revision_tasks.append(missing_task)
        
        # Task 3: Fix quality issues
        if quality_data["status"] == "success" and quality_data["assessment"]["overall_score"] < 80:
            quality_task = {
                "task_id": "improve_code_quality",
                "priority": "medium",
                "description": "Address code quality issues",
                "details": {
                    "overall_score": quality_data["assessment"]["overall_score"],
                    "complexity_issues": quality_data["assessment"]["complexity_issues"],
                    "style_issues": quality_data["assessment"]["style_issues"],
                    "potential_bugs": quality_data["assessment"]["potential_bugs"],
                    "security_issues": quality_data["assessment"]["security_issues"]
                },
                "action_required": "Refactor code to address quality issues",
                "estimated_files": len(set([issue.split(':')[0] for issues in [
                    quality_data["assessment"]["complexity_issues"],
                    quality_data["assessment"]["style_issues"], 
                    quality_data["assessment"]["potential_bugs"]
                ] for issue in issues]))
            }
            revision_tasks.append(quality_task)
        
        # Calculate overall project health
        total_issues = (
            empty_files_data["empty_files"]["total_empty"] +
            empty_files_data["empty_files"]["total_minimal"] +
            len(missing_files_data["missing_files"])
        )
        
        if total_issues == 0:
            project_health = "excellent"
        elif total_issues <= 2:
            project_health = "good"
        elif total_issues <= 5:
            project_health = "needs_work"
        else:
            project_health = "critical"
        
        result = {
            "status": "success",
            "repo_path": repo_path,
            "revision_report": {
                "timestamp": time.strftime('%Y-%m-%d %H:%M:%S'),
                "project_health": project_health,
                "total_issues": total_issues,
                "revision_tasks": revision_tasks,
                "task_summary": {
                    "high_priority_tasks": len([t for t in revision_tasks if t["priority"] == "high"]),
                    "medium_priority_tasks": len([t for t in revision_tasks if t["priority"] == "medium"]),
                    "total_tasks": len(revision_tasks)
                }
            },
            "detailed_analysis": {
                "empty_files": empty_files_data,
                "missing_files": missing_files_data,
                "code_quality": quality_data
            },
            "next_steps": []
        }
        
        # Generate next steps
        if revision_tasks:
            result["next_steps"].append("Execute Code Revise Agent to address identified issues")
            for task in sorted(revision_tasks, key=lambda x: {"high": 1, "medium": 2, "low": 3}[x["priority"]]):
                result["next_steps"].append(f"Priority {task['priority']}: {task['description']}")
        else:
            result["next_steps"].append("No major issues found - project appears complete")
        
        return json.dumps(result, indent=2, ensure_ascii=False)
        
    except Exception as e:
        logger.error(f"Code revision report generation failed: {e}")
        return json.dumps({
            "status": "error",
            "message": f"Code revision report generation failed: {str(e)}"
        })


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
async def generate_evaluation_summary(repo_path: str, docs_path: Optional[str] = None) -> str:
    """
    Generate comprehensive evaluation summary combining all analysis results.
    
    Args:
        repo_path: Path to the repository
        docs_path: Optional path to reproduction documentation
        
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
        
        # Run all analysis tools
        structure_result = await analyze_repo_structure(repo_path)
        deps_result = await detect_dependencies(repo_path)
        quality_result = await assess_code_quality(repo_path)
        docs_result = await evaluate_documentation(repo_path, docs_path)
        readiness_result = await check_reproduction_readiness(repo_path, docs_path)
        
        # Parse all results
        analyses = {}
        for name, result_str in [
            ("structure", structure_result),
            ("dependencies", deps_result),
            ("quality", quality_result),
            ("documentation", docs_result),
            ("reproduction_readiness", readiness_result)
        ]:
            try:
                analyses[name] = json.loads(result_str)
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
                        file_language = get_file_language(file)
                        
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
                        file_language = get_file_language(file)
                        
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
                    elif language == 'java':
                        fixes_applied = format_java_file(abs_file_path, available_tools)
                    elif language == 'go':
                        fixes_applied = format_go_file(abs_file_path, available_tools)
                    elif language == 'rust':
                        fixes_applied = format_rust_file(abs_file_path, available_tools)
                    elif language in ['cpp', 'c']:
                        fixes_applied = format_cpp_file(abs_file_path, available_tools)
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
            language = get_file_language(issue.file_path)
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


# ===== PHASE 4: ADVANCED ERROR ANALYSIS TOOLS =====

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


async def find_symbol_references_lsp(lsp_manager: LSPManager, repo_path: str, symbol_name: str, language: str = "python") -> List[Dict[str, Any]]:
    """
    Find symbol references across the repository using actual LSP
    
    Args:
        lsp_manager: LSP manager instance
        repo_path: Repository path
        symbol_name: Symbol to search for
        language: Programming language
        
    Returns:
        List of symbol reference information
    """
    references = []
    
    try:
        client = lsp_manager.get_client(language)
        if not client:
            logger.warning(f"No LSP client available for {language}")
            return []
        
        # First, find all occurrences of the symbol name in files
        language_files = []
        for root, dirs, files in os.walk(repo_path):
            dirs[:] = [d for d in dirs if d not in {'.git', '__pycache__', '.pytest_cache', 'node_modules'}]
            for file in files:
                file_path = os.path.join(root, file)
                if _is_language_file(file_path, language):
                    language_files.append(file_path)
        
        # Search for symbol in each file and get LSP references
        for file_path in language_files:
            try:
                # Open document in LSP
                await client.open_document(file_path)
                
                # Read file content to find potential symbol locations
                with open(file_path, 'r', encoding='utf-8') as f:
                    content = f.read()
                
                lines = content.split('\n')
                for line_idx, line in enumerate(lines):
                    if symbol_name in line:
                        char_idx = line.find(symbol_name)
                        if char_idx >= 0:
                            # Get LSP references for this position
                            lsp_refs = await client.find_references(file_path, line_idx, char_idx)
                            
                            for ref in lsp_refs:
                                uri = ref.uri
                                if uri.startswith('file://'):
                                    ref_file_path = uri[7:]  # Remove 'file://' prefix
                                else:
                                    ref_file_path = uri
                                
                                references.append({
                                    "symbol_name": symbol_name,
                                    "file_path": ref_file_path,
                                    "line": ref.range.get("start", {}).get("line", 0),
                                    "character": ref.range.get("start", {}).get("character", 0),
                                    "kind": "reference",
                                    "language": language
                                })
                
            except Exception as e:
                logger.warning(f"Failed to get LSP references for {file_path}: {e}")
        
        # Remove duplicates
        unique_refs = []
        seen = set()
        for ref in references:
            key = (ref["file_path"], ref["line"], ref["character"])
            if key not in seen:
                seen.add(key)
                unique_refs.append(ref)
        
        return unique_refs
        
    except Exception as e:
        logger.error(f"LSP symbol reference search failed: {e}")
        return []


def _is_language_file(file_path: str, language: str) -> bool:
    """Check if file is of specified language"""
    ext = os.path.splitext(file_path)[1].lower()
    language_extensions = {
        'python': ['.py'],
        'javascript': ['.js', '.jsx'],
        'typescript': ['.ts', '.tsx'],
        'java': ['.java'],
        'cpp': ['.cpp', '.cc', '.cxx'],
        'c': ['.c', '.h'],
        'rust': ['.rs'],
        'go': ['.go'],
        'php': ['.php'],
        'ruby': ['.rb'],
        'csharp': ['.cs']
    }
    return ext in language_extensions.get(language, [])


def find_symbol_references(repo_path: str, symbol_name: str, language: str = "python") -> List[LSPSymbolInfo]:
    """
    Find symbol references across the repository (AST-based fallback)
    
    Args:
        repo_path: Repository path
        symbol_name: Symbol to search for
        language: Programming language
        
    Returns:
        List of LSPSymbolInfo with symbol occurrences
    """
    symbols = []
    
    try:
        if language.lower() == "python":
            # Find all Python files
            python_files = []
            for root, dirs, files in os.walk(repo_path):
                dirs[:] = [d for d in dirs if d not in {'.git', '__pycache__', '.pytest_cache'}]
                for file in files:
                    if file.endswith('.py'):
                        python_files.append(os.path.join(root, file))
            
            # Search for symbol in each file
            for file_path in python_files:
                try:
                    with open(file_path, 'r', encoding='utf-8') as f:
                        content = f.read()
                        tree = ast.parse(content)
                    
                    lines = content.split('\n')
                    
                    for node in ast.walk(tree):
                        # Function definitions
                        if isinstance(node, ast.FunctionDef) and node.name == symbol_name:
                            symbols.append(LSPSymbolInfo(
                                name=symbol_name,
                                kind="function",
                                file_path=file_path,
                                line=node.lineno,
                                column=node.col_offset
                            ))
                        
                        # Class definitions
                        elif isinstance(node, ast.ClassDef) and node.name == symbol_name:
                            symbols.append(LSPSymbolInfo(
                                name=symbol_name,
                                kind="class",
                                file_path=file_path,
                                line=node.lineno,
                                column=node.col_offset
                            ))
                        
                        # Variable assignments
                        elif isinstance(node, ast.Assign):
                            for target in node.targets:
                                if isinstance(target, ast.Name) and target.id == symbol_name:
                                    symbols.append(LSPSymbolInfo(
                                        name=symbol_name,
                                        kind="variable",
                                        file_path=file_path,
                                        line=node.lineno,
                                        column=node.col_offset
                                    ))
                    
                    # Simple text search for references (fallback)
                    for i, line in enumerate(lines):
                        if symbol_name in line:
                            # Avoid duplicates by checking if we already found this location
                            line_num = i + 1
                            found_exact = any(s.line == line_num for s in symbols if s.file_path == file_path)
                            if not found_exact:
                                symbols.append(LSPSymbolInfo(
                                    name=symbol_name,
                                    kind="reference",
                                    file_path=file_path,
                                    line=line_num,
                                    column=line.find(symbol_name)
                                ))
                
                except Exception as e:
                    logger.warning(f"Failed to analyze symbols in {file_path}: {e}")
        
        return symbols
        
    except Exception as e:
        logger.error(f"Symbol reference search failed: {e}")
        return []


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
    Uses LSP for enhanced analysis when available, falls back to AST-based analysis
    
    Args:
        traceback_text: Raw error traceback from execution
        repo_path: Repository path
        execution_context: Optional context about what was being executed
        
    Returns:
        JSON string with comprehensive error analysis and remediation plan
    """
    try:
        # Try LSP-enhanced analysis first
        try:
            return await lsp_enhanced_error_analysis(traceback_text, repo_path, execution_context)
        except Exception as lsp_error:
            logger.warning(f"LSP-enhanced analysis failed, falling back to AST-based: {lsp_error}")
        
        # Fallback to original AST-based analysis
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
        
        logger.info(f"Generated error analysis report (fallback): {len(suspect_files)} suspect files identified")
        
        return json.dumps(result, indent=2, ensure_ascii=False)
        
    except Exception as e:
        logger.error(f"Error analysis report generation failed: {e}")
        return json.dumps({
            "status": "error",
            "message": f"Error analysis report generation failed: {str(e)}"
        })


# Global LSP manager instance (initialized when needed)
global_lsp_manager = None

async def get_or_create_lsp_manager(repo_path: str) -> LSPManager:
    """Get or create global LSP manager"""
    global global_lsp_manager
    
    if global_lsp_manager is None:
        global_lsp_manager = LSPManager(repo_path)
        # Set up LSP servers for detected languages
        await global_lsp_manager.setup_for_repository(repo_path)
    
    return global_lsp_manager


@mcp.tool()
async def setup_lsp_servers(repo_path: str) -> str:
    """
    Set up LSP servers for detected languages in repository
    
    Args:
        repo_path: Repository path
        
    Returns:
        JSON string with LSP server setup results
    """
    try:
        lsp_manager = await get_or_create_lsp_manager(repo_path)
        setup_results = await lsp_manager.setup_for_repository(repo_path)
        
        result = {
            "status": "success",
            "repo_path": repo_path,
            "lsp_servers": setup_results,
            "total_servers": len(setup_results),
            "successful_servers": len([r for r in setup_results.values() if r]),
            "failed_servers": len([r for r in setup_results.values() if not r])
        }
        
        logger.info(f"LSP setup completed: {result['successful_servers']}/{result['total_servers']} servers started")
        
        return json.dumps(result, indent=2, ensure_ascii=False)
        
    except Exception as e:
        logger.error(f"LSP setup failed: {e}")
        return json.dumps({
            "status": "error",
            "message": f"LSP setup failed: {str(e)}"
        })


@mcp.tool()
async def lsp_find_symbol_references(repo_path: str, symbol_name: str, language: str = "python") -> str:
    """
    Find symbol references using actual LSP
    
    Args:
        repo_path: Repository path
        symbol_name: Symbol name to search for
        language: Programming language (default: python)
        
    Returns:
        JSON string with LSP-based symbol reference information
    """
    try:
        lsp_manager = await get_or_create_lsp_manager(repo_path)
        references = await find_symbol_references_lsp(lsp_manager, repo_path, symbol_name, language)
        
        # Group by kind
        by_kind = defaultdict(list)
        for ref in references:
            kind = ref.get("kind", "reference")
            by_kind[kind].append(ref)
        
        # Group by file
        by_file = defaultdict(list)
        for ref in references:
            file_path = ref.get("file_path", "")
            by_file[file_path].append(ref)
        
        result = {
            "status": "success",
            "symbol_name": symbol_name,
            "language": language,
            "lsp_enabled": True,
            "total_references": len(references),
            "references_by_kind": {
                kind: [
                    {
                        "file_path": r["file_path"],
                        "line": r["line"],
                        "character": r["character"]
                    }
                    for r in refs
                ]
                for kind, refs in by_kind.items()
            },
            "references_by_file": {
                file_path: len(refs) for file_path, refs in by_file.items()
            },
            "all_references": references
        }
        
        logger.info(f"LSP symbol search completed: {len(references)} references found for '{symbol_name}'")
        
        return json.dumps(result, indent=2, ensure_ascii=False)
        
    except Exception as e:
        logger.error(f"LSP symbol reference search failed: {e}")
        return json.dumps({
            "status": "error",
            "message": f"LSP symbol reference search failed: {str(e)}"
        })


@mcp.tool()
async def lsp_get_diagnostics(repo_path: str, file_path: Optional[str] = None) -> str:
    """
    Get LSP diagnostics for files
    
    Args:
        repo_path: Repository path
        file_path: Optional specific file path (if None, gets diagnostics for all open files)
        
    Returns:
        JSON string with LSP diagnostic information
    """
    try:
        lsp_manager = await get_or_create_lsp_manager(repo_path)
        
        diagnostics = []
        
        if file_path:
            # Get diagnostics for specific file
            languages_detected = detect_repository_languages(repo_path)
            file_language = None
            
            for lang, files in languages_detected.items():
                if file_path in files:
                    file_language = lang
                    break
            
            if file_language:
                client = lsp_manager.get_client(file_language)
                if client:
                    await client.open_document(file_path)
                    file_diagnostics = await client.get_diagnostics(file_path)
                    diagnostics.extend([
                        {
                            "file_path": file_path,
                            "line": d.range.get("start", {}).get("line", 0),
                            "character": d.range.get("start", {}).get("character", 0),
                            "severity": d.severity,
                            "message": d.message,
                            "source": d.source,
                            "code": d.code
                        }
                        for d in file_diagnostics
                    ])
        else:
            # Get diagnostics for all languages
            for language, client in lsp_manager.clients.items():
                # This would require storing diagnostics from notifications
                # For now, return placeholder
                pass
        
        result = {
            "status": "success",
            "repo_path": repo_path,
            "target_file": file_path,
            "lsp_enabled": True,
            "total_diagnostics": len(diagnostics),
            "diagnostics_by_severity": {
                "error": len([d for d in diagnostics if d.get("severity") == 1]),
                "warning": len([d for d in diagnostics if d.get("severity") == 2]),
                "information": len([d for d in diagnostics if d.get("severity") == 3]),
                "hint": len([d for d in diagnostics if d.get("severity") == 4])
            },
            "diagnostics": diagnostics
        }
        
        logger.info(f"LSP diagnostics retrieved: {len(diagnostics)} issues found")
        
        return json.dumps(result, indent=2, ensure_ascii=False)
        
    except Exception as e:
        logger.error(f"LSP diagnostics retrieval failed: {e}")
        return json.dumps({
            "status": "error",
            "message": f"LSP diagnostics retrieval failed: {str(e)}"
        })


@mcp.tool()
async def lsp_get_code_actions(repo_path: str, file_path: str, start_line: int, end_line: int) -> str:
    """
    Get LSP code actions for a range in a file
    
    Args:
        repo_path: Repository path
        file_path: File path
        start_line: Start line number (0-based)
        end_line: End line number (0-based)
        
    Returns:
        JSON string with available LSP code actions
    """
    try:
        lsp_manager = await get_or_create_lsp_manager(repo_path)
        
        # Determine file language
        languages_detected = detect_repository_languages(repo_path)
        file_language = None
        
        for lang, files in languages_detected.items():
            if file_path in files:
                file_language = lang
                break
        
        if not file_language:
            return json.dumps({
                "status": "error",
                "message": f"Could not determine language for file: {file_path}"
            })
        
        client = lsp_manager.get_client(file_language)
        if not client:
            return json.dumps({
                "status": "error", 
                "message": f"No LSP client available for {file_language}"
            })
        
        # Open document
        await client.open_document(file_path)
        
        # Create range
        range_data = {
            "start": {"line": start_line, "character": 0},
            "end": {"line": end_line, "character": 0}
        }
        
        # Get code actions
        code_actions = await client.get_code_actions(file_path, range_data)
        
        result = {
            "status": "success",
            "file_path": file_path,
            "language": file_language,
            "range": {"start_line": start_line, "end_line": end_line},
            "lsp_enabled": True,
            "total_actions": len(code_actions),
            "code_actions": [
                {
                    "title": action.title,
                    "kind": action.kind,
                    "has_edit": action.edit is not None,
                    "has_command": action.command is not None
                }
                for action in code_actions
            ]
        }
        
        logger.info(f"LSP code actions retrieved: {len(code_actions)} actions available")
        
        return json.dumps(result, indent=2, ensure_ascii=False)
        
    except Exception as e:
        logger.error(f"LSP code actions retrieval failed: {e}")
        return json.dumps({
            "status": "error",
            "message": f"LSP code actions retrieval failed: {str(e)}"
        })


@mcp.tool()
async def lsp_enhanced_error_analysis(
    traceback_text: str, 
    repo_path: str, 
    execution_context: Optional[str] = None
) -> str:
    """
    Generate enhanced error analysis using LSP for precise symbol resolution
    
    Args:
        traceback_text: Raw error traceback from execution
        repo_path: Repository path
        execution_context: Optional context about what was being executed
        
    Returns:
        JSON string with LSP-enhanced error analysis and remediation plan
    """
    try:
        # 1. Parse traceback (same as before)
        traceback_analysis = parse_python_traceback(traceback_text, repo_path)
        
        # 2. Set up LSP for enhanced analysis
        lsp_manager = await get_or_create_lsp_manager(repo_path)
        
        # 3. Build import graph (same as before)
        import_graph = build_import_graph(repo_path, "python")
        
        # 4. Enhanced suspect file identification using LSP
        suspect_files = await identify_suspect_files_lsp(
            traceback_analysis, import_graph, repo_path, lsp_manager
        )
        
        # 5. Enhanced remediation suggestions using LSP
        remediation_suggestions = []
        
        if traceback_analysis.root_cause_file:
            remediation_suggestions.append(f"LSP Analysis: Start by examining the root cause file: {traceback_analysis.root_cause_file}")
        
        if suspect_files:
            top_suspect = suspect_files[0]
            remediation_suggestions.append(f"High priority (LSP-verified): Review {top_suspect.file_path} (confidence: {top_suspect.confidence_score:.2f})")
            
            # Get LSP-specific recommendations
            if top_suspect.related_symbols:
                symbol_names = [s.name for s in top_suspect.related_symbols[:3]]
                remediation_suggestions.append(f"LSP symbols to investigate: {', '.join(symbol_names)}")
        
        # 6. LSP-enhanced error classification
        if traceback_analysis.error_type:
            if "ImportError" in traceback_analysis.error_type or "ModuleNotFoundError" in traceback_analysis.error_type:
                remediation_suggestions.append("LSP recommendation: Check import statements and module availability using textDocument/definition")
            elif "AttributeError" in traceback_analysis.error_type:
                remediation_suggestions.append("LSP recommendation: Verify object attributes using textDocument/completion and hover")
            elif "TypeError" in traceback_analysis.error_type:
                remediation_suggestions.append("LSP recommendation: Check function signatures using textDocument/signatureHelp")
            elif "NameError" in traceback_analysis.error_type:
                remediation_suggestions.append("LSP recommendation: Check variable definitions using textDocument/references")
        
        # 7. Build LSP-enhanced call chain analysis
        call_chain = {}
        for location in traceback_analysis.error_locations:
            if location.file_path not in call_chain:
                call_chain[location.file_path] = []
            call_chain[location.file_path].append({
                "function": location.function_name,
                "line": location.line_number,
                "lsp_enhanced": True
            })
        
        # 8. Create comprehensive report
        result = {
            "status": "success",
            "lsp_enhanced": True,
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
                        "lsp_symbols": [
                            {
                                "name": sym.name,
                                "kind": sym.kind,
                                "detail": sym.detail
                            }
                            for sym in sf.related_symbols
                        ] if sf.related_symbols else [],
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
            "lsp_capabilities": {
                "symbol_resolution": True,
                "diagnostics": True,
                "code_actions": True,
                "references": True
            },
            "summary": {
                "total_suspect_files": len(suspect_files),
                "high_confidence_suspects": len([sf for sf in suspect_files if sf.confidence_score > 0.7]),
                "lsp_symbols_identified": sum(len(sf.related_symbols) if sf.related_symbols else 0 for sf in suspect_files),
                "error_classification": traceback_analysis.error_type,
                "remediation_priority": "high" if suspect_files and suspect_files[0].confidence_score > 0.8 else "medium"
            }
        }
        
        logger.info(f"LSP-enhanced error analysis completed: {len(suspect_files)} suspect files identified with LSP symbol resolution")
        
        return json.dumps(result, indent=2, ensure_ascii=False)
        
    except Exception as e:
        logger.error(f"LSP-enhanced error analysis failed: {e}")
        return json.dumps({
            "status": "error",
            "message": f"LSP-enhanced error analysis failed: {str(e)}"
        })


async def identify_suspect_files_lsp(
    traceback_analysis: TracebackAnalysis,
    import_graph: Dict[str, List[ImportRelationship]],
    repo_path: str,
    lsp_manager: LSPManager
) -> List[SuspectFile]:
    """
    Identify suspect files using LSP for enhanced symbol resolution
    """
    suspect_files = []
    file_scores = defaultdict(float)
    file_reasons = defaultdict(list)
    file_contexts = defaultdict(list)
    file_symbols = defaultdict(list)
    
    try:
        # 1. Direct files from traceback (highest priority)
        for location in traceback_analysis.error_locations:
            if location.file_path.startswith(repo_path):
                file_scores[location.file_path] += location.confidence * 10
                file_reasons[location.file_path].append(f"Direct error location: {location.function_name} line {location.line_number}")
                file_contexts[location.file_path].append(location)
                
                # LSP enhancement: Get symbols for error functions
                try:
                    language = "python"  # Could be detected dynamically
                    client = lsp_manager.get_client(language)
                    if client:
                        await client.open_document(location.file_path)
                        symbols = await client.get_symbols(location.file_path)
                        # Filter symbols related to error function
                        related_symbols = [s for s in symbols if s.name == location.function_name]
                        file_symbols[location.file_path].extend(related_symbols)
                except Exception as e:
                    logger.warning(f"Failed to get LSP symbols for {location.file_path}: {e}")
        
        # 2-3. Files that import/are imported by error-causing files (same as before)
        error_files = {loc.file_path for loc in traceback_analysis.error_locations 
                      if loc.file_path.startswith(repo_path)}
        
        for file_path, imports in import_graph.items():
            for import_rel in imports:
                for error_file in error_files:
                    if (import_rel.imported in error_file or 
                        any(part in import_rel.imported for part in error_file.split('/')[-1].split('.'))):
                        file_scores[file_path] += 3.0
                        file_reasons[file_path].append(f"Imports error-related module: {import_rel.imported}")
        
        # 4. Create SuspectFile objects with LSP symbols
        for file_path, score in file_scores.items():
            if score > 0.5:
                normalized_score = min(score / 20.0, 1.0)
                
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
                    suggested_focus_areas=focus_areas,
                    related_symbols=file_symbols[file_path]
                ))
        
        # Sort by confidence score (highest first)
        suspect_files.sort(key=lambda x: x.confidence_score, reverse=True)
        
        return suspect_files
        
    except Exception as e:
        logger.error(f"LSP-enhanced suspect file identification failed: {e}")
        return []


# Keep the original function for backward compatibility
@mcp.tool()
async def search_symbol_references(repo_path: str, symbol_name: str, language: str = "python") -> str:
    """
    Search for symbol references (fallback to AST-based analysis if LSP unavailable)
    
    Args:
        repo_path: Repository path
        symbol_name: Symbol name to search for
        language: Programming language (default: python)
        
    Returns:
        JSON string with symbol reference information
    """
    try:
        # Try LSP first
        try:
            return await lsp_find_symbol_references(repo_path, symbol_name, language)
        except Exception as lsp_error:
            logger.warning(f"LSP symbol search failed, falling back to AST: {lsp_error}")
        
        # Fallback to original AST-based implementation
        symbols = find_symbol_references(repo_path, symbol_name, language)
        
        # Group by kind
        by_kind = defaultdict(list)
        for symbol in symbols:
            by_kind[symbol.kind].append(symbol)
        
        result = {
            "status": "success",
            "symbol_name": symbol_name,
            "language": language,
            "lsp_enabled": False,
            "fallback_method": "AST-based",
            "total_references": len(symbols),
            "references_by_kind": {
                kind: [
                    {
                        "file_path": s.file_path,
                        "line": s.line,
                        "column": s.column
                    }
                    for s in refs
                ]
                for kind, refs in by_kind.items()
            },
            "all_references": [
                {
                    "file_path": s.file_path,
                    "line": s.line,
                    "column": s.column,
                    "kind": s.kind
                }
                for s in symbols
            ]
        }
        
        logger.info(f"Symbol search completed (fallback): {len(symbols)} references found for '{symbol_name}'")
        
        return json.dumps(result, indent=2, ensure_ascii=False)
        
    except Exception as e:
        logger.error(f"Symbol reference search failed: {e}")
        return json.dumps({
            "status": "error",
            "message": f"Symbol reference search failed: {str(e)}"
        })


# ===== PRECISE CODE REVISION TOOLS (Enhanced Code Revise Agent) =====

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


def parse_llm_code_changes(llm_response: str) -> List[Dict[str, Any]]:
    """
    Parse LLM response containing code changes in a structured format
    
    Expected format:
    ```python:file_path.py:start_line:end_line
    new code content
    ```
    
    Args:
        llm_response: LLM response containing structured code changes
        
    Returns:
        List of parsed code changes
    """
    changes = []
    
    try:
        import re
        
        # Pattern to match code blocks with metadata
        pattern = r'```(\w+):([^:]+):(\d+):(\d+)\n(.*?)\n```'
        matches = re.findall(pattern, llm_response, re.DOTALL)
        
        for match in matches:
            language, file_path, start_line, end_line, content = match
            
            changes.append({
                "file_path": file_path.strip(),
                "start_line": int(start_line),
                "end_line": int(end_line),
                "new_content": content.strip(),
                "language": language,
                "change_type": "replace"
            })
        
        # Also look for simpler diff-style format
        diff_pattern = r'--- ([^\n]+)\n\+\+\+ ([^\n]+)\n(.*?)(?=---|$)'
        diff_matches = re.findall(diff_pattern, llm_response, re.DOTALL | re.MULTILINE)
        
        for match in diff_matches:
            old_file, new_file, diff_content = match
            
            # Parse the diff content for line changes
            lines = diff_content.split('\n')
            for i, line in enumerate(lines):
                if line.startswith('@@'):
                    # Parse hunk header
                    hunk_match = re.match(r'@@ -(\d+),?\d* \+(\d+),?\d* @@', line)
                    if hunk_match:
                        old_start = int(hunk_match.group(1))
                        new_start = int(hunk_match.group(2))
                        
                        # Collect the change content
                        change_lines = []
                        j = i + 1
                        while j < len(lines) and not lines[j].startswith('@@'):
                            if lines[j].startswith('+') and not lines[j].startswith('+++'):
                                change_lines.append(lines[j][1:])  # Remove + prefix
                            j += 1
                        
                        if change_lines:
                            changes.append({
                                "file_path": new_file.strip(),
                                "start_line": new_start,
                                "end_line": new_start + len(change_lines) - 1,
                                "new_content": '\n'.join(change_lines),
                                "language": "python",  # Default assumption
                                "change_type": "replace"
                            })
        
        return changes
        
    except Exception as e:
        logger.error(f"Failed to parse LLM code changes: {e}")
        return []


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


@mcp.tool()
async def generate_targeted_code_revision(
    suspect_files: List[str],
    error_context: str,
    repo_path: str,
    revision_scope: str = "minimal"
) -> str:
    """
    Generate targeted code revision for specific suspect files
    
    Args:
        suspect_files: List of file paths to target for revision
        error_context: Error context/traceback information
        repo_path: Repository path
        revision_scope: Scope of revision ("minimal", "moderate", "comprehensive")
        
    Returns:
        JSON string with targeted revision tasks
    """
    try:
        revision_tasks = []
        
        for file_path in suspect_files:
            full_file_path = os.path.join(repo_path, file_path) if not os.path.isabs(file_path) else file_path
            
            if not os.path.exists(full_file_path):
                continue
            
            # Analyze the file for potential issues
            try:
                with open(full_file_path, 'r', encoding='utf-8') as f:
                    content = f.read()
                
                # Create targeted revision task
                task = {
                    "task_id": f"revise_{os.path.basename(file_path).replace('.', '_')}",
                    "file_path": file_path,
                    "revision_type": "targeted_fix",
                    "scope": revision_scope,
                    "priority": "high",
                    "error_context": error_context,
                    "current_content_lines": len(content.splitlines()),
                    "revision_instructions": [
                        f"Review {file_path} for issues identified in error analysis",
                        "Focus on error-prone areas identified by traceback analysis",
                        "Apply precise fixes with minimal code changes",
                        "Ensure changes don't break existing functionality"
                    ],
                    "focus_areas": []
                }
                
                # Add specific focus areas based on error context
                if "ImportError" in error_context or "ModuleNotFoundError" in error_context:
                    task["focus_areas"].append("Import statements and module dependencies")
                
                if "AttributeError" in error_context:
                    task["focus_areas"].append("Object attribute access and method calls")
                
                if "TypeError" in error_context:
                    task["focus_areas"].append("Function signatures and type consistency")
                
                if "NameError" in error_context:
                    task["focus_areas"].append("Variable definitions and scope")
                
                # Add general focus areas if none specific
                if not task["focus_areas"]:
                    task["focus_areas"] = [
                        "Code logic and control flow",
                        "Error handling and validation",
                        "Function and class definitions"
                    ]
                
                revision_tasks.append(task)
                
            except Exception as e:
                logger.warning(f"Failed to analyze {file_path} for revision: {e}")
        
        result = {
            "status": "success",
            "revision_scope": revision_scope,
            "total_files_targeted": len(suspect_files),
            "revision_tasks_generated": len(revision_tasks),
            "revision_tasks": revision_tasks,
            "summary": {
                "files_with_tasks": len(revision_tasks),
                "average_content_lines": sum(t["current_content_lines"] for t in revision_tasks) / len(revision_tasks) if revision_tasks else 0,
                "focus_areas_distribution": {}
            }
        }
        
        # Calculate focus areas distribution
        focus_area_counts = {}
        for task in revision_tasks:
            for area in task["focus_areas"]:
                focus_area_counts[area] = focus_area_counts.get(area, 0) + 1
        result["summary"]["focus_areas_distribution"] = focus_area_counts
        
        logger.info(f"Generated {len(revision_tasks)} targeted revision tasks for {len(suspect_files)} files")
        
        return json.dumps(result, indent=2, ensure_ascii=False)
        
    except Exception as e:
        logger.error(f"Targeted code revision generation failed: {e}")
        return json.dumps({
            "status": "error",
            "message": f"Targeted code revision generation failed: {str(e)}"
        })


# ===== SANDBOX AGENT INTERFACE (Phase 4+) =====

@mcp.tool()
async def execute_in_sandbox(
    repo_path: str,
    command: str,
    timeout: int = 30,
    capture_output: bool = True
) -> str:
    """
    Execute command in sandbox environment
    
    Args:
        repo_path: Repository path
        command: Command to execute
        timeout: Execution timeout in seconds
        capture_output: Whether to capture stdout/stderr
        
    Returns:
        JSON string with execution results
    """
    try:
        logger.info(f"Executing in sandbox: {command} in {repo_path}")
        
        start_time = time.time()
        
        # Execute the command in the specified directory
        result = subprocess.run(
            command,
            shell=True,
            cwd=repo_path,
            capture_output=capture_output,
            text=True,
            timeout=timeout
        )
        
        execution_time = time.time() - start_time
        
        # Extract error traceback if present
        error_traceback = None
        if result.returncode != 0 and result.stderr:
            if "Traceback" in result.stderr or "Error:" in result.stderr:
                error_traceback = result.stderr
        
        success = result.returncode == 0
        
        logger.info(f"Sandbox execution {'succeeded' if success else 'failed'} in {execution_time:.2f}s")
        
        return json.dumps({
            "status": "success",
            "sandbox_result": {
                "success": success,
                "stdout": result.stdout,
                "stderr": result.stderr,
                "exit_code": result.returncode,
                "execution_time": execution_time,
                "command": command,
                "working_directory": repo_path,
                "error_traceback": error_traceback
            }
        }, indent=2)
        
    except subprocess.TimeoutExpired:
        logger.error(f"Sandbox execution timed out after {timeout} seconds")
        return json.dumps({
            "status": "error",
            "message": f"Command timed out after {timeout} seconds",
            "sandbox_result": {
                "success": False,
                "stdout": "",
                "stderr": f"Command timed out after {timeout} seconds",
                "exit_code": -1,
                "execution_time": timeout,
                "command": command,
                "working_directory": repo_path
            }
        }, indent=2)
        
    except Exception as e:
        logger.error(f"Sandbox execution failed: {str(e)}")
        return json.dumps({
            "status": "error",
            "message": f"Execution failed: {str(e)}",
            "sandbox_result": {
                "success": False,
                "stdout": "",
                "stderr": str(e),
                "exit_code": -1,
                "execution_time": 0.0,
                "command": command,
                "working_directory": repo_path
            }
        }, indent=2)


@mcp.tool()
async def run_code_validation(repo_path: str, test_command: Optional[str] = None) -> str:
    """
    Run code validation in sandbox environment
    
    Args:
        repo_path: Repository path
        test_command: Optional test command (defaults to common test patterns)
        
    Returns:
        JSON string with validation results
    """
    try:
        logger.info(f"Running code validation in {repo_path}")
        
        validation_results = {
            "validation_success": True,
            "test_results": {},
            "lint_results": {},
            "import_errors": [],
            "runtime_errors": [],
            "suggestions": []
        }
        
        # Auto-detect test patterns if no command provided
        if test_command is None:
            test_commands = []
            
            # Check for Python test patterns
            if os.path.exists(os.path.join(repo_path, "requirements.txt")) or any(f.endswith('.py') for f in os.listdir(repo_path) if os.path.isfile(os.path.join(repo_path, f))):
                # Python project detected
                if os.path.exists(os.path.join(repo_path, "pytest.ini")) or any("test_" in f for f in os.listdir(repo_path)):
                    test_commands.append("python -m pytest --tb=short")
                if any(f.startswith("test") and f.endswith(".py") for f in os.listdir(repo_path)):
                    test_commands.append("python -m unittest discover")
                
                # Basic import check
                test_commands.append("python -c 'import sys; print(f\"Python {sys.version} available\")'")
            
            # Check for JavaScript test patterns  
            if os.path.exists(os.path.join(repo_path, "package.json")):
                test_commands.extend(["npm test", "npm start"])
            
            # Fallback: basic file listing
            if not test_commands:
                test_commands = ["ls -la", "find . -name '*.py' -o -name '*.js' -o -name '*.ts' | head -10"]
        else:
            test_commands = [test_command]
        
        # Execute validation commands
        for cmd in test_commands[:3]:  # Limit to 3 commands to avoid excessive execution
            try:
                logger.info(f"Executing validation command: {cmd}")
                
                result = subprocess.run(
                    cmd,
                    shell=True,
                    cwd=repo_path,
                    capture_output=True,
                    text=True,
                    timeout=60  # 1 minute timeout for validation
                )
                
                if result.returncode == 0:
                    validation_results["test_results"][cmd] = {
                        "success": True,
                        "stdout": result.stdout,
                        "stderr": result.stderr
                    }
                    logger.info(f"Validation command succeeded: {cmd}")
                else:
                    validation_results["test_results"][cmd] = {
                        "success": False,
                        "stdout": result.stdout,
                        "stderr": result.stderr
                    }
                    validation_results["validation_success"] = False
                    
                    # Extract runtime errors
                    if result.stderr:
                        if "ImportError" in result.stderr or "ModuleNotFoundError" in result.stderr:
                            validation_results["import_errors"].append(result.stderr)
                        else:
                            validation_results["runtime_errors"].append(result.stderr)
                    
                    logger.warning(f"Validation command failed: {cmd}")
                    
            except subprocess.TimeoutExpired:
                validation_results["test_results"][cmd] = {
                    "success": False,
                    "stdout": "",
                    "stderr": f"Command timed out after 60 seconds"
                }
                validation_results["validation_success"] = False
                validation_results["runtime_errors"].append(f"Timeout: {cmd}")
                
            except Exception as e:
                validation_results["test_results"][cmd] = {
                    "success": False,
                    "stdout": "",
                    "stderr": str(e)
                }
                validation_results["validation_success"] = False
                validation_results["runtime_errors"].append(f"Error executing {cmd}: {str(e)}")
        
        # Generate suggestions based on results
        if validation_results["import_errors"]:
            validation_results["suggestions"].append("Install missing dependencies listed in requirements.txt or package.json")
        if validation_results["runtime_errors"]:
            validation_results["suggestions"].append("Fix runtime errors to enable proper project execution")
        if not validation_results["test_results"]:
            validation_results["suggestions"].append("No test commands could be executed - check project structure")
        
        logger.info(f"Code validation completed. Success: {validation_results['validation_success']}")
        
        return json.dumps({
            "status": "success",
            "validation_results": validation_results
        }, indent=2)
        
    except Exception as e:
        logger.error(f"Code validation failed: {str(e)}")
        return json.dumps({
            "status": "error",
            "message": f"Code validation failed: {str(e)}",
            "validation_results": {
                "validation_success": False,
                "test_results": {},
                "lint_results": {},
                "import_errors": [],
                "runtime_errors": [str(e)],
                "suggestions": ["Fix validation errors and retry"]
            }
        }, indent=2)


@mcp.tool()
async def lsp_generate_code_fixes(
    repo_path: str, 
    file_path: str, 
    start_line: int, 
    end_line: int, 
    error_context: Optional[str] = None
) -> str:
    """
    Generate LSP-based code fixes for a range in a file
    
    Args:
        repo_path: Repository path
        file_path: File path to fix
        start_line: Start line number (0-based)
        end_line: End line number (0-based)
        error_context: Optional error context to help generate fixes
        
    Returns:
        JSON string with LSP-generated code fixes
    """
    try:
        lsp_manager = await get_or_create_lsp_manager(repo_path)
        
        # Determine file language
        languages_detected = detect_repository_languages(repo_path)
        file_language = None
        
        for lang, files in languages_detected.items():
            if file_path in files:
                file_language = lang
                break
        
        if not file_language:
            return json.dumps({
                "status": "error",
                "message": f"Could not determine language for file: {file_path}"
            })
        
        client = lsp_manager.get_client(file_language)
        if not client:
            return json.dumps({
                "status": "error", 
                "message": f"No LSP client available for {file_language}"
            })
        
        # Open document and get diagnostics
        await client.open_document(file_path)
        diagnostics = await client.get_diagnostics(file_path)
        
        # Filter diagnostics for the specified range
        range_diagnostics = []
        for diag in diagnostics:
            diag_line = diag.range.get("start", {}).get("line", 0)
            if start_line <= diag_line <= end_line:
                range_diagnostics.append(diag)
        
        # Get code actions for the range
        range_data = {
            "start": {"line": start_line, "character": 0},
            "end": {"line": end_line, "character": 0}
        }
        
        code_actions = await client.get_code_actions(file_path, range_data, range_diagnostics)
        
        # Generate fix proposals
        fix_proposals = []
        for action in code_actions:
            if action.edit:
                fix_proposals.append({
                    "title": action.title,
                    "kind": action.kind,
                    "edit": action.edit,
                    "confidence": 0.9,  # LSP actions are high confidence
                    "description": f"LSP-suggested fix: {action.title}"
                })
            elif action.command:
                fix_proposals.append({
                    "title": action.title,
                    "kind": action.kind,
                    "command": action.command,
                    "confidence": 0.8,
                    "description": f"LSP command: {action.title}"
                })
        
        result = {
            "status": "success",
            "file_path": file_path,
            "language": file_language,
            "range": {"start_line": start_line, "end_line": end_line},
            "lsp_enhanced": True,
            "error_context": error_context,
            "diagnostics_found": len(range_diagnostics),
            "total_fixes": len(fix_proposals),
            "fix_proposals": fix_proposals,
            "lsp_capabilities_used": ["diagnostics", "codeAction"]
        }
        
        logger.info(f"LSP code fixes generated: {len(fix_proposals)} fixes for {file_path}:{start_line}-{end_line}")
        
        return json.dumps(result, indent=2, ensure_ascii=False)
        
    except Exception as e:
        logger.error(f"LSP code fix generation failed: {e}")
        return json.dumps({
            "status": "error",
            "message": f"LSP code fix generation failed: {str(e)}"
        })


@mcp.tool()
async def lsp_apply_workspace_edit(
    repo_path: str,
    workspace_edit: str
) -> str:
    """
    Apply LSP workspace edit to files
    
    Args:
        repo_path: Repository path
        workspace_edit: JSON string containing LSP WorkspaceEdit
        
    Returns:
        JSON string with application results
    """
    try:
        lsp_manager = await get_or_create_lsp_manager(repo_path)
        edit_data = json.loads(workspace_edit)
        
        applied_changes = []
        failed_changes = []
        
        # Apply document changes
        if "documentChanges" in edit_data:
            for change in edit_data["documentChanges"]:
                try:
                    file_uri = change.get("textDocument", {}).get("uri", "")
                    if file_uri.startswith("file://"):
                        file_path = file_uri[7:]
                    else:
                        file_path = file_uri
                    
                    edits = change.get("edits", [])
                    
                    # Read current file content
                    with open(file_path, 'r', encoding='utf-8') as f:
                        content = f.read()
                    
                    lines = content.split('\n')
                    
                    # Apply edits in reverse order to maintain line numbers
                    edits.sort(key=lambda e: e.get("range", {}).get("start", {}).get("line", 0), reverse=True)
                    
                    for edit in edits:
                        range_data = edit.get("range", {})
                        new_text = edit.get("newText", "")
                        start_line = range_data.get("start", {}).get("line", 0)
                        start_char = range_data.get("start", {}).get("character", 0)
                        end_line = range_data.get("end", {}).get("line", 0)
                        end_char = range_data.get("end", {}).get("character", 0)
                        
                        # Apply the edit
                        if start_line == end_line:
                            # Single line edit
                            line = lines[start_line]
                            lines[start_line] = line[:start_char] + new_text + line[end_char:]
                        else:
                            # Multi-line edit
                            start_line_content = lines[start_line][:start_char]
                            end_line_content = lines[end_line][end_char:]
                            
                            new_lines = new_text.split('\n')
                            if len(new_lines) == 1:
                                lines[start_line:end_line+1] = [start_line_content + new_text + end_line_content]
                            else:
                                new_lines[0] = start_line_content + new_lines[0]
                                new_lines[-1] = new_lines[-1] + end_line_content
                                lines[start_line:end_line+1] = new_lines
                    
                    # Write back to file
                    new_content = '\n'.join(lines)
                    with open(file_path, 'w', encoding='utf-8') as f:
                        f.write(new_content)
                    
                    # Generate diff
                    diff = generate_code_diff(content, new_content, file_path)
                    
                    applied_changes.append({
                        "file_path": file_path,
                        "edits_applied": len(edits),
                        "diff": diff
                    })
                    
                except Exception as e:
                    failed_changes.append({
                        "file_path": change.get("textDocument", {}).get("uri", "unknown"),
                        "error": str(e)
                    })
        
        result = {
            "status": "success",
            "lsp_enhanced": True,
            "total_files_changed": len(applied_changes),
            "total_failures": len(failed_changes),
            "applied_changes": applied_changes,
            "failed_changes": failed_changes,
            "summary": {
                "successful_applications": len(applied_changes),
                "failed_applications": len(failed_changes),
                "total_edits": sum(change.get("edits_applied", 0) for change in applied_changes)
            }
        }
        
        logger.info(f"LSP workspace edit applied: {len(applied_changes)} files changed, {len(failed_changes)} failures")
        
        return json.dumps(result, indent=2, ensure_ascii=False)
        
    except Exception as e:
        logger.error(f"LSP workspace edit application failed: {e}")
        return json.dumps({
            "status": "error",
            "message": f"LSP workspace edit application failed: {str(e)}"
        })


# Run the server
if __name__ == "__main__":
    mcp.run()
