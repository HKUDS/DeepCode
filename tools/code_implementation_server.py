#!/usr/bin/env python3
"""
Code Implementation MCP Server

This MCP server provides core functions needed for paper code reproduction:
1. File read/write operations
2. Code execution and testing
3. Code search and analysis
4. Iterative improvement support

Usage:
python tools/code_implementation_server.py
"""

import os
import subprocess
import json
import sys
import io
from pathlib import Path
import re
from typing import Dict, Any
import tempfile
import shutil
import logging
from datetime import datetime

# Set standard output encoding to UTF-8
if sys.stdout.encoding != "utf-8":
    try:
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8")
            sys.stderr.reconfigure(encoding="utf-8")
        else:
            sys.stdout = io.TextIOWrapper(sys.stdout.detach(), encoding="utf-8")
            sys.stderr = io.TextIOWrapper(sys.stderr.detach(), encoding="utf-8")
    except Exception as e:
        print(f"Warning: Could not set UTF-8 encoding: {e}")

# Import MCP related modules
from mcp.server.fastmcp import FastMCP

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Create FastMCP server instance
mcp = FastMCP("code-implementation-server")

# Global variables: workspace directory and operation history
WORKSPACE_DIR = None
OPERATION_HISTORY = []
CURRENT_FILES = {}


def initialize_workspace(workspace_dir: str = None):
    """
    Initialize workspace

    By default, the workspace will be set by the workflow via the set_workspace tool to:
    {plan_file_parent}/generate_code

    Args:
        workspace_dir: Optional workspace directory path
    """
    global WORKSPACE_DIR
    if workspace_dir is None:
        # Default to generate_code directory under current directory, but don't create immediately
        # This default value will be overridden by workflow via set_workspace tool
        WORKSPACE_DIR = Path.cwd() / "generate_code"
        # logger.info(f"Workspace initialized (default value, will be overridden by workflow): {WORKSPACE_DIR}")
        # logger.info("Note: Actual workspace will be set by workflow via set_workspace tool to {plan_file_parent}/generate_code")
    else:
        WORKSPACE_DIR = Path(workspace_dir).resolve()
        # Only create when explicitly specified
        WORKSPACE_DIR.mkdir(parents=True, exist_ok=True)
        logger.info(f"Workspace initialized: {WORKSPACE_DIR}")


def ensure_workspace_exists():
    """Ensure workspace directory exists"""
    global WORKSPACE_DIR
    if WORKSPACE_DIR is None:
        initialize_workspace()

    # Create workspace directory (if it doesn't exist)
    if not WORKSPACE_DIR.exists():
        WORKSPACE_DIR.mkdir(parents=True, exist_ok=True)
        logger.info(f"Workspace directory created: {WORKSPACE_DIR}")


def validate_path(path: str) -> Path:
    """Validate if path is within workspace"""
    if WORKSPACE_DIR is None:
        initialize_workspace()

    full_path = (WORKSPACE_DIR / path).resolve()
    if not str(full_path).startswith(str(WORKSPACE_DIR)):
        raise ValueError(f"Path {path} is outside workspace scope")
    return full_path


def log_operation(action: str, details: Dict[str, Any]):
    """Log operation history"""
    OPERATION_HISTORY.append(
        {"timestamp": datetime.now().isoformat(), "action": action, "details": details}
    )


# ==================== File Operation Tools ====================


