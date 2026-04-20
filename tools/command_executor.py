#!/usr/bin/env python3
"""
Command Executor MCP Tool / 命令执行器 MCP 工具

专门负责执行LLM生成的shell命令来创建文件树结构
Specialized in executing LLM-generated shell commands to create file tree structures
"""

import platform
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# Force UTF-8 on stdio so emoji prints don't kill the server on Windows
# consoles whose default encoding is GBK / cp936. See tools/git_command.py
# for the full rationale.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass

from mcp.server.models import InitializationOptions
import mcp.types as types
from mcp.server import NotificationOptions, Server
import mcp.server.stdio

IS_WINDOWS = platform.system() == "Windows"

app = Server("command-executor")


def _try_native_execute(command: str, cwd: Path) -> Optional[Tuple[int, str, str]]:
    """Try to execute common file-tree commands natively (no shell).

    Handles Unix-style commands so they work on Windows where cmd.exe would
    misinterpret flags like ``-p`` as directory names. Returns
    ``(returncode, stdout, stderr)`` when the command is handled, otherwise
    ``None`` so the caller can fall back to running through the system shell.
    """
    try:
        tokens = shlex.split(command, posix=True)
    except ValueError:
        return None
    if not tokens:
        return None

    cmd = tokens[0]
    args = tokens[1:]
    flags = [a for a in args if a.startswith("-") and a != "-"]
    paths = [a for a in args if not a.startswith("-")]

    def _resolve(p: str) -> Path:
        pp = Path(p)
        return pp if pp.is_absolute() else (cwd / pp)

    try:
        if cmd == "mkdir":
            for p in paths:
                _resolve(p).mkdir(parents=True, exist_ok=True)
            return 0, f"Created {len(paths)} directory/directories", ""

        if cmd == "touch":
            count = 0
            for p in paths:
                target = _resolve(p)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.touch(exist_ok=True)
                count += 1
            return 0, f"Touched {count} file(s)", ""

        if cmd == "rm":
            recursive = any(("r" in f) or ("R" in f) for f in flags)
            force = any("f" in f for f in flags)
            removed = 0
            for p in paths:
                target = _resolve(p)
                if target.is_dir():
                    if recursive:
                        shutil.rmtree(target, ignore_errors=force)
                        removed += 1
                    else:
                        if not force:
                            return 1, "", f"rm: cannot remove '{p}': Is a directory"
                elif target.exists():
                    target.unlink()
                    removed += 1
                elif not force:
                    return 1, "", f"rm: cannot remove '{p}': No such file or directory"
            return 0, f"Removed {removed} item(s)", ""

        if cmd in ("cp", "copy"):
            recursive = any(("r" in f) or ("R" in f) for f in flags)
            if len(paths) < 2:
                return None
            *srcs, dst = paths
            dst_path = _resolve(dst)
            for s in srcs:
                sp = _resolve(s)
                if sp.is_dir():
                    if not recursive:
                        return 1, "", f"cp: -r not specified; omitting directory '{s}'"
                    target = (
                        dst_path / sp.name
                        if dst_path.exists() and dst_path.is_dir()
                        else dst_path
                    )
                    shutil.copytree(sp, target, dirs_exist_ok=True)
                else:
                    if dst_path.exists() and dst_path.is_dir():
                        shutil.copy2(sp, dst_path / sp.name)
                    else:
                        dst_path.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(sp, dst_path)
            return 0, f"Copied {len(srcs)} item(s)", ""

        if cmd in ("mv", "move"):
            if len(paths) < 2:
                return None
            *srcs, dst = paths
            dst_path = _resolve(dst)
            for s in srcs:
                sp = _resolve(s)
                if dst_path.exists() and dst_path.is_dir():
                    shutil.move(str(sp), str(dst_path / sp.name))
                else:
                    dst_path.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(sp), str(dst_path))
            return 0, f"Moved {len(srcs)} item(s)", ""

    except Exception as e:
        return 1, "", f"{cmd}: {e}"

    return None


