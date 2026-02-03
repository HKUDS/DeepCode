"""
Code Indexer for Repository Analysis

Analyzes code repositories to build comprehensive indexes for each subdirectory,
identifying file relationships and reusable components for implementation.

Features:
- Recursive file traversal
- LLM-powered code similarity analysis using augmented LLM classes
- JSON-based relationship storage
- Configurable matching strategies
- Progress tracking and error handling
- Automatic LLM provider selection based on API key availability
"""

import asyncio
import json
import logging
import os
import re
from datetime import datetime
from pathlib import Path
from dataclasses import dataclass, asdict
from typing import List, Dict, Any

# MCP Agent imports for LLM
from utils.llm_utils import get_preferred_llm_class, get_default_models


@dataclass
class FileRelationship:
    """Represents a relationship between a repo file and target structure file"""

    repo_file_path: str
    target_file_path: str
    relationship_type: str  # 'direct_match', 'partial_match', 'reference', 'utility'
    confidence_score: float  # 0.0 to 1.0
    helpful_aspects: List[str]
    potential_contributions: List[str]
    usage_suggestions: str


@dataclass
class FileSummary:
    """Summary information for a repository file"""

    file_path: str
    file_type: str
    main_functions: List[str]
    key_concepts: List[str]
    dependencies: List[str]
    summary: str
    lines_of_code: int
    last_modified: str


@dataclass
class RepoIndex:
    """Complete index for a repository"""

    repo_name: str
    total_files: int
    file_summaries: List[FileSummary]
    relationships: List[FileRelationship]
    analysis_metadata: Dict[str, Any]


