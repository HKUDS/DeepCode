#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Comprehensive Dialogue Logger for Code Implementation Workflow
Logs complete conversation rounds with detailed formatting and paper-specific organization
"""

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, List


class DialogueLogger:
    """
    Comprehensive dialogue logger for code implementation workflow
    Captures complete conversation rounds with proper formatting and organization
    """

    def __init__(self, paper_id: str, base_path: str = None):
        """
        Initialize dialogue logger for a specific paper

        Args:
            paper_id: Paper identifier (e.g., "1", "2", etc.)
            base_path: Base path for logs (defaults to agent_folders structure)
        """
        self.paper_id = paper_id
        self.base_path = (
            base_path
            or "/data2/bjdwhzzh/project-hku/Code-Agent2.0/Code-Agent/deepcode-mcp/agent_folders"
        )
        self.log_directory = os.path.join(
            self.base_path, "papers", str(paper_id), "logs"
        )

        # Create log directory if it doesn't exist
        Path(self.log_directory).mkdir(parents=True, exist_ok=True)

        # Session tracking (initialize before log file creation)
        self.round_counter = 0
        self.session_start_time = datetime.now()
        self.current_round_data = {}

        # Generate log filename with timestamp
        timestamp = self.session_start_time.strftime("%Y%m%d_%H%M%S")
        self.log_filename = f"dialogue_log_{timestamp}.md"
        self.log_filepath = os.path.join(self.log_directory, self.log_filename)

        # Initialize log file with header
        self._initialize_log_file()

        print(f"📝 Dialogue Logger initialized for Paper {paper_id}")
        print(f"📁 Log file: {self.log_filepath}")

    def _initialize_log_file(self):
        """Initialize the log file with header information"""
        header = f"""# Code Implementation Dialogue Log

**Paper ID:** {self.paper_id}
**Session Start:** {self.session_start_time.strftime('%Y-%m-%d %H:%M:%S')}
**Log File:** {self.log_filename}

---

## Session Overview

This log contains the complete conversation rounds between the user and assistant during the code implementation workflow. Each round includes:

- System prompts and user messages
- Assistant responses with tool calls
- Tool execution results
- Implementation progress markers

---

