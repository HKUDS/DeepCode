"""
Concise Memory Agent for Code Implementation Workflow

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
        code_directory: Optional[str] = None,
    ):
        """
        Initialize Concise Memory Agent

        Args:
            initial_plan_content: Content of initial_plan.txt
            logger: Logger instance
            target_directory: Target directory for saving summaries
            default_models: Default models configuration from workflow
            code_directory: Generated code directory path (e.g., target_directory/generate_code)
        """
        self.logger = logger or self._create_default_logger()
        self.initial_plan = initial_plan_content

        # Store default models configuration
        self.default_models = default_models or {
            "anthropic": "claude-sonnet-4-20250514",
            "openai": "o3-mini",
            "google": "gemini-2.0-flash",
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

        # Store code directory for file extraction
        self.code_directory = code_directory or os.path.join(
            self.save_path, "generate_code"
        )

        # Extract all files - prioritize generated directory over plan parsing
        self.all_files_list = self._extract_all_files()

        # Code summary file path
        self.code_summary_path = os.path.join(
            self.save_path, "implement_code_summary.md"
        )

        # Current round tool results storage
        self.current_round_tool_results = []

        # Track all implemented files
        self.implemented_files = []

        # Store Next Steps information temporarily (not saved to file)
        self.current_next_steps = ""

        self.logger.info(
            f"Concise Memory Agent initialized with target directory: {self.save_path}"
        )
        self.logger.info(f"Code directory: {self.code_directory}")
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

    def _extract_all_files(self) -> List[str]:
        """
        Extract all code files - prioritizes generated directory over plan parsing

        Strategy:
        1. First try to extract from the generated code directory (reliable)
        2. Fall back to plan parsing if directory doesn't exist yet

        Returns:
            List of all file paths that should be implemented
        """
        # Try extracting from generated directory first (more reliable)
        if os.path.exists(self.code_directory):
            files_from_dir = self._extract_files_from_generated_directory()
            if files_from_dir:
                self.logger.info(
                    f"📁 Extracted {len(files_from_dir)} files from generated directory"
                )
                return files_from_dir

        # Fall back to plan parsing
        self.logger.info(
            "📁 Generated directory not found, extracting from plan (less reliable)"
        )
        return self._extract_all_files_from_plan()

    def _extract_files_from_generated_directory(self) -> List[str]:
        """
        Extract all code files from the generated code directory
        This is more reliable than parsing the LLM-generated plan

        Returns:
            List of relative file paths within the code directory
        """
        code_files = []

        # Define code file extensions to track
        code_extensions = {
            ".py",
            ".js",
            ".ts",
            ".jsx",
            ".tsx",
            ".vue",
            ".html",
            ".css",
            ".scss",
            ".sass",
            ".less",
            ".json",
            ".yaml",
            ".yml",
            ".toml",
            ".xml",
            ".ini",
            ".cfg",
            ".md",
            ".rst",
            ".txt",
            ".sh",
            ".bash",
            ".zsh",
            ".bat",
            ".ps1",
            ".cmd",
            ".c",
            ".cpp",
            ".h",
            ".hpp",
            ".cc",
            ".cxx",
            ".java",
            ".kt",
            ".scala",
            ".go",
            ".rs",
            ".php",
            ".rb",
            ".pl",
            ".lua",
            ".r",
            ".sql",
        }

        # Files and directories to exclude
        exclude_patterns = {
            "__pycache__",
            ".pyc",
            "node_modules",
            ".git",
            ".vscode",
            ".idea",
            "dist",
            "build",
            "output",
            ".egg-info",
            "venv",
            ".venv",
            "env",
            ".env",
        }

        try:
            for root, dirs, files in os.walk(self.code_directory):
                # Filter out excluded directories
                dirs[:] = [
                    d
                    for d in dirs
                    if d not in exclude_patterns and not d.startswith(".")
                ]

                for file in files:
                    # Skip hidden files and excluded patterns
                    if file.startswith("."):
                        continue

                    # Check if file has a code extension
                    has_code_ext = any(
                        file.lower().endswith(ext) for ext in code_extensions
                    )
                    if not has_code_ext:
                        continue

                    # Get full path and convert to relative path
                    full_path = os.path.join(root, file)
                    relative_path = os.path.relpath(full_path, self.code_directory)

                    # Normalize path separators
                    relative_path = relative_path.replace(os.sep, "/")

                    code_files.append(relative_path)

            # Sort for consistency
            code_files = sorted(code_files)

            if code_files:
                self.logger.info(f"📄 Found {len(code_files)} code files in directory")
                self.logger.info(f"📄 Sample files: {code_files[:3]}...")

            return code_files

        except Exception as e:
            self.logger.error(f"Failed to extract files from directory: {e}")
            return []

    def _extract_all_files_from_plan(self) -> List[str]:
        """
        Extract all file paths from the file_structure section in initial plan
        Handles multiple formats: tree structure, YAML, and simple lists

        Returns:
            List of all file paths that should be implemented
        """
        try:
            lines = self.initial_plan.split("\n")
            files = []

            # Method 1: Try to extract from tree structure in file_structure section
            files.extend(self._extract_from_tree_structure(lines))

            # Method 2: If no files found, try to extract from simple list format
            if not files:
                files.extend(self._extract_from_simple_list(lines))

            # Method 3: If still no files, try to extract from anywhere in the plan
            if not files:
                files.extend(self._extract_from_plan_content(lines))

            # Clean and validate file paths
            cleaned_files = self._clean_and_validate_files(files)

            # Log the extracted files
            self.logger.info(
                f"📁 Extracted {len(cleaned_files)} files from initial plan"
            )
            if cleaned_files:
                self.logger.info(f"📁 Sample files: {cleaned_files[:3]}...")

            return cleaned_files

        except Exception as e:
            self.logger.error(f"Failed to extract files from initial plan: {e}")
            return []

    def _extract_from_tree_structure(self, lines: List[str]) -> List[str]:
        """
        Extract files from tree structure format - Advanced algorithm with multi-strategy approach

        Strategy:
        1. Precise indentation-based depth calculation
        2. Smart directory vs file detection using multiple heuristics
        3. Robust path stack management with depth tracking
        4. Fallback to regex pattern matching if tree parsing fails
        """
        files = []
        in_file_structure = False

        # Enhanced path tracking: store (depth, name) pairs
        path_stack = []  # [(depth, dir_name), ...]
        root_dir = None

        # Track the base indentation of tree structure
        base_indent = None

        for line_num, line in enumerate(lines):
            # === Section Boundary Detection ===
            if "file_structure:" in line or "file_structure |" in line:
                in_file_structure = True
                continue

            # End of file_structure section (next YAML key without indentation)
            if (
                in_file_structure
                and line.strip()
                and not line.startswith(" ")
                and ":" in line
            ):
                break

            if not in_file_structure:
                continue

            if not line.strip():
                continue

            # Skip YAML comments and keys that are clearly not files
            stripped = line.strip()
            if stripped.startswith("#") or (
                stripped.endswith(":") and "/" not in stripped
            ):
                continue

            # === Root Directory Detection ===
            # Pattern: "project-name/" at minimal indentation, no tree chars
            if stripped.endswith("/") and not any(
                c in line for c in ["├", "└", "│", "─"]
            ):
                indent = len(line) - len(line.lstrip())
                if indent <= 4:  # Root level
                    root_dir = stripped.rstrip("/")
                    path_stack = []
                    base_indent = None
                    self.logger.debug(f"🌳 Detected root directory: {root_dir}")
                    continue

            # === Tree Structure Line Detection ===
            has_tree_chars = any(c in line for c in ["├", "└", "│", "─"])
            if not has_tree_chars:
                continue

            # === Calculate Precise Depth ===
            # Method: Count the actual tree structure symbols to determine hierarchy
            indent = len(line) - len(line.lstrip())

            # Set base indent on first tree line
            if base_indent is None:
                base_indent = indent

            # Count tree depth indicators
            # Each "│   " or "    " block represents one level
            # "├── " or "└── " marks the current item
            tree_prefix = line[
                : line.find("├")
                if "├" in line
                else line.find("└")
                if "└" in line
                else len(line)
            ]

            # Count depth by analyzing tree prefix structure
            # Pattern: "    │   │   ├── filename" -> depth 3
            # Pattern: "    ├── filename" -> depth 1
            # Pattern: "    │   ├── filename" -> depth 2

            depth = 0
            i = 0
            while i < len(tree_prefix):
                # Look for pipe or tree junction
                if i + 4 <= len(tree_prefix):
                    chunk = tree_prefix[i : i + 4]
                    if "│" in chunk or all(c == " " for c in chunk):
                        depth += 1
                        i += 4
                    else:
                        i += 1
                else:
                    break

            # Fallback: use relative indentation
            if depth == 0:
                depth = max(1, (indent - base_indent) // 4 + 1)

            # === Clean and Extract Item Name ===
            item_name = line
            # Remove all tree characters
            for pattern in ["├──", "└──", "│", "├", "└", "─"]:
                item_name = item_name.replace(pattern, "")
            item_name = item_name.strip()

            # Remove inline comments
            if "#" in item_name:
                item_name = item_name.split("#")[0].strip()

            if not item_name or ":" in item_name:
                continue

            # === Smart Directory vs File Detection ===
            is_directory = self._is_directory(item_name)

            # === Update Path Stack ===
            # Remove items deeper than current depth
            path_stack = [(d, n) for d, n in path_stack if d < depth]

            if is_directory:
                dir_name = item_name.rstrip("/")
                path_stack.append((depth, dir_name))
                self.logger.debug(f"  {'  ' * depth}📁 {dir_name} (depth={depth})")
            else:
                # Construct full file path
                path_parts = [root_dir] if root_dir else []
                path_parts.extend([name for _, name in path_stack])
                path_parts.append(item_name)

                full_path = "/".join(path_parts)
                files.append(full_path)
                self.logger.debug(f"  {'  ' * depth}📄 {full_path}")

        return files

    def _is_directory(self, name: str) -> bool:
        """
        Advanced directory detection using multiple heuristics

        Returns True if the name represents a directory, False if it's a file
        """
        # Rule 1: Explicit directory marker
        if name.endswith("/"):
            return True

        # Rule 2: Has file extension -> definitely a file
        basename = name.split("/")[-1]
        if "." in basename:
            # Check if it's a known file extension
            known_extensions = [
                ".py",
                ".js",
                ".ts",
                ".jsx",
                ".tsx",
                ".vue",
                ".html",
                ".css",
                ".scss",
                ".sass",
                ".json",
                ".yaml",
                ".yml",
                ".xml",
                ".toml",
                ".md",
                ".txt",
                ".rst",
                ".sh",
                ".bat",
                ".ps1",
                ".c",
                ".cpp",
                ".h",
                ".hpp",
                ".java",
                ".go",
                ".rs",
                ".sql",
                ".db",
                ".env",
                ".gitignore",
                ".dockerignore",
                ".lock",
                ".sum",
                ".mod",
            ]
            if any(basename.lower().endswith(ext) for ext in known_extensions):
                return False

            # Has extension but not recognized -> might be config file, treat as file
            if basename.count(".") == 1:
                return False

        # Rule 3: Known special files without extensions
        special_files = [
            "README",
            "LICENSE",
            "CHANGELOG",
            "CONTRIBUTING",
            "Makefile",
            "Dockerfile",
            "Vagrantfile",
            "requirements.txt",
            "setup.py",
            "setup.cfg",
            "package.json",
            "package-lock.json",
            "Cargo.toml",
            "go.mod",
        ]
        if basename in special_files or basename.upper() in special_files:
            return False

        # Rule 4: Common directory names (even without trailing /)
        common_dirs = [
            "src",
            "lib",
            "app",
            "core",
            "api",
            "web",
            "client",
            "server",
            "config",
            "configs",
            "settings",
            "data",
            "datasets",
            "models",
            "model",
            "utils",
            "helpers",
            "common",
            "shared",
            "tests",
            "test",
            "testing",
            "__tests__",
            "docs",
            "documentation",
            "scripts",
            "bin",
            "tools",
            "assets",
            "static",
            "public",
            "resources",
            "components",
            "views",
            "pages",
            "routes",
            "services",
            "controllers",
            "handlers",
            "middleware",
            "middlewares",
            "types",
            "interfaces",
            "schemas",
            "experiments",
            "notebooks",
            "dist",
            "build",
            "output",
            "node_modules",
            "vendor",
            "packages",
            "__pycache__",
            ".git",
            ".vscode",
            "training",
            "evaluation",
            "inference",
        ]
        if basename.lower() in common_dirs:
            return True

        # Rule 5: Plural forms often indicate directories
        if basename.endswith("s") and len(basename) > 3:
            singular = basename[:-1]
            if singular in common_dirs:
                return True

        # Rule 6: Python package indicators
        if basename == "__init__.py":
            return False  # This is a file

        # Default: if no extension and not a known file, likely a directory
        return "." not in basename

    def _extract_from_simple_list(self, lines: List[str]) -> List[str]:
        """Extract files from simple list format (- filename)"""
        files = []

        for line in lines:
            line = line.strip()
            if line.startswith("- ") and not line.startswith('- "'):
                # Remove leading "- " and clean up
                filename = line[2:].strip()

                # Remove quotes if present
                if filename.startswith('"') and filename.endswith('"'):
                    filename = filename[1:-1]

                # Check if it looks like a file (has extension)
                if "." in filename and "/" in filename:
                    files.append(filename)

        return files

    def _extract_from_plan_content(self, lines: List[str]) -> List[str]:
        """
        Advanced fallback extraction: Extract files from anywhere in the plan content
        Uses multiple regex patterns and intelligent filtering
        """
        files = []
        import re

        # === Pattern 1: Standard file paths ===
        # Matches: path/to/file.py, src/model/apt_layer.py
        pattern1 = r"([a-zA-Z0-9_\-]+(?:/[a-zA-Z0-9_\-]+)+\.[a-zA-Z0-9]+)"

        # === Pattern 2: Quoted file paths ===
        # Matches: "path/to/file.py", 'src/utils.py'
        pattern2 = r'["\']([a-zA-Z0-9_\-]+(?:/[a-zA-Z0-9_\-]+)+\.[a-zA-Z0-9]+)["\']'

        # === Pattern 3: File paths with special characters ===
        # Matches: data/data_loader.py, __init__.py paths
        pattern3 = r"([a-zA-Z0-9_\-]+(?:/[a-zA-Z0-9_\-]+)*/__init__\.py)"
        pattern4 = r"([a-zA-Z0-9_\-]+(?:/[a-zA-Z0-9_\-]+)+\.(?:py|js|ts|jsx|tsx|html|css|md|txt|json|yaml|yml|xml|sql|sh|bat))"

        # === Pattern 5: Backtick-wrapped paths (in code blocks) ===
        pattern5 = r"`([a-zA-Z0-9_\-]+(?:/[a-zA-Z0-9_\-]+)+\.[a-zA-Z0-9]+)`"

        all_patterns = [pattern1, pattern2, pattern3, pattern4, pattern5]

        # Collect all potential matches
        potential_files = set()

        for line in lines:
            # Skip comment-only lines
            stripped = line.strip()
            if stripped.startswith("#") and not ("/" in stripped and "." in stripped):
                continue

            # Apply all patterns
            for pattern in all_patterns:
                matches = re.findall(pattern, line)
                potential_files.update(matches)

        # === Filter and validate matches ===
        code_extensions = {
            ".py",
            ".js",
            ".ts",
            ".jsx",
            ".tsx",
            ".vue",
            ".html",
            ".css",
            ".scss",
            ".sass",
            ".less",
            ".json",
            ".yaml",
            ".yml",
            ".toml",
            ".xml",
            ".ini",
            ".cfg",
            ".md",
            ".rst",
            ".txt",
            ".sh",
            ".bash",
            ".zsh",
            ".bat",
            ".ps1",
            ".cmd",
            ".c",
            ".cpp",
            ".h",
            ".hpp",
            ".cc",
            ".cxx",
            ".java",
            ".kt",
            ".scala",
            ".go",
            ".rs",
            ".php",
            ".rb",
            ".pl",
            ".lua",
            ".r",
            ".sql",
            ".db",
            ".dockerfile",
            ".env",
            ".gitignore",
            ".lock",
            ".sum",
            ".mod",
        }

        for file_path in potential_files:
            # Must have path separator
            if "/" not in file_path:
                continue

            # Must have valid extension
            has_valid_ext = any(
                file_path.lower().endswith(ext) for ext in code_extensions
            )
            if not has_valid_ext:
                continue

            # Filter out obvious non-files
            if any(
                bad in file_path.lower()
                for bad in [
                    "http://",
                    "https://",
                    ".png",
                    ".jpg",
                    ".jpeg",
                    ".gif",
                    ".svg",
                    ".ico",
                ]
            ):
                continue

            # Must not be too short (avoid false positives)
            if len(file_path) < 5:
                continue

            # Path components should be reasonable
            parts = file_path.split("/")
            if any(len(part) == 0 for part in parts):
                continue

            files.append(file_path)

        # Sort for consistency
        files = sorted(list(set(files)))

        return files

    def _clean_and_validate_files(self, files: List[str]) -> List[str]:
        """
        Clean and validate extracted file paths - advanced filtering and deduplication

        Features:
        1. Remove duplicates while preserving order
        2. Normalize paths (handle ../,  ./, double slashes)
        3. Filter out non-code files
        4. Smart deduplication (recognize same file with different path prefixes)
        """
        cleaned_files = []
        seen_normalized = set()

        # Define code file extensions we want to track
        code_extensions = {
            ".py",
            ".js",
            ".ts",
            ".jsx",
            ".tsx",
            ".vue",
            ".html",
            ".css",
            ".scss",
            ".sass",
            ".less",
            ".json",
            ".yaml",
            ".yml",
            ".toml",
            ".xml",
            ".ini",
            ".cfg",
            ".md",
            ".rst",
            ".txt",
            ".sh",
            ".bash",
            ".zsh",
            ".bat",
            ".ps1",
            ".cmd",
            ".c",
            ".cpp",
            ".h",
            ".hpp",
            ".cc",
            ".cxx",
            ".java",
            ".kt",
            ".scala",
            ".go",
            ".rs",
            ".php",
            ".rb",
            ".pl",
            ".lua",
            ".r",
            ".sql",
            ".db",
            ".dockerfile",
            ".env",
            ".gitignore",
            ".lock",
            ".sum",
            ".mod",
        }

        for file_path in files:
            # === Step 1: Basic Cleaning ===
            cleaned_path = file_path.strip().strip('"').strip("'").strip("`")

            if not cleaned_path:
                continue

            # Remove leading/trailing slashes
            cleaned_path = cleaned_path.strip("/")

            # === Step 2: Path Normalization ===
            # Remove double slashes
            while "//" in cleaned_path:
                cleaned_path = cleaned_path.replace("//", "/")

            # Handle relative paths (remove ./ prefix)
            if cleaned_path.startswith("./"):
                cleaned_path = cleaned_path[2:]

            # === Step 3: Validate File Structure ===
            # Must have filename (not just directory)
            if not cleaned_path or "/" not in cleaned_path:
                # Single file without path - only accept if it has extension
                if "." not in cleaned_path:
                    continue

            # Extract basename
            basename = cleaned_path.split("/")[-1]

            # Skip directories (no file extension in basename)
            if "." not in basename:
                continue

            # === Step 4: Extension Validation ===
            # Only include files with code extensions
            has_code_extension = any(
                cleaned_path.lower().endswith(ext) for ext in code_extensions
            )
            if not has_code_extension:
                continue

            # === Step 5: Filter Invalid Patterns ===
            # Skip files that look like YAML keys or config entries
            if ":" in cleaned_path and not any(
                cleaned_path.endswith(ext) for ext in [".yaml", ".yml"]
            ):
                continue

            # Skip paths with invalid characters
            if any(
                char in cleaned_path for char in ['"', "'", "|", "<", ">", "*", "?"]
            ):
                continue

            # Skip obvious build/temp artifacts
            if any(
                part in cleaned_path
                for part in [
                    "__pycache__",
                    ".pyc",
                    "node_modules",
                    ".git/",
                    "dist/build",
                ]
            ):
                continue

            # === Step 6: Smart Deduplication ===
            # Normalize for comparison (lowercase, remove common prefixes)
            normalized_for_comparison = cleaned_path.lower()

            # Check if we've already seen this file (exact match)
            if normalized_for_comparison in seen_normalized:
                continue

            # Check for duplicate with different path (e.g., "src/model/apt_layer.py" vs "model/apt_layer.py")
            # Keep the longer (more specific) path
            is_duplicate = False
            paths_to_remove = []

            for existing_normalized in seen_normalized:
                # If current path is suffix of existing, it's a shorter version - skip it
                if existing_normalized.endswith("/" + normalized_for_comparison):
                    is_duplicate = True
                    break

                # If existing path is suffix of current, current is longer - replace existing
                if normalized_for_comparison.endswith("/" + existing_normalized):
                    paths_to_remove.append(existing_normalized)

            if is_duplicate:
                continue

            # Remove shorter versions
            for path_to_remove in paths_to_remove:
                seen_normalized.discard(path_to_remove)
                # Also remove from cleaned_files list
                cleaned_files = [
                    f for f in cleaned_files if f.lower() != path_to_remove
                ]

            # === Step 7: Add to Results ===
            seen_normalized.add(normalized_for_comparison)
            cleaned_files.append(cleaned_path)

        return sorted(cleaned_files)

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

            # Extract different sections from LLM summary
            sections = self._extract_summary_sections(llm_summary)

            # Store Next Steps in temporary variable (not saved to file)
            self.current_next_steps = sections.get("next_steps", "")
            if self.current_next_steps:
                self.logger.info("📝 Next Steps stored temporarily (not saved to file)")

            # Format summary with only Implementation Progress and Dependencies for file saving
            file_summary_content = ""
            if sections.get("core_purpose"):
                file_summary_content += sections["core_purpose"] + "\n\n"
            if sections.get("public_interface"):
                file_summary_content += sections["public_interface"] + "\n\n"
            if sections.get("internal_dependencies"):
                file_summary_content += sections["internal_dependencies"] + "\n\n"
            if sections.get("external_dependencies"):
                file_summary_content += sections["external_dependencies"] + "\n\n"
            if sections.get("implementation_notes"):
                file_summary_content += sections["implementation_notes"] + "\n\n"

            # Create the formatted summary for file saving (without Next Steps)
            formatted_summary = self._format_code_implementation_summary(
                file_path, file_summary_content.strip(), files_implemented
            )

            # Save to implement_code_summary.md (append mode) - only Implementation Progress and Dependencies
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

        # Get formatted file lists
        file_lists = self.get_formatted_files_lists()
        implemented_files_list = file_lists["implemented"]
        unimplemented_files_list = file_lists["unimplemented"]

        prompt = f"""You are an expert code implementation summarizer. Analyze the implemented code file and create a structured summary.