_PLATFORM_HINT = (
    f"Current host OS: {platform.system()} ({platform.platform()}). "
    "Common Unix file-tree commands (mkdir -p, touch, rm -rf, cp -r, mv) are "
    "auto-translated to native cross-platform operations, so you may use them "
    "directly. Avoid shell-specific syntax like heredocs or process substitution. "
    "Prefer one filesystem operation per line."
)


@app.list_tools()
async def handle_list_tools() -> list[types.Tool]:
    """List available tools."""
    return [
        types.Tool(
            name="execute_commands",
            description=(
                "Execute a list of shell commands to build a file tree structure.\n"
                f"{_PLATFORM_HINT}\n\n"
                "Args:\n"
                "    commands: shell commands, one per line\n"
                "    working_directory: working directory for command execution\n\n"
                "Returns: execution results and a detailed report."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "commands": {
                        "type": "string",
                        "title": "Commands",
                        "description": "要执行的shell命令列表，每行一个命令",
                    },
                    "working_directory": {
                        "type": "string",
                        "title": "Working Directory",
                        "description": "执行命令的工作目录",
                    },
                },
                "required": ["commands", "working_directory"],
            },
        ),
        types.Tool(
            name="execute_single_command",
            description=(
                "Execute a single shell command.\n"
                f"{_PLATFORM_HINT}\n\n"
                "Args:\n"
                "    command: a single shell command\n"
                "    working_directory: working directory for execution\n\n"
                "Returns: execution result."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "title": "Command",
                        "description": "要执行的单个shell命令",
                    },
                    "working_directory": {
                        "type": "string",
                        "title": "Working Directory",
                        "description": "执行命令的工作目录",
                    },
                },
                "required": ["command", "working_directory"],
            },
        ),
    ]


@app.call_tool()
async def handle_call_tool(name: str, arguments: dict) -> list[types.TextContent]:
    """
    处理工具调用 / Handle tool calls
    """
    try:
        if name == "execute_commands":
            return await execute_command_batch(
                arguments.get("commands", ""), arguments.get("working_directory", ".")
            )
        elif name == "execute_single_command":
            return await execute_single_command(
                arguments.get("command", ""), arguments.get("working_directory", ".")
            )
        else:
            raise ValueError(f"未知工具 / Unknown tool: {name}")

    except Exception as e:
        return [
            types.TextContent(
                type="text",
                text=f"工具执行错误 / Error executing tool {name}: {str(e)}",
            )
        ]


async def execute_command_batch(
    commands: str, working_directory: str
) -> list[types.TextContent]:
    """
    执行多个shell命令 / Execute multiple shell commands

    Args:
        commands: 命令列表，每行一个命令 / Command list, one command per line
        working_directory: 工作目录 / Working directory

    Returns:
        执行结果 / Execution results
    """
    try:
        # 确保工作目录存在 / Ensure working directory exists
        Path(working_directory).mkdir(parents=True, exist_ok=True)

        # 分割命令行 / Split command lines
        command_lines = [
            cmd.strip() for cmd in commands.strip().split("\n") if cmd.strip()
        ]

        if not command_lines:
            return [
                types.TextContent(
                    type="text", text="没有提供有效命令 / No valid commands provided"
                )
            ]

        results = []
        stats = {"successful": 0, "failed": 0, "timeout": 0, "native": 0}
        cwd_path = Path(working_directory)

        for i, command in enumerate(command_lines, 1):
            native = _try_native_execute(command, cwd_path)
            if native is not None:
                rc, out, err = native
                if rc == 0:
                    results.append(f"✅ Command {i}: {command}")
                    if out.strip():
                        results.append(f"   Output: {out.strip()}")
                    stats["successful"] += 1
                    stats["native"] += 1
                else:
                    results.append(f"❌ Command {i}: {command}")
                    if err.strip():
                        results.append(f"   Error: {err.strip()}")
                    stats["failed"] += 1
                continue

            try:
                result = subprocess.run(
                    command,
                    shell=True,
                    cwd=working_directory,
                    capture_output=True,
                    text=True,
                    timeout=30,
                    encoding="utf-8",
                    errors="replace",
                )

                if result.returncode == 0:
                    results.append(f"✅ Command {i}: {command}")
                    if result.stdout.strip():
                        results.append(f"   Output: {result.stdout.strip()}")
                    stats["successful"] += 1
                else:
                    results.append(f"❌ Command {i}: {command}")
                    if result.stderr.strip():
                        results.append(f"   Error: {result.stderr.strip()}")
                    stats["failed"] += 1

            except subprocess.TimeoutExpired:
                results.append(f"⏱️ Command {i} timeout: {command}")
                stats["timeout"] += 1
            except Exception as e:
                results.append(f"💥 Command {i} exception: {command} - {str(e)}")
                stats["failed"] += 1

        # 生成执行报告 / Generate execution report
        summary = generate_execution_summary(working_directory, command_lines, stats)
        final_result = summary + "\n" + "\n".join(results)

        return [types.TextContent(type="text", text=final_result)]

    except Exception as e:
        return [
            types.TextContent(
                type="text",
                text=f"批量命令执行失败 / Failed to execute command batch: {str(e)}",
            )
        ]


