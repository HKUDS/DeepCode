"""
Codebase Index Workflow

This workflow integrates the functionality of run_indexer.py and code_indexer.py
to build intelligent relationships between existing codebase and target structure.

Features:
- Extract target file structure from initial_plan.txt
- Analyze codebase and build indexes
- Generate relationship mappings and statistical reports
- Provide reference basis for code reproduction
"""

import asyncio
import json
import logging
import os
import re
import sys
from pathlib import Path
from typing import Dict, Any, Optional
import yaml

# Add tools directory to path
sys.path.append(str(Path(__file__).parent.parent / "tools"))

from tools.code_indexer import CodeIndexer


class CodebaseIndexWorkflow:
    """Codebase Index Workflow Class"""

    def __init__(self, logger=None):
        """
        Initialize workflow

        Args:
            logger: Logger instance
        """
        self.logger = logger or self._setup_default_logger()
        self.indexer = None

    def _setup_default_logger(self) -> logging.Logger:
        """Setup default logger"""
        logger = logging.getLogger("CodebaseIndexWorkflow")
        logger.setLevel(logging.INFO)

        if not logger.handlers:
            handler = logging.StreamHandler()
            formatter = logging.Formatter(
                "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
            )
            handler.setFormatter(formatter)
            logger.addHandler(handler)

        return logger

    def extract_file_tree_from_plan(self, plan_content: str) -> Optional[str]:
        """
        Extract file tree structure from initial_plan.txt content

        Args:
            plan_content: Content of the initial_plan.txt file

        Returns:
            Extracted file tree structure as string
        """
        # Look for file structure section, specifically "## File Structure" format
        file_structure_pattern = r"## File Structure[^\n]*\n```[^\n]*\n(.*?)\n```"

        match = re.search(file_structure_pattern, plan_content, re.DOTALL)
        if match:
            file_tree = match.group(1).strip()
            lines = file_tree.split("\n")

            # Clean tree structure - remove empty lines and comments not part of structure
            cleaned_lines = []
            for line in lines:
                # Keep tree structure lines
                if line.strip() and (
                    any(char in line for char in ["├──", "└──", "│"])
                    or line.strip().endswith("/")
                    or "." in line.split("/")[-1]  # has file extension
                    or line.strip().endswith(".py")
                    or line.strip().endswith(".txt")
                    or line.strip().endswith(".md")
                    or line.strip().endswith(".yaml")
                ):
                    cleaned_lines.append(line)

            if len(cleaned_lines) >= 5:
                file_tree = "\n".join(cleaned_lines)
                self.logger.info(
                    f"📊 Extracted file tree structure from ## File Structure section ({len(cleaned_lines)} lines)"
                )
                return file_tree

        # Fallback: look for any code block containing project structure
        code_block_patterns = [
            r"```[^\n]*\n(project/.*?(?:├──|└──).*?)\n```",
            r"```[^\n]*\n(src/.*?(?:├──|└──).*?)\n```",
            r"```[^\n]*\n(core/.*?(?:├──|└──).*?)\n```",
            r"```[^\n]*\n(.*?(?:├──|└──).*?(?:\.py|\.txt|\.md|\.yaml).*?)\n```",
        ]

        for pattern in code_block_patterns:
            match = re.search(pattern, plan_content, re.DOTALL)
            if match:
                file_tree = match.group(1).strip()
                lines = [line for line in file_tree.split("\n") if line.strip()]
                if len(lines) >= 5:
                    self.logger.info(
                        f"📊 Extracted file tree structure from code block ({len(lines)} lines)"
                    )
                    return file_tree

        # Final fallback: extract file paths from file mentions and create basic structure
        self.logger.warning(
            "⚠️ No standard file tree found, trying to extract from file mentions..."
        )

        # Search for file paths in backticks throughout the document
        file_mentions = re.findall(
            r"`([^`]*(?:\.py|\.txt|\.md|\.yaml|\.yml)[^`]*)`", plan_content
        )

        if file_mentions:
            # Organize files into directory structure
            dirs = set()
            files_by_dir = {}

            for file_path in file_mentions:
                file_path = file_path.strip()
                if "/" in file_path:
                    dir_path = "/".join(file_path.split("/")[:-1])
                    filename = file_path.split("/")[-1]
                    dirs.add(dir_path)
                    if dir_path not in files_by_dir:
                        files_by_dir[dir_path] = []
                    files_by_dir[dir_path].append(filename)
                else:
                    if "root" not in files_by_dir:
                        files_by_dir["root"] = []
                    files_by_dir["root"].append(file_path)

            # Create tree structure
            structure_lines = []

            # Determine root directory name from common patterns
            if any("src/" in f for f in file_mentions):
                root_name = "src"
            elif any("core/" in f for f in file_mentions):
                root_name = "core"
            elif any("lib/" in f for f in file_mentions):
                root_name = "lib"
            else:
                root_name = "project"
            structure_lines.append(f"{root_name}/")

            # Add directories and files
            sorted_dirs = sorted(dirs) if dirs else []
            for i, dir_path in enumerate(sorted_dirs):
                is_last_dir = i == len(sorted_dirs) - 1
                prefix = "└──" if is_last_dir else "├──"
                structure_lines.append(f"{prefix} {dir_path}/")

                if dir_path in files_by_dir:
                    files = sorted(files_by_dir[dir_path])
                    for j, filename in enumerate(files):
                        is_last_file = j == len(files) - 1
                        if is_last_dir:
                            file_prefix = "    └──" if is_last_file else "    ├──"
                        else:
                            file_prefix = "│   └──" if is_last_file else "│   ├──"
                        structure_lines.append(f"{file_prefix} {filename}")

            # Add root files (if any)
            if "root" in files_by_dir:
                root_files = sorted(files_by_dir["root"])
                for i, filename in enumerate(root_files):
                    is_last = (i == len(root_files) - 1) and not sorted_dirs
                    prefix = "└──" if is_last else "├──"
                    structure_lines.append(f"{prefix} {filename}")

            if len(structure_lines) >= 3:
                file_tree = "\n".join(structure_lines)
                self.logger.info(
                    f"📊 Generated file tree from file mentions ({len(structure_lines)} lines)"
                )
                return file_tree

        # If no file tree found, return None
        self.logger.warning("⚠️ No file tree structure found in initial plan")
        return None

    def load_target_structure_from_plan(self, plan_path: str) -> str:
        """
        Load target structure from initial_plan.txt and extract file tree

        Args:
            plan_path: Path to initial_plan.txt file

        Returns:
            Extracted file tree structure
        """
        try:
            # Load complete plan content
            with open(plan_path, "r", encoding="utf-8") as f:
                plan_content = f.read()

            self.logger.info(f"📄 Loaded initial plan ({len(plan_content)} characters)")

            # Extract file tree structure
            file_tree = self.extract_file_tree_from_plan(plan_content)

            if file_tree:
                self.logger.info(
                    "✅ Successfully extracted file tree from initial plan"
                )
                self.logger.info("📋 Extracted structure preview:")
                # Show first few lines of extracted tree
                preview_lines = file_tree.split("\n")[:8]
                for line in preview_lines:
                    self.logger.info(f"   {line}")
                if len(file_tree.split("\n")) > 8:
                    self.logger.info(
                        f"   ... {len(file_tree.split('\n')) - 8} more lines"
                    )
                return file_tree
            else:
                self.logger.warning("⚠️ Unable to extract file tree from initial plan")
                self.logger.info("🔄 Falling back to default target structure")
                return self.get_default_target_structure()

        except Exception as e:
            self.logger.error(f"❌ Failed to load initial plan file {plan_path}: {e}")
            self.logger.info("🔄 Falling back to default target structure")
            return self.get_default_target_structure()

    def get_default_target_structure(self) -> str:
        """Get default target structure"""
        return """
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

    def load_or_create_indexer_config(self, paper_dir: str) -> Dict[str, Any]:
        """
        Load or create indexer configuration

        Args:
            paper_dir: Paper directory path

        Returns:
            Configuration dictionary
        """
        # Try to load existing configuration file
        config_path = Path(__file__).parent.parent / "tools" / "indexer_config.yaml"

        try:
            if config_path.exists():
                with open(config_path, "r", encoding="utf-8") as f:
                    config = yaml.safe_load(f)

                # Update path configuration to current paper directory
                if "paths" not in config:
                    config["paths"] = {}
                config["paths"]["code_base_path"] = os.path.join(paper_dir, "code_base")
                config["paths"]["output_dir"] = os.path.join(paper_dir, "indexes")

                # Adjust performance settings for workflow
                if "performance" in config:
                    config["performance"]["enable_concurrent_analysis"] = (
                        False  # Disable concurrency to avoid API limits
                    )
                if "debug" in config:
                    config["debug"]["verbose_output"] = True  # Enable verbose output
                if "llm" in config:
                    config["llm"]["request_delay"] = 0.5  # Increase request delay

                self.logger.info(f"Loaded configuration file: {config_path}")
                return config

        except Exception as e:
            self.logger.warning(f"Failed to load configuration file: {e}")

        # If loading fails, use default configuration
        self.logger.info("Using default configuration")
        default_config = {
            "paths": {
                "code_base_path": os.path.join(paper_dir, "code_base"),
                "output_dir": os.path.join(paper_dir, "indexes"),
            },
            "llm": {
                "model_provider": "anthropic",
                "max_tokens": 4000,
                "temperature": 0.3,
                "request_delay": 0.5,  # Increase request delay
                "max_retries": 3,
                "retry_delay": 1.0,
            },
            "file_analysis": {
                "max_file_size": 1048576,  # 1MB
                "max_content_length": 3000,
                "supported_extensions": [
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
                    ".yaml",
                    ".yml",
                    ".json",
                    ".xml",
                    ".toml",
                    ".md",
                    ".txt",
                ],
                "skip_directories": [
                    "__pycache__",
                    "node_modules",
                    "target",
                    "build",
                    "dist",
                    "venv",
                    "env",
                    ".git",
                    ".svn",
                    "data",
                    "datasets",
                ],
            },
            "relationships": {
                "min_confidence_score": 0.3,
                "high_confidence_threshold": 0.7,
                "relationship_types": {
                    "direct_match": 1.0,
                    "partial_match": 0.8,
                    "reference": 0.6,
                    "utility": 0.4,
                },
            },
            "performance": {
                "enable_concurrent_analysis": False,  # Disable concurrency to avoid API limits
                "max_concurrent_files": 3,
                "enable_content_caching": True,
                "max_cache_size": 100,
            },
            "debug": {
                "verbose_output": True,
                "save_raw_responses": False,
                "mock_llm_responses": False,
            },
            "output": {
                "generate_summary": True,
                "generate_statistics": True,
                "include_metadata": True,
                "json_indent": 2,
            },
            "logging": {"level": "INFO", "log_to_file": False},
        }

        return default_config

    async def run_indexing_workflow(
        self,
        paper_dir: str,
        initial_plan_path: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Run the complete code indexing workflow

        Args:
            paper_dir: Paper directory path
            initial_plan_path: Initial plan file path (optional)

        Returns:
            Index result dictionary
        """
        try:
            self.logger.info("🚀 Starting codebase index workflow...")

            # Step 1: Determine initial plan file path
            if not initial_plan_path:
                initial_plan_path = os.path.join(paper_dir, "initial_plan.txt")

            # Step 2: Load target structure
            if os.path.exists(initial_plan_path):
                self.logger.info(
                    f"📐 Loading target structure from {initial_plan_path}"
                )
                target_structure = self.load_target_structure_from_plan(
                    initial_plan_path
                )
            else:
                self.logger.warning(
                    f"⚠️ Initial plan file does not exist: {initial_plan_path}"
                )
                self.logger.info("📐 Using default target structure")
                target_structure = self.get_default_target_structure()

            # Step 3: Check codebase path
            code_base_path = os.path.join(paper_dir, "code_base")
            if not os.path.exists(code_base_path):
                self.logger.error(f"❌ Codebase path does not exist: {code_base_path}")
                return {
                    "status": "error",
                    "message": f"Code base path does not exist: {code_base_path}",
                    "output_files": {},
                }

            # Step 4: Create output directory
            output_dir = os.path.join(paper_dir, "indexes")
            os.makedirs(output_dir, exist_ok=True)

            # Step 5: Load configuration
            indexer_config = self.load_or_create_indexer_config(paper_dir)

            self.logger.info(f"📁 Codebase path: {code_base_path}")
            self.logger.info(f"📤 Output directory: {output_dir}")

            # Step 6: Create code indexer
            self.indexer = CodeIndexer(
                code_base_path=code_base_path,
                target_structure=target_structure,
                output_dir=output_dir,
                enable_pre_filtering=True,
            )

            # Apply configuration settings
            self.indexer.indexer_config = indexer_config

            # Directly set configuration attributes to indexer
            if "file_analysis" in indexer_config:
                file_config = indexer_config["file_analysis"]
                self.indexer.supported_extensions = set(
                    file_config.get(
                        "supported_extensions", self.indexer.supported_extensions
                    )
                )
                self.indexer.skip_directories = set(
                    file_config.get("skip_directories", self.indexer.skip_directories)
                )
                self.indexer.max_file_size = file_config.get(
                    "max_file_size", self.indexer.max_file_size
                )
                self.indexer.max_content_length = file_config.get(
                    "max_content_length", self.indexer.max_content_length
                )

            if "llm" in indexer_config:
                llm_config = indexer_config["llm"]
                self.indexer.model_provider = llm_config.get(
                    "model_provider", self.indexer.model_provider
                )
                self.indexer.llm_max_tokens = llm_config.get(
                    "max_tokens", self.indexer.llm_max_tokens
                )
                self.indexer.llm_temperature = llm_config.get(
                    "temperature", self.indexer.llm_temperature
                )
                self.indexer.request_delay = llm_config.get(
                    "request_delay", self.indexer.request_delay
                )
                self.indexer.max_retries = llm_config.get(
                    "max_retries", self.indexer.max_retries
                )
                self.indexer.retry_delay = llm_config.get(
                    "retry_delay", self.indexer.retry_delay
                )

            if "relationships" in indexer_config:
                rel_config = indexer_config["relationships"]
                self.indexer.min_confidence_score = rel_config.get(
                    "min_confidence_score", self.indexer.min_confidence_score
                )
                self.indexer.high_confidence_threshold = rel_config.get(
                    "high_confidence_threshold", self.indexer.high_confidence_threshold
                )
                self.indexer.relationship_types = rel_config.get(
                    "relationship_types", self.indexer.relationship_types
                )

            if "performance" in indexer_config:
                perf_config = indexer_config["performance"]
                self.indexer.enable_concurrent_analysis = perf_config.get(
                    "enable_concurrent_analysis",
                    self.indexer.enable_concurrent_analysis,
                )
                self.indexer.max_concurrent_files = perf_config.get(
                    "max_concurrent_files", self.indexer.max_concurrent_files
                )
                self.indexer.enable_content_caching = perf_config.get(
                    "enable_content_caching", self.indexer.enable_content_caching
                )
                self.indexer.max_cache_size = perf_config.get(
                    "max_cache_size", self.indexer.max_cache_size
                )

            if "debug" in indexer_config:
                debug_config = indexer_config["debug"]
                self.indexer.verbose_output = debug_config.get(
                    "verbose_output", self.indexer.verbose_output
                )
                self.indexer.save_raw_responses = debug_config.get(
                    "save_raw_responses", self.indexer.save_raw_responses
                )
                self.indexer.mock_llm_responses = debug_config.get(
                    "mock_llm_responses", self.indexer.mock_llm_responses
                )

            if "output" in indexer_config:
                output_config = indexer_config["output"]
                self.indexer.generate_summary = output_config.get(
                    "generate_summary", self.indexer.generate_summary
                )
                self.indexer.generate_statistics = output_config.get(
                    "generate_statistics", self.indexer.generate_statistics
                )
                self.indexer.include_metadata = output_config.get(
                    "include_metadata", self.indexer.include_metadata
                )

            self.logger.info("🔧 Indexer configuration completed")
            self.logger.info(f"🤖 Model provider: {self.indexer.model_provider}")
            self.logger.info(
                f"⚡ Concurrent analysis: {'Enabled' if self.indexer.enable_concurrent_analysis else 'Disabled'}"
            )
            self.logger.info(
                f"🗄️ Content caching: {'Enabled' if self.indexer.enable_content_caching else 'Disabled'}"
            )
            self.logger.info(
                f"🔍 Pre-filtering: {'Enabled' if self.indexer.enable_pre_filtering else 'Disabled'}"
            )

            self.logger.info("=" * 60)
            self.logger.info("🚀 Starting code indexing process...")

            # Step 7: Build all indexes
            output_files = await self.indexer.build_all_indexes()

            # Step 8: Generate summary report
            if output_files:
                summary_report = self.indexer.generate_summary_report(output_files)

                self.logger.info("=" * 60)
                self.logger.info("✅ Indexing completed successfully!")
                self.logger.info(f"📊 Processed {len(output_files)} repositories")
                self.logger.info("📁 Generated index files:")
                for repo_name, file_path in output_files.items():
                    self.logger.info(f"   📄 {repo_name}: {file_path}")
                self.logger.info(f"📋 Summary report: {summary_report}")

                # Statistics (if enabled)
                if self.indexer.generate_statistics:
                    self.logger.info("\n📈 Processing statistics:")
                    total_relationships = 0
                    high_confidence_relationships = 0

                    for file_path in output_files.values():
                        try:
                            with open(file_path, "r", encoding="utf-8") as f:
                                index_data = json.load(f)
                                relationships = index_data.get("relationships", [])
                                total_relationships += len(relationships)
                                high_confidence_relationships += len(
                                    [
                                        r
                                        for r in relationships
                                        if r.get("confidence_score", 0)
                                        > self.indexer.high_confidence_threshold
                                    ]
                                )
                        except Exception as e:
                            self.logger.warning(
                                f"   ⚠️ Unable to load statistics from {file_path}: {e}"
                            )

                    self.logger.info(
                        f"   🔗 Total relationships found: {total_relationships}"
                    )
                    self.logger.info(
                        f"   ⭐ High confidence relationships: {high_confidence_relationships}"
                    )
                    self.logger.info(
                        f"   📊 Average relationships per repository: {total_relationships / len(output_files) if output_files else 0:.1f}"
                    )

                self.logger.info("\n🎉 Code indexing process completed successfully!")

                return {
                    "status": "success",
                    "message": f"Successfully indexed {len(output_files)} repositories",
                    "output_files": output_files,
                    "summary_report": summary_report,
                    "statistics": {
                        "total_repositories": len(output_files),
                        "total_relationships": total_relationships,
                        "high_confidence_relationships": high_confidence_relationships,
                    }
                    if self.indexer.generate_statistics
                    else None,
                }
            else:
                self.logger.warning("⚠️ No index files generated")
                return {
                    "status": "warning",
                    "message": "No index files were generated",
                    "output_files": {},
                }

        except Exception as e:
            self.logger.error(f"❌ Index workflow failed: {e}")
            # If there are detailed error messages, log them
            import traceback

            self.logger.error(f"Detailed error information: {traceback.format_exc()}")
            return {"status": "error", "message": str(e), "output_files": {}}

    def print_banner(self):
        """Print application banner"""
        banner = """
╔═══════════════════════════════════════════════════════════════════════╗
║                    🔍 Codebase Index Workflow v1.0                   ║
║              Intelligent Code Relationship Analysis Tool              ║
╠═══════════════════════════════════════════════════════════════════════╣
║  📁 Analyzes existing codebases                                      ║
║  🔗 Builds intelligent relationships with target structure           ║
║  🤖 Powered by LLM analysis                                          ║
║  📊 Generates detailed JSON indexes                                   ║
║  🎯 Provides reference for code reproduction                          ║
╚═══════════════════════════════════════════════════════════════════════╝
        """
        print(banner)


# Convenience function for direct workflow invocation
async def run_codebase_indexing(
    paper_dir: str,
    initial_plan_path: Optional[str] = None,
    logger=None,
) -> Dict[str, Any]:
    """
    Convenience function to run codebase indexing

    Args:
        paper_dir: Paper directory path
        initial_plan_path: Initial plan file path (optional)
        logger: Logger instance (optional)

    Returns:
        Index result dictionary
    """
    workflow = CodebaseIndexWorkflow(logger=logger)
    workflow.print_banner()

    return await workflow.run_indexing_workflow(
        paper_dir=paper_dir,
        initial_plan_path=initial_plan_path,
    )


# Main function for testing
async def main():
    """Main function for testing workflow"""
    import logging

    # Setup logging
    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger(__name__)

    # Test parameters
    paper_dir = "./deepcode_lab/papers/1"
    initial_plan_path = os.path.join(paper_dir, "initial_plan.txt")

    # Run workflow
    result = await run_codebase_indexing(
        paper_dir=paper_dir, initial_plan_path=initial_plan_path, logger=logger
    )

    logger.info(f"Index result: {result}")


if __name__ == "__main__":
    asyncio.run(main())