**🚨 CRITICAL: The files listed below are ALREADY IMPLEMENTED - DO NOT suggest them in Next Steps! 🚨**

**All Previously Implemented Files:**
{implemented_files_list}

**Remaining Unimplemented Files (choose ONLY from these for Next Steps):**
{unimplemented_files_list}

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

**Next Steps**: List the code file (ONLY ONE) that will be implemented in the next round (MUST choose from "Remaining Unimplemented Files" above)
  Format: Code will be implemented: {{file_path}}
  **NEVER suggest any file from the "All Previously Implemented Files" list!**

**Instructions:**
- Be precise and concise
- Focus on function interfaces that other files will need
- Extract actual function signatures from the code
- **CRITICAL: For Next Steps, ONLY choose ONE file from the "Remaining Unimplemented Files" list above**
- **NEVER suggest implementing a file that is already in the implemented files list**
- Choose the next file based on logical dependencies and implementation order
- Use the exact format specified above

**Summary:**"""

        return prompt

    # TODO: The prompt is not good, need to be improved
    # **Implementation Progress**: List the code file completed in current round and core implementation ideas
    #   Format: {{file_path}}: {{core implementation ideas}}

    # **Dependencies**: According to the File Structure and initial plan, list functions that may be called by other files
    #   Format: {{file_path}}: Function {{function_name}}: core ideas--{{ideas}}; Required parameters--{{params}}; Return parameters--{{returns}}
    #   Required packages: {{packages}}

    def _extract_summary_sections(self, llm_summary: str) -> Dict[str, str]:
        """
        Extract different sections from LLM-generated summary

        Args:
            llm_summary: Raw LLM-generated summary text

        Returns:
            Dictionary with extracted sections: core_purpose, public_interface, internal_dependencies,
            external_dependencies, implementation_notes, next_steps
        """
        sections = {
            "core_purpose": "",
            "public_interface": "",
            "internal_dependencies": "",
            "external_dependencies": "",
            "implementation_notes": "",
            "next_steps": "",
        }

        try:
            lines = llm_summary.split("\n")
            current_section = None
            current_content = []

            for line in lines:
                line_lower = line.lower().strip()

                # Check for section headers
                if "core purpose" in line_lower:
                    if current_section and current_content:
                        sections[current_section] = "\n".join(current_content).strip()
                    current_section = "core_purpose"
                    current_content = [line]  # Include the header
                elif "public interface" in line_lower:
                    if current_section and current_content:
                        sections[current_section] = "\n".join(current_content).strip()
                    current_section = "public_interface"
                    current_content = [line]  # Include the header
                elif "internal dependencies" in line_lower:
                    if current_section and current_content:
                        sections[current_section] = "\n".join(current_content).strip()
                    current_section = "internal_dependencies"
                    current_content = [line]  # Include the header
                elif "external dependencies" in line_lower:
                    if current_section and current_content:
                        sections[current_section] = "\n".join(current_content).strip()
                    current_section = "external_dependencies"
                    current_content = [line]  # Include the header
                elif "implementation notes" in line_lower:
                    if current_section and current_content:
                        sections[current_section] = "\n".join(current_content).strip()
                    current_section = "implementation_notes"
                    current_content = [line]  # Include the header
                elif "next steps" in line_lower:
                    if current_section and current_content:
                        sections[current_section] = "\n".join(current_content).strip()
                    current_section = "next_steps"
                    current_content = [line]  # Include the header
                else:
                    # Add content to current section
                    if current_section:
                        current_content.append(line)

            # Don't forget the last section
            if current_section and current_content:
                sections[current_section] = "\n".join(current_content).strip()

            self.logger.info(f"📋 Extracted sections: {list(sections.keys())}")

        except Exception as e:
            self.logger.error(f"Failed to extract summary sections: {e}")
            # Fallback: put everything in core_purpose
            sections["core_purpose"] = llm_summary

        return sections

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

        # # Create formatted list of implemented files
        # implemented_files_list = (
        #     "\n".join([f"- {file}" for file in self.implemented_files])
        #     if self.implemented_files
        #     else "- None yet"
        # )

        #         formatted_summary = f"""# Code Implementation Summary
        # **All Previously Implemented Files:**
        # {implemented_files_list}
        # **Generated**: {timestamp}
        # **File Implemented**: {file_path}
        # **Total Files Implemented**: {files_implemented}

        # {llm_summary}

        # ---
        # *Auto-generated by Memory Agent*
        # """
        formatted_summary = f"""# Code Implementation Summary
**Generated**: {timestamp}
**File Implemented**: {file_path}

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
            if response and hasattr(response, "content") and response.content:
                for block in response.content:
                    if block.type == "text":
                        content += block.text
            else:
                self.logger.warning("Anthropic response is empty or malformed")

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
                    max_tokens=5000,
                    temperature=0.2,
                )
            except Exception as e:
                if "max_tokens" in str(e) and "max_completion_tokens" in str(e):
                    # Retry with max_completion_tokens and no temperature for models that require it
                    response = await client.chat.completions.create(
                        model=self.default_models["openai"],
                        messages=openai_messages,
                        max_completion_tokens=5000,
                    )
                else:
                    raise

            # Safely extract content from response
            if response and hasattr(response, "choices") and response.choices:
                return {"content": response.choices[0].message.content or ""}
            else:
                self.logger.warning("OpenAI response is empty or malformed")
                return {"content": ""}

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
                max_output_tokens=5000,
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
            # "read_code_mem",  # Read code summary from implement_code_summary.md
            # "read_file",  # Read file contents
            "write_file",  # Write file contents (important for tracking implementations)
            # "execute_python",  # Execute Python code (for testing/validation)
            "execute_bash",  # Execute bash commands (for build/execution)
            # "search_code",  # Search code patterns
            "search_reference_code",  # Search reference code (if available)
            # "get_file_structure",  # Get file structure (for understanding project layout)
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
        self,
        system_prompt: str,
        messages: List[Dict[str, Any]],
        files_implemented: int,
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

        # Get formatted file lists
        file_lists = self.get_formatted_files_lists()
        implemented_files_list = file_lists["implemented"]
        unimplemented_files_list = file_lists["unimplemented"]

        # Debug output for unimplemented files (clean format without dashes)
        unimplemented_files = self.get_unimplemented_files()
        print("✅ Unimplemented Files:")
        for file_path in unimplemented_files:
            print(f"{file_path}")
        if self.current_next_steps.strip():
            print(f"\n📋 {self.current_next_steps}")

        # 1. Add initial plan message (always preserved)
        initial_plan_message = {
            "role": "user",
            "content": f"""**Task: Implement code based on the following reproduction plan**

