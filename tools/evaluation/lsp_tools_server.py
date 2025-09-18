#!/usr/bin/env python3
"""
LSP Tools MCP Server

This module provides LSP (Language Server Protocol) based tools for advanced code analysis.
Contains tools for symbol resolution, diagnostics, and code fixes using actual language servers.
"""

import os
import json
import asyncio
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
mcp = FastMCP("lsp-tools")


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
        self.diagnostics_storage = {}  # Store diagnostics by file URI
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
                # Handle diagnostics notification
                await self._handle_diagnostics(message.get("params", {}))
                
    async def _handle_diagnostics(self, params: Dict[str, Any]):
        """Handle diagnostics notification from LSP server"""
        try:
            uri = params.get("uri", "")
            diagnostics_data = params.get("diagnostics", [])
            
            # Convert to LSPDiagnostic objects
            diagnostics = []
            for diag_data in diagnostics_data:
                diagnostic = LSPDiagnostic(
                    range=diag_data.get("range", {}),
                    severity=diag_data.get("severity", 1),
                    code=diag_data.get("code"),
                    message=diag_data.get("message", ""),
                    source=diag_data.get("source")
                )
                diagnostics.append(diagnostic)
            
            # Store diagnostics for this file
            self.diagnostics_storage[uri] = diagnostics
            self.logger.debug(f"Stored {len(diagnostics)} diagnostics for {uri}")
            
        except Exception as e:
            self.logger.error(f"Failed to handle diagnostics: {e}")
    
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
    
    async def get_diagnostics(self, file_path: str, wait_timeout: float = 2.0) -> List[LSPDiagnostic]:
        """Get diagnostics for a file"""
        try:
            # Convert file path to URI
            file_uri = f"file://{os.path.abspath(file_path)}"
            
            # Wait a bit for diagnostics to arrive if file was just opened
            initial_count = len(self.diagnostics_storage.get(file_uri, []))
            if initial_count == 0 and wait_timeout > 0:
                await asyncio.sleep(wait_timeout)
            
            # Return stored diagnostics for this file
            diagnostics = self.diagnostics_storage.get(file_uri, [])
            self.logger.debug(f"Retrieved {len(diagnostics)} diagnostics for {file_path}")
            return diagnostics
            
        except Exception as e:
            self.logger.error(f"Failed to get diagnostics for {file_path}: {e}")
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


def detect_repository_languages(repo_path: str) -> Dict[str, List[str]]:
    """Detect all programming languages used in a repository"""
    from collections import defaultdict
    
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
            
            # Check if file_path is relative or absolute
            if not os.path.isabs(file_path):
                file_path = os.path.join(repo_path, file_path)
            
            # Find the relative path for language detection
            rel_file_path = os.path.relpath(file_path, repo_path)
            
            for lang, files in languages_detected.items():
                if rel_file_path in files or file_path in files:
                    file_language = lang
                    break
            
            if file_language:
                client = lsp_manager.get_client(file_language)
                if client:
                    await client.open_document(file_path)
                    # Send didSave notification to trigger diagnostics
                    await client._send_notification("textDocument/didSave", {
                        "textDocument": {
                            "uri": f"file://{os.path.abspath(file_path)}"
                        }
                    })
                    # Wait longer for diagnostics to be received
                    file_diagnostics = await client.get_diagnostics(file_path, wait_timeout=5.0)
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
        file_language = _determine_file_language(repo_path, file_path)
        
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
        file_language = _determine_file_language(repo_path, file_path)
        
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
                    from difflib import unified_diff
                    diff = '\n'.join(unified_diff(
                        content.splitlines(),
                        new_content.splitlines(),
                        fromfile=f"a/{file_path}",
                        tofile=f"b/{file_path}",
                        lineterm=""
                    ))
                    
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

def _determine_file_language(repo_path: str, file_path: str) -> Optional[str]:
    """Determine the programming language of a file"""
    # Check if file_path is relative or absolute
    if not os.path.isabs(file_path):
        abs_file_path = os.path.join(repo_path, file_path)
    else:
        abs_file_path = file_path
    
    # Find the relative path for language detection
    rel_file_path = os.path.relpath(abs_file_path, repo_path)
    
    # Detect languages in repository
    languages_detected = detect_repository_languages(repo_path)
    
    for lang, files in languages_detected.items():
        if rel_file_path in files or abs_file_path in files:
            return lang
    
    return None


# Run the server
if __name__ == "__main__":
    mcp.run()
