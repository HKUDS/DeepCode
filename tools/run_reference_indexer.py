#!/usr/bin/env python3
"""
Reference Code Indexer - Standalone Script

This script indexes reference codebases in deepcode_lab/reference_code/ directory
and generates JSON index files for use during code generation.

Usage:
    python tools/run_reference_indexer.py [options]

Options:
    --reference-path PATH    Path to reference code directory (default: deepcode_lab/reference_code)
    --output-path PATH       Path to output indexes directory (default: deepcode_lab/indexes)
    --target-structure FILE  Path to target structure file (optional)
    --verbose               Enable verbose output
    --mock                  Use mock LLM responses for testing

Environment Variables:
    DEEPCODE_REFERENCE_PATH  Override reference code path
    DEEPCODE_INDEXES_PATH    Override indexes output path

Examples:
    # Index all repos in reference_code/
    python tools/run_reference_indexer.py

    # Specify custom paths
    python tools/run_reference_indexer.py --reference-path /path/to/code --output-path /path/to/indexes

    # Test mode with mock responses
    python tools/run_reference_indexer.py --mock --verbose
"""

import asyncio
import argparse
import os
import sys
import logging
from pathlib import Path
from typing import Optional

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from tools.code_indexer import CodeIndexer


class ReferenceCodeIndexer:
    """Standalone indexer for reference codebases"""

    def __init__(
        self,
        reference_path: str = None,
        output_path: str = None,
        target_structure: str = None,
        verbose: bool = False,
        mock_responses: bool = False,
    ):
        """
        Initialize the reference code indexer.

        Args:
            reference_path: Path to reference code directory
            output_path: Path to output indexes directory
            target_structure: Target project structure for relationship analysis
            verbose: Enable verbose logging
            mock_responses: Use mock LLM responses for testing
        """
        self.project_root = project_root

        # Determine paths from args, env vars, or defaults
        self.reference_path = Path(
            reference_path
            or os.environ.get("DEEPCODE_REFERENCE_PATH")
            or self.project_root / "deepcode_lab" / "reference_code"
        )

        self.output_path = Path(
            output_path
            or os.environ.get("DEEPCODE_INDEXES_PATH")
            or self.project_root / "deepcode_lab" / "indexes"
        )

        self.target_structure = target_structure or self._get_default_target_structure()
        self.verbose = verbose
        self.mock_responses = mock_responses

        # Setup logging
        self.logger = self._setup_logger()

    def _setup_logger(self) -> logging.Logger:
        """Setup logging configuration"""
        logger = logging.getLogger("ReferenceCodeIndexer")
        log_level = logging.DEBUG if self.verbose else logging.INFO
        logger.setLevel(log_level)

        if not logger.handlers:
            handler = logging.StreamHandler()
            formatter = logging.Formatter(
                "%(asctime)s - %(levelname)s - %(message)s",
                datefmt="%H:%M:%S"
            )
            handler.setFormatter(formatter)
            logger.addHandler(handler)

        return logger

    def _get_default_target_structure(self) -> str:
        """Get default target structure for general code analysis"""
        return """
project/
├── src/
│   ├── core/           # Core functionality modules
│   ├── models/         # Model implementations
│   ├── utils/          # Utility functions
│   └── configs/        # Configuration files
├── tests/              # Test files
├── docs/               # Documentation
├── examples/           # Example usage
├── requirements.txt
└── setup.py
"""

    def print_banner(self):
        """Print application banner"""
        banner = """
╔═══════════════════════════════════════════════════════════════════════╗
║            🗂️  Reference Code Indexer for DeepCode                    ║
║                                                                       ║
║  Indexes reference codebases for intelligent code generation          ║
╚═══════════════════════════════════════════════════════════════════════╝
"""
        print(banner)

    def validate_paths(self) -> bool:
        """Validate that required paths exist and are accessible"""
        if not self.reference_path.exists():
            self.logger.error(f"Reference code path does not exist: {self.reference_path}")
            self.logger.info("Please clone reference repositories to this directory first.")
            return False

        # Check for subdirectories (repos)
        repos = [
            d for d in self.reference_path.iterdir()
            if d.is_dir() and not d.name.startswith(".")
        ]

        if not repos:
            self.logger.error(f"No repositories found in: {self.reference_path}")
            self.logger.info("Please clone at least one repository to index.")
            return False

        self.logger.info(f"Found {len(repos)} repository/repositories to index:")
        for repo in repos:
            self.logger.info(f"  📁 {repo.name}")

        # Create output directory if it doesn't exist
        self.output_path.mkdir(parents=True, exist_ok=True)
        self.logger.info(f"Output directory: {self.output_path}")

        return True

    async def run_indexing(self) -> dict:
        """
        Run the indexing process on reference codebases.

        Returns:
            dict: Indexing result with status and details
        """
        self.print_banner()

        self.logger.info("=" * 60)
        self.logger.info("Starting Reference Code Indexing")
        self.logger.info("=" * 60)
        self.logger.info(f"Reference path: {self.reference_path}")
        self.logger.info(f"Output path: {self.output_path}")
        self.logger.info(f"Verbose mode: {'enabled' if self.verbose else 'disabled'}")
        self.logger.info(f"Mock mode: {'enabled' if self.mock_responses else 'disabled'}")
        self.logger.info("=" * 60)

        # Validate paths
        if not self.validate_paths():
            return {
                "status": "error",
                "message": "Path validation failed",
                "output_files": {}
            }

        try:
            # Create indexer with configuration
            config_path = self.project_root / "mcp_agent.secrets.yaml"
            indexer_config_path = self.project_root / "tools" / "indexer_config.yaml"

            # Initialize CodeIndexer
            indexer = CodeIndexer(
                code_base_path=str(self.reference_path),
                target_structure=self.target_structure,
                output_dir=str(self.output_path),
                config_path=str(config_path),
                indexer_config_path=str(indexer_config_path) if indexer_config_path.exists() else None,
                enable_pre_filtering=True,
            )

            # Apply settings
            indexer.verbose_output = self.verbose
            indexer.mock_llm_responses = self.mock_responses

            # Disable concurrent analysis to avoid API rate limits
            indexer.enable_concurrent_analysis = False
            indexer.request_delay = 0.5  # Add delay between requests

            self.logger.info("🚀 Starting code analysis and indexing...")

            # Build indexes
            output_files = await indexer.build_all_indexes()

            if output_files:
                self.logger.info("=" * 60)
                self.logger.info("✅ Indexing completed successfully!")
                self.logger.info(f"📊 Processed {len(output_files)} repositories")
                self.logger.info("📁 Generated index files:")

                for repo_name, file_path in output_files.items():
                    self.logger.info(f"   📄 {repo_name}: {file_path}")

                # Print usage instructions
                self.logger.info("")
                self.logger.info("=" * 60)
                self.logger.info("📝 Usage Instructions:")
                self.logger.info("=" * 60)
                self.logger.info("1. Index files are stored in: deepcode_lab/indexes/")
                self.logger.info("2. The code-reference-indexer MCP tool can now search these indexes")
                self.logger.info("3. Enable indexing in CLI with: python cli/main_cli.py --enable-indexing")
                self.logger.info("   Or toggle in Configuration menu (option C)")
                self.logger.info("=" * 60)

                return {
                    "status": "success",
                    "message": f"Successfully indexed {len(output_files)} repositories",
                    "output_files": output_files
                }
            else:
                self.logger.warning("⚠️ No index files were generated")
                return {
                    "status": "warning",
                    "message": "No index files were generated",
                    "output_files": {}
                }

        except FileNotFoundError as e:
            self.logger.error(f"❌ File not found: {e}")
            return {
                "status": "error",
                "message": str(e),
                "output_files": {}
            }
        except Exception as e:
            self.logger.error(f"❌ Indexing failed: {e}")
            if self.verbose:
                import traceback
                self.logger.error(traceback.format_exc())
            return {
                "status": "error",
                "message": str(e),
                "output_files": {}
            }


