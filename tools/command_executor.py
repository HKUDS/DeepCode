#!/usr/bin/env python3
"""
Command Executor MCP Tool / 命令执行器 MCP 工具

专门负责执行LLM生成的shell命令来创建文件树结构
Specialized in executing LLM-generated shell commands to create file tree structures
"""

import subprocess
from pathlib import Path
from typing import List, Dict
from mcp.server.models import InitializationOptions
import mcp.types as types
from mcp.server import NotificationOptions, Server
import mcp.server.stdio

# 创建MCP服务器实例 / Create MCP server instance
app = Server("command-executor")


@app.list_tools()
async def handle_list_tools() -> list[types.Tool]:
    """
    列出可用工具 / List available tools
    """
    return [
        types.Tool(
            name="execute_commands",
            description="""
            执行shell命令列表来创建文件树结构
            Execute shell command list to create file tree structure

            Args:
                commands: 要执行的shell命令列表（每行一个命令）
                working_directory: 执行命令的工作目录

            Returns:
                命令执行结果和详细报告
            """,
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
            description="""
            执行单个shell命令
            Execute single shell command

            Args:
                command: 要执行的单个命令
                working_directory: 执行命令的工作目录

            Returns:
                命令执行结果
            """,
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
        stats = {"successful": 0, "failed": 0, "timeout": 0}

        for i, command in enumerate(command_lines, 1):
            try:
                # 执行命令 / Execute command
                result = subprocess.run(
                    command,
                    shell=True,
                    cwd=working_directory,
                    capture_output=True,
                    text=True,
                    timeout=30,  # 30秒超时
                )

                if result.returncode == 0:
                    results.append(f"✅ Command {i}: {command}")
                    if result.stdout.strip():
                        results.append(f"   输出 / Output: {result.stdout.strip()}")
                    stats["successful"] += 1
                else:
                    results.append(f"❌ Command {i}: {command}")
                    if result.stderr.strip():
                        results.append(f"   错误 / Error: {result.stderr.strip()}")
                    stats["failed"] += 1

            except subprocess.TimeoutExpired:
                results.append(f"⏱️ Command {i} 超时 / timeout: {command}")
                stats["timeout"] += 1
            except Exception as e:
                results.append(f"💥 Command {i} 异常 / exception: {command} - {str(e)}")
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
        # 确保工作目录存在 / Ensure working directory exists
        Path(working_directory).mkdir(parents=True, exist_ok=True)

        # 执行命令 / Execute command
        result = subprocess.run(
            command,
            shell=True,
            cwd=working_directory,
            capture_output=True,
            text=True,
            timeout=30,
        )

        # 格式化输出 / Format output
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
    return f"""
命令执行总结 / Command Execution Summary:
{'='*50}
工作目录 / Working Directory: {working_directory}
总命令数 / Total Commands: {len(command_lines)}
成功 / Successful: {stats['successful']}
失败 / Failed: {stats['failed']}
超时 / Timeout: {stats['timeout']}

详细结果 / Detailed Results:
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
单命令执行 / Single Command Execution:
{'='*40}
工作目录 / Working Directory: {working_directory}
命令 / Command: {command}
返回码 / Return Code: {result.returncode}

"""

    if result.returncode == 0:
        output += "✅ 状态 / Status: SUCCESS / 成功\n"
        if result.stdout.strip():
            output += f"输出 / Output:\n{result.stdout.strip()}\n"
    else:
        output += "❌ 状态 / Status: FAILED / 失败\n"
        if result.stderr.strip():
            output += f"错误 / Error:\n{result.stderr.strip()}\n"

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
