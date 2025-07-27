#!/usr/bin/env python3
"""
Code Reference Indexer MCP Tool - Unified Version

Specialized MCP tool for searching relevant index content in indexes folder
and formatting it for LLM code implementation reference.

Core Features:
1. **UNIFIED TOOL**: Combined search_code_references that handles directory setup, loading, and searching in one call
2. Match relevant reference code based on target file path and functionality requirements
3. Format output of relevant code examples, functions and concepts
4. Provide structured reference information for LLM use

Key Improvement:
- Single tool call that handles all steps internally
- Agent only needs to provide indexes_path and target_file
- No dependency on calling order or global state management
"""

import json
from pathlib import Path
from typing import Dict, List, Tuple
from dataclasses import dataclass
import logging

# Import MCP modules
from mcp.server.fastmcp import FastMCP

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Create FastMCP server instance
mcp = FastMCP("code-reference-indexer")


@dataclass
class CodeReference:
    """Code reference information structure"""

    file_path: str
    file_type: str
    main_functions: List[str]
    key_concepts: List[str]
    dependencies: List[str]
    summary: str
    lines_of_code: int
    repo_name: str
    confidence_score: float = 0.0


@dataclass
class RelationshipInfo:
    """Relationship information structure"""

    repo_file_path: str
    target_file_path: str
    relationship_type: str
    confidence_score: float
    helpful_aspects: List[str]
    potential_contributions: List[str]
    usage_suggestions: str


def load_index_files_from_directory(indexes_directory: str) -> Dict[str, Dict]:
    """Load all index files from specified directory"""
    indexes_path = Path(indexes_directory).resolve()

    if not indexes_path.exists():
        logger.warning(f"Indexes directory does not exist: {indexes_path}")
        return {}

    index_cache = {}

    for index_file in indexes_path.glob("*.json"):
        try:
            with open(index_file, "r", encoding="utf-8") as f:
                index_data = json.load(f)
                index_cache[index_file.stem] = index_data
                logger.info(f"Loaded index file: {index_file.name}")
        except Exception as e:
            logger.error(f"Failed to load index file {index_file.name}: {e}")

    logger.info(f"Loaded {len(index_cache)} index files from {indexes_path}")
    return index_cache


def extract_code_references(index_data: Dict) -> List[CodeReference]:
    """Extract code reference information from index data"""
    references = []

    repo_name = index_data.get("repo_name", "Unknown")
    file_summaries = index_data.get("file_summaries", [])

    for file_summary in file_summaries:
        reference = CodeReference(
            file_path=file_summary.get("file_path", ""),
            file_type=file_summary.get("file_type", ""),
            main_functions=file_summary.get("main_functions", []),
            key_concepts=file_summary.get("key_concepts", []),
            dependencies=file_summary.get("dependencies", []),
            summary=file_summary.get("summary", ""),
            lines_of_code=file_summary.get("lines_of_code", 0),
            repo_name=repo_name,
        )
        references.append(reference)

    return references


def extract_relationships(index_data: Dict) -> List[RelationshipInfo]:
    """Extract relationship information from index data"""
    relationships = []

    relationship_list = index_data.get("relationships", [])

    for rel in relationship_list:
        relationship = RelationshipInfo(
            repo_file_path=rel.get("repo_file_path", ""),
            target_file_path=rel.get("target_file_path", ""),
            relationship_type=rel.get("relationship_type", ""),
            confidence_score=rel.get("confidence_score", 0.0),
            helpful_aspects=rel.get("helpful_aspects", []),
            potential_contributions=rel.get("potential_contributions", []),
            usage_suggestions=rel.get("usage_suggestions", ""),
        )
        relationships.append(relationship)

    return relationships


def calculate_relevance_score(
    target_file: str, reference: CodeReference, keywords: List[str] = None
) -> float:
    """Calculate relevance score between reference code and target file"""
    score = 0.0

    # File name similarity
    target_name = Path(target_file).stem.lower()
    ref_name = Path(reference.file_path).stem.lower()

    if target_name in ref_name or ref_name in target_name:
        score += 0.3

    # File type matching
    target_extension = Path(target_file).suffix
    ref_extension = Path(reference.file_path).suffix

    if target_extension == ref_extension:
        score += 0.2

    # Keyword matching
    if keywords:
        keyword_matches = 0
        total_searchable_text = (
            " ".join(reference.key_concepts)
            + " "
            + " ".join(reference.main_functions)
            + " "
            + reference.summary
            + " "
            + reference.file_type
        ).lower()

        for keyword in keywords:
            if keyword.lower() in total_searchable_text:
                keyword_matches += 1

        if keywords:
            score += (keyword_matches / len(keywords)) * 0.5

    return min(score, 1.0)