@mcp.tool()
async def read_file(
    file_path: str, start_line: int = None, end_line: int = None
) -> str:
    """
    Read file content, supports specifying line number range

    Args:
        file_path: File path, relative to workspace
        start_line: Starting line number (1-based, optional)
        end_line: Ending line number (1-based, optional)

    Returns:
        JSON string of file content or error message
    """
    try:
        full_path = validate_path(file_path)

        if not full_path.exists():
            result = {"status": "error", "message": f"File does not exist: {file_path}"}
            log_operation(
                "read_file_error", {"file_path": file_path, "error": "file_not_found"}
            )
            return json.dumps(result, ensure_ascii=False, indent=2)

        with open(full_path, "r", encoding="utf-8") as f:
            lines = f.readlines()

        # 处理行号范围
        if start_line is not None or end_line is not None:
            start_idx = (start_line - 1) if start_line else 0
            end_idx = end_line if end_line else len(lines)
            lines = lines[start_idx:end_idx]

        content = "".join(lines)

        result = {
            "status": "success",
            "content": content,
            "file_path": file_path,
            "total_lines": len(lines),
            "size_bytes": len(content.encode("utf-8")),
        }

        log_operation(
            "read_file",
            {
                "file_path": file_path,
                "start_line": start_line,
                "end_line": end_line,
                "lines_read": len(lines),
            },
        )

        return json.dumps(result, ensure_ascii=False, indent=2)

    except Exception as e:
        result = {
            "status": "error",
            "message": f"Failed to read file: {str(e)}",
            "file_path": file_path,
        }
        log_operation("read_file_error", {"file_path": file_path, "error": str(e)})
        return json.dumps(result, ensure_ascii=False, indent=2)


@mcp.tool()
async def write_file(
    file_path: str, content: str, create_dirs: bool = True, create_backup: bool = False
) -> str:
    """
    Write content to file

    Args:
        file_path: File path, relative to workspace
        content: Content to write to file
        create_dirs: Whether to create directories if they don't exist
        create_backup: Whether to create backup file if file already exists

    Returns:
        JSON string of operation result
    """
    try:
        full_path = validate_path(file_path)

        # Create directories (if needed)
        if create_dirs:
            full_path.parent.mkdir(parents=True, exist_ok=True)

        # Backup existing file (only when explicitly requested)
        backup_created = False
        if full_path.exists() and create_backup:
            backup_path = full_path.with_suffix(full_path.suffix + ".backup")
            shutil.copy2(full_path, backup_path)
            backup_created = True

        # Write file
        with open(full_path, "w", encoding="utf-8") as f:
            f.write(content)

        # Update current file record
        CURRENT_FILES[file_path] = {
            "last_modified": datetime.now().isoformat(),
            "size_bytes": len(content.encode("utf-8")),
            "lines": len(content.split("\n")),
        }

        result = {
            "status": "success",
            "message": f"File written successfully: {file_path}",
            "file_path": file_path,
            "size_bytes": len(content.encode("utf-8")),
            "lines_written": len(content.split("\n")),
            "backup_created": backup_created,
        }

        log_operation(
            "write_file",
            {
                "file_path": file_path,
                "size_bytes": len(content.encode("utf-8")),
                "lines": len(content.split("\n")),
                "backup_created": backup_created,
            },
        )

        return json.dumps(result, ensure_ascii=False, indent=2)

    except Exception as e:
        result = {
            "status": "error",
            "message": f"Failed to write file: {str(e)}",
            "file_path": file_path,
        }
        log_operation("write_file_error", {"file_path": file_path, "error": str(e)})
        return json.dumps(result, ensure_ascii=False, indent=2)


# ==================== Code Execution Tools ====================


@mcp.tool()
async def execute_python(code: str, timeout: int = 30) -> str:
    """
    Execute Python code and return output

    Args:
        code: Python code to execute
        timeout: Timeout in seconds

    Returns:
        JSON string of execution result
    """
    try:
        # Create temporary file
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".py", delete=False, encoding="utf-8"
        ) as f:
            f.write(code)
            temp_file = f.name

        try:
            # Ensure workspace directory exists
            ensure_workspace_exists()

            # Execute Python code
            result = subprocess.run(
                [sys.executable, temp_file],
                cwd=WORKSPACE_DIR,
                capture_output=True,
                text=True,
                timeout=timeout,
                encoding="utf-8",
            )

            execution_result = {
                "status": "success" if result.returncode == 0 else "error",
                "return_code": result.returncode,
                "stdout": result.stdout,
                "stderr": result.stderr,
                "timeout": timeout,
            }

            if result.returncode != 0:
                execution_result["message"] = "Python code execution failed"
            else:
                execution_result["message"] = "Python code execution successful"

            log_operation(
                "execute_python",
                {
                    "return_code": result.returncode,
                    "stdout_length": len(result.stdout),
                    "stderr_length": len(result.stderr),
                },
            )

            return json.dumps(execution_result, ensure_ascii=False, indent=2)

        finally:
            # Clean up temporary file
            os.unlink(temp_file)

    except subprocess.TimeoutExpired:
        result = {
            "status": "error",
            "message": f"Python code execution timeout ({timeout}秒)",
            "timeout": timeout,
        }
        log_operation("execute_python_timeout", {"timeout": timeout})
        return json.dumps(result, ensure_ascii=False, indent=2)

    except Exception as e:
        result = {
            "status": "error",
            "message": f"Python code execution failed: {str(e)}",
        }
        log_operation("execute_python_error", {"error": str(e)})
        return json.dumps(result, ensure_ascii=False, indent=2)