"""
        try:
            with open(self.log_filepath, "w", encoding="utf-8") as f:
                f.write(header)
        except Exception as e:
            print(f"⚠️ Failed to initialize log file: {e}")

    def start_new_round(
        self, round_type: str = "implementation", context: Dict[str, Any] = None
    ):
        """
        Start a new dialogue round

        Args:
            round_type: Type of round (implementation, summary, error_handling, etc.)
            context: Additional context information (may include 'iteration' to sync with workflow)
        """
        # Use iteration from context if provided, otherwise increment round_counter
        if context and "iteration" in context:
            self.round_counter = context["iteration"]
        else:
            self.round_counter += 1

        self.current_round_data = {
            "round_number": self.round_counter,
            "round_type": round_type,
            "start_time": datetime.now(),
            "context": context or {},
            "messages": [],
            "tool_calls": [],
            "results": [],
            "metadata": {},
        }

        print(f"🔄 Starting Round {self.round_counter}: {round_type}")

    def log_system_prompt(self, prompt: str, prompt_type: str = "system"):
        """
        Log system prompt or instructions

        Args:
            prompt: System prompt content
            prompt_type: Type of prompt (system, instruction, etc.)
        """
        if not self.current_round_data:
            self.start_new_round("system_setup")

        self.current_round_data["messages"].append(
            {
                "role": "system",
                "type": prompt_type,
                "content": prompt,
                "timestamp": datetime.now().isoformat(),
            }
        )

    def log_user_message(self, message: str, message_type: str = "user_input"):
        """
        Log user message

        Args:
            message: User message content
            message_type: Type of message (user_input, feedback, guidance, etc.)
        """
        if not self.current_round_data:
            self.start_new_round("user_interaction")

        self.current_round_data["messages"].append(
            {
                "role": "user",
                "type": message_type,
                "content": message,
                "timestamp": datetime.now().isoformat(),
            }
        )

    def log_assistant_response(
        self, response: str, response_type: str = "assistant_response"
    ):
        """
        Log assistant response

        Args:
            response: Assistant response content
            response_type: Type of response (assistant_response, analysis, etc.)
        """
        if not self.current_round_data:
            self.start_new_round("assistant_interaction")

        self.current_round_data["messages"].append(
            {
                "role": "assistant",
                "type": response_type,
                "content": response,
                "timestamp": datetime.now().isoformat(),
            }
        )

    def log_tool_calls(self, tool_calls: List[Dict[str, Any]]):
        """
        Log tool calls made by the assistant

        Args:
            tool_calls: List of tool calls with id, name, and input
        """
        if not self.current_round_data:
            self.start_new_round("tool_execution")

        for tool_call in tool_calls:
            self.current_round_data["tool_calls"].append(
                {
                    "id": tool_call.get("id", ""),
                    "name": tool_call.get("name", ""),
                    "input": tool_call.get("input", {}),
                    "timestamp": datetime.now().isoformat(),
                }
            )

    def log_tool_results(self, tool_results: List[Dict[str, Any]]):
        """
        Log tool execution results

        Args:
            tool_results: List of tool results with tool_name and result
        """
        if not self.current_round_data:
            self.start_new_round("tool_results")

        for result in tool_results:
            self.current_round_data["results"].append(
                {
                    "tool_name": result.get("tool_name", ""),
                    "result": result.get("result", ""),
                    "timestamp": datetime.now().isoformat(),
                }
            )

    def log_metadata(self, key: str, value: Any):
        """
        Log metadata information

        Args:
            key: Metadata key
            value: Metadata value
        """
        if not self.current_round_data:
            self.start_new_round("metadata")

        self.current_round_data["metadata"][key] = value

    def log_memory_optimization(
        self,
        messages_before: List[Dict],
        messages_after: List[Dict],
        optimization_stats: Dict[str, Any],
        approach: str = "memory_optimization",
    ):
        """
        Log memory optimization details including before/after message content

        Args:
            messages_before: Messages before optimization
            messages_after: Messages after optimization
            optimization_stats: Statistics about the optimization
            approach: Optimization approach used
        """
        if not self.current_round_data:
            self.start_new_round("memory_optimization")

        # Calculate what was removed/kept
        removed_count = len(messages_before) - len(messages_after)
        compression_ratio = (
            (removed_count / len(messages_before) * 100) if messages_before else 0
        )

        # Log the optimization details
        optimization_data = {
            "approach": approach,
            "messages_before_count": len(messages_before),
            "messages_after_count": len(messages_after),
            "messages_removed_count": removed_count,
            "compression_ratio": f"{compression_ratio:.1f}%",
            "optimization_stats": optimization_stats,
            "timestamp": datetime.now().isoformat(),
        }

        # Store the optimization data
        if "memory_optimizations" not in self.current_round_data:
            self.current_round_data["memory_optimizations"] = []

        self.current_round_data["memory_optimizations"].append(
            {
                "optimization_data": optimization_data,
                "messages_before": messages_before,
                "messages_after": messages_after,
            }
        )

        # Log metadata
        self.log_metadata("memory_optimization", optimization_data)

        print(
            f"🧹 Memory optimization logged: {len(messages_before)} → {len(messages_after)} messages ({compression_ratio:.1f}% compression)"
        )

    def complete_round(self, summary: str = "", status: str = "completed"):
        """
        Complete the current round and write to log file

        Args:
            summary: Round summary
            status: Round completion status
        """
        if not self.current_round_data:
            print("⚠️ No active round to complete")
            return

        self.current_round_data["end_time"] = datetime.now()
        self.current_round_data["duration"] = (
            self.current_round_data["end_time"] - self.current_round_data["start_time"]
        ).total_seconds()
        self.current_round_data["summary"] = summary
        self.current_round_data["status"] = status

        # Write round to log file
        self._write_round_to_log()

        print(f"✅ Round {self.round_counter} completed: {status}")

        # Clear current round data
        self.current_round_data = {}

    def _write_round_to_log(self):
        """Write the current round data to the log file in markdown format"""
        try:
            with open(self.log_filepath, "a", encoding="utf-8") as f:
                round_data = self.current_round_data

                # Round header
                f.write(
                    f"\n## Round {round_data['round_number']}: {round_data['round_type'].title()}\n\n"
                )
                f.write(
                    f"**Start Time:** {round_data['start_time'].strftime('%Y-%m-%d %H:%M:%S')}\n"
                )
                f.write(
                    f"**End Time:** {round_data['end_time'].strftime('%Y-%m-%d %H:%M:%S')}\n"
                )
                f.write(f"**Duration:** {round_data['duration']:.2f} seconds\n")
                f.write(f"**Status:** {round_data['status']}\n\n")

                # Context information
                if round_data.get("context"):
                    f.write("### Context\n\n")
                    for key, value in round_data["context"].items():
                        f.write(f"- **{key}:** {value}\n")
                    f.write("\n")

                # Messages
                if round_data.get("messages"):
                    f.write("### Messages\n\n")
                    for i, msg in enumerate(round_data["messages"], 1):
                        role_emoji = {
                            "system": "🔧",
                            "user": "👤",
                            "assistant": "🤖",
                        }.get(msg["role"], "📝")
                        f.write(
                            f"#### {role_emoji} {msg['role'].title()} Message {i}\n\n"
                        )
                        f.write(f"**Type:** {msg['type']}\n")
                        f.write(f"**Timestamp:** {msg['timestamp']}\n\n")
                        f.write("```\n")
                        f.write(msg["content"])
                        f.write("\n```\n\n")

                # Tool calls
                if round_data.get("tool_calls"):
                    f.write("### Tool Calls\n\n")
                    for i, tool_call in enumerate(round_data["tool_calls"], 1):
                        f.write(f"#### 🛠️ Tool Call {i}: {tool_call['name']}\n\n")
                        f.write(f"**ID:** {tool_call['id']}\n")
                        f.write(f"**Timestamp:** {tool_call['timestamp']}\n\n")
                        f.write("**Input:**\n")
                        f.write("```json\n")
                        f.write(
                            json.dumps(tool_call["input"], indent=2, ensure_ascii=False)
                        )
                        f.write("\n```\n\n")

                # Tool results
                if round_data.get("results"):
                    f.write("### Tool Results\n\n")
                    for i, result in enumerate(round_data["results"], 1):
                        f.write(f"#### 📊 Result {i}: {result['tool_name']}\n\n")
                        f.write(f"**Timestamp:** {result['timestamp']}\n\n")
                        f.write("**Result:**\n")
                        f.write("```\n")
                        f.write(str(result["result"]))
                        f.write("\n```\n\n")

                # Memory Optimizations
                if round_data.get("memory_optimizations"):
                    f.write("### Memory Optimizations\n\n")
                    for i, opt in enumerate(round_data["memory_optimizations"], 1):
                        opt_data = opt["optimization_data"]
                        messages_before = opt["messages_before"]
                        messages_after = opt["messages_after"]

                        f.write(f"#### 🧹 Memory Optimization {i}\n\n")
                        f.write(f"**Approach:** {opt_data['approach']}\n")
                        f.write(
                            f"**Messages Before:** {opt_data['messages_before_count']}\n"
                        )
                        f.write(
                            f"**Messages After:** {opt_data['messages_after_count']}\n"
                        )
                        f.write(
                            f"**Messages Removed:** {opt_data['messages_removed_count']}\n"
                        )
                        f.write(
                            f"**Compression Ratio:** {opt_data['compression_ratio']}\n"
                        )
                        f.write(f"**Timestamp:** {opt_data['timestamp']}\n\n")

                        # Show optimization stats
                        if opt_data.get("optimization_stats"):
                            f.write("**Optimization Statistics:**\n")
                            f.write("```json\n")
                            f.write(
                                json.dumps(
                                    opt_data["optimization_stats"],
                                    indent=2,
                                    ensure_ascii=False,
                                )
                            )
                            f.write("\n```\n\n")

                        # Show messages before optimization (limited to last 5 for readability)
                        if messages_before:
                            f.write("**Messages Before Optimization (last 5):**\n\n")
                            for j, msg in enumerate(messages_before[-5:], 1):
                                role = msg.get("role", "unknown")
                                content = msg.get("content", "")
                                # Truncate very long messages
                                if len(content) > 3000:
                                    content = content[:3000] + "...[truncated]"
                                f.write(
                                    f"- **{role} {j}:** {content[:3000]}{'...' if len(content) > 100 else ''}\n"
                                )
                            f.write("\n")

                        # Show messages after optimization
                        if messages_after:
                            f.write("**Messages After Optimization:**\n\n")
                            for j, msg in enumerate(messages_after, 1):
                                role = msg.get("role", "unknown")
                                content = msg.get("content", "")
                                # Truncate very long messages
                                if len(content) > 3000:
                                    content = content[:3000] + "...[truncated]"
                                f.write(
                                    f"- **{role} {j}:** {content[:3000]}{'...' if len(content) > 100 else ''}\n"
                                )
                            f.write("\n")

                        # Show what was removed
                        if len(messages_before) > len(messages_after):
                            removed_messages = (
                                messages_before[: -len(messages_after)]
                                if messages_after
                                else messages_before
                            )
                            f.write(
                                f"**Messages Removed ({len(removed_messages)}):**\n\n"
                            )
                            for j, msg in enumerate(
                                removed_messages[-3:], 1
                            ):  # Show last 3 removed
                                role = msg.get("role", "unknown")
                                content = msg.get("content", "")
                                if len(content) > 3000:
                                    content = content[:3000] + "...[truncated]"
                                f.write(f"- **{role} {j}:** {content}\n")
                            f.write("\n")

                        f.write("\n")

                # Metadata
                if round_data.get("metadata"):
                    f.write("### Metadata\n\n")
                    for key, value in round_data["metadata"].items():
                        if (
                            key != "memory_optimization"
                        ):  # Skip memory optimization metadata as it's shown above
                            f.write(f"- **{key}:** {value}\n")
                    f.write("\n")

                # Summary
                if round_data.get("summary"):
                    f.write("### Summary\n\n")
                    f.write(round_data["summary"])
                    f.write("\n\n")

                # Separator
                f.write("---\n\n")

        except Exception as e:
            print(f"⚠️ Failed to write round to log: {e}")

    def log_complete_exchange(
        self,
        system_prompt: str = "",
        user_message: str = "",
        assistant_response: str = "",
        tool_calls: List[Dict] = None,
        tool_results: List[Dict] = None,
        round_type: str = "exchange",
        context: Dict = None,
        summary: str = "",
    ):
        """
        Log a complete exchange in a single call

        Args:
            system_prompt: System prompt (optional)
            user_message: User message
            assistant_response: Assistant response
            tool_calls: Tool calls made
            tool_results: Tool execution results
            round_type: Type of round
            context: Additional context
            summary: Round summary
        """
        self.start_new_round(round_type, context)

        if system_prompt:
            self.log_system_prompt(system_prompt)

        if user_message:
            self.log_user_message(user_message)

        if assistant_response:
            self.log_assistant_response(assistant_response)

        if tool_calls:
            self.log_tool_calls(tool_calls)

        if tool_results:
            self.log_tool_results(tool_results)

        self.complete_round(summary)

    def get_session_stats(self) -> Dict[str, Any]:
        """Get session statistics"""
        return {
            "paper_id": self.paper_id,
            "session_start": self.session_start_time.isoformat(),
            "total_rounds": self.round_counter,
            "log_file": self.log_filepath,
            "session_duration": (
                datetime.now() - self.session_start_time
            ).total_seconds(),
        }

    def finalize_session(self, final_summary: str = ""):
        """
        Finalize the logging session

        Args:
            final_summary: Final session summary
        """
        try:
            with open(self.log_filepath, "a", encoding="utf-8") as f:
                f.write("\n## Session Summary\n\n")
                f.write(f"**Total Rounds:** {self.round_counter}\n")
                f.write(
                    f"**Session Duration:** {(datetime.now() - self.session_start_time).total_seconds():.2f} seconds\n"
                )
                f.write(
                    f"**End Time:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
                )

                if final_summary:
                    f.write("### Final Summary\n\n")
                    f.write(final_summary)
                    f.write("\n\n")

                f.write("---\n\n")
                f.write("*End of Session*\n")

        except Exception as e:
            print(f"⚠️ Failed to finalize session: {e}")

        print(f"🎯 Session finalized: {self.round_counter} rounds logged")


# Utility functions for easy integration
def create_dialogue_logger(paper_id: str, base_path: str = None) -> DialogueLogger:
    """
    Create a dialogue logger for a specific paper

    Args:
        paper_id: Paper identifier
        base_path: Base path for logs

    Returns:
        DialogueLogger instance
    """
    return DialogueLogger(paper_id, base_path)


def extract_paper_id_from_path(path: str) -> str:
    """
    Extract paper ID from a file path

    Args:
        path: File path containing paper information

    Returns:
        Paper ID string
    """
    # Extract paper ID from path like "/data2/.../papers/1/initial_plan.txt"
    parts = path.split("/")
    for i, part in enumerate(parts):
        if part == "papers" and i + 1 < len(parts):
            return parts[i + 1]
    return "unknown"


# Example usage
if __name__ == "__main__":
    # Test the dialogue logger
    logger = DialogueLogger("1")

    # Log a complete exchange
    logger.log_complete_exchange(
        system_prompt="You are a code implementation assistant.",
        user_message="Implement the transformer model",
        assistant_response="I'll implement the transformer model step by step.",
        tool_calls=[
            {"id": "1", "name": "write_file", "input": {"filename": "transformer.py"}}
        ],
        tool_results=[
            {"tool_name": "write_file", "result": "File created successfully"}
        ],
        round_type="implementation",
        context={"files_implemented": 1},
        summary="Successfully implemented transformer model",
    )

    # Test memory optimization logging
    logger.start_new_round(
        "memory_optimization", {"trigger_reason": "write_file_detected"}
    )

    # Mock messages before and after optimization
    messages_before = [
        {"role": "user", "content": "Original message 1"},
        {"role": "assistant", "content": "Original response 1"},
        {"role": "user", "content": "Original message 2"},
        {"role": "assistant", "content": "Original response 2"},
        {"role": "user", "content": "Original message 3"},
    ]

    messages_after = [
        {"role": "user", "content": "Original message 1"},
        {"role": "assistant", "content": "Original response 1"},
        {"role": "user", "content": "Original message 3"},
    ]

    # Mock optimization stats
    optimization_stats = {
        "implemented_files_tracked": 2,
        "current_round": 5,
        "concise_mode_active": True,
    }

    # Log memory optimization
    logger.log_memory_optimization(
        messages_before=messages_before,
        messages_after=messages_after,
        optimization_stats=optimization_stats,
        approach="clear_after_write_file",
    )

    logger.complete_round("Memory optimization test completed")

    # Finalize session
    logger.finalize_session(
        "Test session with memory optimization logging completed successfully"
    )

    print("✅ Dialogue logger test completed with memory optimization")
