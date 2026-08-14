"""
Code Implementation Agent for File-by-File Development

Handles systematic code implementation with progress tracking and
memory optimization for long-running development sessions.
"""

import json
import time
import logging
from typing import Dict, Any, List, Optional

# Import tiktoken for token calculation
try:
    import tiktoken

    TIKTOKEN_AVAILABLE = True
except ImportError:
    TIKTOKEN_AVAILABLE = False

# Import prompts from code_prompts
import sys
import os

sys.path.insert(
    0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)
from prompts.code_prompts import (
    GENERAL_CODE_IMPLEMENTATION_SYSTEM_PROMPT,
)


class CodeImplementationAgent:
    """
    Code Implementation Agent for systematic file-by-file development

    Responsibilities:
    - Track file implementation progress
    - Execute MCP tool calls for code generation
    - Monitor implementation status
    - Coordinate with Summary Agent for memory optimization
    - Calculate token usage for context management
    """

    def __init__(
        self,
        mcp_agent,
        logger: Optional[logging.Logger] = None,
        enable_read_tools: bool = True,
    ):
        """
        Initialize Code Implementation Agent

        Args:
            mcp_agent: MCP agent instance for tool calls
            logger: Logger instance for tracking operations
            enable_read_tools: Whether to enable read_file and read_code_mem tools (default: True)
        """
        self.mcp_agent = mcp_agent
        self.logger = logger or self._create_default_logger()
        self.enable_read_tools = enable_read_tools  # Control read tools execution

        self.implementation_summary = {
            "completed_files": [],
            "technical_decisions": [],
            "important_constraints": [],
            "architecture_notes": [],
            "dependency_analysis": [],  # Track dependency analysis and file reads
        }
        self.files_implemented_count = 0
        self.implemented_files_set = (
            set()
        )  # Track unique file paths to avoid duplicate counting
        self.files_read_for_dependencies = (
            set()
        )  # Track files read for dependency analysis
        self.last_summary_file_count = (
            0  # Track the file count when last summary was triggered
        )

        # Token calculation settings
        self.max_context_tokens = (
            200000  # Default max context tokens for Claude-3.5-Sonnet
        )
        self.token_buffer = 10000  # Safety buffer before reaching max
        self.summary_trigger_tokens = (
            self.max_context_tokens - self.token_buffer
        )  # Trigger summary when approaching limit
        self.last_summary_token_count = (
            0  # Track token count when last summary was triggered
        )

        # Initialize tokenizer
        if TIKTOKEN_AVAILABLE:
            try:
                # Use Claude-3 tokenizer (approximation with OpenAI's o200k_base)
                self.tokenizer = tiktoken.get_encoding("o200k_base")
                self.logger.info("Token calculation enabled with o200k_base encoding")
            except Exception as e:
                self.tokenizer = None
                self.logger.warning(f"Failed to initialize tokenizer: {e}")
        else:
            self.tokenizer = None
            self.logger.warning(
                "tiktoken not available, token-based summary triggering disabled"
            )

        # Analysis loop detection
        self.recent_tool_calls = []  # Track recent tool calls to detect analysis loops
        self.max_read_without_write = 5  # Max read_file calls without write_file

        # Memory agent integration
        self.memory_agent = None  # Will be set externally
        self.llm_client = None  # Will be set externally
        self.llm_client_type = None  # Will be set externally

        # Log read tools configuration
        read_tools_status = "ENABLED" if self.enable_read_tools else "DISABLED"
        self.logger.info(
            f"🔧 Code Implementation Agent initialized - Read tools: {read_tools_status}"
        )
        if not self.enable_read_tools:
            self.logger.info(
                "🚫 Testing mode: read_file and read_code_mem will be skipped when called"
            )

    def _create_default_logger(self) -> logging.Logger:
        """Create default logger if none provided"""
        logger = logging.getLogger(f"{__name__}.CodeImplementationAgent")
        # Don't add handlers to child loggers - let them propagate to root
        logger.setLevel(logging.INFO)
        return logger

    def get_system_prompt(self) -> str:
        """
        Get the system prompt for code implementation
        """
        return GENERAL_CODE_IMPLEMENTATION_SYSTEM_PROMPT

    def set_memory_agent(self, memory_agent, llm_client=None, llm_client_type=None):
        """
        Set memory agent for code summary generation

        Args:
            memory_agent: Memory agent instance
            llm_client: LLM client for summary generation
            llm_client_type: Type of LLM client ("anthropic" or "openai")
        """
        self.memory_agent = memory_agent
        self.llm_client = llm_client
        self.llm_client_type = llm_client_type
        self.logger.info("Memory agent integration configured")

    async def execute_tool_calls(self, tool_calls: List[Dict]) -> List[Dict]:
        """
        Execute MCP tool calls and track implementation progress

        Args:
            tool_calls: List of tool calls to execute

        Returns:
            List of tool execution results
        """
        results = []

        for tool_call in tool_calls:
            tool_name = tool_call["name"]
            tool_input = tool_call["input"]

            self.logger.info(f"Executing MCP tool: {tool_name}")

            try:
                # Check if read tools are disabled
                if not self.enable_read_tools and tool_name in [
                    "read_file",
                    "read_code_mem",
                ]:
                    # self.logger.info(f"🚫 SKIPPING {tool_name} - Read tools disabled for testing")
                    # Return a mock result indicating the tool was skipped
                    mock_result = json.dumps(
                        {
                            "status": "skipped",
                            "message": f"{tool_name} tool disabled for testing",
                            "tool_disabled": True,
                            "original_input": tool_input,
                        },
                        ensure_ascii=False,
                    )

                    results.append(
                        {
                            "tool_id": tool_call["id"],
                            "tool_name": tool_name,
                            "result": mock_result,
                        }
                    )
                    continue

                # read_code_mem is now a proper MCP tool, no special handling needed

                # INTERCEPT read_file calls - redirect to read_code_mem first if memory agent is available
                if tool_name == "read_file":
                    file_path = tool_call["input"].get("file_path", "unknown")
                    self.logger.info(f"🔍 READ_FILE CALL DETECTED: {file_path}")
                    self.logger.info(
                        f"📊 Files implemented count: {self.files_implemented_count}"
                    )
                    self.logger.info(
                        f"🧠 Memory agent available: {self.memory_agent is not None}"
                    )

                    # Enable optimization if memory agent is available (more aggressive approach)
                    if self.memory_agent is not None:
                        self.logger.info(
                            f"🔄 INTERCEPTING read_file call for {file_path} (memory agent available)"
                        )
                        result = await self._handle_read_file_with_memory_optimization(
                            tool_call
                        )
                        results.append(result)
                        continue
                    else:
                        self.logger.info(
                            "📁 NO INTERCEPTION: no memory agent available"
                        )

                if self.mcp_agent:
                    # Execute tool call through MCP protocol
                    result = await self.mcp_agent.call_tool(tool_name, tool_input)

                    # Track file implementation progress
                    if tool_name == "write_file":
                        await self._track_file_implementation_with_summary(
                            tool_call, result
                        )
                    elif tool_name == "read_file":
                        self._track_dependency_analysis(tool_call, result)

                    # Track tool calls for analysis loop detection
                    self._track_tool_call_for_loop_detection(tool_name)

                    results.append(
                        {
                            "tool_id": tool_call["id"],
                            "tool_name": tool_name,
                            "result": result,
                        }
                    )
                else:
                    results.append(
                        {
                            "tool_id": tool_call["id"],
                            "tool_name": tool_name,
                            "result": json.dumps(
                                {
                                    "status": "error",
                                    "message": "MCP agent not initialized",
                                },
                                ensure_ascii=False,
                            ),
                        }
                    )

            except Exception as e:
                self.logger.error(f"MCP tool execution failed: {e}")
                results.append(
                    {
                        "tool_id": tool_call["id"],
                        "tool_name": tool_name,
                        "result": json.dumps(
                            {"status": "error", "message": str(e)}, ensure_ascii=False
                        ),
                    }
                )

        return results

    # _handle_read_code_mem method removed - read_code_mem is now a proper MCP tool

    async def _handle_read_file_with_memory_optimization(self, tool_call: Dict) -> Dict:
        """
        Intercept read_file calls and redirect to read_code_mem if a summary exists.
        This prevents unnecessary file reads if the summary is already available.
        """
        file_path = tool_call["input"].get("file_path")
        if not file_path:
            return {
                "tool_id": tool_call["id"],
                "tool_name": "read_file",
                "result": json.dumps(
                    {"status": "error", "message": "file_path parameter is required"},
                    ensure_ascii=False,
                ),
            }

        # Check if a summary exists for this file using read_code_mem MCP tool
        should_use_summary = False
        if self.memory_agent and self.mcp_agent:
            try:
                # Use read_code_mem MCP tool to check if summary exists (pass file path as list)
                read_code_mem_result = await self.mcp_agent.call_tool(
                    "read_code_mem", {"file_paths": [file_path]}
                )

                # Parse the result to check if summary was found
                import json

                if isinstance(read_code_mem_result, str):
                    try:
                        result_data = json.loads(read_code_mem_result)
                        # Check if any summaries were found in the results
                        should_use_summary = (
                            result_data.get("status")
                            in ["all_summaries_found", "partial_summaries_found"]
                            and result_data.get("summaries_found", 0) > 0
                        )
                    except json.JSONDecodeError:
                        should_use_summary = False
            except Exception as e:
                self.logger.debug(f"read_code_mem check failed for {file_path}: {e}")
                should_use_summary = False

        if should_use_summary:
            self.logger.info(f"🔄 READ_FILE INTERCEPTED: Using summary for {file_path}")

            # Use the MCP agent to call read_code_mem tool
            if self.mcp_agent:
                result = await self.mcp_agent.call_tool(
                    "read_code_mem", {"file_paths": [file_path]}
                )

                # Modify the result to indicate it was originally a read_file call
                import json

                try:
                    result_data = (
                        json.loads(result) if isinstance(result, str) else result
                    )
                    if isinstance(result_data, dict):
                        # Extract the specific file result for the single file we requested
                        file_results = result_data.get("results", [])
                        if file_results and len(file_results) > 0:
                            specific_result = file_results[
                                0
                            ]  # Get the first (and only) result
                            # Transform to match the old single-file format for backward compatibility
                            transformed_result = {
                                "status": specific_result.get("status", "no_summary"),
                                "file_path": specific_result.get(
                                    "file_path", file_path
                                ),
                                "summary_content": specific_result.get(
                                    "summary_content"
                                ),
                                "message": specific_result.get("message", ""),
                                "original_tool": "read_file",
                                "optimization": "redirected_to_read_code_mem",
                            }
                            final_result = json.dumps(
                                transformed_result, ensure_ascii=False
                            )
                        else:
                            # Fallback if no results
                            result_data["original_tool"] = "read_file"
                            result_data["optimization"] = "redirected_to_read_code_mem"
                            final_result = json.dumps(result_data, ensure_ascii=False)
                    else:
                        final_result = result
                except (json.JSONDecodeError, TypeError):
                    final_result = result

                return {
                    "tool_id": tool_call["id"],
                    "tool_name": "read_file",  # Keep original tool name for tracking
                    "result": final_result,
                }
            else:
                self.logger.warning(
                    "MCP agent not available for read_code_mem optimization"
                )
        else:
            self.logger.info(
                f"📁 READ_FILE: No summary for {file_path}, using actual file"
            )

            # Execute the original read_file call
            if self.mcp_agent:
                result = await self.mcp_agent.call_tool("read_file", tool_call["input"])

                # Track dependency analysis for the actual file read
                self._track_dependency_analysis(tool_call, result)

                # Track tool calls for analysis loop detection
                self._track_tool_call_for_loop_detection("read_file")

                return {
                    "tool_id": tool_call["id"],
                    "tool_name": "read_file",
                    "result": result,
                }
            else:
                return {
                    "tool_id": tool_call["id"],
                    "tool_name": "read_file",
                    "result": json.dumps(
                        {"status": "error", "message": "MCP agent not initialized"},
                        ensure_ascii=False,
                    ),
                }

    async def _track_file_implementation_with_summary(
        self, tool_call: Dict, result: Any
    ):
        """
        Track file implementation and create code summary

        Args:
            tool_call: The write_file tool call
            result: Result of the tool execution
        """
        # First do the regular tracking
        self._track_file_implementation(tool_call, result)

        # Then create and save code summary if memory agent is available
        if self.memory_agent and self.llm_client and self.llm_client_type:
            try:
                file_path = tool_call["input"].get("file_path")
                file_content = tool_call["input"].get("content", "")

                if file_path and file_content:
                    # Create code implementation summary
                    summary = await self.memory_agent.create_code_implementation_summary(
                        self.llm_client,
                        self.llm_client_type,
                        file_path,
                        file_content,
                        self.get_files_implemented_count(),  # Pass the current file count
                    )

                    self.logger.info(
                        f"Created code summary for implemented file: {file_path}, summary: {summary[:100]}..."
                    )
                else:
                    self.logger.warning(
                        "Missing file path or content for summary generation"
                    )

            except Exception as e:
                self.logger.error(f"Failed to create code summary: {e}")

    def _track_file_implementation(self, tool_call: Dict, result: Any):
        """
        Track file implementation progress
        """
        try:
            # Handle different result types from MCP
            result_data = None

            # Check if result is a CallToolResult object
            if hasattr(result, "content"):
                # Extract content from CallToolResult
                if hasattr(result.content, "text"):
                    result_content = result.content.text
                else:
                    result_content = str(result.content)

                # Try to parse as JSON
                try:
                    result_data = json.loads(result_content)
                except json.JSONDecodeError:
                    # If not JSON, create a structure
                    result_data = {
                        "status": "success",
                        "file_path": tool_call["input"].get("file_path", "unknown"),
                    }
            elif isinstance(result, str):
                # Try to parse string result
                try:
                    result_data = json.loads(result)
                except json.JSONDecodeError:
                    result_data = {
                        "status": "success",
                        "file_path": tool_call["input"].get("file_path", "unknown"),
                    }
            elif isinstance(result, dict):
                # Direct dictionary result
                result_data = result
            else:
                # Fallback: assume success and extract file path from input
                result_data = {
                    "status": "success",
                    "file_path": tool_call["input"].get("file_path", "unknown"),
                }

            # Extract file path for tracking
            file_path = None
            if result_data and result_data.get("status") == "success":
                file_path = result_data.get(
                    "file_path", tool_call["input"].get("file_path", "unknown")
                )
            else:
                file_path = tool_call["input"].get("file_path")

            # Only count unique files, not repeated tool calls on same file
            if file_path and file_path not in self.implemented_files_set:
                # This is a new file implementation
                self.implemented_files_set.add(file_path)
                self.files_implemented_count += 1
                # self.logger.info(f"New file implementation tracked: count={self.files_implemented_count}, file={file_path}")
                # print(f"New file implementation tracked: count={self.files_implemented_count}, file={file_path}")

                # Add to completed files list
                self.implementation_summary["completed_files"].append(
                    {
                        "file": file_path,
                        "iteration": self.files_implemented_count,
                        "timestamp": time.time(),
                        "size": result_data.get("size", 0) if result_data else 0,
                    }
                )

                # self.logger.info(
                #     f"New file implementation tracked: count={self.files_implemented_count}, file={file_path}"
                # )
                # print(f"📝 NEW FILE IMPLEMENTED: count={self.files_implemented_count}, file={file_path}")
                # print(f"🔧 OPTIMIZATION NOW ENABLED: files_implemented_count > 0 = {self.files_implemented_count > 0}")
            elif file_path and file_path in self.implemented_files_set:
                # This file was already implemented (duplicate tool call)
                self.logger.debug(
                    f"File already tracked, skipping duplicate count: {file_path}"
                )
            else:
                # No valid file path found
                self.logger.warning("No valid file path found for tracking")

        except Exception as e:
            self.logger.warning(f"Failed to track file implementation: {e}")
            # Even if tracking fails, try to count based on tool input (but check for duplicates)

            file_path = tool_call["input"].get("file_path")
            if file_path and file_path not in self.implemented_files_set:
                self.implemented_files_set.add(file_path)
                self.files_implemented_count += 1
                self.logger.info(
                    f"File implementation counted (emergency fallback): count={self.files_implemented_count}, file={file_path}"
                )

    def _track_dependency_analysis(self, tool_call: Dict, result: Any):
        """
        Track dependency analysis through read_file calls
        """
        try:
            file_path = tool_call["input"].get("file_path")
            if file_path:
                # Track unique files read for dependency analysis
                if file_path not in self.files_read_for_dependencies:
                    self.files_read_for_dependencies.add(file_path)

                    # Add to dependency analysis summary
                    self.implementation_summary["dependency_analysis"].append(
                        {
                            "file_read": file_path,
                            "timestamp": time.time(),
                            "purpose": "dependency_analysis",
                        }
                    )

                    self.logger.info(
                        f"Dependency analysis tracked: file_read={file_path}"
                    )

        except Exception as e:
            self.logger.warning(f"Failed to track dependency analysis: {e}")

    def calculate_messages_token_count(self, messages: List[Dict]) -> int:
        """
        Calculate total token count for a list of messages

        Args:
            messages: List of chat messages with 'role' and 'content' keys

        Returns:
            Total token count
        """
        if not self.tokenizer:
            # Fallback: rough estimation based on character count
            total_chars = sum(len(str(msg.get("content", ""))) for msg in messages)
            # Rough approximation: 1 token ≈ 4 characters
            return total_chars // 4

        try:
            total_tokens = 0
            for message in messages:
                content = str(message.get("content", ""))
                role = message.get("role", "")

                # Count tokens for content
                if content:
                    content_tokens = len(
                        self.tokenizer.encode(content, disallowed_special=())
                    )
                    total_tokens += content_tokens

                # Add tokens for role and message structure
                role_tokens = len(self.tokenizer.encode(role, disallowed_special=()))
                total_tokens += role_tokens + 4  # Extra tokens for message formatting

            return total_tokens

        except Exception as e:
            self.logger.warning(f"Token calculation failed: {e}")
            # Fallback estimation
            total_chars = sum(len(str(msg.get("content", ""))) for msg in messages)
            return total_chars // 4

    def should_trigger_summary_by_tokens(self, messages: List[Dict]) -> bool:
        """
        Check if summary should be triggered based on token count

        Args:
            messages: Current conversation messages

        Returns:
            True if summary should be triggered based on token count
        """
        if not messages:
            return False

        # Calculate current token count / 计算当前token数
        current_token_count = self.calculate_messages_token_count(messages)

        # Check if we should trigger summary / 检查是否应触发总结
        should_trigger = (
            current_token_count > self.summary_trigger_tokens
            and current_token_count
            > self.last_summary_token_count
            + 10000  # Minimum 10k tokens between summaries / 总结间最少10k tokens
        )

        if should_trigger:
            self.logger.info(
                f"Token-based summary trigger: current={current_token_count:,}, "
                f"threshold={self.summary_trigger_tokens:,}, "
                f"last_summary={self.last_summary_token_count:,}"
            )

        return should_trigger

    def should_trigger_summary(
        self, summary_trigger: int = 5, messages: List[Dict] = None
    ) -> bool:
        """
        Check if summary should be triggered based on token count (preferred) or file count (fallback)
        根据token数（首选）或文件数（回退）检查是否应触发总结

        Args:
            summary_trigger: Number of files after which to trigger summary (fallback)
            messages: Current conversation messages for token calculation

        Returns:
            True if summary should be triggered
        """
        # Primary: Token-based triggering / 主要：基于token的触发
        if messages and self.tokenizer:
            return self.should_trigger_summary_by_tokens(messages)

        # Fallback: File-based triggering (original logic) / 回退：基于文件的触发（原始逻辑）
        self.logger.info("Using fallback file-based summary triggering")
        should_trigger = (
            self.files_implemented_count > 0
            and self.files_implemented_count % summary_trigger == 0
            and self.files_implemented_count > self.last_summary_file_count
        )

        return should_trigger

    def mark_summary_triggered(self, messages: List[Dict] = None):
        """
        Mark that summary has been triggered for current state
        标记当前状态的总结已被触发

        Args:
            messages: Current conversation messages for token tracking
        """
        # Update file-based tracking / 更新基于文件的跟踪
        self.last_summary_file_count = self.files_implemented_count

        # Update token-based tracking / 更新基于token的跟踪
        if messages and self.tokenizer:
            self.last_summary_token_count = self.calculate_messages_token_count(
                messages
            )
            self.logger.info(
                f"Summary marked as triggered - file_count: {self.files_implemented_count}, "
                f"token_count: {self.last_summary_token_count:,}"
            )
        else:
            self.logger.info(
                f"Summary marked as triggered for file count: {self.files_implemented_count}"
            )

    def get_implementation_summary(self) -> Dict[str, Any]:
        """
        Get current implementation summary
        获取当前实现总结
        """
        return self.implementation_summary.copy()

    def get_files_implemented_count(self) -> int:
        """
        Get the number of files implemented so far
        获取到目前为止实现的文件数量
        """
        return self.files_implemented_count

    def get_read_tools_status(self) -> Dict[str, Any]:
        """
        Get read tools configuration status
        获取读取工具配置状态

        Returns:
            Dictionary with read tools status information
        """
        return {
            "read_tools_enabled": self.enable_read_tools,
            "status": "ENABLED" if self.enable_read_tools else "DISABLED",
            "tools_affected": ["read_file", "read_code_mem"],
            "description": "Read tools configuration for testing purposes",
        }

    def add_technical_decision(self, decision: str, context: str = ""):
        """
        Add a technical decision to the implementation summary
        向实现总结添加技术决策

        Args:
            decision: Description of the technical decision
            context: Additional context for the decision
        """
        self.implementation_summary["technical_decisions"].append(
            {"decision": decision, "context": context, "timestamp": time.time()}
        )
        self.logger.info(f"Technical decision recorded: {decision}")

    def add_constraint(self, constraint: str, impact: str = ""):
        """
        Add an important constraint to the implementation summary
        向实现总结添加重要约束

        Args:
            constraint: Description of the constraint
            impact: Impact of the constraint on implementation
        """
        self.implementation_summary["important_constraints"].append(
            {"constraint": constraint, "impact": impact, "timestamp": time.time()}
        )
        self.logger.info(f"Constraint recorded: {constraint}")

    def add_architecture_note(self, note: str, component: str = ""):
        """
        Add an architecture note to the implementation summary
        向实现总结添加架构注释

        Args:
            note: Architecture note description
            component: Related component or module
        """
        self.implementation_summary["architecture_notes"].append(
            {"note": note, "component": component, "timestamp": time.time()}
        )
        self.logger.info(f"Architecture note recorded: {note}")

    def get_implementation_statistics(self) -> Dict[str, Any]:
        """
        Get comprehensive implementation statistics
        获取全面的实现统计信息
        """
        return {
            "total_files_implemented": self.files_implemented_count,
            "files_implemented_count": self.files_implemented_count,
            "technical_decisions_count": len(
                self.implementation_summary["technical_decisions"]
            ),
            "constraints_count": len(
                self.implementation_summary["important_constraints"]
            ),
            "architecture_notes_count": len(
                self.implementation_summary["architecture_notes"]
            ),
            "dependency_analysis_count": len(
                self.implementation_summary["dependency_analysis"]
            ),
            "files_read_for_dependencies": len(self.files_read_for_dependencies),
            "unique_files_implemented": len(self.implemented_files_set),
            "completed_files_list": [
                f["file"] for f in self.implementation_summary["completed_files"]
            ],
            "dependency_files_read": list(self.files_read_for_dependencies),
            "last_summary_file_count": self.last_summary_file_count,
            "read_tools_status": self.get_read_tools_status(),  # Include read tools configuration
        }

    def force_enable_optimization(self):
        """
        Force enable optimization for testing purposes
        强制启用优化用于测试目的
        """
        self.files_implemented_count = 1
        self.logger.info(
            f"🔧 OPTIMIZATION FORCE ENABLED: files_implemented_count set to {self.files_implemented_count}"
        )
        print(
            f"🔧 OPTIMIZATION FORCE ENABLED: files_implemented_count set to {self.files_implemented_count}"
        )

    def reset_implementation_tracking(self):
        """
        Reset implementation tracking (useful for new sessions)
        重置实现跟踪（对新会话有用）
        """
        self.implementation_summary = {
            "completed_files": [],
            "technical_decisions": [],
            "important_constraints": [],
            "architecture_notes": [],
            "dependency_analysis": [],  # Reset dependency analysis and file reads
        }
        self.files_implemented_count = 0
        self.implemented_files_set = (
            set()
        )  # Reset the unique files set / 重置唯一文件集合
        self.files_read_for_dependencies = (
            set()
        )  # Reset files read for dependency analysis / 重置为依赖分析而读取的文件
        self.last_summary_file_count = 0  # Reset the file count when last summary was triggered / 重置上次触发总结时的文件数
        self.last_summary_token_count = 0  # Reset token count when last summary was triggered / 重置上次触发总结时的token数
        self.logger.info("Implementation tracking reset")

        # Reset analysis loop detection / 重置分析循环检测
        self.recent_tool_calls = []
        self.logger.info("Analysis loop detection reset")

    def _track_tool_call_for_loop_detection(self, tool_name: str):
        """
        Track tool calls for analysis loop detection
        跟踪工具调用以检测分析循环

        Args:
            tool_name: Name of the tool called
        """
        self.recent_tool_calls.append(tool_name)
        if len(self.recent_tool_calls) > self.max_read_without_write:
            self.recent_tool_calls.pop(0)

        if len(set(self.recent_tool_calls)) == 1:
            self.logger.warning("Analysis loop detected")

    def is_in_analysis_loop(self) -> bool:
        """
        Check if the agent is in an analysis loop (only reading files, not writing)
        检查代理是否在分析循环中（只读文件，不写文件）

        Returns:
            True if in analysis loop
        """
        if len(self.recent_tool_calls) < self.max_read_without_write:
            return False

        # Check if recent calls are all read_file or search_reference_code / 检查最近的调用是否都是read_file或search_reference_code
        analysis_tools = {
            "read_file",
            "search_reference_code",
            "get_all_available_references",
        }
        recent_calls_set = set(self.recent_tool_calls)

        # If all recent calls are analysis tools, we're in an analysis loop / 如果最近的调用都是分析工具，我们在分析循环中
        in_loop = (
            recent_calls_set.issubset(analysis_tools) and len(recent_calls_set) >= 1
        )

        if in_loop:
            self.logger.warning(
                f"Analysis loop detected! Recent calls: {self.recent_tool_calls}"
            )

        return in_loop

    def get_analysis_loop_guidance(self) -> str:
        """
        Get guidance to break out of analysis loop

        Returns:
            Guidance message to encourage implementation
        """
        return f"""🚨 **ANALYSIS LOOP DETECTED - IMMEDIATE ACTION REQUIRED**

**Problem**: You've been reading/analyzing files for {len(self.recent_tool_calls)} consecutive calls without writing code.
**Recent tool calls**: {' → '.join(self.recent_tool_calls)}

**SOLUTION - IMPLEMENT CODE NOW**:
1. **STOP ANALYZING** - You have enough information
2. **Use write_file** to create the next code file according to the implementation plan
3. **Choose ANY file** from the plan that hasn't been implemented yet
4. **Write complete, working code** - don't ask for permission or clarification

**Files implemented so far**: {self.files_implemented_count}
**Your goal**: Implement MORE files, not analyze existing ones!

**CRITICAL**: Your next response MUST use write_file to create a new code file!"""

    async def test_summary_functionality(self, test_file_path: str = None):
        """
        Test if the code summary functionality is working correctly
        测试代码总结功能是否正常工作

        Args:
            test_file_path: Specific file to test, if None will test all implemented files
        """
        if not self.memory_agent:
            self.logger.warning("No memory agent available for testing")
            return

        if test_file_path:
            files_to_test = [test_file_path]
        else:
            # Use implemented files from tracking
            files_to_test = list(self.implemented_files_set)[
                :3
            ]  # Limit to first 3 files

        if not files_to_test:
            self.logger.warning("No implemented files to test")
            return

        # Test each file silently
        summary_files_found = 0

        for file_path in files_to_test:
            if self.mcp_agent:
                try:
                    result = await self.mcp_agent.call_tool(
                        "read_code_mem", {"file_paths": [file_path]}
                    )

                    # Parse the result to check if summary was found
                    import json

                    result_data = (
                        json.loads(result) if isinstance(result, str) else result
                    )

                    if (
                        result_data.get("status")
                        in ["all_summaries_found", "partial_summaries_found"]
                        and result_data.get("summaries_found", 0) > 0
                    ):
                        summary_files_found += 1
                except Exception as e:
                    self.logger.warning(
                        f"Failed to test read_code_mem for {file_path}: {e}"
                    )
            else:
                self.logger.warning("MCP agent not available for testing")

        self.logger.info(
            f"📋 Summary testing: {summary_files_found}/{len(files_to_test)} files have summaries"
        )

    async def test_automatic_read_file_optimization(self):
        """
        Test the automatic read_file optimization that redirects to read_code_mem
        测试自动read_file优化，重定向到read_code_mem
        """
        print("=" * 80)
        print("🔄 TESTING AUTOMATIC READ_FILE OPTIMIZATION")
        print("=" * 80)

        # Simulate that at least one file has been implemented (to trigger optimization)
        self.files_implemented_count = 1

        # Test with a generic config file that should have a summary
        test_file = "config.py"

        print(f"📁 Testing automatic optimization for: {test_file}")
        print(f"📊 Files implemented count: {self.files_implemented_count}")
        print(
            f"🔧 Optimization should be: {'ENABLED' if self.files_implemented_count > 0 else 'DISABLED'}"
        )

        # Create a simulated read_file tool call
        simulated_read_file_call = {
            "id": "test_read_file_optimization",
            "name": "read_file",
            "input": {"file_path": test_file},
        }

        print("\n🔄 Simulating read_file call:")
        print(f"   Tool: {simulated_read_file_call['name']}")
        print(f"   File: {simulated_read_file_call['input']['file_path']}")

        # Execute the tool call (this should trigger automatic optimization)
        results = await self.execute_tool_calls([simulated_read_file_call])

        if results:
            result = results[0]
            print("\n✅ Tool execution completed:")
            print(f"   Tool name: {result.get('tool_name', 'N/A')}")
            print(f"   Tool ID: {result.get('tool_id', 'N/A')}")

            # Parse the result to check if optimization occurred
            import json

            try:
                result_data = json.loads(result.get("result", "{}"))
                if result_data.get("optimization") == "redirected_to_read_code_mem":
                    print("🎉 SUCCESS: read_file was automatically optimized!")
                    print(
                        f"   Original tool: {result_data.get('original_tool', 'N/A')}"
                    )
                    print(f"   Status: {result_data.get('status', 'N/A')}")
                elif result_data.get("status") == "summary_found":
                    print("🎉 SUCCESS: Summary was found and returned!")
                else:
                    print("ℹ️  INFO: No optimization occurred (no summary available)")
            except json.JSONDecodeError:
                print("⚠️  WARNING: Could not parse result as JSON")
        else:
            print("❌ ERROR: No results returned from tool execution")

        print("\n" + "=" * 80)
        print("🔄 AUTOMATIC READ_FILE OPTIMIZATION TEST COMPLETE")
        print("=" * 80)

    async def test_summary_optimization(self, test_file_path: str = "config.py"):
        """
        Test the summary optimization functionality with a specific file
        测试特定文件的总结优化功能

        Args:
            test_file_path: File path to test (default: config.py which should be in summary)
        """
        if not self.mcp_agent:
            return False

        try:
            # Use MCP agent to call read_code_mem tool
            result = await self.mcp_agent.call_tool(
                "read_code_mem", {"file_paths": [test_file_path]}
            )

            # Parse the result to check if summary was found
            import json

            result_data = json.loads(result) if isinstance(result, str) else result

            return (
                result_data.get("status")
                in ["all_summaries_found", "partial_summaries_found"]
                and result_data.get("summaries_found", 0) > 0
            )
        except Exception as e:
            self.logger.warning(f"Failed to test read_code_mem optimization: {e}")
            return False

    async def test_read_tools_configuration(self):
        """
        Test the read tools configuration to verify enabling/disabling works correctly
        测试读取工具配置以验证启用/禁用是否正常工作
        """
        print("=" * 60)
        print("🧪 TESTING READ TOOLS CONFIGURATION")
        print("=" * 60)

        status = self.get_read_tools_status()
        print(f"Read tools enabled: {status['read_tools_enabled']}")
        print(f"Status: {status['status']}")
        print(f"Tools affected: {status['tools_affected']}")

        # Test with mock tool calls
        test_tools = [
            {
                "id": "test_read_file",
                "name": "read_file",
                "input": {"file_path": "test.py"},
            },
            {
                "id": "test_read_code_mem",
                "name": "read_code_mem",
                "input": {"file_path": "test.py"},
            },
            {
                "id": "test_write_file",
                "name": "write_file",
                "input": {"file_path": "test.py", "content": "# test"},
            },
        ]

        print(
            f"\n🔄 Testing tool execution with read_tools_enabled={self.enable_read_tools}"
        )

        for tool_call in test_tools:
            tool_name = tool_call["name"]
            if not self.enable_read_tools and tool_name in [
                "read_file",
                "read_code_mem",
            ]:
                print(f"🚫 {tool_name}: Would be SKIPPED (disabled)")
            else:
                print(f"✅ {tool_name}: Would be EXECUTED")

        print("=" * 60)
        print("🧪 READ TOOLS CONFIGURATION TEST COMPLETE")
        print("=" * 60)

        return status