@mcp.tool()
async def execute_bash(command: str, timeout: int = 30) -> str:
    """
    Execute bash command

    Args:
        command: Bash command to execute
        timeout: Timeout in seconds

    Returns:
        JSON string of execution result
    """
    try:
        # 安全检查：禁止危险命令
        dangerous_commands = ["rm -rf", "sudo", "chmod 777", "mkfs", "dd if="]
        if any(dangerous in command.lower() for dangerous in dangerous_commands):
            result = {
                "status": "error",
                "message": f"Dangerous command execution prohibited: {command}",
            }
            log_operation(
                "execute_bash_blocked",
                {"command": command, "reason": "dangerous_command"},
            )
            return json.dumps(result, ensure_ascii=False, indent=2)

        # Ensure workspace directory exists
        ensure_workspace_exists()

        # Execute command
        result = subprocess.run(
            command,
            shell=True,
            cwd=WORKSPACE_DIR,
            capture_output=True,
            text=True,
            timeout=timeout,
            encoding="utf-8",
        )

        execution_result = {
            "status": "success" if result.returncode == 0 else "error",
            "return_code": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "command": command,
            "timeout": timeout,
        }

        if result.returncode != 0:
            execution_result["message"] = "Bash command execution failed"
        else:
            execution_result["message"] = "Bash command execution successful"

        log_operation(
            "execute_bash",
            {
                "command": command,
                "return_code": result.returncode,
                "stdout_length": len(result.stdout),
                "stderr_length": len(result.stderr),
            },
        )

        return json.dumps(execution_result, ensure_ascii=False, indent=2)

    except subprocess.TimeoutExpired:
        result = {
            "status": "error",
            "message": f"Bash command execution timeout ({timeout} seconds)",
            "command": command,
            "timeout": timeout,
        }
        log_operation("execute_bash_timeout", {"command": command, "timeout": timeout})
        return json.dumps(result, ensure_ascii=False, indent=2)

    except Exception as e:
        result = {
            "status": "error",
            "message": f"Failed to execute bash command: {str(e)}",
            "command": command,
        }
        log_operation("execute_bash_error", {"command": command, "error": str(e)})
        return json.dumps(result, ensure_ascii=False, indent=2)