def find_relevant_references_in_cache(
    target_file: str,
    index_cache: Dict[str, Dict],
    keywords: List[str] = None,
    max_results: int = 10,
) -> List[Tuple[CodeReference, float]]:
    """Find reference code relevant to target file from provided cache"""
    all_references = []

    # Collect reference information from all index files
    for repo_name, index_data in index_cache.items():
        references = extract_code_references(index_data)
        for ref in references:
            relevance_score = calculate_relevance_score(target_file, ref, keywords)
            if relevance_score > 0.1:  # Only keep results with certain relevance
                all_references.append((ref, relevance_score))

    # Sort by relevance score
    all_references.sort(key=lambda x: x[1], reverse=True)

    return all_references[:max_results]


def find_direct_relationships_in_cache(
    target_file: str, index_cache: Dict[str, Dict]
) -> List[RelationshipInfo]:
    """Find direct relationships with target file from provided cache"""
    relationships = []

    # Normalize target file path (remove rice/ prefix if exists)
    normalized_target = target_file.replace("rice/", "").strip("/")

    # Collect relationship information from all index files
    for repo_name, index_data in index_cache.items():
        repo_relationships = extract_relationships(index_data)
        for rel in repo_relationships:
            # Normalize target file path in relationship
            normalized_rel_target = rel.target_file_path.replace("rice/", "").strip("/")

            # Check target file path matching (support multiple matching methods)
            if (
                normalized_target == normalized_rel_target
                or normalized_target in normalized_rel_target
                or normalized_rel_target in normalized_target
                or target_file in rel.target_file_path
                or rel.target_file_path in target_file
            ):
                relationships.append(rel)

    # Sort by confidence score
    relationships.sort(key=lambda x: x.confidence_score, reverse=True)

    return relationships


def format_reference_output(
    target_file: str,
    relevant_refs: List[Tuple[CodeReference, float]],
    relationships: List[RelationshipInfo],
) -> str:
    """Format reference information output"""
    output_lines = []

    output_lines.append(f"# Code Reference Information - {target_file}")
    output_lines.append("=" * 80)
    output_lines.append("")

    # Direct relationship information
    if relationships:
        output_lines.append("## 🎯 Direct Relationships")
        output_lines.append("")

        for i, rel in enumerate(relationships[:5], 1):
            output_lines.append(f"### {i}. {rel.repo_file_path}")
            output_lines.append(f"**Relationship Type**: {rel.relationship_type}")
            output_lines.append(f"**Confidence Score**: {rel.confidence_score:.2f}")
            output_lines.append(
                f"**Helpful Aspects**: {', '.join(rel.helpful_aspects)}"
            )
            output_lines.append(
                f"**Potential Contributions**: {', '.join(rel.potential_contributions)}"
            )
            output_lines.append(f"**Usage Suggestions**: {rel.usage_suggestions}")
            output_lines.append("")

    # Relevant code references
    if relevant_refs:
        output_lines.append("## 📚 Relevant Code References")
        output_lines.append("")

        for i, (ref, score) in enumerate(relevant_refs[:8], 1):
            output_lines.append(f"### {i}. {ref.file_path} (Relevance: {score:.2f})")
            output_lines.append(f"**Repository**: {ref.repo_name}")
            output_lines.append(f"**File Type**: {ref.file_type}")
            output_lines.append(
                f"**Main Functions**: {', '.join(ref.main_functions[:5])}"
            )
            output_lines.append(f"**Key Concepts**: {', '.join(ref.key_concepts[:8])}")
            output_lines.append(f"**Dependencies**: {', '.join(ref.dependencies[:6])}")
            output_lines.append(f"**Lines of Code**: {ref.lines_of_code}")
            output_lines.append(f"**Summary**: {ref.summary[:300]}...")
            output_lines.append("")

    # Implementation suggestions
    output_lines.append("## 💡 Implementation Suggestions")
    output_lines.append("")

    if relevant_refs:
        # Collect all function names and concepts
        all_functions = set()
        all_concepts = set()
        all_dependencies = set()

        for ref, _ in relevant_refs[:5]:
            all_functions.update(ref.main_functions)
            all_concepts.update(ref.key_concepts)
            all_dependencies.update(ref.dependencies)

        output_lines.append("**Reference Function Name Patterns**:")
        for func in sorted(list(all_functions))[:10]:
            output_lines.append(f"- {func}")
        output_lines.append("")

        output_lines.append("**Important Concepts and Patterns**:")
        for concept in sorted(list(all_concepts))[:15]:
            output_lines.append(f"- {concept}")
        output_lines.append("")

        output_lines.append("**Potential Dependencies Needed**:")
        for dep in sorted(list(all_dependencies))[:10]:
            output_lines.append(f"- {dep}")
        output_lines.append("")

    output_lines.append("## 🚀 Next Actions")
    output_lines.append(
        "1. Analyze design patterns and architectural styles from the above reference code"
    )
    output_lines.append("2. Determine core functionalities and interfaces to implement")
    output_lines.append("3. Choose appropriate dependency libraries and tools")
    output_lines.append(
        "4. Design implementation solution consistent with existing code style"
    )
    output_lines.append("5. Start writing specific code implementation")

    return "\n".join(output_lines)