**Code Reproduction Plan:**
{self.initial_plan}

**Working Directory:** Current workspace

**All Previously Implemented Files:**
{implemented_files_list}

**Current Status:** {files_implemented} files implemented

**Remaining Files to Implement:**
{unimplemented_files_list}

**IMPORTANT:** If the remaining files list shows "All files implemented!", you MUST reply with "All files implemented" to complete the task. Do NOT continue calling tools.

**Objective:** {"Reply 'All files implemented' to finish" if not unimplemented_files else "Continue implementation by analyzing dependencies and implementing the next required file according to the plan's priority order."}""",
        }

        # Append Next Steps information if available
        # if self.current_next_steps.strip():
        #     initial_plan_message["content"] += (
        #         f"\n\n**Next Steps (from previous analysis):**\n{self.current_next_steps}"
        #     )

        concise_messages.append(initial_plan_message)

        # 2. Add Knowledge Base
        knowledge_base_message = {
            "role": "user",
            "content": f"""**Below is the Knowledge Base of the LATEST implemented code file:**
{self._read_code_knowledge_base()}

**Development Cycle - START HERE:**

**FIRST - Check completion status:**
- If "Remaining Files to Implement" above shows "All files implemented!", reply "All files implemented" immediately

**For NEW file implementation (if remaining files exist):**
1. `search_code_references` → OPTIONALLY search reference patterns for inspiration (use for reference only, original paper specs take priority)
2. Write_file can be used to implement the new component