@mcp.tool()
async def read_code_mem(file_path: str) -> str:
    """
    Check if file summary exists in implement_code_summary.md

    Args:
        file_path: File path to check for summary information in implement_code_summary.md

    Returns:
        Summary information if available
    """
    try:
        if not file_path:
            result = {"status": "error", "message": "file_path parameter is required"}
            log_operation("read_code_mem_error", {"error": "missing_file_path"})
            return json.dumps(result, ensure_ascii=False, indent=2)

        # Ensure workspace exists
        ensure_workspace_exists()

        # Look for implement_code_summary.md in the workspace
        current_path = Path(WORKSPACE_DIR)
        summary_file_path = current_path.parent / "implement_code_summary.md"

        if not summary_file_path.exists():
            result = {
                "status": "no_summary",
                "file_path": file_path,
                "message": "No summary file found.",
                # "recommendation": f"read_file(file_path='{file_path}')"
            }
            log_operation(
                "read_code_mem", {"file_path": file_path, "status": "no_summary_file"}
            )
            return json.dumps(result, ensure_ascii=False, indent=2)

        # Read the summary file
        with open(summary_file_path, "r", encoding="utf-8") as f:
            summary_content = f.read()

        if not summary_content.strip():
            result = {
                "status": "no_summary",
                "file_path": file_path,
                "message": "Summary file is empty.",
                # "recommendation": f"read_file(file_path='{file_path}')"
            }
            log_operation(
                "read_code_mem", {"file_path": file_path, "status": "empty_summary"}
            )
            return json.dumps(result, ensure_ascii=False, indent=2)

        # Extract file-specific section from summary
        file_section = _extract_file_section_from_summary(summary_content, file_path)

        if file_section:
            result = {
                "status": "summary_found",
                "file_path": file_path,
                "summary_content": file_section,
                "message": f"Summary information found for {file_path} in implement_code_summary.md",
            }
            log_operation(
                "read_code_mem",
                {
                    "file_path": file_path,
                    "status": "summary_found",
                    "section_length": len(file_section),
                },
            )
            return json.dumps(result, ensure_ascii=False, indent=2)
        else:
            result = {
                "status": "no_summary",
                "file_path": file_path,
                "message": f"No summary found for {file_path} in implement_code_summary.md",
                # "recommendation": f"Use read_file tool to read the actual file: read_file(file_path='{file_path}')"
            }
            log_operation(
                "read_code_mem", {"file_path": file_path, "status": "no_match"}
            )
            return json.dumps(result, ensure_ascii=False, indent=2)

    except Exception as e:
        result = {
            "status": "error",
            "message": f"Failed to check code memory: {str(e)}",
            "file_path": file_path,
            # "recommendation": "Use read_file tool instead"
        }
        log_operation("read_code_mem_error", {"file_path": file_path, "error": str(e)})
        return json.dumps(result, ensure_ascii=False, indent=2)


def _extract_file_section_from_summary(
    summary_content: str, target_file_path: str
) -> str:
    """
    Extract the specific section for a file from the summary content

    Args:
        summary_content: Full summary content
        target_file_path: Path of the target file

    Returns:
        File-specific section or None if not found
    """
    import re

    # Normalize the target path for comparison
    normalized_target = _normalize_file_path(target_file_path)

    # Pattern to match implementation sections with separator lines
    section_pattern = r"={80}\s*\n## IMPLEMENTATION File ([^;]+); ROUND \d+\s*\n={80}(.*?)(?=\n={80}|\Z)"

    matches = re.findall(section_pattern, summary_content, re.DOTALL)

    for file_path_in_summary, section_content in matches:
        file_path_in_summary = file_path_in_summary.strip()
        section_content = section_content.strip()

        # Normalize the path from summary for comparison
        normalized_summary_path = _normalize_file_path(file_path_in_summary)

        # Check if paths match using multiple strategies
        if _paths_match(
            normalized_target,
            normalized_summary_path,
            target_file_path,
            file_path_in_summary,
        ):
            # Return the complete section with proper formatting
            file_section = f"""================================================================================
## IMPLEMENTATION File {file_path_in_summary}; ROUND [X]
================================================================================

{section_content}

---
*Extracted from implement_code_summary.md*"""
            return file_section

    # If no section-based match, try alternative parsing method
    return _extract_file_section_alternative(summary_content, target_file_path)


def _normalize_file_path(file_path: str) -> str:
    """Normalize file path for comparison"""
    # Remove leading/trailing slashes and convert to lowercase
    normalized = file_path.strip("/").lower()
    # Replace backslashes with forward slashes
    normalized = normalized.replace("\\", "/")

    # Remove common prefixes to make matching more flexible
    common_prefixes = ["rice/", "src/", "./rice/", "./src/", "./"]
    for prefix in common_prefixes:
        if normalized.startswith(prefix):
            normalized = normalized[len(prefix) :]
            break

    return normalized