def parse_arguments():
    """Parse command line arguments"""
    parser = argparse.ArgumentParser(
        description="Reference Code Indexer for DeepCode",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Index all repos in default reference_code/ directory
  python tools/run_reference_indexer.py

  # Specify custom paths
  python tools/run_reference_indexer.py --reference-path ./my_repos --output-path ./my_indexes

  # Test mode (no LLM calls)
  python tools/run_reference_indexer.py --mock --verbose
        """
    )

    parser.add_argument(
        "--reference-path", "-r",
        type=str,
        help="Path to reference code directory (default: deepcode_lab/reference_code)"
    )

    parser.add_argument(
        "--output-path", "-o",
        type=str,
        help="Path to output indexes directory (default: deepcode_lab/indexes)"
    )

    parser.add_argument(
        "--target-structure", "-t",
        type=str,
        help="Path to file containing target project structure"
    )

    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable verbose output"
    )

    parser.add_argument(
        "--mock", "-m",
        action="store_true",
        help="Use mock LLM responses for testing (no API calls)"
    )

    return parser.parse_args()


async def main():
    """Main entry point"""
    args = parse_arguments()

    # Load target structure from file if provided
    target_structure = None
    if args.target_structure:
        try:
            with open(args.target_structure, "r", encoding="utf-8") as f:
                target_structure = f.read()
        except Exception as e:
            print(f"Warning: Could not load target structure file: {e}")

    # Create and run indexer
    indexer = ReferenceCodeIndexer(
        reference_path=args.reference_path,
        output_path=args.output_path,
        target_structure=target_structure,
        verbose=args.verbose,
        mock_responses=args.mock
    )

    result = await indexer.run_indexing()

    # Exit with appropriate code
    if result["status"] == "success":
        sys.exit(0)
    elif result["status"] == "warning":
        sys.exit(0)
    else:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