async def execute_single_command(
    command: str, working_directory: str
) -> list[types.TextContent]:
    """
    执行单个shell命令 / Execute single shell command

    Args:
        command: 要执行的命令 / Command to execute
        working_directory: 工作目录 / Working directory

    Returns:
        执行结果 / Execution result
    """
    try:
        cwd_path = Path(working_directory)
        cwd_path.mkdir(parents=True, exist_ok=True)

        native = _try_native_execute(command, cwd_path)
        if native is not None:
            rc, out, err = native
            result = subprocess.CompletedProcess(
                args=command, returncode=rc, stdout=out, stderr=err
            )
        else:
            result = subprocess.run(
                command,
                shell=True,
                cwd=working_directory,
                capture_output=True,
                text=True,
                timeout=30,
                encoding="utf-8",
                errors="replace",
            )

        output = format_single_command_result(command, working_directory, result)

        return [types.TextContent(type="text", text=output)]

    except subprocess.TimeoutExpired:
        return [
            types.TextContent(
                type="text", text=f"⏱️ 命令超时 / Command timeout: {command}"
            )
        ]
    except Exception as e:
        return [
            types.TextContent(
                type="text", text=f"💥 命令执行错误 / Command execution error: {str(e)}"
            )
        ]


def generate_execution_summary(
    working_directory: str, command_lines: List[str], stats: Dict[str, int]
) -> str:
    """
    生成执行总结 / Generate execution summary

    Args:
        working_directory: 工作目录 / Working directory
        command_lines: 命令列表 / Command list
        stats: 统计信息 / Statistics

    Returns:
        格式化的总结 / Formatted summary
    """
    native_count = stats.get("native", 0)
    return f"""
Command Execution Summary:
{'='*50}
Working Directory: {working_directory}
Total Commands: {len(command_lines)}
Successful: {stats['successful']} (native: {native_count})
Failed: {stats['failed']}
Timeout: {stats['timeout']}

Detailed Results:
{'-'*50}"""


def format_single_command_result(
    command: str, working_directory: str, result: subprocess.CompletedProcess
) -> str:
    """
    格式化单命令执行结果 / Format single command execution result

    Args:
        command: 执行的命令 / Executed command
        working_directory: 工作目录 / Working directory
        result: 执行结果 / Execution result

    Returns:
        格式化的结果 / Formatted result
    """
    output = f"""
Single Command Execution:
{'='*40}
Working Directory: {working_directory}
Command: {command}
Return Code: {result.returncode}

"""

    if result.returncode == 0:
        output += "Status: SUCCESS\n"
        if result.stdout.strip():
            output += f"Output:\n{result.stdout.strip()}\n"
    else:
        output += "Status: FAILED\n"
        if result.stderr.strip():
            output += f"Error:\n{result.stderr.strip()}\n"

    return output


async def main():
    """
    运行MCP服务器 / Run MCP server
    """
    # 通过stdio运行服务器 / Run server via stdio
    async with mcp.server.stdio.stdio_server() as (read_stream, write_stream):
        await app.run(
            read_stream,
            write_stream,
            InitializationOptions(
                server_name="command-executor",
                server_version="1.0.0",
                capabilities=app.get_capabilities(
                    notification_options=NotificationOptions(),
                    experimental_capabilities={},
                ),
            ),
        )


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