def _paths_match(
    normalized_target: str,
    normalized_summary: str,
    original_target: str,
    original_summary: str,
) -> bool:
    """Check if two file paths match using multiple strategies"""

    # Strategy 1: Exact normalized match
    if normalized_target == normalized_summary:
        return True

    # Strategy 2: Basename match (filename only)
    target_basename = os.path.basename(original_target)
    summary_basename = os.path.basename(original_summary)
    if target_basename == summary_basename and len(target_basename) > 4:
        return True

    # Strategy 3: Suffix match (remove common prefixes and compare)
    target_suffix = _remove_common_prefixes(normalized_target)
    summary_suffix = _remove_common_prefixes(normalized_summary)
    if target_suffix == summary_suffix:
        return True

    # Strategy 4: Ends with match
    if normalized_target.endswith(normalized_summary) or normalized_summary.endswith(
        normalized_target
    ):
        return True

    # Strategy 5: Contains match for longer paths
    if len(normalized_target) > 10 and normalized_target in normalized_summary:
        return True
    if len(normalized_summary) > 10 and normalized_summary in normalized_target:
        return True

    return False


def _remove_common_prefixes(file_path: str) -> str:
    """Remove common prefixes from file path"""
    prefixes_to_remove = ["rice/", "src/", "core/", "./"]
    path = file_path

    for prefix in prefixes_to_remove:
        if path.startswith(prefix):
            path = path[len(prefix) :]

    return path


def _extract_file_section_alternative(
    summary_content: str, target_file_path: str
) -> str:
    """Alternative method to extract file section using simpler pattern matching"""

    # Get the basename for fallback matching
    target_basename = os.path.basename(target_file_path)

    # Split by separator lines to get individual sections
    sections = summary_content.split("=" * 80)

    for i, section in enumerate(sections):
        if "## IMPLEMENTATION File" in section:
            # Extract the file path from the header
            lines = section.strip().split("\n")
            for line in lines:
                if "## IMPLEMENTATION File" in line:
                    # Extract file path between "File " and "; ROUND"
                    try:
                        file_part = line.split("File ")[1].split("; ROUND")[0].strip()

                        # Check if this matches our target
                        if (
                            _normalize_file_path(target_file_path)
                            == _normalize_file_path(file_part)
                            or target_basename == os.path.basename(file_part)
                            or target_file_path in file_part
                            or file_part.endswith(target_file_path)
                        ):
                            # Get the next section which contains the content
                            if i + 1 < len(sections):
                                content_section = sections[i + 1].strip()
                                return f"""================================================================================
## IMPLEMENTATION File {file_part}
================================================================================

{content_section}

---
*Extracted from implement_code_summary.md using alternative method*"""
                    except (IndexError, AttributeError):
                        continue

    return None


# ==================== Code Search Tools ====================


