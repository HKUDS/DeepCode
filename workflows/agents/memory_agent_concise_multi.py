"""
Concise Memory Agent for Code Implementation Workflow - Multi-File Only Support

This memory agent implements a focused approach with ONLY multi-file capabilities:
1. Before first batch: Normal conversation flow
2. After first batch: Keep only system_prompt + initial_plan + current round tool results
3. Clean slate for each new code batch generation
4. MULTI-FILE ONLY: Support for summarizing multiple files simultaneously (max 5)

Key Features:
- Preserves system prompt and initial plan always
- After first batch generation, discards previous conversation history
- Keeps only current round tool results from essential tools:
  * read_multiple_files, write_multiple_files
  * execute_python, execute_bash
  * search_code, search_reference_code, get_file_structure
- Provides clean, focused input for next write_multiple_files operation
- MULTI-FILE ONLY: No single file support
- FILE TRACKING: Gets ALL file information from workflow, no internal tracking
"""

import json
import logging
import os
import time
from datetime import datetime
from typing import Dict, Any, List, Optional


class ConciseMemoryAgent:
    """
    Concise Memory Agent - Focused Information Retention with MULTI-FILE ONLY Support

    Core Philosophy:
    - Preserve essential context (system prompt + initial plan)
    - After first batch generation, use clean slate approach
    - Keep only current round tool results from multi-file MCP tools
    - Remove conversational clutter and previous tool calls
    - MULTI-FILE ONLY: Support for multiple file implementations in single operation
    - FILE TRACKING: Receives ALL file information from workflow (no internal tracking)

    Essential Tools Tracked:
    - Multi-File Operations: read_multiple_files, write_multiple_files
    - Code Analysis: search_code, search_reference_code, get_file_structure
    - Execution: execute_python, execute_bash
    """

    def __init__(
        self,
        initial_plan_content: str,
        logger: Optional[logging.Logger] = None,
        target_directory: Optional[str] = None,
        default_models: Optional[Dict[str, str]] = None,
        max_files_per_batch: int = 3,
    ):
        """
        Initialize Concise Memory Agent with MULTI-FILE ONLY support

        Args:
            initial_plan_content: Content of initial_plan.txt
            logger: Logger instance
            target_directory: Target directory for saving summaries
            default_models: Default models configuration from workflow
            max_files_per_batch: Maximum number of files to implement simultaneously (default: 3)
        """
        self.logger = logger or self._create_default_logger()
        self.initial_plan = initial_plan_content
        self.max_files_per_batch = max_files_per_batch

        # Store default models configuration
        self.default_models = default_models or {
            "anthropic": "claude-sonnet-4-20250514",
            "openai": "o3-mini",
            "google": "gemini-2.0-flash",
        }

        # Memory state tracking - new logic: trigger after each write_multiple_files
        self.last_write_multiple_files_detected = (
            False  # Track if write_multiple_files was called in current iteration
        )
        self.should_clear_memory_next = False  # Flag to clear memory in next round
        self.current_round = 0

        # self.phase_structure = self._parse_phase_structure()

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

        self.logger.info(
            f"Concise Memory Agent initialized with target directory: {self.save_path}"
        )
        self.logger.info(f"Code summary will be saved to: {self.code_summary_path}")
        self.logger.info(f"Max files per batch: {self.max_files_per_batch}")
        self.logger.info(
            "📝 MULTI-FILE LOGIC: Memory clearing triggered after each write_multiple_files call"
        )
        self.logger.info(
            "🆕 MULTI-FILE ONLY: No single file support - batch operations only"
        )
        self.logger.info(
            "📊 FILE TRACKING: ALL file information received from workflow (no internal tracking)"
        )

    def _create_default_logger(self) -> logging.Logger:
        """Create default logger"""
        logger = logging.getLogger(f"{__name__}.ConciseMemoryAgent")
        logger.setLevel(logging.INFO)
        return logger

    async def create_multi_code_implementation_summary(
        self,
        client,
        client_type: str,
        file_implementations: Dict[str, str],
        files_implemented: int,
        implemented_files: List[str],  # Receive from workflow
    ) -> str:
        """
        Create LLM-based code implementation summary for multiple files
        ONLY AVAILABLE METHOD: Handles multiple files simultaneously with separate summaries for each

        Args:
            client: LLM client instance
            client_type: Type of LLM client ("anthropic" or "openai")
            file_implementations: Dictionary mapping file_path to implementation_content
            files_implemented: Number of files implemented so far
            implemented_files: List of all implemented files (from workflow)

        Returns:
            LLM-generated formatted code implementation summaries for all files
        """
        try:
            # Validate input
            if not file_implementations:
                raise ValueError("No file implementations provided")

            if len(file_implementations) > self.max_files_per_batch:
                raise ValueError(
                    f"Too many files provided ({len(file_implementations)}), max is {self.max_files_per_batch}"
                )

            # Create prompt for LLM summary of multiple files
            summary_prompt = self._create_multi_code_summary_prompt(
                file_implementations, files_implemented, implemented_files
            )
            summary_messages = [{"role": "user", "content": summary_prompt}]

            # Get LLM-generated summary
            llm_response = await self._call_llm_for_summary(
                client, client_type, summary_messages
            )
            llm_summary = llm_response.get("content", "")

            # Extract sections for each file and next steps
            multi_sections = self._extract_multi_summary_sections(
                llm_summary, file_implementations.keys()
            )

            # Format and save summary for each file (WITHOUT Next Steps)
            all_formatted_summaries = []

            for file_path in file_implementations.keys():
                file_sections = multi_sections.get("files", {}).get(file_path, {})

                # Format summary with ONLY Implementation Progress and Dependencies for file saving
                file_summary_content = ""
                if file_sections.get("core_purpose"):
                    file_summary_content += file_sections["core_purpose"] + "\n\n"
                if file_sections.get("public_interface"):
                    file_summary_content += file_sections["public_interface"] + "\n\n"
                if file_sections.get("internal_dependencies"):
                    file_summary_content += (
                        file_sections["internal_dependencies"] + "\n\n"
                    )
                if file_sections.get("external_dependencies"):
                    file_summary_content += (
                        file_sections["external_dependencies"] + "\n\n"
                    )
                if file_sections.get("implementation_notes"):
                    file_summary_content += (
                        file_sections["implementation_notes"] + "\n\n"
                    )

                # Create the formatted summary for file saving (WITHOUT Next Steps)
                formatted_summary = self._format_code_implementation_summary(
                    file_path, file_summary_content.strip(), files_implemented
                )

                all_formatted_summaries.append(formatted_summary)

                # Save to implement_code_summary.md (append mode) - ONLY Implementation Progress and Dependencies
                await self._save_code_summary_to_file(formatted_summary, file_path)

            # Combine all summaries for return
            combined_summary = "\n".join(all_formatted_summaries)

            self.logger.info(
                f"Created and saved multi-file code summaries for {len(file_implementations)} files"
            )

            return combined_summary

        except Exception as e:
            self.logger.error(
                f"Failed to create LLM-based multi-file code implementation summary: {e}"
            )
            # Fallback to simple summary for each file
            return self._create_fallback_multi_code_summary(
                file_implementations, files_implemented
            )

    def _create_multi_code_summary_prompt(
        self,
        file_implementations: Dict[str, str],
        files_implemented: int,
        implemented_files: List[str],
    ) -> str:
        """
        Create prompt for LLM to generate multi-file code implementation summary

        Args:
            file_implementations: Dictionary mapping file_path to implementation_content
            files_implemented: Number of files implemented so far
            implemented_files: List of all implemented files (from workflow)

        Returns:
            Prompt for LLM multi-file summarization
        """

        # Format file lists using workflow data
        implemented_files_list = (
            "\n".join([f"- {file}" for file in implemented_files])
            if implemented_files
            else "- None yet"
        )

        # Note: We don't have unimplemented files list anymore - workflow will provide when needed

        # Format file implementations for the prompt
        implementation_sections = []
        for file_path, content in file_implementations.items():
            implementation_sections.append(f"""
            **File: {file_path}**
            {content}
            """)

        files_list = list(file_implementations.keys())
        files_count = len(files_list)

        prompt = f"""You are an expert code implementation summarizer. Analyze the {files_count} implemented code files and create structured summaries for each.

**All Previously Implemented Files:**
{implemented_files_list}

**Current Implementation Context:**
- **Files Implemented**: {', '.join(files_list)}
- **Total Files Implemented**: {files_implemented}
- **Files in This Batch**: {files_count}

**Initial Plan Reference:**
{self.initial_plan[:]}

**Implemented Code Content:**
{''.join(implementation_sections)}

**Required Summary Format:**

**FOR EACH FILE, provide separate sections:**

**File: {{file_path}}**
**Core Purpose** (provide a general overview of the file's main responsibility):
- {{1-2 sentence description of file's main responsibility}}

**Public Interface** (what other files can use, if any):
- Class {{ClassName}}: {{purpose}} | Key methods: {{method_names}} | Constructor params: {{params}}
- Function {{function_name}}({{params}}): {{purpose}} -> {{return_type}}: {{purpose}}
- Constants/Types: {{name}}: {{value/description}}

**Internal Dependencies** (what this file imports/requires, if any):
- From {{module/file}}: {{specific_imports}}
- External packages: {{package_name}} - {{usage_context}}

**External Dependencies** (what depends on this file, if any):
- Expected to be imported by: {{likely_consumer_files}}
- Key exports used elsewhere: {{main_interfaces}}

**Implementation Notes**: (if any)
- Architecture decisions: {{key_choices_made}}
- Cross-File Relationships: {{how_files_work_together}}

[Repeat for all {files_count} files...]

**Instructions:**
- Provide separate Implementation Progress and Dependencies sections for each of the {files_count} files
- Be precise and concise for each file
- Focus on function interfaces that other files will need
- Extract actual function signatures from the code
- Use the exact format specified above

**Summary:**"""

        return prompt

    def _extract_multi_summary_sections(
        self, llm_summary: str, file_paths: List[str]
    ) -> Dict[str, Any]:
        """
        Extract different sections from LLM-generated multi-file summary
        """
        result = {
            "files": {},
        }

        try:
            # Convert dict_keys to list if needed
            if hasattr(file_paths, "keys"):
                file_paths = list(file_paths)
            elif not isinstance(file_paths, list):
                file_paths = list(file_paths)

            lines = llm_summary.split("\n")
            current_file = None
            current_section = None
            current_content = []
            file_sections = {}

            for i, line in enumerate(lines):
                line_lower = line.lower().strip()
                original_line = line.strip()

                # Skip empty lines
                if not original_line:
                    if current_section:
                        current_content.append(line)
                    continue

                # File header detection
                if (
                    "**file:" in line_lower or "file:" in line_lower
                ) and "**" in original_line:
                    # Save previous section
                    if current_file and current_section and current_content:
                        if current_file not in file_sections:
                            file_sections[current_file] = {}
                        file_sections[current_file][current_section] = "\n".join(
                            current_content
                        ).strip()

                    # Extract file path
                    file_header = original_line.lower()
                    if "**file:" in file_header:
                        file_header = original_line[
                            original_line.lower().find("file:") + 5 :
                        ]
                        if "**" in file_header:
                            file_header = file_header[: file_header.find("**")]
                    else:
                        file_header = original_line[
                            original_line.lower().find("file:") + 5 :
                        ]

                    file_header = file_header.strip()
                    current_file = None

                    # File matching
                    for file_path in file_paths:
                        file_name = file_path.split("/")[-1]
                        if (
                            file_path in file_header
                            or file_header in file_path
                            or file_name in file_header
                            or file_header in file_name
                        ):
                            current_file = file_path
                            break

                    current_section = None
                    current_content = []
                    continue

                # Section detection within files
                if current_file:
                    section_matched = False

                    if "core purpose" in line_lower and "**" in original_line:
                        if current_section and current_content:
                            if current_file not in file_sections:
                                file_sections[current_file] = {}
                            file_sections[current_file][current_section] = "\n".join(
                                current_content
                            ).strip()
                        current_section = "core_purpose"
                        current_content = []
                        section_matched = True
                    elif "public interface" in line_lower and "**" in original_line:
                        if current_section and current_content:
                            if current_file not in file_sections:
                                file_sections[current_file] = {}
                            file_sections[current_file][current_section] = "\n".join(
                                current_content
                            ).strip()
                        current_section = "public_interface"
                        current_content = []
                        section_matched = True
                    elif (
                        "internal dependencies" in line_lower and "**" in original_line
                    ):
                        if current_section and current_content:
                            if current_file not in file_sections:
                                file_sections[current_file] = {}
                            file_sections[current_file][current_section] = "\n".join(
                                current_content
                            ).strip()
                        current_section = "internal_dependencies"
                        current_content = []
                        section_matched = True
                    elif (
                        "external dependencies" in line_lower and "**" in original_line
                    ):
                        if current_section and current_content:
                            if current_file not in file_sections:
                                file_sections[current_file] = {}
                            file_sections[current_file][current_section] = "\n".join(
                                current_content
                            ).strip()
                        current_section = "external_dependencies"
                        current_content = []
                        section_matched = True
                    elif "implementation notes" in line_lower and "**" in original_line:
                        if current_section and current_content:
                            if current_file not in file_sections:
                                file_sections[current_file] = {}
                            file_sections[current_file][current_section] = "\n".join(
                                current_content
                            ).strip()
                        current_section = "implementation_notes"
                        current_content = []
                        section_matched = True

                    # If no section header matched, add to current content
                    if not section_matched and current_section:
                        current_content.append(line)

            # Save the final section
            if current_file and current_section and current_content:
                if current_file not in file_sections:
                    file_sections[current_file] = {}
                file_sections[current_file][current_section] = "\n".join(
                    current_content
                ).strip()

            # Build final result
            for file_path in file_paths:
                sections = file_sections.get(file_path, {})
                result["files"][file_path] = {}
                if "core_purpose" in sections:
                    result["files"][file_path]["core_purpose"] = (
                        "**Core Purpose**:\n" + sections["core_purpose"]
                    )
                if "public_interface" in sections:
                    result["files"][file_path]["public_interface"] = (
                        "**Public Interface**:\n" + sections["public_interface"]
                    )
                if "implementation_notes" in sections:
                    result["files"][file_path]["implementation_notes"] = (
                        "**Implementation Notes**:\n" + sections["implementation_notes"]
                    )
                if "internal_dependencies" in sections:
                    result["files"][file_path]["internal_dependencies"] = (
                        "**Internal Dependencies**:\n"
                        + sections["internal_dependencies"]
                    )
                if "external_dependencies" in sections:
                    result["files"][file_path]["external_dependencies"] = (
                        "**External Dependencies**:\n"
                        + sections["external_dependencies"]
                    )

            self.logger.info(
                f"📋 Extracted multi-file sections for {len(result['files'])} files"
            )

        except Exception as e:
            self.logger.error(f"Failed to extract multi-file summary sections: {e}")
            self.logger.error(f"📋 file_paths type: {type(file_paths)}")
            self.logger.error(f"📋 file_paths value: {file_paths}")
            self.logger.error(f"📋 file_paths length: {len(file_paths)}")
            for file_path in file_paths:
                result["files"][file_path] = {
                    "core_purpose": f"**Core Purpose**: {file_path} completed.",
                    "public_interface": "**Public Interface**: Public interface need manual review.",
                    "internal_dependencies": "**Internal Dependencies**: Internal dependencies need manual review.",
                    "external_dependencies": "**External Dependencies**: External dependencies need manual review.",
                    "implementation_notes": "**Implementation Notes**: Implementation notes need manual review.",
                }

        return result

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

        formatted_summary = f"""# Code Implementation Summary
**Generated**: {timestamp}
**File Implemented**: {file_path}

{llm_summary}

---
*Auto-generated by Memory Agent*
"""
        return formatted_summary

    def _create_fallback_multi_code_summary(
        self, file_implementations: Dict[str, str], files_implemented: int
    ) -> str:
        """
        Create fallback multi-file summary when LLM is unavailable

        Args:
            file_implementations: Dictionary mapping file_path to implementation_content
            files_implemented: Number of files implemented so far

        Returns:
            Fallback multi-file summary
        """
        # Create fallback summaries for each file
        fallback_summaries = []
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        for file_path in file_implementations.keys():
            fallback_summary = f"""# Code Implementation Summary
**Generated**: {timestamp}
**File Implemented**: {file_path}
**Multi-file batch summary failed to generate.**

---
*Auto-generated by Concise Memory Agent (Multi-File Fallback Mode)*
"""
            fallback_summaries.append(fallback_summary)

        return "\n".join(fallback_summaries)

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
                f.write(f"## IMPLEMENTATION File {file_path}\n")
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

        This method is used only for creating code implementation summaries,
        NOT for conversation summarization which has been removed.
        """
        if client_type == "anthropic":
            response = await client.messages.create(
                model=self.default_models["anthropic"],
                system="You are an expert code implementation summarizer. Create structured summaries of implemented code files that preserve essential information about functions, dependencies, and implementation approaches.",
                messages=summary_messages,
                max_tokens=8000,  # Increased for multi-file support
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

            # Try max_tokens and temperature first, fallback to max_completion_tokens without temperature if unsupported
            try:
                response = await client.chat.completions.create(
                    model=self.default_models["openai"],
                    messages=openai_messages,
                    max_tokens=8000,  # Increased for multi-file support
                    temperature=0.2,
                )
            except Exception as e:
                if "max_tokens" in str(e) and "max_completion_tokens" in str(e):
                    # Retry with max_completion_tokens and no temperature for models that require it
                    response = await client.chat.completions.create(
                        model=self.default_models["openai"],
                        messages=openai_messages,
                        max_completion_tokens=8000,  # Increased for multi-file support
                    )
                else:
                    raise

            return {"content": response.choices[0].message.content or ""}

        elif client_type == "google":
            from google.genai import types

            # Convert messages to Gemini format
            system_instruction = "You are an expert code implementation summarizer. Create structured summaries of implemented code files that preserve essential information about functions, dependencies, and implementation approaches."

            gemini_messages = []
            for msg in summary_messages:
                role = msg.get("role", "user")
                content = msg.get("content", "")

                # Convert role names: "assistant" -> "model"
                if role == "assistant":
                    role = "model"
                elif role not in ["user", "model"]:
                    role = "user"

                gemini_messages.append(
                    types.Content(role=role, parts=[types.Part.from_text(text=content)])
                )

            config = types.GenerateContentConfig(
                max_output_tokens=8000,  # Increased for multi-file support
                temperature=0.2,
                system_instruction=system_instruction,
            )

            response = await client.aio.models.generate_content(
                model=self.default_models.get("google", "gemini-2.0-flash"),
                contents=gemini_messages,
                config=config,
            )

            # Extract content from Gemini response
            content = ""
            if response and hasattr(response, "candidates") and response.candidates:
                candidate = response.candidates[0]
                if hasattr(candidate, "content") and candidate.content:
                    if hasattr(candidate.content, "parts") and candidate.content.parts:
                        for part in candidate.content.parts:
                            if hasattr(part, "text") and part.text:
                                content += part.text

            if not content:
                self.logger.warning("Google response is empty or malformed")

            return {"content": content}

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
        else:
            # Default behavior: increment round counter
            self.current_round += 1
            self.logger.info(f"🔄 Started new round {self.current_round}")

        self.current_round_tool_results = []  # Clear previous round results

    def record_tool_result(
        self, tool_name: str, tool_input: Dict[str, Any], tool_result: Any
    ):
        """
        Record tool result for current round and detect write_multiple_files calls

        Args:
            tool_name: Name of the tool called
            tool_input: Input parameters for the tool
            tool_result: Result returned by the tool
        """
        # Detect write_multiple_files calls to trigger memory clearing
        if tool_name == "write_multiple_files":
            self.last_write_multiple_files_detected = True
            self.should_clear_memory_next = True

        # Only record specific tools that provide essential information
        essential_tools = [
            "read_multiple_files",  # Read multiple file contents
            "write_multiple_files",  # Write multiple file contents (important for tracking implementations)
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

    def should_use_concise_mode(self) -> bool:
        """
        Check if concise memory mode should be used

        Returns:
            True if first batch has been generated and concise mode should be active
        """
        return self.last_write_multiple_files_detected

    def create_concise_messages_revise(
        self,
        system_prompt: str,
        messages: List[Dict[str, Any]],
        files_implemented: int,
        task_description: str,
        file_batch: List[str],
        is_first_batch: bool = True,
        implemented_files: List[str] = None,  # Receive from workflow
        all_files: List[str] = None,  # NEW: Receive all files from workflow
    ) -> List[Dict[str, Any]]:
        """
        Create concise message list for LLM input specifically for revision execution
        ALIGNED with _execute_multi_file_batch_revision in code_evaluation_workflow

        Args:
            system_prompt: Current system prompt
            messages: Original message list
            files_implemented: Number of files implemented so far
            task_description: Description of the current task
            file_batch: Files to implement in this batch
            is_first_batch: Whether this is the first batch (use file_batch) or subsequent
            implemented_files: List of all implemented files (from workflow)
            all_files: List of all files that should be implemented (from workflow)

        Returns:
            Concise message list containing only essential information for revision
        """
        # Use empty lists if not provided
        if implemented_files is None:
            implemented_files = []
        if all_files is None:
            all_files = []

        self.logger.info(
            "🎯 Using CONCISE memory mode for revision - Clear slate after write_multiple_files"
        )

        concise_messages = []

        # Format file lists using workflow data
        implemented_files_list = (
            "\n".join([f"- {file}" for file in implemented_files])
            if implemented_files
            else "- None yet"
        )

        # Calculate unimplemented files from workflow data

        # Read initial plan and memory content
        initial_plan_content = self.initial_plan
        memory_content = (
            self._read_code_knowledge_base()
            or "No previous implementation memory available"
        )

        files_to_implement = file_batch
        file_list = "\n".join([f"- {file_path}" for file_path in files_to_implement])

        # Create revision-specific task message
        task_message = f"""Task: {task_description}

    Files to implement in this batch ({len(files_to_implement)} files):
    {file_list}

    MANDATORY JSON FORMAT REQUIREMENTS:
    1. Use write_multiple_files tool
    2. Parameter name: "file_implementations"
    3. Value must be a VALID JSON string with ESCAPED newlines
    4. Use \\n for newlines, \\t for tabs, \\" for quotes
    5. NO literal newlines in the JSON string

    CORRECT JSON FORMAT EXAMPLE:
    {{
    "file1.py": "# Comment\\nclass MyClass:\\n    def __init__(self):\\n        pass\\n",
    "file2.py": "import os\\n\\ndef main():\\n    print('Hello')\\n"
    }}

    Initial Implementation Plan Context:
    {initial_plan_content}

    Previous Implementation Memory:
    {memory_content}

    **All Previously Implemented Files:**
    {implemented_files_list}

    **Current Status:** {files_implemented} files implemented

    IMPLEMENTATION REQUIREMENTS:
    - Create functional code for each file
    - Use proper Python syntax and imports
    - Include docstrings and comments
    - Follow the existing patterns from memory

    Files to implement: {files_to_implement}

    Call write_multiple_files NOW with PROPERLY ESCAPED JSON containing all {len(files_to_implement)} files."""

        concise_messages.append({"role": "user", "content": task_message})

        # Debug output for files to implement
        print("✅ Files to implement:")
        for file_path in files_to_implement:
            print(f"{file_path}")

        return concise_messages

    def _calculate_message_statistics(
        self, messages: List[Dict[str, Any]], label: str
    ) -> Dict[str, Any]:
        """
        Calculate statistics for a message list

        Args:
            messages: List of messages to analyze
            label: Label for logging

        Returns:
            Dictionary with statistics
        """
        total_chars = 0
        total_words = 0

        for msg in messages:
            content = msg.get("content", "")
            total_chars += len(content)
            total_words += len(content.split())

        # Estimate tokens (rough approximation: ~4 characters per token)
        estimated_tokens = total_chars // 4

        stats = {
            "message_count": len(messages),
            "total_characters": total_chars,
            "total_words": total_words,
            "estimated_tokens": estimated_tokens,
            "summary": f"{len(messages)} msgs, {total_chars:,} chars, ~{estimated_tokens:,} tokens",
        }

        return stats

    def _calculate_memory_savings(
        self, original_stats: Dict[str, Any], optimized_stats: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Calculate memory savings between original and optimized messages

        Args:
            original_stats: Statistics for original messages
            optimized_stats: Statistics for optimized messages

        Returns:
            Dictionary with savings calculations
        """
        messages_saved = (
            original_stats["message_count"] - optimized_stats["message_count"]
        )
        chars_saved = (
            original_stats["total_characters"] - optimized_stats["total_characters"]
        )
        tokens_saved_estimate = (
            original_stats["estimated_tokens"] - optimized_stats["estimated_tokens"]
        )

        # Calculate percentages (avoid division by zero)
        messages_saved_percent = (
            messages_saved / max(original_stats["message_count"], 1)
        ) * 100
        chars_saved_percent = (
            chars_saved / max(original_stats["total_characters"], 1)
        ) * 100
        tokens_saved_percent = (
            tokens_saved_estimate / max(original_stats["estimated_tokens"], 1)
        ) * 100

        return {
            "messages_saved": messages_saved,
            "chars_saved": chars_saved,
            "tokens_saved_estimate": tokens_saved_estimate,
            "messages_saved_percent": messages_saved_percent,
            "chars_saved_percent": chars_saved_percent,
            "tokens_saved_percent": tokens_saved_percent,
        }

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
                return content
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
            section_pattern = r"={80}\s*\n## IMPLEMENTATION File .+?"

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
            if tool_name == "read_multiple_files":
                file_requests = tool_input.get("file_requests", "unknown")
                formatted_results.append(f"""
**read_multiple_files Result for {file_requests}:**
{self._format_tool_result_content(tool_result)}
""")
            elif tool_name == "write_multiple_files":
                formatted_results.append(f"""
**write_multiple_files Result for batch:**
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
                    if result_data.get("status") == "success":
                        return json.dumps(result_data, indent=2)
                    else:
                        return json.dumps(result_data, indent=2)
                else:
                    return str(result_data)
            except json.JSONDecodeError:
                return tool_result
        else:
            return str(tool_result)

    def get_memory_statistics(
        self, all_files: List[str] = None, implemented_files: List[str] = None
    ) -> Dict[str, Any]:
        """
        Get memory agent statistics for multi-file operations

        Args:
            all_files: List of all files that should be implemented (from workflow)
            implemented_files: List of all implemented files (from workflow)
        """
        if all_files is None:
            all_files = []
        if implemented_files is None:
            implemented_files = []

        # Calculate unimplemented files from workflow data
        unimplemented_files = [f for f in all_files if f not in implemented_files]

        return {
            "last_write_multiple_files_detected": self.last_write_multiple_files_detected,
            "should_clear_memory_next": self.should_clear_memory_next,
            "current_round": self.current_round,
            "concise_mode_active": self.should_use_concise_mode(),
            "current_round_tool_results": len(self.current_round_tool_results),
            "essential_tools_recorded": [
                r["tool_name"] for r in self.current_round_tool_results
            ],
            # File tracking statistics (from workflow)
            "total_files_in_plan": len(all_files),
            "files_implemented_count": len(implemented_files),
            "files_remaining_count": len(unimplemented_files),
            "all_files_list": all_files.copy(),
            "implemented_files_list": implemented_files.copy(),
            "unimplemented_files_list": unimplemented_files,
            "implementation_progress_percent": (
                len(implemented_files) / len(all_files) * 100
            )
            if all_files
            else 0,
            # Multi-file support statistics
            "max_files_per_batch": self.max_files_per_batch,
            "multi_file_support": True,
            "single_file_support": False,  # Explicitly disabled
        }

    def record_multi_file_implementation(self, file_implementations: Dict[str, str]):
        """
        Record multi-file implementation (for compatibility with workflow)
        NOTE: This method doesn't track files internally - workflow manages file tracking

        Args:
            file_implementations: Dictionary mapping file_path to implementation_content
        """
        self.logger.info(
            f"📝 Recorded multi-file implementation batch: {len(file_implementations)} files"
        )
        # Note: We don't track files internally anymore - workflow handles this

    # ===== ENHANCED MEMORY SYNCHRONIZATION METHODS (Phase 4+) =====

    async def synchronize_revised_file_memory(
        self,
        client,
        client_type: str,
        revised_file_path: str,
        diff_content: str,
        new_content: str,
        revision_type: str = "targeted_fix",
    ) -> str:
        """
        Synchronize memory for a single revised file with diff information

        Args:
            client: LLM client instance
            client_type: Type of LLM client ("anthropic" or "openai")
            revised_file_path: Path of the revised file
            diff_content: Unified diff showing changes made
            new_content: Complete new content of the file
            revision_type: Type of revision ("targeted_fix", "comprehensive_revision", etc.)

        Returns:
            Updated memory summary for the revised file
        """
        try:
            self.logger.info(
                f"🔄 Synchronizing memory for revised file: {revised_file_path}"
            )

            # Create revision-specific summary prompt
            revision_prompt = self._create_file_revision_summary_prompt(
                revised_file_path, diff_content, new_content, revision_type
            )

            summary_messages = [{"role": "user", "content": revision_prompt}]

            # Get LLM-generated revision summary
            llm_response = await self._call_llm_for_summary(
                client, client_type, summary_messages
            )
            llm_summary = llm_response.get("content", "")

            # Extract summary sections
            revision_sections = self._extract_revision_summary_sections(llm_summary)

            # Format revision summary
            formatted_summary = self._format_file_revision_summary(
                revised_file_path, revision_sections, diff_content, revision_type
            )

            # Save the revision summary (replace old summary)
            await self._save_revised_file_summary(formatted_summary, revised_file_path)

            self.logger.info(
                f"✅ Memory synchronized for revised file: {revised_file_path}"
            )

            return formatted_summary

        except Exception as e:
            self.logger.error(
                f"Failed to synchronize memory for revised file {revised_file_path}: {e}"
            )

            # Fallback to simple revision summary
            return self._create_fallback_revision_summary(
                revised_file_path, revision_type
            )

    async def synchronize_multiple_revised_files(
        self, client, client_type: str, revision_results: List[Dict[str, Any]]
    ) -> Dict[str, str]:
        """
        Synchronize memory for multiple revised files based on revision results

        Args:
            client: LLM client instance
            client_type: Type of LLM client
            revision_results: List of revision results with file paths, diffs, and new content

        Returns:
            Dictionary mapping file paths to updated memory summaries
        """
        try:
            self.logger.info(
                f"🔄 Synchronizing memory for {len(revision_results)} revised files"
            )

            synchronized_summaries = {}

            for revision_result in revision_results:
                file_path = revision_result.get("file_path", "")
                diff_content = revision_result.get("diff", "")
                new_content = revision_result.get("new_content", "")
                revision_type = revision_result.get("revision_type", "targeted_fix")

                if file_path and revision_result.get("success", False):
                    summary = await self.synchronize_revised_file_memory(
                        client,
                        client_type,
                        file_path,
                        diff_content,
                        new_content,
                        revision_type,
                    )
                    synchronized_summaries[file_path] = summary
                else:
                    self.logger.warning(
                        f"⚠️ Skipping memory sync for failed revision: {file_path}"
                    )

            self.logger.info(
                f"✅ Memory synchronized for {len(synchronized_summaries)} successfully revised files"
            )

            return synchronized_summaries

        except Exception as e:
            self.logger.error(
                f"Failed to synchronize memory for multiple revised files: {e}"
            )
            return {}

    def _create_file_revision_summary_prompt(
        self, file_path: str, diff_content: str, new_content: str, revision_type: str
    ) -> str:
        """
        Create prompt for LLM to generate file revision summary

        Args:
            file_path: Path of the revised file
            diff_content: Unified diff showing changes
            new_content: Complete new content of the file
            revision_type: Type of revision performed

        Returns:
            Prompt for LLM revision summarization
        """
        # Truncate content if too long for prompt
        content_preview = (
            new_content[:2000] + "..." if len(new_content) > 2000 else new_content
        )
        diff_preview = (
            diff_content[:1000] + "..." if len(diff_content) > 1000 else diff_content
        )

        prompt = f"""You are an expert code revision summarizer. A file has been REVISED with targeted changes. Create a structured summary of the revision.

**File Revised**: {file_path}
**Revision Type**: {revision_type}

**Changes Made (Diff):**
```diff
{diff_preview}
```

**Updated File Content:**
```python
{content_preview}
```

**Required Summary Format:**

**Revision Summary**:
- Brief description of what was changed and why

**Changes Made**:
- Specific modifications applied (line-level changes)
- Functions/classes affected
- New functionality added or bugs fixed

**Impact Assessment**:
- How the changes affect the file's behavior
- Dependencies that might be affected
- Integration points that need attention

**Quality Improvements**:
- Code quality enhancements made
- Error handling improvements
- Performance or maintainability gains

**Post-Revision Status**:
- Current functionality of the file
- Key interfaces and exports
- Dependencies and imports

**Instructions:**
- Focus on the CHANGES made, not just the final state
- Highlight the specific improvements and fixes applied
- Be concise but comprehensive about the revision impact
- Use the exact format specified above

**Summary:**"""

        return prompt

    def _extract_revision_summary_sections(self, llm_summary: str) -> Dict[str, str]:
        """
        Extract different sections from LLM-generated revision summary

        Args:
            llm_summary: Raw LLM response containing revision summary

        Returns:
            Dictionary with extracted sections
        """
        sections = {
            "revision_summary": "",
            "changes_made": "",
            "impact_assessment": "",
            "quality_improvements": "",
            "post_revision_status": "",
        }

        try:
            lines = llm_summary.split("\n")
            current_section = None
            current_content = []

            for line in lines:
                line_lower = line.lower().strip()
                original_line = line.strip()

                # Skip empty lines
                if not original_line:
                    if current_section:
                        current_content.append(line)
                    continue

                # Section detection
                section_matched = False

                if "revision summary" in line_lower and "**" in original_line:
                    if current_section and current_content:
                        sections[current_section] = "\n".join(current_content).strip()
                    current_section = "revision_summary"
                    current_content = []
                    section_matched = True
                elif "changes made" in line_lower and "**" in original_line:
                    if current_section and current_content:
                        sections[current_section] = "\n".join(current_content).strip()
                    current_section = "changes_made"
                    current_content = []
                    section_matched = True
                elif "impact assessment" in line_lower and "**" in original_line:
                    if current_section and current_content:
                        sections[current_section] = "\n".join(current_content).strip()
                    current_section = "impact_assessment"
                    current_content = []
                    section_matched = True
                elif "quality improvements" in line_lower and "**" in original_line:
                    if current_section and current_content:
                        sections[current_section] = "\n".join(current_content).strip()
                    current_section = "quality_improvements"
                    current_content = []
                    section_matched = True
                elif "post-revision status" in line_lower and "**" in original_line:
                    if current_section and current_content:
                        sections[current_section] = "\n".join(current_content).strip()
                    current_section = "post_revision_status"
                    current_content = []
                    section_matched = True

                # If no section header matched, add to current content
                if not section_matched and current_section:
                    current_content.append(line)

            # Save the final section
            if current_section and current_content:
                sections[current_section] = "\n".join(current_content).strip()

            self.logger.info(
                f"📋 Extracted {len([s for s in sections.values() if s])} revision summary sections"
            )

        except Exception as e:
            self.logger.error(f"Failed to extract revision summary sections: {e}")
            # Provide fallback content
            sections["revision_summary"] = "File revision completed"
            sections["changes_made"] = (
                "Targeted changes applied based on error analysis"
            )
            sections["impact_assessment"] = (
                "Changes should improve code functionality and reduce errors"
            )
            sections["quality_improvements"] = (
                "Code quality enhanced through targeted fixes"
            )
            sections["post_revision_status"] = "File functionality updated and improved"

        return sections

    def _format_file_revision_summary(
        self,
        file_path: str,
        revision_sections: Dict[str, str],
        diff_content: str,
        revision_type: str,
    ) -> str:
        """
        Format the revision summary into the final structure

        Args:
            file_path: Path of the revised file
            revision_sections: Extracted sections from LLM summary
            diff_content: Unified diff content
            revision_type: Type of revision performed

        Returns:
            Formatted revision summary
        """
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # Format sections with fallbacks
        revision_summary = revision_sections.get(
            "revision_summary", "File revision completed"
        )
        changes_made = revision_sections.get("changes_made", "Targeted changes applied")
        impact_assessment = revision_sections.get(
            "impact_assessment", "Changes should improve functionality"
        )
        quality_improvements = revision_sections.get(
            "quality_improvements", "Code quality enhanced"
        )
        post_revision_status = revision_sections.get(
            "post_revision_status", "File updated successfully"
        )

        formatted_summary = f"""# File Revision Summary (UPDATED)
**Generated**: {timestamp}
**File Revised**: {file_path}
**Revision Type**: {revision_type}

## Revision Summary
{revision_summary}

## Changes Made
{changes_made}

## Impact Assessment
{impact_assessment}

## Quality Improvements
{quality_improvements}

## Post-Revision Status
{post_revision_status}

## Technical Details
**Diff Applied:**
```diff
{diff_content[:500]}{"..." if len(diff_content) > 500 else ""}
```

---
*Auto-generated by Enhanced Memory Agent (Revision Mode)*
"""
        return formatted_summary

    def _create_fallback_revision_summary(
        self, file_path: str, revision_type: str
    ) -> str:
        """
        Create fallback revision summary when LLM is unavailable

        Args:
            file_path: Path of the revised file
            revision_type: Type of revision performed

        Returns:
            Fallback revision summary
        """
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        fallback_summary = f"""# File Revision Summary (UPDATED)
**Generated**: {timestamp}
**File Revised**: {file_path}
**Revision Type**: {revision_type}

## Revision Summary
File has been revised with targeted changes. LLM summary generation failed.

## Changes Made
- Targeted modifications applied based on error analysis
- Specific line-level changes implemented
- Code functionality updated

## Impact Assessment
- File behavior should be improved
- Error conditions addressed
- Integration points maintained

## Quality Improvements
- Code quality enhanced through precise fixes
- Error handling improved
- Maintainability increased

## Post-Revision Status
- File successfully updated
- Functionality preserved and enhanced
- Ready for integration testing

---
*Auto-generated by Enhanced Memory Agent (Revision Fallback Mode)*
"""
        return fallback_summary

    async def _save_revised_file_summary(self, revision_summary: str, file_path: str):
        """
        Save or update the revision summary for a file (replaces old summary)

        Args:
            revision_summary: New revision summary content
            file_path: Path of the file for which the summary was generated
        """
        try:
            # For revised files, we replace the existing summary rather than append
            # Read existing content to find and replace the specific file's summary
            file_exists = os.path.exists(self.code_summary_path)

            if file_exists:
                with open(self.code_summary_path, "r", encoding="utf-8") as f:
                    existing_content = f.read()

                # Look for existing summary for this file and replace it
                import re

                # Pattern to match existing implementation section for this file
                file_pattern = re.escape(file_path)
                section_pattern = rf"={80}\s*\n## IMPLEMENTATION File {file_pattern}\n={80}.*?(?=\n={80}|\Z)"

                # Check if this file already has a summary
                if re.search(section_pattern, existing_content, re.DOTALL):
                    # Replace existing summary
                    new_section = f"\n{'=' * 80}\n## IMPLEMENTATION File {file_path} (REVISED)\n{'=' * 80}\n\n{revision_summary}\n\n"
                    updated_content = re.sub(
                        section_pattern,
                        new_section.strip(),
                        existing_content,
                        flags=re.DOTALL,
                    )

                    with open(self.code_summary_path, "w", encoding="utf-8") as f:
                        f.write(updated_content)

                    self.logger.info(
                        f"Updated existing summary for revised file: {file_path}"
                    )
                else:
                    # Append new summary for this file
                    with open(self.code_summary_path, "a", encoding="utf-8") as f:
                        f.write("\n" + "=" * 80 + "\n")
                        f.write(f"## IMPLEMENTATION File {file_path} (REVISED)\n")
                        f.write("=" * 80 + "\n\n")
                        f.write(revision_summary)
                        f.write("\n\n")

                    self.logger.info(
                        f"Appended new summary for revised file: {file_path}"
                    )
            else:
                # Create new file with header
                os.makedirs(os.path.dirname(self.code_summary_path), exist_ok=True)

                with open(self.code_summary_path, "w", encoding="utf-8") as f:
                    f.write("# Code Implementation Progress Summary\n")
                    f.write("*Accumulated implementation progress for all files*\n\n")
                    f.write("\n" + "=" * 80 + "\n")
                    f.write(f"## IMPLEMENTATION File {file_path} (REVISED)\n")
                    f.write("=" * 80 + "\n\n")
                    f.write(revision_summary)
                    f.write("\n\n")

                self.logger.info(
                    f"Created new summary file with revised file: {file_path}"
                )

        except Exception as e:
            self.logger.error(
                f"Failed to save revised file summary for {file_path}: {e}"
            )

    def get_revision_memory_statistics(
        self, revised_files: List[str]
    ) -> Dict[str, Any]:
        """
        Get memory statistics for revised files

        Args:
            revised_files: List of file paths that have been revised

        Returns:
            Dictionary with revision memory statistics
        """
        try:
            total_revisions = len(revised_files)

            # Count how many files have updated summaries
            summaries_updated = 0
            if os.path.exists(self.code_summary_path):
                with open(self.code_summary_path, "r", encoding="utf-8") as f:
                    content = f.read()

                for file_path in revised_files:
                    if f"File {file_path} (REVISED)" in content:
                        summaries_updated += 1

            return {
                "total_revised_files": total_revisions,
                "summaries_updated": summaries_updated,
                "memory_sync_rate": (summaries_updated / total_revisions * 100)
                if total_revisions > 0
                else 0,
                "revised_files_list": revised_files.copy(),
                "memory_summary_path": self.code_summary_path,
                "revision_memory_mode": "active",
            }

        except Exception as e:
            self.logger.error(f"Failed to get revision memory statistics: {e}")
            return {
                "total_revised_files": len(revised_files),
                "summaries_updated": 0,
                "memory_sync_rate": 0,
                "revised_files_list": revised_files.copy(),
                "memory_summary_path": self.code_summary_path,
                "revision_memory_mode": "error",
            }