class CodeIndexer:
    """Main class for building code repository indexes"""

    def __init__(
        self,
        code_base_path: str = None,
        target_structure: str = None,
        output_dir: str = None,
        config_path: str = "mcp_agent.secrets.yaml",
        indexer_config_path: str = None,
        enable_pre_filtering: bool = True,
    ):
        # Load configurations first
        self.config_path = config_path
        self.indexer_config_path = indexer_config_path
        self.api_config = self._load_api_config()
        self.indexer_config = self._load_indexer_config()
        self.default_models = get_default_models("mcp_agent.config.yaml")

        # Use config paths if not provided as parameters
        paths_config = self.indexer_config.get("paths", {})
        self.code_base_path = Path(
            code_base_path or paths_config.get("code_base_path", "code_base")
        )
        self.output_dir = Path(output_dir or paths_config.get("output_dir", "indexes"))
        self.target_structure = (
            target_structure  # This must be provided as it's project-specific
        )
        self.enable_pre_filtering = enable_pre_filtering

        # LLM clients
        self.llm_client = None
        self.llm_client_type = None

        # Initialize logger early
        self.logger = self._setup_logger()

        # Create output directory if it doesn't exist
        self.output_dir.mkdir(parents=True, exist_ok=True)

        # Load file analysis configuration
        file_analysis_config = self.indexer_config.get("file_analysis", {})
        self.supported_extensions = set(
            file_analysis_config.get(
                "supported_extensions",
                [
                    ".py",
                    ".js",
                    ".ts",
                    ".java",
                    ".cpp",
                    ".c",
                    ".h",
                    ".hpp",
                    ".cs",
                    ".php",
                    ".rb",
                    ".go",
                    ".rs",
                    ".scala",
                    ".kt",
                    ".swift",
                    ".m",
                    ".mm",
                    ".r",
                    ".matlab",
                    ".sql",
                    ".sh",
                    ".bat",
                    ".ps1",
                    ".yaml",
                    ".yml",
                    ".json",
                    ".xml",
                    ".toml",
                ],
            )
        )

        self.skip_directories = set(
            file_analysis_config.get(
                "skip_directories",
                [
                    "__pycache__",
                    "node_modules",
                    "target",
                    "build",
                    "dist",
                    "venv",
                    "env",
                ],
            )
        )

        self.max_file_size = file_analysis_config.get("max_file_size", 1048576)  # 1MB
        self.max_content_length = file_analysis_config.get("max_content_length", 3000)

        # Load LLM configuration
        llm_config = self.indexer_config.get("llm", {})
        self.model_provider = llm_config.get("model_provider", "anthropic")
        self.llm_max_tokens = llm_config.get("max_tokens", 4000)
        self.llm_temperature = llm_config.get("temperature", 0.3)
        self.llm_system_prompt = llm_config.get(
            "system_prompt",
            "You are a code analysis expert. Provide precise, structured analysis of code relationships and similarities.",
        )
        self.request_delay = llm_config.get("request_delay", 0.1)
        self.max_retries = llm_config.get("max_retries", 3)
        self.retry_delay = llm_config.get("retry_delay", 1.0)

        # Load relationship configuration
        relationship_config = self.indexer_config.get("relationships", {})
        self.min_confidence_score = relationship_config.get("min_confidence_score", 0.3)
        self.high_confidence_threshold = relationship_config.get(
            "high_confidence_threshold", 0.7
        )
        self.relationship_types = relationship_config.get(
            "relationship_types",
            {
                "direct_match": 1.0,
                "partial_match": 0.8,
                "reference": 0.6,
                "utility": 0.4,
            },
        )

        # Load performance configuration
        performance_config = self.indexer_config.get("performance", {})
        self.enable_concurrent_analysis = performance_config.get(
            "enable_concurrent_analysis", False
        )
        self.max_concurrent_files = performance_config.get("max_concurrent_files", 5)
        self.enable_content_caching = performance_config.get(
            "enable_content_caching", False
        )
        self.max_cache_size = performance_config.get("max_cache_size", 100)

        # Load debug configuration
        debug_config = self.indexer_config.get("debug", {})
        self.save_raw_responses = debug_config.get("save_raw_responses", False)
        self.raw_responses_dir = debug_config.get(
            "raw_responses_dir", "debug_responses"
        )
        self.verbose_output = debug_config.get("verbose_output", False)
        self.mock_llm_responses = debug_config.get("mock_llm_responses", False)

        # Load output configuration
        output_config = self.indexer_config.get("output", {})
        self.generate_summary = output_config.get("generate_summary", True)
        self.generate_statistics = output_config.get("generate_statistics", True)
        self.include_metadata = output_config.get("include_metadata", True)
        self.index_filename_pattern = output_config.get(
            "index_filename_pattern", "{repo_name}_index.json"
        )
        self.summary_filename = output_config.get(
            "summary_filename", "indexing_summary.json"
        )
        self.stats_filename = output_config.get(
            "stats_filename", "indexing_statistics.json"
        )

        # Initialize caching if enabled
        self.content_cache = {} if self.enable_content_caching else None

        # Create debug directory if needed
        if self.save_raw_responses:
            Path(self.raw_responses_dir).mkdir(parents=True, exist_ok=True)

        # Debug logging
        if self.verbose_output:
            self.logger.info(
                f"Initialized CodeIndexer with config: {self.indexer_config_path}"
            )
            self.logger.info(f"Code base path: {self.code_base_path}")
            self.logger.info(f"Output directory: {self.output_dir}")
            self.logger.info(f"Model provider: {self.model_provider}")
            self.logger.info(f"Concurrent analysis: {self.enable_concurrent_analysis}")
            self.logger.info(f"Content caching: {self.enable_content_caching}")
            self.logger.info(f"Mock LLM responses: {self.mock_llm_responses}")

    def _setup_logger(self) -> logging.Logger:
        """Setup logging configuration from config file"""
        logger = logging.getLogger("CodeIndexer")

        # Get logging config
        logging_config = self.indexer_config.get("logging", {})
        log_level = logging_config.get("level", "INFO")
        log_format = logging_config.get(
            "log_format", "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
        )

        logger.setLevel(getattr(logging, log_level.upper(), logging.INFO))

        # Clear existing handlers
        logger.handlers.clear()

        # Console handler
        handler = logging.StreamHandler()
        formatter = logging.Formatter(log_format)
        handler.setFormatter(formatter)
        logger.addHandler(handler)

        # File handler if enabled
        if logging_config.get("log_to_file", False):
            log_file = logging_config.get("log_file", "indexer.log")
            file_handler = logging.FileHandler(log_file, encoding="utf-8")
            file_handler.setFormatter(formatter)
            logger.addHandler(file_handler)

        return logger

    def _load_api_config(self) -> Dict[str, Any]:
        """Load API configuration from YAML file"""
        try:
            import yaml

            with open(self.config_path, "r", encoding="utf-8") as f:
                return yaml.safe_load(f)
        except Exception as e:
            # Create a basic logger for this error since self.logger doesn't exist yet
            print(f"Warning: Failed to load API config from {self.config_path}: {e}")
            return {}

    def _load_indexer_config(self) -> Dict[str, Any]:
        """Load indexer configuration from YAML file"""
        try:
            import yaml

            with open(self.indexer_config_path, "r", encoding="utf-8") as f:
                config = yaml.safe_load(f)
                if config is None:
                    config = {}
                return config
        except Exception as e:
            print(
                f"Warning: Failed to load indexer config from {self.indexer_config_path}: {e}"
            )
            print("Using default configuration values")
            return {}

    async def _initialize_llm_client(self):
        """Initialize LLM client (Anthropic or OpenAI) based on API key availability"""
        if self.llm_client is not None:
            return self.llm_client, self.llm_client_type

        # Check if mock responses are enabled
        if self.mock_llm_responses:
            self.logger.info("Using mock LLM responses for testing")
            self.llm_client = "mock"
            self.llm_client_type = "mock"
            return "mock", "mock"

        # Check which API has available key and try that first
        anthropic_key = self.api_config.get("anthropic", {}).get("api_key", "")
        openai_key = self.api_config.get("openai", {}).get("api_key", "")

        # Try Anthropic API first if key is available
        if anthropic_key and anthropic_key.strip():
            try:
                from anthropic import AsyncAnthropic

                client = AsyncAnthropic(api_key=anthropic_key)
                # Test connection with default model from config
                await client.messages.create(
                    model=self.default_models["anthropic"],
                    max_tokens=10,
                    messages=[{"role": "user", "content": "test"}],
                )
                self.logger.info(
                    f"Using Anthropic API with model: {self.default_models['anthropic']}"
                )
                self.llm_client = client
                self.llm_client_type = "anthropic"
                return client, "anthropic"
            except Exception as e:
                self.logger.warning(f"Anthropic API unavailable: {e}")

        # Try OpenAI API if Anthropic failed or key not available
        if openai_key and openai_key.strip():
            try:
                from openai import AsyncOpenAI

                # Handle custom base_url if specified
                openai_config = self.api_config.get("openai", {})
                base_url = openai_config.get("base_url")

                if base_url:
                    client = AsyncOpenAI(api_key=openai_key, base_url=base_url)
                else:
                    client = AsyncOpenAI(api_key=openai_key)

                # Test connection with default model from config
                await client.chat.completions.create(
                    model=self.default_models["openai"],
                    max_tokens=10,
                    messages=[{"role": "user", "content": "test"}],
                )
                self.logger.info(
                    f"Using OpenAI API with model: {self.default_models['openai']}"
                )
                if base_url:
                    self.logger.info(f"Using custom base URL: {base_url}")
                self.llm_client = client
                self.llm_client_type = "openai"
                return client, "openai"
            except Exception as e:
                self.logger.warning(f"OpenAI API unavailable: {e}")

        raise ValueError(
            "No available LLM API - please check your API keys in configuration"
        )

    async def _call_llm(
        self, prompt: str, system_prompt: str = None, max_tokens: int = None
    ) -> str:
        """Call LLM for code analysis with retry mechanism and debugging support"""
        if system_prompt is None:
            system_prompt = self.llm_system_prompt
        if max_tokens is None:
            max_tokens = self.llm_max_tokens

        # Mock response for testing
        if self.mock_llm_responses:
            mock_response = self._generate_mock_response(prompt)
            if self.save_raw_responses:
                self._save_debug_response("mock", prompt, mock_response)
            return mock_response

        last_error = None

        # Retry mechanism
        for attempt in range(self.max_retries):
            try:
                if self.verbose_output and attempt > 0:
                    self.logger.info(
                        f"LLM call attempt {attempt + 1}/{self.max_retries}"
                    )

                client, client_type = await self._initialize_llm_client()

                if client_type == "anthropic":
                    response = await client.messages.create(
                        model=self.default_models["anthropic"],
                        system=system_prompt,
                        messages=[{"role": "user", "content": prompt}],
                        max_tokens=max_tokens,
                        temperature=self.llm_temperature,
                    )

                    content = ""
                    for block in response.content:
                        if block.type == "text":
                            content += block.text

                    # Save debug response if enabled
                    if self.save_raw_responses:
                        self._save_debug_response("anthropic", prompt, content)

                    return content

                elif client_type == "openai":
                    messages = [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": prompt},
                    ]

                    response = await client.chat.completions.create(
                        model=self.default_models["openai"],
                        messages=messages,
                        max_tokens=max_tokens,
                        temperature=self.llm_temperature,
                    )

                    content = response.choices[0].message.content or ""

                    # Save debug response if enabled
                    if self.save_raw_responses:
                        self._save_debug_response("openai", prompt, content)

                    return content
                else:
                    raise ValueError(f"Unsupported client type: {client_type}")

            except Exception as e:
                last_error = e
                self.logger.warning(f"LLM call attempt {attempt + 1} failed: {e}")

                if attempt < self.max_retries - 1:
                    await asyncio.sleep(
                        self.retry_delay * (attempt + 1)
                    )  # Exponential backoff

        # All retries failed
        error_msg = f"LLM call failed after {self.max_retries} attempts. Last error: {str(last_error)}"
        self.logger.error(error_msg)
        return f"Error in LLM analysis: {error_msg}"

    def _generate_mock_response(self, prompt: str) -> str:
        """Generate mock LLM response for testing"""
        if "JSON format" in prompt and "file_type" in prompt:
            # File analysis mock
            return """
            {
                "file_type": "Python module",
                "main_functions": ["main_function", "helper_function"],
                "key_concepts": ["data_processing", "algorithm"],
                "dependencies": ["numpy", "pandas"],
                "summary": "Mock analysis of code file functionality."
            }
            """
        elif "relationships" in prompt:
            # Relationship analysis mock
            return """
            {
                "relationships": [
                    {
                        "target_file_path": "src/core/mock.py",
                        "relationship_type": "partial_match",
                        "confidence_score": 0.8,
                        "helpful_aspects": ["algorithm implementation", "data structures"],
                        "potential_contributions": ["core functionality", "utility methods"],
                        "usage_suggestions": "Mock relationship suggestion for testing."
                    }
                ]
            }
            """
        elif "relevant_files" in prompt:
            # File filtering mock
            return """
            {
                "relevant_files": [
                    {
                        "file_path": "mock_file.py",
                        "relevance_reason": "Mock relevance reason",
                        "confidence": 0.9,
                        "expected_contribution": "Mock contribution"
                    }
                ],
                "summary": {
                    "total_files_analyzed": "10",
                    "relevant_files_count": "1",
                    "filtering_strategy": "Mock filtering strategy"
                }
            }
            """
        else:
            return "Mock LLM response for testing purposes."

    def _save_debug_response(self, provider: str, prompt: str, response: str):
        """Save LLM response for debugging"""
        try:
            import hashlib
            from datetime import datetime

            # Create a hash of the prompt for filename
            prompt_hash = hashlib.md5(prompt.encode()).hexdigest()[:8]
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"{provider}_{timestamp}_{prompt_hash}.json"

            debug_data = {
                "timestamp": datetime.now().isoformat(),
                "provider": provider,
                "prompt": prompt[:500] + "..." if len(prompt) > 500 else prompt,
                "response": response,
                "full_prompt_length": len(prompt),
            }

            debug_file = Path(self.raw_responses_dir) / filename
            with open(debug_file, "w", encoding="utf-8") as f:
                json.dump(debug_data, f, indent=2, ensure_ascii=False)

        except Exception as e:
            self.logger.warning(f"Failed to save debug response: {e}")

    def get_all_repo_files(self, repo_path: Path) -> List[Path]:
        """Recursively get all supported files in a repository"""
        files = []

        try:
            for root, dirs, filenames in os.walk(repo_path):
                # Skip common non-code directories
                dirs[:] = [
                    d
                    for d in dirs
                    if not d.startswith(".") and d not in self.skip_directories
                ]

                for filename in filenames:
                    file_path = Path(root) / filename
                    if file_path.suffix.lower() in self.supported_extensions:
                        files.append(file_path)

        except Exception as e:
            self.logger.error(f"Error traversing {repo_path}: {e}")

        return files

    def generate_file_tree(self, repo_path: Path, max_depth: int = 5) -> str:
        """Generate file tree structure string for the repository"""
        tree_lines = []

        def add_to_tree(current_path: Path, prefix: str = "", depth: int = 0):
            if depth > max_depth:
                return

            try:
                items = sorted(
                    current_path.iterdir(), key=lambda x: (x.is_file(), x.name.lower())
                )
                # Filter out irrelevant directories and files
                items = [
                    item
                    for item in items
                    if not item.name.startswith(".")
                    and item.name not in self.skip_directories
                ]

                for i, item in enumerate(items):
                    is_last = i == len(items) - 1
                    current_prefix = "└── " if is_last else "├── "
                    tree_lines.append(f"{prefix}{current_prefix}{item.name}")

                    if item.is_dir():
                        extension_prefix = "    " if is_last else "│   "
                        add_to_tree(item, prefix + extension_prefix, depth + 1)
                    elif item.suffix.lower() in self.supported_extensions:
                        # Add file size information
                        try:
                            size = item.stat().st_size
                            if size > 1024:
                                size_str = f" ({size // 1024}KB)"
                            else:
                                size_str = f" ({size}B)"
                            tree_lines[-1] += size_str
                        except (OSError, PermissionError):
                            pass

            except PermissionError:
                tree_lines.append(f"{prefix}├── [Permission Denied]")
            except Exception as e:
                tree_lines.append(f"{prefix}├── [Error: {str(e)}]")

        tree_lines.append(f"{repo_path.name}/")
        add_to_tree(repo_path)
        return "\n".join(tree_lines)

    async def pre_filter_files(self, repo_path: Path, file_tree: str) -> List[str]:
        """Use LLM to pre-filter relevant files based on target structure"""
        filter_prompt = f"""
        You are a code analysis expert. Please analyze the following code repository file tree based on the target project structure and filter out files that may be relevant to the target project.

        Target Project Structure:
        {self.target_structure}

        Code Repository File Tree:
        {file_tree}

        Please analyze which files might be helpful for implementing the target project structure, including:
        - Core algorithm implementation files (such as GCN, recommendation systems, graph neural networks, etc.)
        - Data processing and preprocessing files
        - Loss functions and evaluation metric files
        - Configuration and utility files
        - Test files
        - Documentation files

        Please return the filtering results in JSON format:
        {{
            "relevant_files": [
                {{
                    "file_path": "file path relative to repository root",
                    "relevance_reason": "why this file is relevant",
                    "confidence": 0.0-1.0,
                    "expected_contribution": "expected contribution to the target project"
                }}
            ],
            "summary": {{
                "total_files_analyzed": "total number of files analyzed",
                "relevant_files_count": "number of relevant files",
                "filtering_strategy": "explanation of filtering strategy"
            }}
        }}

        Only return files with confidence > {self.min_confidence_score}. Focus on files related to recommendation systems, graph neural networks, and diffusion models.
        """

        try:
            self.logger.info("Starting LLM pre-filtering of files...")
            llm_response = await self._call_llm(
                filter_prompt,
                system_prompt="You are a professional code analysis and project architecture expert, skilled at identifying code file functionality and relevance.",
                max_tokens=2000,
            )

            # Parse JSON response
            match = re.search(r"\{.*\}", llm_response, re.DOTALL)
            if not match:
                self.logger.warning(
                    "Unable to parse LLM filtering response, will use all files"
                )
                return []

            filter_data = json.loads(match.group(0))
            relevant_files = filter_data.get("relevant_files", [])

            # Extract file paths
            selected_files = []
            for file_info in relevant_files:
                file_path = file_info.get("file_path", "")
                confidence = file_info.get("confidence", 0.0)
                # Use configured minimum confidence threshold
                if file_path and confidence > self.min_confidence_score:
                    selected_files.append(file_path)

            summary = filter_data.get("summary", {})
            self.logger.info(
                f"LLM filtering completed: {summary.get('relevant_files_count', len(selected_files))} relevant files selected"
            )
            self.logger.info(
                f"Filtering strategy: {summary.get('filtering_strategy', 'Not provided')}"
            )

            return selected_files

        except Exception as e:
            self.logger.error(f"LLM pre-filtering failed: {e}")
            self.logger.info("Will fallback to analyzing all files")
            return []

    def filter_files_by_paths(
        self, all_files: List[Path], selected_paths: List[str], repo_path: Path
    ) -> List[Path]:
        """Filter file list based on LLM-selected paths"""
        if not selected_paths:
            return all_files

        filtered_files = []

        for file_path in all_files:
            # Get path relative to repository root
            relative_path = str(file_path.relative_to(repo_path))

            # Check if it's in the selected list
            for selected_path in selected_paths:
                # Normalize path comparison
                if (
                    relative_path == selected_path
                    or relative_path.replace("\\", "/")
                    == selected_path.replace("\\", "/")
                    or selected_path in relative_path
                    or relative_path in selected_path
                ):
                    filtered_files.append(file_path)
                    break

        return filtered_files

    def _get_cache_key(self, file_path: Path) -> str:
        """Generate cache key for file content"""
        try:
            stats = file_path.stat()
            return f"{file_path}:{stats.st_mtime}:{stats.st_size}"
        except (OSError, PermissionError):
            return str(file_path)

    def _manage_cache_size(self):
        """Manage cache size to stay within limits"""
        if not self.enable_content_caching or not self.content_cache:
            return

        if len(self.content_cache) > self.max_cache_size:
            # Remove oldest entries (simple FIFO strategy)
            excess_count = len(self.content_cache) - self.max_cache_size + 10
            keys_to_remove = list(self.content_cache.keys())[:excess_count]

            for key in keys_to_remove:
                del self.content_cache[key]

            if self.verbose_output:
                self.logger.info(
                    f"Cache cleaned: removed {excess_count} entries, {len(self.content_cache)} entries remaining"
                )

    async def analyze_file_content(self, file_path: Path) -> FileSummary:
        """Analyze a single file and create summary with caching support"""
        try:
            # Check file size before reading
            file_size = file_path.stat().st_size
            if file_size > self.max_file_size:
                self.logger.warning(
                    f"Skipping file {file_path} - size {file_size} bytes exceeds limit {self.max_file_size}"
                )
                return FileSummary(
                    file_path=str(file_path.relative_to(self.code_base_path)),
                    file_type="skipped - too large",
                    main_functions=[],
                    key_concepts=[],
                    dependencies=[],
                    summary=f"File skipped - size {file_size} bytes exceeds {self.max_file_size} byte limit",
                    lines_of_code=0,
                    last_modified=datetime.fromtimestamp(
                        file_path.stat().st_mtime
                    ).isoformat(),
                )

            # Check cache if enabled
            cache_key = None
            if self.enable_content_caching:
                cache_key = self._get_cache_key(file_path)
                if cache_key in self.content_cache:
                    if self.verbose_output:
                        self.logger.info(f"Using cached analysis for {file_path.name}")
                    return self.content_cache[cache_key]

            with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()

            # Get file stats
            stats = file_path.stat()
            lines_of_code = len([line for line in content.split("\n") if line.strip()])

            # Truncate content based on config
            content_for_analysis = content[: self.max_content_length]
            content_suffix = "..." if len(content) > self.max_content_length else ""

            # Create analysis prompt
            analysis_prompt = f"""
            Analyze this code file and provide a structured summary:

            File: {file_path.name}
            Content:
            ```
            {content_for_analysis}{content_suffix}
            ```

            Please provide analysis in this JSON format:
            {{
                "file_type": "description of what type of file this is",
                "main_functions": ["list", "of", "main", "functions", "or", "classes"],
                "key_concepts": ["important", "concepts", "algorithms", "patterns"],
                "dependencies": ["external", "libraries", "or", "imports"],
                "summary": "2-3 sentence summary of what this file does"
            }}

            Focus on the core functionality and potential reusability.
            """

            # Get LLM analysis with configured parameters
            llm_response = await self._call_llm(analysis_prompt, max_tokens=1000)

            try:
                # Try to parse JSON response
                match = re.search(r"\{.*\}", llm_response, re.DOTALL)
                analysis_data = json.loads(match.group(0))
            except json.JSONDecodeError:
                # Fallback to basic analysis if JSON parsing fails
                analysis_data = {
                    "file_type": f"{file_path.suffix} file",
                    "main_functions": [],
                    "key_concepts": [],
                    "dependencies": [],
                    "summary": "File analysis failed - JSON parsing error",
                }

            file_summary = FileSummary(
                file_path=str(file_path.relative_to(self.code_base_path)),
                file_type=analysis_data.get("file_type", "unknown"),
                main_functions=analysis_data.get("main_functions", []),
                key_concepts=analysis_data.get("key_concepts", []),
                dependencies=analysis_data.get("dependencies", []),
                summary=analysis_data.get("summary", "No summary available"),
                lines_of_code=lines_of_code,
                last_modified=datetime.fromtimestamp(stats.st_mtime).isoformat(),
            )

            # Cache the result if caching is enabled
            if self.enable_content_caching and cache_key:
                self.content_cache[cache_key] = file_summary
                self._manage_cache_size()

            return file_summary

        except Exception as e:
            self.logger.error(f"Error analyzing file {file_path}: {e}")
            return FileSummary(
                file_path=str(file_path.relative_to(self.code_base_path)),
                file_type="error",
                main_functions=[],
                key_concepts=[],
                dependencies=[],
                summary=f"Analysis failed: {str(e)}",
                lines_of_code=0,
                last_modified="",
            )

    async def find_relationships(
        self, file_summary: FileSummary
    ) -> List[FileRelationship]:
        """Find relationships between a repo file and target structure"""

        # Build relationship type description from config
        relationship_type_desc = []
        for rel_type, weight in self.relationship_types.items():
            relationship_type_desc.append(f"- {rel_type} (priority: {weight})")

        relationship_prompt = f"""
        Analyze the relationship between this existing code file and the target project structure.

        Existing File Analysis:
        - Path: {file_summary.file_path}
        - Type: {file_summary.file_type}
        - Functions: {', '.join(file_summary.main_functions)}
        - Concepts: {', '.join(file_summary.key_concepts)}
        - Summary: {file_summary.summary}

        Target Project Structure:
        {self.target_structure}

        Available relationship types (with priority weights):
        {chr(10).join(relationship_type_desc)}

        Identify potential relationships and provide analysis in this JSON format:
        {{
            "relationships": [
                {{
                    "target_file_path": "path/in/target/structure",
                    "relationship_type": "direct_match|partial_match|reference|utility",
                    "confidence_score": 0.0-1.0,
                    "helpful_aspects": ["specific", "aspects", "that", "could", "help"],
                    "potential_contributions": ["how", "this", "could", "contribute"],
                    "usage_suggestions": "detailed suggestion on how to use this file"
                }}
            ]
        }}

        Consider the priority weights when determining relationship types. Higher weight types should be preferred when multiple types apply.
        Only include relationships with confidence > {self.min_confidence_score}. Focus on concrete, actionable connections.
        """

        try:
            llm_response = await self._call_llm(relationship_prompt, max_tokens=1500)

            match = re.search(r"\{.*\}", llm_response, re.DOTALL)
            relationship_data = json.loads(match.group(0))

            relationships = []
            for rel_data in relationship_data.get("relationships", []):
                confidence_score = float(rel_data.get("confidence_score", 0.0))
                relationship_type = rel_data.get("relationship_type", "reference")

                # Validate relationship type is in config
                if relationship_type not in self.relationship_types:
                    if self.verbose_output:
                        self.logger.warning(
                            f"Unknown relationship type '{relationship_type}', using 'reference'"
                        )
                    relationship_type = "reference"

                # Apply configured minimum confidence filter
                if confidence_score > self.min_confidence_score:
                    relationship = FileRelationship(
                        repo_file_path=file_summary.file_path,
                        target_file_path=rel_data.get("target_file_path", ""),
                        relationship_type=relationship_type,
                        confidence_score=confidence_score,
                        helpful_aspects=rel_data.get("helpful_aspects", []),
                        potential_contributions=rel_data.get(
                            "potential_contributions", []
                        ),
                        usage_suggestions=rel_data.get("usage_suggestions", ""),
                    )
                    relationships.append(relationship)

            return relationships

        except Exception as e:
            self.logger.error(
                f"Error finding relationships for {file_summary.file_path}: {e}"
            )
            return []

    async def _analyze_single_file_with_relationships(
        self, file_path: Path, index: int, total: int
    ) -> tuple:
        """Analyze a single file and its relationships (for concurrent processing)"""
        if self.verbose_output:
            self.logger.info(f"Analyzing file {index}/{total}: {file_path.name}")

        # Get file summary
        file_summary = await self.analyze_file_content(file_path)

        # Find relationships
        relationships = await self.find_relationships(file_summary)

        return file_summary, relationships

    async def process_repository(self, repo_path: Path) -> RepoIndex:
        """Process a single repository and create complete index with optional concurrent processing"""
        repo_name = repo_path.name
        self.logger.info(f"Processing repository: {repo_name}")

        # Step 1: Generate file tree
        self.logger.info("Generating file tree structure...")
        file_tree = self.generate_file_tree(repo_path)

        # Step 2: Get all files
        all_files = self.get_all_repo_files(repo_path)
        self.logger.info(f"Found {len(all_files)} files in {repo_name}")

        # Step 3: LLM pre-filtering of relevant files
        if self.enable_pre_filtering:
            self.logger.info("Using LLM for file pre-filtering...")
            selected_file_paths = await self.pre_filter_files(repo_path, file_tree)
        else:
            self.logger.info("Pre-filtering is disabled, will analyze all files")
            selected_file_paths = []

        # Step 4: Filter file list based on filtering results
        if selected_file_paths:
            files_to_analyze = self.filter_files_by_paths(
                all_files, selected_file_paths, repo_path
            )
            self.logger.info(
                f"After LLM filtering, will analyze {len(files_to_analyze)} relevant files (from {len(all_files)} total)"
            )
        else:
            files_to_analyze = all_files
            self.logger.info("LLM filtering failed, will analyze all files")

        # Step 5: Analyze filtered files (concurrent or sequential)
        if self.enable_concurrent_analysis and len(files_to_analyze) > 1:
            self.logger.info(
                f"Using concurrent analysis with max {self.max_concurrent_files} parallel files"
            )
            file_summaries, all_relationships = await self._process_files_concurrently(
                files_to_analyze
            )
        else:
            self.logger.info("Using sequential file analysis")
            file_summaries, all_relationships = await self._process_files_sequentially(
                files_to_analyze
            )

        # Step 6: Create repository index
        repo_index = RepoIndex(
            repo_name=repo_name,
            total_files=len(all_files),  # Record original file count
            file_summaries=file_summaries,
            relationships=all_relationships,
            analysis_metadata={
                "analysis_date": datetime.now().isoformat(),
                "target_structure_analyzed": self.target_structure[:200] + "...",
                "total_relationships_found": len(all_relationships),
                "high_confidence_relationships": len(
                    [
                        r
                        for r in all_relationships
                        if r.confidence_score > self.high_confidence_threshold
                    ]
                ),
                "analyzer_version": "1.4.0",  # Updated version to reflect augmented LLM support
                "pre_filtering_enabled": self.enable_pre_filtering,
                "files_before_filtering": len(all_files),
                "files_after_filtering": len(files_to_analyze),
                "filtering_efficiency": round(
                    (1 - len(files_to_analyze) / len(all_files)) * 100, 2
                )
                if all_files
                else 0,
                "config_file_used": self.indexer_config_path,
                "min_confidence_score": self.min_confidence_score,
                "high_confidence_threshold": self.high_confidence_threshold,
                "concurrent_analysis_used": self.enable_concurrent_analysis,
                "content_caching_enabled": self.enable_content_caching,
                "cache_hits": len(self.content_cache) if self.content_cache else 0,
            },
        )

        return repo_index

    async def _process_files_sequentially(self, files_to_analyze: list) -> tuple:
        """Process files sequentially (original method)"""
        file_summaries = []
        all_relationships = []

        for i, file_path in enumerate(files_to_analyze, 1):
            (
                file_summary,
                relationships,
            ) = await self._analyze_single_file_with_relationships(
                file_path, i, len(files_to_analyze)
            )
            file_summaries.append(file_summary)
            all_relationships.extend(relationships)

            # Add configured delay to avoid overwhelming the LLM API
            await asyncio.sleep(self.request_delay)

        return file_summaries, all_relationships

    async def _process_files_concurrently(self, files_to_analyze: list) -> tuple:
        """Process files concurrently with semaphore limiting"""
        file_summaries = []
        all_relationships = []

        # Create semaphore to limit concurrent tasks
        semaphore = asyncio.Semaphore(self.max_concurrent_files)
        tasks = []

        async def _process_with_semaphore(file_path: Path, index: int, total: int):
            async with semaphore:
                # Add a small delay to space out concurrent requests
                if index > 1:
                    await asyncio.sleep(
                        self.request_delay * 0.5
                    )  # Reduced delay for concurrent processing
                return await self._analyze_single_file_with_relationships(
                    file_path, index, total
                )

        try:
            # Create tasks for all files
            tasks = [
                _process_with_semaphore(file_path, i, len(files_to_analyze))
                for i, file_path in enumerate(files_to_analyze, 1)
            ]

            # Process tasks and collect results
            if self.verbose_output:
                self.logger.info(
                    f"Starting concurrent analysis of {len(tasks)} files..."
                )

            try:
                results = await asyncio.gather(*tasks, return_exceptions=True)

                for i, result in enumerate(results):
                    if isinstance(result, Exception):
                        self.logger.error(
                            f"Failed to analyze file {files_to_analyze[i]}: {result}"
                        )
                        # Create error summary
                        error_summary = FileSummary(
                            file_path=str(
                                files_to_analyze[i].relative_to(self.code_base_path)
                            ),
                            file_type="error",
                            main_functions=[],
                            key_concepts=[],
                            dependencies=[],
                            summary=f"Concurrent analysis failed: {str(result)}",
                            lines_of_code=0,
                            last_modified="",
                        )
                        file_summaries.append(error_summary)
                    else:
                        file_summary, relationships = result
                        file_summaries.append(file_summary)
                        all_relationships.extend(relationships)

            except Exception as e:
                self.logger.error(f"Concurrent processing failed: {e}")
                # Cancel any remaining tasks
                for task in tasks:
                    if not task.done() and not task.cancelled():
                        task.cancel()

                # Wait for cancelled tasks to complete
                try:
                    await asyncio.sleep(0.1)  # Brief wait for cancellation
                except Exception:
                    pass

                # Fallback to sequential processing
                self.logger.info("Falling back to sequential processing...")
                return await self._process_files_sequentially(files_to_analyze)

            if self.verbose_output:
                self.logger.info(
                    f"Concurrent analysis completed: {len(file_summaries)} files processed"
                )

            return file_summaries, all_relationships

        except Exception as e:
            # Ensure all tasks are cancelled in case of unexpected errors
            if tasks:
                for task in tasks:
                    if not task.done() and not task.cancelled():
                        task.cancel()

            # Wait briefly for cancellation to complete
            try:
                await asyncio.sleep(0.1)
            except Exception:
                pass

            self.logger.error(f"Critical error in concurrent processing: {e}")
            # Fallback to sequential processing
            self.logger.info(
                "Falling back to sequential processing due to critical error..."
            )
            return await self._process_files_sequentially(files_to_analyze)

        finally:
            # Final cleanup: ensure all tasks are properly finished
            if tasks:
                for task in tasks:
                    if not task.done() and not task.cancelled():
                        task.cancel()

            # Clear task references to help with garbage collection
            tasks.clear()

            # Force garbage collection to help clean up semaphore and related resources
            import gc

            gc.collect()

    async def build_all_indexes(self) -> Dict[str, str]:
        """Build indexes for all repositories in code_base"""
        if not self.code_base_path.exists():
            raise FileNotFoundError(
                f"Code base path does not exist: {self.code_base_path}"
            )

        # Get all repository directories
        repo_dirs = [
            d
            for d in self.code_base_path.iterdir()
            if d.is_dir() and not d.name.startswith(".")
        ]

        if not repo_dirs:
            raise ValueError(f"No repositories found in {self.code_base_path}")

        self.logger.info(f"Found {len(repo_dirs)} repositories to process")

        # Process each repository
        output_files = {}
        statistics_data = []

        for repo_dir in repo_dirs:
            try:
                # Process repository
                repo_index = await self.process_repository(repo_dir)

                # Generate output filename using configured pattern
                output_filename = self.index_filename_pattern.format(
                    repo_name=repo_index.repo_name
                )
                output_file = self.output_dir / output_filename

                # Get output configuration
                output_config = self.indexer_config.get("output", {})
                json_indent = output_config.get("json_indent", 2)
                ensure_ascii = not output_config.get("ensure_ascii", False)

                # Save to JSON file
                with open(output_file, "w", encoding="utf-8") as f:
                    if self.include_metadata:
                        json.dump(
                            asdict(repo_index),
                            f,
                            indent=json_indent,
                            ensure_ascii=ensure_ascii,
                        )
                    else:
                        # Save without metadata if disabled
                        index_data = asdict(repo_index)
                        index_data.pop("analysis_metadata", None)
                        json.dump(
                            index_data, f, indent=json_indent, ensure_ascii=ensure_ascii
                        )

                output_files[repo_index.repo_name] = str(output_file)
                self.logger.info(
                    f"Saved index for {repo_index.repo_name} to {output_file}"
                )

                # Collect statistics for report
                if self.generate_statistics:
                    stats = self._extract_repository_statistics(repo_index)
                    statistics_data.append(stats)

            except Exception as e:
                self.logger.error(f"Failed to process repository {repo_dir.name}: {e}")
                continue

        # Generate additional reports if configured
        if self.generate_summary:
            summary_path = self.generate_summary_report(output_files)
            self.logger.info(f"Generated summary report: {summary_path}")

        if self.generate_statistics:
            stats_path = self.generate_statistics_report(statistics_data)
            self.logger.info(f"Generated statistics report: {stats_path}")

        return output_files

    def _extract_repository_statistics(self, repo_index: RepoIndex) -> Dict[str, Any]:
        """Extract statistical information from a repository index"""
        metadata = repo_index.analysis_metadata

        # Count relationship types
        relationship_type_counts = {}
        for rel in repo_index.relationships:
            rel_type = rel.relationship_type
            relationship_type_counts[rel_type] = (
                relationship_type_counts.get(rel_type, 0) + 1
            )

        # Count file types
        file_type_counts = {}
        for file_summary in repo_index.file_summaries:
            file_type = file_summary.file_type
            file_type_counts[file_type] = file_type_counts.get(file_type, 0) + 1

        # Calculate statistics
        total_lines = sum(fs.lines_of_code for fs in repo_index.file_summaries)
        avg_lines = (
            total_lines / len(repo_index.file_summaries)
            if repo_index.file_summaries
            else 0
        )

        avg_confidence = (
            sum(r.confidence_score for r in repo_index.relationships)
            / len(repo_index.relationships)
            if repo_index.relationships
            else 0
        )

        return {
            "repo_name": repo_index.repo_name,
            "total_files": repo_index.total_files,
            "analyzed_files": len(repo_index.file_summaries),
            "total_relationships": len(repo_index.relationships),
            "high_confidence_relationships": metadata.get(
                "high_confidence_relationships", 0
            ),
            "relationship_type_counts": relationship_type_counts,
            "file_type_counts": file_type_counts,
            "total_lines_of_code": total_lines,
            "average_lines_per_file": round(avg_lines, 2),
            "average_confidence_score": round(avg_confidence, 3),
            "filtering_efficiency": metadata.get("filtering_efficiency", 0),
            "concurrent_analysis_used": metadata.get("concurrent_analysis_used", False),
            "cache_hits": metadata.get("cache_hits", 0),
            "analysis_date": metadata.get("analysis_date", "unknown"),
        }

    def generate_statistics_report(self, statistics_data: List[Dict[str, Any]]) -> str:
        """Generate a detailed statistics report"""
        stats_path = self.output_dir / self.stats_filename

        # Calculate aggregate statistics
        total_repos = len(statistics_data)
        total_files_analyzed = sum(stat["analyzed_files"] for stat in statistics_data)
        total_relationships = sum(
            stat["total_relationships"] for stat in statistics_data
        )
        total_lines = sum(stat["total_lines_of_code"] for stat in statistics_data)

        # Aggregate relationship types
        aggregated_rel_types = {}
        for stat in statistics_data:
            for rel_type, count in stat["relationship_type_counts"].items():
                aggregated_rel_types[rel_type] = (
                    aggregated_rel_types.get(rel_type, 0) + count
                )

        # Aggregate file types
        aggregated_file_types = {}
        for stat in statistics_data:
            for file_type, count in stat["file_type_counts"].items():
                aggregated_file_types[file_type] = (
                    aggregated_file_types.get(file_type, 0) + count
                )

        # Calculate averages
        avg_files_per_repo = total_files_analyzed / total_repos if total_repos else 0
        avg_relationships_per_repo = (
            total_relationships / total_repos if total_repos else 0
        )
        avg_lines_per_repo = total_lines / total_repos if total_repos else 0

        # Build statistics report
        statistics_report = {
            "report_generation_time": datetime.now().isoformat(),
            "analyzer_version": "1.4.0",
            "configuration_used": {
                "config_file": self.indexer_config_path,
                "concurrent_analysis_enabled": self.enable_concurrent_analysis,
                "content_caching_enabled": self.enable_content_caching,
                "pre_filtering_enabled": self.enable_pre_filtering,
                "min_confidence_score": self.min_confidence_score,
                "high_confidence_threshold": self.high_confidence_threshold,
            },
            "aggregate_statistics": {
                "total_repositories_processed": total_repos,
                "total_files_analyzed": total_files_analyzed,
                "total_relationships_found": total_relationships,
                "total_lines_of_code": total_lines,
                "average_files_per_repository": round(avg_files_per_repo, 2),
                "average_relationships_per_repository": round(
                    avg_relationships_per_repo, 2
                ),
                "average_lines_per_repository": round(avg_lines_per_repo, 2),
            },
            "relationship_type_distribution": aggregated_rel_types,
            "file_type_distribution": aggregated_file_types,
            "repository_details": statistics_data,
            "performance_metrics": {
                "concurrent_processing_repos": sum(
                    1
                    for s in statistics_data
                    if s.get("concurrent_analysis_used", False)
                ),
                "cache_efficiency": {
                    "total_cache_hits": sum(
                        s.get("cache_hits", 0) for s in statistics_data
                    ),
                    "repositories_with_caching": sum(
                        1 for s in statistics_data if s.get("cache_hits", 0) > 0
                    ),
                },
                "filtering_efficiency": {
                    "average_filtering_efficiency": round(
                        sum(s.get("filtering_efficiency", 0) for s in statistics_data)
                        / total_repos,
                        2,
                    )
                    if total_repos
                    else 0,
                    "max_filtering_efficiency": max(
                        (s.get("filtering_efficiency", 0) for s in statistics_data),
                        default=0,
                    ),
                    "min_filtering_efficiency": min(
                        (s.get("filtering_efficiency", 0) for s in statistics_data),
                        default=0,
                    ),
                },
            },
        }

        # Get output configuration
        output_config = self.indexer_config.get("output", {})
        json_indent = output_config.get("json_indent", 2)
        ensure_ascii = not output_config.get("ensure_ascii", False)

        with open(stats_path, "w", encoding="utf-8") as f:
            json.dump(
                statistics_report, f, indent=json_indent, ensure_ascii=ensure_ascii
            )

        return str(stats_path)

    def generate_summary_report(self, output_files: Dict[str, str]) -> str:
        """Generate a summary report of all indexes created"""
        report_path = self.output_dir / "indexing_summary.json"

        # Get output configuration from config file
        output_config = self.indexer_config.get("output", {})
        json_indent = output_config.get("json_indent", 2)
        ensure_ascii = not output_config.get("ensure_ascii", False)

        summary_data = {
            "indexing_completion_time": datetime.now().isoformat(),
            "total_repositories_processed": len(output_files),
            "output_files": output_files,
            "target_structure": self.target_structure,
            "code_base_path": str(self.code_base_path),
            "configuration": {
                "config_file_used": self.indexer_config_path,
                "api_config_file": self.config_path,
                "pre_filtering_enabled": self.enable_pre_filtering,
                "min_confidence_score": self.min_confidence_score,
                "high_confidence_threshold": self.high_confidence_threshold,
                "max_file_size": self.max_file_size,
                "max_content_length": self.max_content_length,
                "request_delay": self.request_delay,
                "supported_extensions_count": len(self.supported_extensions),
                "skip_directories_count": len(self.skip_directories),
            },
        }

        with open(report_path, "w", encoding="utf-8") as f:
            json.dump(summary_data, f, indent=json_indent, ensure_ascii=ensure_ascii)

        return str(report_path)