@mcp.tool()
async def search_code(
    pattern: str,
    file_pattern: str = "*.json",
    use_regex: bool = False,
    search_directory: str = None,
) -> str:
    """
    Search patterns in code files

    Args:
        pattern: Search pattern
        file_pattern: File pattern (e.g., '*.py')
        use_regex: Whether to use regular expressions
        search_directory: Specify search directory (optional, uses WORKSPACE_DIR if not specified)

    Returns:
        JSON string of search results
    """
    try:
        # Determine search directory
        if search_directory:
            # If search directory is specified, use the specified directory
            if os.path.isabs(search_directory):
                search_path = Path(search_directory)
            else:
                # Relative path, relative to current working directory
                search_path = Path.cwd() / search_directory
        else:
            # 如果没有指定Search directory，使用默认的WORKSPACE_DIR
            ensure_workspace_exists()
            search_path = WORKSPACE_DIR

        # 检查Search directory是否存在
        if not search_path.exists():
            result = {
                "status": "error",
                "message": f"Search directory不存在: {search_path}",
                "pattern": pattern,
            }
            return json.dumps(result, ensure_ascii=False, indent=2)

        import glob

        # Get matching files
        file_paths = glob.glob(str(search_path / "**" / file_pattern), recursive=True)

        matches = []
        total_files_searched = 0

        for file_path in file_paths:
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    lines = f.readlines()

                total_files_searched += 1
                relative_path = os.path.relpath(file_path, search_path)

                for line_num, line in enumerate(lines, 1):
                    if use_regex:
                        if re.search(pattern, line):
                            matches.append(
                                {
                                    "file": relative_path,
                                    "line_number": line_num,
                                    "line_content": line.strip(),
                                    "match_type": "regex",
                                }
                            )
                    else:
                        if pattern.lower() in line.lower():
                            matches.append(
                                {
                                    "file": relative_path,
                                    "line_number": line_num,
                                    "line_content": line.strip(),
                                    "match_type": "substring",
                                }
                            )

            except Exception as e:
                logger.warning(f"Error searching file {file_path}: {e}")
                continue

        result = {
            "status": "success",
            "pattern": pattern,
            "file_pattern": file_pattern,
            "use_regex": use_regex,
            "search_directory": str(search_path),
            "total_matches": len(matches),
            "total_files_searched": total_files_searched,
            "matches": matches[:50],  # 限制返回前50个匹配
        }

        if len(matches) > 50:
            result["note"] = f"显示前50个匹配，总共找到{len(matches)}个匹配"

        log_operation(
            "search_code",
            {
                "pattern": pattern,
                "file_pattern": file_pattern,
                "use_regex": use_regex,
                "search_directory": str(search_path),
                "total_matches": len(matches),
                "files_searched": total_files_searched,
            },
        )

        return json.dumps(result, ensure_ascii=False, indent=2)

    except Exception as e:
        result = {
            "status": "error",
            "message": f"Code search failed: {str(e)}",
            "pattern": pattern,
        }
        log_operation("search_code_error", {"pattern": pattern, "error": str(e)})
        return json.dumps(result, ensure_ascii=False, indent=2)


# ==================== File Structure Tools ====================


@mcp.tool()
async def get_file_structure(directory: str = ".", max_depth: int = 5) -> str:
    """
    Get directory file structure

    Args:
        directory: Directory path, relative to workspace
        max_depth: 最大遍历深度

    Returns:
        JSON string of file structure
    """
    try:
        ensure_workspace_exists()

        if directory == ".":
            target_dir = WORKSPACE_DIR
        else:
            target_dir = validate_path(directory)

        if not target_dir.exists():
            result = {
                "status": "error",
                "message": f"Directory does not exist: {directory}",
            }
            return json.dumps(result, ensure_ascii=False, indent=2)

        def scan_directory(path: Path, current_depth: int = 0) -> Dict[str, Any]:
            """Recursively scan directory"""
            if current_depth >= max_depth:
                return {"type": "directory", "name": path.name, "truncated": True}

            items = []
            try:
                for item in sorted(path.iterdir()):
                    relative_path = os.path.relpath(item, WORKSPACE_DIR)

                    if item.is_file():
                        file_info = {
                            "type": "file",
                            "name": item.name,
                            "path": relative_path,
                            "size_bytes": item.stat().st_size,
                            "extension": item.suffix,
                        }
                        items.append(file_info)
                    elif item.is_dir() and not item.name.startswith("."):
                        dir_info = scan_directory(item, current_depth + 1)
                        dir_info["path"] = relative_path
                        items.append(dir_info)
            except PermissionError:
                pass

            return {
                "type": "directory",
                "name": path.name,
                "items": items,
                "item_count": len(items),
            }

        structure = scan_directory(target_dir)

        # 统计信息
        def count_items(node):
            if node["type"] == "file":
                return {"files": 1, "directories": 0}
            else:
                counts = {"files": 0, "directories": 1}
                for item in node.get("items", []):
                    item_counts = count_items(item)
                    counts["files"] += item_counts["files"]
                    counts["directories"] += item_counts["directories"]
                return counts

        counts = count_items(structure)

        result = {
            "status": "success",
            "directory": directory,
            "max_depth": max_depth,
            "structure": structure,
            "summary": {
                "total_files": counts["files"],
                "total_directories": counts["directories"]
                - 1,  # Exclude root directory
            },
        }

        log_operation(
            "get_file_structure",
            {
                "directory": directory,
                "max_depth": max_depth,
                "total_files": counts["files"],
                "total_directories": counts["directories"] - 1,
            },
        )

        return json.dumps(result, ensure_ascii=False, indent=2)

    except Exception as e:
        result = {
            "status": "error",
            "message": f"Failed to get file structure: {str(e)}",
            "directory": directory,
        }
        log_operation(
            "get_file_structure_error", {"directory": directory, "error": str(e)}
        )
        return json.dumps(result, ensure_ascii=False, indent=2)