**Remember:** Stop and declare completion when all files are done!""",
        }
        if self.current_next_steps.strip():
            knowledge_base_message["content"] += (
                f"\n\n**Next Steps (from previous analysis):**\n{self.current_next_steps}"
            )
        concise_messages.append(knowledge_base_message)

        # 3. Add current tool results (essential information for next file generation)
        if self.current_round_tool_results:
            tool_results_content = self._format_tool_results()

            # # Append Next Steps information if available
            # if self.current_next_steps.strip():
            #     tool_results_content += f"\n\n**Next Steps (from previous analysis):**\n{self.current_next_steps}"

            tool_results_message = {
                "role": "user",
                "content": f"""**Current Tool Results:**
{tool_results_content}""",
            }
            concise_messages.append(tool_results_message)
        else:
            # If no tool results yet, add guidance for next steps
            guidance_content = f"""**Current Round:** {self.current_round}

**Development Cycle - START HERE:**

**For NEW file implementation:**
1. `search_code_references` → OPTIONALLY search reference patterns for inspiration (use for reference only, original paper specs take priority)
2. Write_file can be used to implement the new component"""

            # # Append Next Steps information if available (even when no tool results)
            # if self.current_next_steps.strip():
            #     guidance_content += f"\n\n**Next Steps (from previous analysis):**\n{self.current_next_steps}"

            guidance_message = {
                "role": "user",
                "content": guidance_content,
            }
            concise_messages.append(guidance_message)
        # **Available Essential Tools:** read_code_mem, write_file, execute_python, execute_bash
        # **Remember:** Start with read_code_mem when implementing NEW files to understand existing code. When all files are implemented, focus on testing and completion. Implement according to the original paper's specifications - any reference code is for inspiration only.
        # self.logger.info(f"✅ Concise messages created: {len(concise_messages)} messages (original: {len(messages)})")
        return concise_messages

    def _read_code_knowledge_base(self) -> Optional[str]:
        """
        Read the implement_code_summary.md file as code knowledge base
        Returns all content from the file

        Returns:
            Full content of the file if it exists, None otherwise
        """
        try:
            if os.path.exists(self.code_summary_path):
                with open(self.code_summary_path, "r", encoding="utf-8") as f:
                    content = f.read().strip()

                if content:
                    # Return all content instead of just the latest entry
                    return content
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
        unimplemented_files = self.get_unimplemented_files()
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
            "next_steps_available": bool(self.current_next_steps.strip()),
            "next_steps_length": len(self.current_next_steps.strip())
            if self.current_next_steps
            else 0,
            # File tracking statistics
            "total_files_in_plan": len(self.all_files_list),
            "files_implemented_count": len(self.implemented_files),
            "files_remaining_count": len(unimplemented_files),
            "all_files_list": self.all_files_list.copy(),
            "unimplemented_files_list": unimplemented_files,
            "implementation_progress_percent": (
                len(self.implemented_files) / len(self.all_files_list) * 100
            )
            if self.all_files_list
            else 0,
        }

    def get_implemented_files(self) -> List[str]:
        """Get list of all implemented files"""
        return self.implemented_files.copy()

    def get_all_files_list(self) -> List[str]:
        """Get list of all files that should be implemented according to the plan"""
        return self.all_files_list.copy()

    def refresh_files_list_from_directory(self) -> bool:
        """
        Refresh the files list by extracting from the generated directory
        Useful when the directory structure has been updated after initialization

        Returns:
            True if successfully refreshed from directory, False if fell back to plan
        """
        if os.path.exists(self.code_directory):
            files_from_dir = self._extract_files_from_generated_directory()
            if files_from_dir:
                old_count = len(self.all_files_list)
                self.all_files_list = files_from_dir
                new_count = len(self.all_files_list)
                self.logger.info(
                    f"🔄 Files list refreshed from directory: {old_count} → {new_count} files"
                )
                return True

        self.logger.warning("Cannot refresh from directory, keeping current list")
        return False

    def get_unimplemented_files(self) -> List[str]:
        """
        Get list of files that haven't been implemented yet
        Uses fuzzy path matching to handle partial paths

        Returns:
            List of file paths that still need to be implemented
        """

        # def is_implemented(plan_file: str) -> bool:
        #     """Check if a file from plan is implemented (with fuzzy matching)"""
        #     # Normalize paths for comparison
        #     plan_file_normalized = plan_file.replace("\\", "/").strip("/")
        #     plan_filename = plan_file_normalized.split("/")[-1]  # Extract filename

        #     for impl_file in self.implemented_files:
        #         impl_file_normalized = impl_file.replace("\\", "/").strip("/")
        #         impl_filename = impl_file_normalized.split("/")[-1]  # Extract filename

        #         # Strategy 1: Exact path match
        #         if plan_file_normalized == impl_file_normalized:
        #             return True

        #         # Strategy 2: One path ends with the other (partial path match)
        #         if plan_file_normalized.endswith(
        #             impl_file_normalized
        #         ) or impl_file_normalized.endswith(plan_file_normalized):
        #             # Ensure match is at a path boundary (not middle of directory name)
        #             if (
        #                 plan_file_normalized.endswith("/" + impl_file_normalized)
        #                 or impl_file_normalized.endswith("/" + plan_file_normalized)
        #             ):
        #                 return True

        #         # Strategy 3: Same filename (fallback for different directory structures)
        #         # Only match if filenames are identical and reasonably unique (length > 5)
        #         if (plan_filename == impl_filename and len(plan_filename) > 5):
        #             return True

        #     return False
        def is_implemented(plan_file: str) -> bool:
            """Check if a file from plan is implemented (with fuzzy matching)"""
            # Normalize paths for comparison
            plan_file_normalized = plan_file.replace("\\", "/").strip("/")

            for impl_file in self.implemented_files:
                impl_file_normalized = impl_file.replace("\\", "/").strip("/")

                # Check if plan_file ends with impl_file (partial path match)
                # or impl_file ends with plan_file (reverse partial match)
                if plan_file_normalized.endswith(
                    impl_file_normalized
                ) or impl_file_normalized.endswith(plan_file_normalized):
                    # Ensure match is at a path boundary (not middle of directory name)
                    if (
                        plan_file_normalized.endswith("/" + impl_file_normalized)
                        or plan_file_normalized == impl_file_normalized
                        or impl_file_normalized.endswith("/" + plan_file_normalized)
                    ):
                        return True
            return False

        # unimplemented = [f for f in self.all_files_list if not is_implemented(f)]
        # return unimplemented

        unimplemented = [f for f in self.all_files_list if not is_implemented(f)]
        return unimplemented

    def get_formatted_files_lists(self) -> Dict[str, str]:
        """
        Get formatted strings for implemented and unimplemented files

        Returns:
            Dictionary with 'implemented' and 'unimplemented' formatted lists
        """
        implemented_list = (
            "\n".join([f"- {file}" for file in self.implemented_files])
            if self.implemented_files
            else "- None yet"
        )

        unimplemented_files = self.get_unimplemented_files()
        unimplemented_list = (
            "\n".join([f"- {file}" for file in unimplemented_files])
            if unimplemented_files
            else "- All files implemented!"
        )

        return {"implemented": implemented_list, "unimplemented": unimplemented_list}

    def get_current_next_steps(self) -> str:
        """Get the current Next Steps information"""
        return self.current_next_steps

    def clear_next_steps(self):
        """Clear the stored Next Steps information"""
        if self.current_next_steps.strip():
            self.logger.info("🧹 Next Steps information cleared")
        self.current_next_steps = ""

    def set_next_steps(self, next_steps: str):
        """Manually set Next Steps information"""
        self.current_next_steps = next_steps
        self.logger.info(
            f"📝 Next Steps manually set ({len(next_steps.strip())} chars)"
        )

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
        print(
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
        print(f"Next Steps available: {stats['next_steps_available']}")
        print(f"Next Steps length: {stats['next_steps_length']} chars")
        if self.current_next_steps.strip():
            print(f"Next Steps preview: {self.current_next_steps[:100]}...")
        print("")
        print("📋 FILE TRACKING:")
        print(f"  Total files in plan: {stats['total_files_in_plan']}")
        print(f"  Files implemented: {stats['files_implemented_count']}")
        print(f"  Files remaining: {stats['files_remaining_count']}")
        print(f"  Progress: {stats['implementation_progress_percent']:.1f}%")
        if stats["unimplemented_files_list"]:
            print(f"  Next possible files: {stats['unimplemented_files_list'][:3]}...")
        print("")
        print(
            "📊 NEW LOGIC: write_file → clear memory → accumulate tools → next write_file"
        )
        print("📊 NEXT STEPS: Stored separately from file, included in tool results")
        print(
            "📊 FILE TRACKING: All files extracted from plan, unimplemented files guide LLM decisions"
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