async def main():
    """Main function to run the code indexer with full configuration support"""

    # Configuration - can be overridden by config file
    config_file = "DeepCode/tools/indexer_config.yaml"
    api_config_file = "DeepCode/mcp_agent.secrets.yaml"

    # You can override these parameters or let them be read from config
    code_base_path = "DeepCode/deepcode_lab/papers/1/code_base/"  # Will use config file value if None
    output_dir = (
        "DeepCode/deepcode_lab/papers/1/indexes/"  # Will use config file value if None
    )

    # Target structure - this should be customized for your specific project
    target_structure = """
    project/
    ├── src/
    │   ├── core/
    │   │   ├── gcn.py        # GCN encoder
    │   │   ├── diffusion.py  # forward/reverse processes
    │   │   ├── denoiser.py   # denoising MLP
    │   │   └── fusion.py     # fusion combiner
    │   ├── models/           # model wrapper classes
    │   │   └── recdiff.py
    │   ├── utils/
    │   │   ├── data.py       # loading & preprocessing
    │   │   ├── predictor.py  # scoring functions
    │   │   ├── loss.py       # loss functions
    │   │   ├── metrics.py    # NDCG, Recall etc.
    │   │   └── sched.py      # beta/alpha schedule utils
    │   └── configs/
    │       └── default.yaml  # hyperparameters, paths
    ├── tests/
    │   ├── test_gcn.py
    │   ├── test_diffusion.py
    │   ├── test_denoiser.py
    │   ├── test_loss.py
    │   └── test_pipeline.py
    ├── docs/
    │   ├── architecture.md
    │   ├── api_reference.md
    │   └── README.md
    ├── experiments/
    │   ├── run_experiment.py
    │   └── notebooks/
    │       └── analysis.ipynb
    ├── requirements.txt
    └── setup.py
    """

    print("🚀 Starting Code Indexer with Enhanced Configuration Support")
    print(f"📋 Configuration file: {config_file}")
    print(f"🔑 API configuration file: {api_config_file}")

    # Create indexer with full configuration support
    try:
        indexer = CodeIndexer(
            code_base_path=code_base_path,  # None = read from config
            target_structure=target_structure,  # Required - project specific
            output_dir=output_dir,  # None = read from config
            config_path=api_config_file,  # API configuration file
            indexer_config_path=config_file,  # Configuration file
            enable_pre_filtering=True,  # Can be overridden in config
        )

        # Display configuration information
        print(f"📁 Code base path: {indexer.code_base_path}")
        print(f"📂 Output directory: {indexer.output_dir}")
        print(
            f"🤖 Default models: Anthropic={indexer.default_models['anthropic']}, OpenAI={indexer.default_models['openai']}"
        )
        print(f"🔧 Preferred LLM: {get_preferred_llm_class(api_config_file).__name__}")
        print(
            f"⚡ Concurrent analysis: {'enabled' if indexer.enable_concurrent_analysis else 'disabled'}"
        )
        print(
            f"🗄️  Content caching: {'enabled' if indexer.enable_content_caching else 'disabled'}"
        )
        print(
            f"🔍 Pre-filtering: {'enabled' if indexer.enable_pre_filtering else 'disabled'}"
        )
        print(f"🐛 Debug mode: {'enabled' if indexer.verbose_output else 'disabled'}")
        print(
            f"🎭 Mock responses: {'enabled' if indexer.mock_llm_responses else 'disabled'}"
        )

        # Validate configuration
        if not indexer.code_base_path.exists():
            raise FileNotFoundError(
                f"Code base path does not exist: {indexer.code_base_path}"
            )

        if not target_structure:
            raise ValueError("Target structure is required for analysis")

        print("\n🔧 Starting indexing process...")

        # Build all indexes
        output_files = await indexer.build_all_indexes()

        # Display results
        print("\n✅ Indexing completed successfully!")
        print(f"📊 Processed {len(output_files)} repositories")
        print("📁 Output files:")
        for repo_name, file_path in output_files.items():
            print(f"   - {repo_name}: {file_path}")

        # Display additional reports generated
        if indexer.generate_summary:
            summary_file = indexer.output_dir / indexer.summary_filename
            if summary_file.exists():
                print(f"📋 Summary report: {summary_file}")

        if indexer.generate_statistics:
            stats_file = indexer.output_dir / indexer.stats_filename
            if stats_file.exists():
                print(f"📈 Statistics report: {stats_file}")

        # Performance information
        if indexer.enable_content_caching and indexer.content_cache:
            print(f"🗄️  Cache performance: {len(indexer.content_cache)} items cached")

        print("\n🎉 Code indexing process completed successfully!")

    except FileNotFoundError as e:
        print(f"❌ File not found error: {e}")
        print("💡 Please check your configuration file paths")
    except ValueError as e:
        print(f"❌ Configuration error: {e}")
        print("💡 Please check your configuration file settings")
    except Exception as e:
        print(f"❌ Indexing failed: {e}")
        print("💡 Check the logs for more details")

        # Print debug information if available
        try:
            indexer
            if indexer.verbose_output:
                import traceback

                print("\n🐛 Debug information:")
                traceback.print_exc()
        except NameError:
            pass


def print_usage_example():
    """Print usage examples for different scenarios"""
    print("""
    📖 Code Indexer Usage Examples:

    1. Basic usage with config file:
       - Update paths in indexer_config.yaml
       - Run: python code_indexer.py

    2. Enable debugging:
       - Set debug.verbose_output: true in config
       - Set debug.save_raw_responses: true to save LLM responses

    3. Enable concurrent processing:
       - Set performance.enable_concurrent_analysis: true
       - Adjust performance.max_concurrent_files as needed

    4. Enable caching:
       - Set performance.enable_content_caching: true
       - Adjust performance.max_cache_size as needed

    5. Mock mode for testing:
       - Set debug.mock_llm_responses: true
       - No API calls will be made

    6. Custom output:
       - Modify output.index_filename_pattern
       - Set output.generate_statistics: true for detailed reports

    📋 Configuration file location: tools/indexer_config.yaml
    """)


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1 and sys.argv[1] in ["--help", "-h", "help"]:
        print_usage_example()
    else:
        asyncio.run(main())