# ==================== Workspace Management Tools ====================


@mcp.tool()
async def set_workspace(workspace_path: str) -> str:
    """
    Set workspace directory

    Called by workflow to set workspace to: {plan_file_parent}/generate_code
    This ensures all file operations are executed relative to the correct project directory

    Args:
        workspace_path: Workspace path (Usually {plan_file_parent}/generate_code)

    Returns:
        JSON string of operation result
    """
    try:
        global WORKSPACE_DIR
        new_workspace = Path(workspace_path).resolve()

        # Create directory (if it does not exist)
        new_workspace.mkdir(parents=True, exist_ok=True)

        old_workspace = WORKSPACE_DIR
        WORKSPACE_DIR = new_workspace

        logger.info(f"New Workspace: {WORKSPACE_DIR}")

        result = {
            "status": "success",
            "message": f"Workspace setup successful: {workspace_path}",
            "new_workspace": str(WORKSPACE_DIR),
        }

        log_operation(
            "set_workspace",
            {
                "old_workspace": str(old_workspace) if old_workspace else None,
                "new_workspace": str(WORKSPACE_DIR),
                "workspace_alignment": "plan_file_parent/generate_code",
            },
        )

        return json.dumps(result, ensure_ascii=False, indent=2)

    except Exception as e:
        result = {
            "status": "error",
            "message": f"Failed to set workspace: {str(e)}",
            "workspace_path": workspace_path,
        }
        log_operation(
            "set_workspace_error", {"workspace_path": workspace_path, "error": str(e)}
        )
        return json.dumps(result, ensure_ascii=False, indent=2)


@mcp.tool()
async def get_operation_history(last_n: int = 10) -> str:
    """
    Get operation history

    Args:
        last_n: Return the last N operations

    Returns:
        JSON string of operation history
    """
    try:
        recent_history = (
            OPERATION_HISTORY[-last_n:] if last_n > 0 else OPERATION_HISTORY
        )

        result = {
            "status": "success",
            "total_operations": len(OPERATION_HISTORY),
            "returned_operations": len(recent_history),
            "workspace": str(WORKSPACE_DIR) if WORKSPACE_DIR else None,
            "history": recent_history,
        }

        return json.dumps(result, ensure_ascii=False, indent=2)

    except Exception as e:
        result = {
            "status": "error",
            "message": f"Failed to get operation history: {str(e)}",
        }
        return json.dumps(result, ensure_ascii=False, indent=2)


# ==================== Server Initialization ====================


def main():
    """Start MCP server"""
    print("🚀 Code Implementation MCP Server")
    print(
        "📝 Paper Code Implementation Tool Server / Paper Code Implementation Tool Server"
    )
    print("")
    print("Available tools / Available tools:")
    # print("  • read_file           - Read file contents / Read file contents")
    print(
        "  • read_code_mem       - Read code summary from implement_code_summary.md / Read code summary from implement_code_summary.md"
    )
    print("  • write_file          - Write file contents / Write file contents")
    print("  • execute_python      - Execute Python code / Execute Python code")
    print("  • execute_bash        - Execute bash command / Execute bash commands")
    print("  • search_code         - Search code patterns / Search code patterns")
    print("  • get_file_structure  - Get file structure / Get file structure")
    print("  • set_workspace       - Set workspace / Set workspace")
    print("  • get_operation_history - Get operation history / Get operation history")
    print("")
    print("🔧 Server starting...")

    # Initialize default workspace
    initialize_workspace()

    # Start server
    mcp.run()


if __name__ == "__main__":
    main()