# ==================== MCP Tool Definitions ====================


@mcp.tool()
async def search_code_references(
    indexes_path: str, target_file: str, keywords: str = "", max_results: int = 10
) -> str:
    """
    **UNIFIED TOOL**: Search relevant reference code from index files for target file implementation.
    This tool combines directory setup, index loading, and searching in a single call.

    Args:
        indexes_path: Path to the indexes directory containing JSON index files
        target_file: Target file path (file to be implemented)
        keywords: Search keywords, comma-separated
        max_results: Maximum number of results to return

    Returns:
        Formatted reference code information JSON string
    """
    try:
        # Step 1: Load index files from specified directory
        logger.info(f"Loading index files from: {indexes_path}")
        index_cache = load_index_files_from_directory(indexes_path)

        if not index_cache:
            result = {
                "status": "error",
                "message": f"No index files found or failed to load from: {indexes_path}",
                "target_file": target_file,
                "indexes_path": indexes_path,
            }
            return json.dumps(result, ensure_ascii=False, indent=2)

        # Step 2: Parse keywords
        keyword_list = (
            [kw.strip() for kw in keywords.split(",") if kw.strip()] if keywords else []
        )

        # Step 3: Find relevant reference code
        relevant_refs = find_relevant_references_in_cache(
            target_file, index_cache, keyword_list, max_results
        )

        # Step 4: Find direct relationships
        relationships = find_direct_relationships_in_cache(target_file, index_cache)

        # Step 5: Format output
        formatted_output = format_reference_output(
            target_file, relevant_refs, relationships
        )

        result = {
            "status": "success",
            "target_file": target_file,
            "indexes_path": indexes_path,
            "keywords_used": keyword_list,
            "total_references_found": len(relevant_refs),
            "total_relationships_found": len(relationships),
            "formatted_content": formatted_output,
            "indexes_loaded": list(index_cache.keys()),
            "total_indexes_loaded": len(index_cache),
        }

        logger.info(
            f"Successfully found {len(relevant_refs)} references and {len(relationships)} relationships for {target_file}"
        )
        return json.dumps(result, ensure_ascii=False, indent=2)

    except Exception as e:
        logger.error(f"Error in search_code_references: {str(e)}")
        result = {
            "status": "error",
            "message": f"Failed to search reference code: {str(e)}",
            "target_file": target_file,
            "indexes_path": indexes_path,
        }
        return json.dumps(result, ensure_ascii=False, indent=2)


@mcp.tool()
async def get_indexes_overview(indexes_path: str) -> str:
    """
    Get overview of all available reference code index information from specified directory

    Args:
        indexes_path: Path to the indexes directory containing JSON index files

    Returns:
        Overview information of all available reference code JSON string
    """
    try:
        # Load index files from specified directory
        index_cache = load_index_files_from_directory(indexes_path)

        if not index_cache:
            result = {
                "status": "error",
                "message": f"No index files found in: {indexes_path}",
                "indexes_path": indexes_path,
            }
            return json.dumps(result, ensure_ascii=False, indent=2)

        overview = {"total_repos": len(index_cache), "repositories": {}}

        for repo_name, index_data in index_cache.items():
            repo_info = {
                "repo_name": index_data.get("repo_name", repo_name),
                "total_files": index_data.get("total_files", 0),
                "file_types": [],
                "main_concepts": [],
                "total_relationships": len(index_data.get("relationships", [])),
            }

            # Collect file types and concepts
            file_summaries = index_data.get("file_summaries", [])
            file_types = set()
            concepts = set()

            for file_summary in file_summaries:
                file_types.add(file_summary.get("file_type", "Unknown"))
                concepts.update(file_summary.get("key_concepts", []))

            repo_info["file_types"] = sorted(list(file_types))
            repo_info["main_concepts"] = sorted(list(concepts))[
                :20
            ]  # Limit concept count

            overview["repositories"][repo_name] = repo_info

        result = {
            "status": "success",
            "overview": overview,
            "indexes_directory": str(Path(indexes_path).resolve()),
            "total_indexes_loaded": len(index_cache),
        }

        return json.dumps(result, ensure_ascii=False, indent=2)

    except Exception as e:
        result = {
            "status": "error",
            "message": f"Failed to get indexes overview: {str(e)}",
            "indexes_path": indexes_path,
        }
        return json.dumps(result, ensure_ascii=False, indent=2)


def main():
    """Main function"""
    logger.info("Starting unified Code Reference Indexer MCP server")
    logger.info("Available tools:")
    logger.info(
        "1. search_code_references(indexes_path, target_file, keywords, max_results) - UNIFIED TOOL"
    )
    logger.info(
        "2. get_indexes_overview(indexes_path) - Get overview of available indexes"
    )

    # Run MCP server
    mcp.run()


if __name__ == "__main__":
    main()
