"""
Concise Memory Agent for Code Implementation Workflow
简洁的代码实现工作流内存代理

This memory agent implements a focused approach:
1. Before first file: Normal conversation flow
2. After first file: Keep only system_prompt + initial_plan + current round tool results
3. Clean slate for each new code file generation

Key Features:
- Preserves system prompt and initial plan always
- After first file generation, discards previous conversation history
- Keeps only current round tool results from essential tools:
  * read_code_mem, read_file, write_file
  * execute_python, execute_bash
  * search_code, search_reference_code, get_file_structure
- Provides clean, focused input for next write_file operation
"""

import json
import logging
import os
import time
from datetime import datetime
from typing import Dict, Any, List, Optional


class ConciseMemoryAgent:
    """
    Concise Memory Agent - Focused Information Retention

    Core Philosophy:
    - Preserve essential context (system prompt + initial plan)
    - After first file generation, use clean slate approach
    - Keep only current round tool results from all essential MCP tools
    - Remove conversational clutter and previous tool calls

    Essential Tools Tracked:
    - File Operations: read_code_mem, read_file, write_file
    - Code Analysis: search_code, search_reference_code, get_file_structure
    - Execution: execute_python, execute_bash
    """

    def __init__(
        self,
        initial_plan_content: str,
        logger: Optional[logging.Logger] = None,
        target_directory: Optional[str] = None,
        default_models: Optional[Dict[str, str]] = None,
    ):
        """
        Initialize Concise Memory Agent

        Args:
            initial_plan_content: Content of initial_plan.txt
            logger: Logger instance
            target_directory: Target directory for saving summaries
            default_models: Default models configuration from workflow
        """
        self.logger = logger or self._create_default_logger()
        self.initial_plan = initial_plan_content

        # Store default models configuration
        self.default_models = default_models or {
            "anthropic": "claude-sonnet-4-20250514",
            "openai": "gpt-4o",
        }

        # Memory state tracking - new logic: trigger after each write_file
        self.last_write_file_detected = (
            False  # Track if write_file was called in current iteration
        )
        self.should_clear_memory_next = False  # Flag to clear memory in next round
        self.current_round = 0

        # Parse phase structure from initial plan
        self.phase_structure = self._parse_phase_structure()

        # Memory configuration
        if target_directory:
            self.save_path = target_directory
        else:
            self.save_path = "./deepcode_lab/papers/1/"

        # Code summary file path
        self.code_summary_path = os.path.join(
            self.save_path, "implement_code_summary.md"
        )

        # Current round tool results storage
        self.current_round_tool_results = []

        # Track all implemented files
        self.implemented_files = []

        self.logger.info(
            f"Concise Memory Agent initialized with target directory: {self.save_path}"
        )
        self.logger.info(f"Code summary will be saved to: {self.code_summary_path}")
        # self.logger.info(f"🤖 Using models - Anthropic: {self.default_models['anthropic']}, OpenAI: {self.default_models['openai']}")
        self.logger.info(
            "📝 NEW LOGIC: Memory clearing triggered after each write_file call"
        )

    def _create_default_logger(self) -> logging.Logger:
        """Create default logger"""
        logger = logging.getLogger(f"{__name__}.ConciseMemoryAgent")
        logger.setLevel(logging.INFO)
        return logger

    def _parse_phase_structure(self) -> Dict[str, List[str]]:
        """Parse implementation phases from initial plan"""
        try:
            phases = {}
            lines = self.initial_plan.split("\n")
            current_phase = None

            for line in lines:
                if "Phase" in line and ":" in line:
                    # Extract phase name
                    phase_parts = line.split(":")
                    if len(phase_parts) >= 2:
                        current_phase = phase_parts[0].strip()
                        phases[current_phase] = []
                elif current_phase and line.strip().startswith("-"):
                    # This is a file in the current phase
                    file_line = line.strip()[1:].strip()
                    if file_line.startswith("`") and file_line.endswith("`"):
                        file_name = file_line[1:-1]
                        phases[current_phase].append(file_name)
                elif current_phase and not line.strip():
                    # Empty line might indicate end of phase
                    continue
                elif current_phase and line.strip().startswith("###"):
                    # New section, end current phase
                    current_phase = None

            return phases

        except Exception as e:
            self.logger.warning(f"Failed to parse phase structure: {e}")
            return {}

    def record_file_implementation(
        self, file_path: str, implementation_content: str = ""
    ):
        """
        Record a newly implemented file (simplified version)
        NEW LOGIC: File implementation is tracked via write_file tool detection

        Args:
            file_path: Path of the implemented file
            implementation_content: Content of the implemented file
        """
        # Add file to implemented files list if not already present
        if file_path not in self.implemented_files:
            self.implemented_files.append(file_path)

        self.logger.info(f"📝 File implementation recorded: {file_path}")

    async def create_code_implementation_summary(
        self,
        client,
        client_type: str,
        file_path: str,
        implementation_content: str,
        files_implemented: int,
    ) -> str:
        """
        Create LLM-based code implementation summary after writing a file
        Uses LLM to analyze and summarize the implemented code

        Args:
            client: LLM client instance
            client_type: Type of LLM client ("anthropic" or "openai")
            file_path: Path of the implemented file
            implementation_content: Content of the implemented file
            files_implemented: Number of files implemented so far

        Returns:
            LLM-generated formatted code implementation summary
        """
        try:
            # Record the file implementation first
            self.record_file_implementation(file_path, implementation_content)

            # Create prompt for LLM summary
            summary_prompt = self._create_code_summary_prompt(
                file_path, implementation_content, files_implemented
            )
            summary_messages = [{"role": "user", "content": summary_prompt}]

            # Get LLM-generated summary
            llm_response = await self._call_llm_for_summary(
                client, client_type, summary_messages
            )
            llm_summary = llm_response.get("content", "")

            # Format the summary in the requested structure
            formatted_summary = self._format_code_implementation_summary(
                file_path, llm_summary, files_implemented
            )

            # Save to implement_code_summary.md (append mode)
            await self._save_code_summary_to_file(formatted_summary, file_path)

            self.logger.info(f"Created and saved code summary for: {file_path}")
            return formatted_summary

        except Exception as e:
            self.logger.error(
                f"Failed to create LLM-based code implementation summary: {e}"
            )
            # Fallback to simple summary
            return self._create_fallback_code_summary(
                file_path, implementation_content, files_implemented
            )

    def _create_code_summary_prompt(
        self, file_path: str, implementation_content: str, files_implemented: int
    ) -> str:
        """
        Create prompt for LLM to generate code implementation summary

        Args:
            file_path: Path of the implemented file
            implementation_content: Content of the implemented file
            files_implemented: Number of files implemented so far

        Returns:
            Prompt for LLM summarization
        """
        current_round = self.current_round

        # Create formatted list of implemented files
        implemented_files_list = (
            "\n".join([f"- {file}" for file in self.implemented_files])
            if self.implemented_files
            else "- None yet"
        )

        prompt = f"""You are an expert code implementation summarizer. Analyze the implemented code file and create a structured summary.

**🚨 CRITICAL: The files listed below are ALREADY IMPLEMENTED - DO NOT suggest them in Next Steps! 🚨**

**All Previously Implemented Files:**
{implemented_files_list}

**Current Implementation Context:**
- **File Implemented**: {file_path}
- **Current Round**: {current_round}
- **Total Files Implemented**: {files_implemented}


**Initial Plan Reference:**
{self.initial_plan[:]}

**Implemented Code Content:**
```
{implementation_content[:]}
```

**Required Summary Format:**

1. **Status Marker**: Mark the phase and round corresponding to this code file
   Format: Phase {{phase_name}}, Round {{round_number}}

2. **Implementation Progress**: List the code file completed in current round and core implementation ideas
   Format: {{file_path}}: {{core implementation ideas}}

3. **Dependencies**: According to the File Structure and initial plan, list functions that may be called by other files
   Format: {{file_path}}: Function {{function_name}}: core ideas--{{ideas}}; Required parameters--{{params}}; Return parameters--{{returns}}
   Required packages: {{packages}}

4. **Next Steps**: List code files that will be implemented in the next round (EXCLUDE already implemented files)
   Format: Code will be implemented: {{file_path}}; will stay on Phase {{phase}}/ will go to Phase {{next_phase}}
   **WARNING: Do NOT suggest any file from the "All Previously Implemented Files" list above!**

**Instructions:**
- Be precise and concise
- Focus on function interfaces that other files will need
- Extract actual function signatures from the code
- **CRITICAL: For Next Steps, ONLY suggest files that are NOT in the "All Previously Implemented Files" list above**
- **NEVER suggest implementing a file that is already in the implemented files list**
- Predict next implementation steps based on the initial plan but exclude already completed files
- Use the exact format specified above

**Summary:**"""

        return prompt

    def _format_code_implementation_summary(
        self, file_path: str, llm_summary: str, files_implemented: int
    ) -> str:
        """
        Format the LLM-generated summary into the final structure

        Args:
            file_path: Path of the implemented file
            llm_summary: LLM-generated summary content
            files_implemented: Number of files implemented so far

        Returns:
            Formatted summary
        """
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # Create formatted list of implemented files
        implemented_files_list = (
            "\n".join([f"- {file}" for file in self.implemented_files])
            if self.implemented_files
            else "- None yet"
        )

        formatted_summary = f"""# Code Implementation Summary
**All Previously Implemented Files:**
{implemented_files_list}
**Generated**: {timestamp}
**File Implemented**: {file_path}
**Total Files Implemented**: {files_implemented}

{llm_summary}

---
*Auto-generated by Memory Agent*
"""
        return formatted_summary

    def _create_fallback_code_summary(
        self, file_path: str, implementation_content: str, files_implemented: int
    ) -> str:
        """
        Create fallback summary when LLM is unavailable

        Args:
            file_path: Path of the implemented file
            implementation_content: Content of the implemented file
            files_implemented: Number of files implemented so far

        Returns:
            Fallback summary
        """
        # Create formatted list of implemented files
        implemented_files_list = (
            "\n".join([f"- {file}" for file in self.implemented_files])
            if self.implemented_files
            else "- None yet"
        )

        summary = f"""# Code Implementation Summary
**All Previously Implemented Files:**
{implemented_files_list}
**Generated**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
**File Implemented**: {file_path}
**Total Files Implemented**: {files_implemented}
**Summary failed to generate.**

---
*Auto-generated by Concise Memory Agent (Fallback Mode)*
"""
        return summary

    async def _save_code_summary_to_file(self, new_summary: str, file_path: str):
        """
        Append code implementation summary to implement_code_summary.md
        Accumulates all implementations with clear separators

        Args:
            new_summary: New summary content to append
            file_path: Path of the file for which the summary was generated
        """
        try:
            # Create directory if it doesn't exist
            os.makedirs(os.path.dirname(self.code_summary_path), exist_ok=True)

            # Check if file exists to determine if we need header
            file_exists = os.path.exists(self.code_summary_path)

            # Open in append mode to accumulate all implementations
            with open(self.code_summary_path, "a", encoding="utf-8") as f:
                if not file_exists:
                    # Write header for new file
                    f.write("# Code Implementation Progress Summary\n")
                    f.write("*Accumulated implementation progress for all files*\n\n")

                # Add clear separator between implementations
                f.write("\n" + "=" * 80 + "\n")
                f.write(
                    f"## IMPLEMENTATION File {file_path}; ROUND {self.current_round} \n"
                )
                f.write("=" * 80 + "\n\n")

                # Write the new summary
                f.write(new_summary)
                f.write("\n\n")

            self.logger.info(
                f"Appended LLM-based code implementation summary to: {self.code_summary_path}"
            )

        except Exception as e:
            self.logger.error(f"Failed to save code implementation summary: {e}")

    async def _call_llm_for_summary(
        self, client, client_type: str, summary_messages: List[Dict]
    ) -> Dict[str, Any]:
        """
        Call LLM for code implementation summary generation ONLY
        调用LLM生成代码实现总结（仅用于代码总结）

        This method is used only for creating code implementation summaries,
        NOT for conversation summarization which has been removed.
        """
        if client_type == "anthropic":
            response = await client.messages.create(
                model=self.default_models["anthropic"],
                system="You are an expert code implementation summarizer. Create structured summaries of implemented code files that preserve essential information about functions, dependencies, and implementation approaches.",
                messages=summary_messages,
                max_tokens=5000,
                temperature=0.2,
            )

            content = ""
            for block in response.content:
                if block.type == "text":
                    content += block.text

            return {"content": content}

        elif client_type == "openai":
            openai_messages = [
                {
                    "role": "system",
                    "content": "You are an expert code implementation summarizer. Create structured summaries of implemented code files that preserve essential information about functions, dependencies, and implementation approaches.",
                }
            ]
            openai_messages.extend(summary_messages)

            response = await client.chat.completions.create(
                model=self.default_models["openai"],
                messages=openai_messages,
                max_tokens=5000,
                temperature=0.2,
            )

            return {"content": response.choices[0].message.content or ""}

        else:
            raise ValueError(f"Unsupported client type: {client_type}")

    def start_new_round(self, iteration: Optional[int] = None):
        """Start a new dialogue round and reset tool results

        Args:
            iteration: Optional iteration number from workflow to sync with current_round
        """
        if iteration is not None:
            # Sync with workflow iteration
            self.current_round = iteration
            # self.logger.info(f"🔄 Synced round with workflow iteration {iteration}")
        else:
            # Default behavior: increment round counter
            self.current_round += 1
            self.logger.info(f"🔄 Started new round {self.current_round}")

        self.current_round_tool_results = []  # Clear previous round results
        # Note: Don't reset last_write_file_detected and should_clear_memory_next here
        # These flags persist across rounds until memory optimization is applied
        # self.logger.info(f"🔄 Round {self.current_round} - Tool results cleared, memory flags preserved")

    def record_tool_result(
        self, tool_name: str, tool_input: Dict[str, Any], tool_result: Any
    ):
        """
        Record tool result for current round and detect write_file calls

        Args:
            tool_name: Name of the tool called
            tool_input: Input parameters for the tool
            tool_result: Result returned by the tool
        """
        # Detect write_file calls to trigger memory clearing
        if tool_name == "write_file":
            self.last_write_file_detected = True
            self.should_clear_memory_next = True
            # self.logger.info(f"🔄 WRITE_FILE DETECTED: {file_path} - Memory will be cleared in next round")

        # Only record specific tools that provide essential information
        essential_tools = [
            "read_code_mem",  # Read code summary from implement_code_summary.md
            "read_file",  # Read file contents
            "write_file",  # Write file contents (important for tracking implementations)
            "execute_python",  # Execute Python code (for testing/validation)
            "execute_bash",  # Execute bash commands (for build/execution)
            "search_code",  # Search code patterns
            "search_reference_code",  # Search reference code (if available)
            "get_file_structure",  # Get file structure (for understanding project layout)
        ]

        if tool_name in essential_tools:
            tool_record = {
                "tool_name": tool_name,
                "tool_input": tool_input,
                "tool_result": tool_result,
                "timestamp": time.time(),
            }
            self.current_round_tool_results.append(tool_record)
            # self.logger.info(f"📊 Essential tool result recorded: {tool_name} ({len(self.current_round_tool_results)} total)")

    def should_use_concise_mode(self) -> bool:
        """
        Check if concise memory mode should be used

        Returns:
            True if first file has been generated and concise mode should be active
        """
        return self.last_write_file_detected

    def create_concise_messages(
        self, system_prompt: str, messages: List[Dict[str, Any]], files_implemented: int
    ) -> List[Dict[str, Any]]:
        """
        Create concise message list for LLM input
        NEW LOGIC: Always clear after write_file, keep system_prompt + initial_plan + current round tools

        Args:
            system_prompt: Current system prompt
            messages: Original message list
            files_implemented: Number of files implemented so far

        Returns:
            Concise message list containing only essential information
        """
        if not self.last_write_file_detected:
            # Before any write_file, use normal flow
            self.logger.info(
                "🔄 Using normal conversation flow (before any write_file)"
            )
            return messages

        # After write_file detection, use concise approach with clean slate
        self.logger.info(
            f"🎯 Using CONCISE memory mode - Clear slate after write_file, Round {self.current_round}"
        )

        concise_messages = []

        # 1. Add initial plan message (always preserved)
        initial_plan_message = {
            "role": "user",
            "content": f"""**Task: Implement code based on the following reproduction plan**

**Code Reproduction Plan:**
{self.initial_plan}

**Working Directory:** Current workspace

**Current Status:** {files_implemented} files implemented

**Objective:** Continue implementation by analyzing dependencies and implementing the next required file according to the plan's priority order.""",
        }
        concise_messages.append(initial_plan_message)

        # 2. Add Knowledge Base
        knowledge_base_message = {
            "role": "user",
            "content": f"""**Below is the Knowledge Base of the LATEST implemented code file:**
{self._read_code_knowledge_base()}
""",
        }
        concise_messages.append(knowledge_base_message)

        # 3. Add current tool results (essential information for next file generation)
        if self.current_round_tool_results:
            tool_results_content = self._format_tool_results()
            tool_results_message = {
                "role": "user",
                "content": f"""**Current Tool Results:**
{tool_results_content}

**🚨 NEXT STEP: First determine if ALL files from the reproduction plan have been implemented:**

**If ALL files are implemented (reproduction plan complete):**
- Use `execute_python` or `execute_bash` to test the complete implementation
- If testing successful, respond with "**implementation complete**" to end the conversation
- Only use `read_code_mem` if debugging is needed during testing

**If MORE files need to be implemented:**
- #1. `read_code_mem` → Query summaries of relevant **already-implemented** files (agent should choose which implemented file paths to reference)(important!!!)
- #2. `search_code_references` → OPTIONALLY search reference patterns for inspiration (use for reference only, original paper specs take priority)
- #3. `write_file` → Create the complete code implementation based on original paper requirements
- #4. `execute_python` or `execute_bash` → Test the partial implementation if needed

**Remember:** Always check if all planned files are implemented before continuing with new file creation.""",
            }
            concise_messages.append(tool_results_message)
        else:
            # If no tool results yet, add guidance for next steps
            guidance_message = {
                "role": "user",
                "content": f"""**Current Round:** {self.current_round}

**Development Cycle - START HERE:**

**FIRST: Check if ALL files from the reproduction plan are implemented**
- If YES: Use `execute_python` or `execute_bash` for testing, then respond "**implementation complete**"
- If NO: Continue with file implementation cycle below

**For NEW file implementation:**
1. **You can call read_code_mem(*already_implemented_file_path*)** to understand existing implementations and dependencies - agent should choose relevant ALREADY IMPLEMENTED file paths for reference, NOT the new file you want to create
2. **Optionally use search_code_references** for reference patterns (OPTIONAL - for inspiration only, original paper specs take priority)
3. Write_file can be used to implement the new component based on original paper requirements
4. Finally: Use execute_python or execute_bash for testing (if needed)

**For TESTING/COMPLETION phase (when all files implemented):**
1. **➡️ FIRST: Use execute_python or execute_bash** to test the complete implementation
2. **If successful: Respond with "implementation complete"** to end the conversation
3. Only use read_code_mem if debugging is needed during testing""",
            }
            concise_messages.append(guidance_message)
        # **Available Essential Tools:** read_code_mem, write_file, execute_python, execute_bash
        # **Remember:** Start with read_code_mem when implementing NEW files to understand existing code. When all files are implemented, focus on testing and completion. Implement according to the original paper's specifications - any reference code is for inspiration only."""
        # self.logger.info(f"✅ Concise messages created: {len(concise_messages)} messages (original: {len(messages)})")
        return concise_messages

    def _read_code_knowledge_base(self) -> Optional[str]:
        """
        Read the implement_code_summary.md file as code knowledge base
        Returns only the final/latest implementation entry, not all historical entries

        Returns:
            Content of the latest implementation entry if it exists, None otherwise
        """
        try:
            if os.path.exists(self.code_summary_path):
                with open(self.code_summary_path, "r", encoding="utf-8") as f:
                    content = f.read().strip()

                if content:
                    # Extract only the final/latest implementation entry
                    return self._extract_latest_implementation_entry(content)
                else:
                    return None
            else:
                return None

        except Exception as e:
            self.logger.error(f"Failed to read code knowledge base: {e}")
            return None

    def _extract_latest_implementation_entry(self, content: str) -> Optional[str]:
        """
        Extract the latest/final implementation entry from the implement_code_summary.md content
        Uses a simpler approach to find the last implementation section

        Args:
            content: Full content of implement_code_summary.md

        Returns:
            Latest implementation entry content, or None if not found
        """
        try:
            import re

            # Pattern to match the start of implementation sections
            section_pattern = (
                r"={80}\s*\n## IMPLEMENTATION File .+?; ROUND \d+\s*\n={80}"
            )

            # Find all implementation section starts
            matches = list(re.finditer(section_pattern, content))

            if not matches:
                # No implementation sections found
                lines = content.split("\n")
                fallback_content = (
                    "\n".join(lines[:10]) + "\n... (truncated for brevity)"
                    if len(lines) > 10
                    else content
                )
                self.logger.info(
                    "📖 No implementation sections found, using fallback content"
                )
                return fallback_content

            # Get the start position of the last implementation section
            last_match = matches[-1]
            start_pos = last_match.start()

            # Take everything from the last section start to the end of content
            latest_entry = content[start_pos:].strip()

            # self.logger.info(f"📖 Extracted latest implementation entry from knowledge base")
            # print(f"DEBUG: Extracted content length: {len(latest_entry)}")
            # print(f"DEBUG: First 200 chars: {latest_entry[:]}")

            return latest_entry

        except Exception as e:
            self.logger.error(f"Failed to extract latest implementation entry: {e}")
            # Return last 1000 characters as fallback
            return content[-500:] if len(content) > 500 else content

    def _format_tool_results(self) -> str:
        """
        Format current round tool results for LLM input

        Returns:
            Formatted string of tool results
        """
        if not self.current_round_tool_results:
            return "No tool results in current round."

        formatted_results = []

        for result in self.current_round_tool_results:
            tool_name = result["tool_name"]
            tool_input = result["tool_input"]
            tool_result = result["tool_result"]

            # Format based on tool type
            if tool_name == "read_code_mem":
                file_path = tool_input.get("file_path", "unknown")
                formatted_results.append(f"""
**read_code_mem Result for {file_path}:**
{self._format_tool_result_content(tool_result)}
""")
            elif tool_name == "read_file":
                file_path = tool_input.get("file_path", "unknown")
                formatted_results.append(f"""
**read_file Result for {file_path}:**
{self._format_tool_result_content(tool_result)}
""")
            elif tool_name == "write_file":
                file_path = tool_input.get("file_path", "unknown")
                formatted_results.append(f"""
**write_file Result for {file_path}:**
{self._format_tool_result_content(tool_result)}
""")
            elif tool_name == "execute_python":
                code_snippet = (
                    tool_input.get("code", "")[:50] + "..."
                    if len(tool_input.get("code", "")) > 50
                    else tool_input.get("code", "")
                )
                formatted_results.append(f"""
**execute_python Result (code: {code_snippet}):**
{self._format_tool_result_content(tool_result)}
""")
            elif tool_name == "execute_bash":
                command = tool_input.get("command", "unknown")
                formatted_results.append(f"""
**execute_bash Result (command: {command}):**
{self._format_tool_result_content(tool_result)}
""")
            elif tool_name == "search_code":
                pattern = tool_input.get("pattern", "unknown")
                file_pattern = tool_input.get("file_pattern", "")
                formatted_results.append(f"""
**search_code Result (pattern: {pattern}, files: {file_pattern}):**
{self._format_tool_result_content(tool_result)}
""")
            elif tool_name == "search_reference_code":
                target_file = tool_input.get("target_file", "unknown")
                keywords = tool_input.get("keywords", "")
                formatted_results.append(f"""
**search_reference_code Result for {target_file} (keywords: {keywords}):**
{self._format_tool_result_content(tool_result)}
""")
            elif tool_name == "get_file_structure":
                directory = tool_input.get(
                    "directory_path", tool_input.get("path", "current")
                )
                formatted_results.append(f"""
**get_file_structure Result for {directory}:**
{self._format_tool_result_content(tool_result)}
""")

        return "\n".join(formatted_results)

    def _format_tool_result_content(self, tool_result: Any) -> str:
        """
        Format tool result content for display

        Args:
            tool_result: Tool result to format

        Returns:
            Formatted string representation
        """
        if isinstance(tool_result, str):
            # Try to parse as JSON for better formatting
            try:
                result_data = json.loads(tool_result)
                if isinstance(result_data, dict):
                    # Format key information
                    if result_data.get("status") == "summary_found":
                        return (
                            f"Summary found:\n{result_data.get('summary_content', '')}"
                        )
                    elif result_data.get("status") == "no_summary":
                        return "No summary available"
                    else:
                        return json.dumps(result_data, indent=2)
                else:
                    return str(result_data)
            except json.JSONDecodeError:
                return tool_result
        else:
            return str(tool_result)

    def get_memory_statistics(self, files_implemented: int = 0) -> Dict[str, Any]:
        """Get memory agent statistics"""
        return {
            "last_write_file_detected": self.last_write_file_detected,
            "should_clear_memory_next": self.should_clear_memory_next,
            "current_round": self.current_round,
            "concise_mode_active": self.should_use_concise_mode(),
            "current_round_tool_results": len(self.current_round_tool_results),
            "essential_tools_recorded": [
                r["tool_name"] for r in self.current_round_tool_results
            ],
            "implemented_files_tracked": files_implemented,
            "implemented_files_list": self.implemented_files.copy(),
            "phases_parsed": len(self.phase_structure),
        }

    def get_implemented_files(self) -> List[str]:
        """Get list of all implemented files"""
        return self.implemented_files.copy()

    def should_trigger_memory_optimization(
        self, messages: List[Dict[str, Any]], files_implemented: int = 0
    ) -> bool:
        """
        Check if memory optimization should be triggered
        NEW LOGIC: Trigger after write_file has been detected

        Args:
            messages: Current message list
            files_implemented: Number of files implemented so far

        Returns:
            True if concise mode should be applied
        """
        # Trigger if we detected write_file and should clear memory
        if self.should_clear_memory_next:
            # self.logger.info(f"🎯 Triggering CONCISE memory optimization (write_file detected, files: {files_implemented})")
            return True

        # No optimization before any write_file
        return False

    def apply_memory_optimization(
        self, system_prompt: str, messages: List[Dict[str, Any]], files_implemented: int
    ) -> List[Dict[str, Any]]:
        """
        Apply memory optimization using concise approach
        NEW LOGIC: Clear all history after write_file, keep only system_prompt + initial_plan + current tools

        Args:
            system_prompt: Current system prompt
            messages: Original message list
            files_implemented: Number of files implemented so far

        Returns:
            Optimized message list
        """
        if not self.should_clear_memory_next:
            # Before any write_file, return original messages
            return messages

        # Apply concise memory optimization after write_file detection
        # self.logger.info(f"🧹 CLEARING MEMORY after write_file - creating clean slate")
        optimized_messages = self.create_concise_messages(
            system_prompt, messages, files_implemented
        )

        # Clear the flag after applying optimization
        self.should_clear_memory_next = False

        compression_ratio = (
            ((len(messages) - len(optimized_messages)) / len(messages) * 100)
            if messages
            else 0
        )
        self.logger.info(
            f"🎯 CONCISE optimization applied: {len(messages)} → {len(optimized_messages)} messages ({compression_ratio:.1f}% compression)"
        )

        return optimized_messages

    def clear_current_round_tool_results(self):
        """Clear current round tool results (called when starting new round)"""
        self.current_round_tool_results = []
        self.logger.info("🧹 Current round tool results cleared")

    def debug_concise_state(self, files_implemented: int = 0):
        """Debug method to show current concise memory state"""
        stats = self.get_memory_statistics(files_implemented)

        print("=" * 60)
        print("🎯 CONCISE MEMORY AGENT STATE (Write-File-Based)")
        print("=" * 60)
        print(f"Last write_file detected: {stats['last_write_file_detected']}")
        print(f"Should clear memory next: {stats['should_clear_memory_next']}")
        print(f"Files implemented: {stats['implemented_files_tracked']}")
        print(f"Current round: {stats['current_round']}")
        print(f"Concise mode active: {stats['concise_mode_active']}")
        print(f"Current round tool results: {stats['current_round_tool_results']}")
        print(f"Essential tools recorded: {stats['essential_tools_recorded']}")
        print(f"Implemented files tracked: {len(self.implemented_files)}")
        print(f"Implemented files list: {self.implemented_files}")
        print(f"Code summary file exists: {os.path.exists(self.code_summary_path)}")
        print("")
        print(
            "📊 NEW LOGIC: write_file → clear memory → accumulate tools → next write_file"
        )
        print("📊 Essential Tools Tracked:")
        essential_tools = [
            "read_code_mem",
            "read_file",
            "write_file",
            "execute_python",
            "execute_bash",
            "search_code",
            "search_reference_code",
            "get_file_structure",
        ]
        for tool in essential_tools:
            tool_count = sum(
                1 for r in self.current_round_tool_results if r["tool_name"] == tool
            )
            print(f"  - {tool}: {tool_count} calls")
        print("=" * 60)
