#!/usr/bin/env python3
"""
Document Segmentation MCP Server

This MCP server provides intelligent document segmentation and retrieval functions for handling
large research papers and technical documents that exceed LLM token limits.

==== CORE FUNCTIONALITY ====
1. Analyze document structure and type using semantic content analysis
2. Create intelligent segments based on content semantics, not just structure
3. Provide query-aware segment retrieval with relevance scoring
4. Support both structured (papers with headers) and unstructured documents
5. Configurable segmentation strategies based on document complexity

==== MCP TOOLS PROVIDED ====

📄 analyze_and_segment_document(paper_dir: str, force_refresh: bool = False)
   Purpose: Analyzes document structure and creates intelligent segments
   - Detects document type (research paper, technical doc, algorithm-focused, etc.)
   - Selects optimal segmentation strategy based on content analysis
   - Creates semantic segments preserving algorithm and concept integrity
   - Stores segmentation index for efficient retrieval
   - Returns: JSON with segmentation status, strategy used, and segment count

📖 read_document_segments(paper_dir: str, query_type: str, keywords: List[str] = None,
                         max_segments: int = 3, max_total_chars: int = None)
   Purpose: Intelligently retrieves relevant document segments based on query context
   - query_type: "concept_analysis", "algorithm_extraction", or "code_planning"
   - Uses semantic relevance scoring to rank segments
   - Applies query-specific filtering and keyword matching
   - Dynamically calculates optimal character limits based on content complexity
   - Returns: JSON with selected segments optimized for the specific query type

📋 get_document_overview(paper_dir: str)
   Purpose: Provides high-level overview of document structure and available segments
   - Shows document type and segmentation strategy used
   - Lists all segments with titles, content types, and relevance scores
   - Displays segment statistics (character counts, keyword summaries)
   - Returns: JSON with complete document analysis metadata

==== SEGMENTATION STRATEGIES ====
- semantic_research_focused: For academic papers with complex algorithmic content
- algorithm_preserve_integrity: Maintains algorithm blocks and formula chains intact
- concept_implementation_hybrid: Merges related concepts with implementation details
- semantic_chunking_enhanced: Advanced boundary detection for long documents
- content_aware_segmentation: Adaptive chunking based on content density

==== INTELLIGENT FEATURES ====
- Semantic boundary detection (not just structural)
- Algorithm block identification and preservation
- Formula chain recognition and grouping
- Concept-implementation relationship mapping
- Multi-level relevance scoring (content type, importance, keyword matching)
- Backward compatibility with existing document indexes
- Configurable via mcp_agent.config.yaml (enabled/disabled, size thresholds)

Usage:
python tools/document_segmentation_server.py
"""

import os
import re
import json
from typing import Dict, List, Tuple
import hashlib
import logging
from datetime import datetime
from dataclasses import dataclass, asdict

from core.platform_compat import configure_utf8_stdio

configure_utf8_stdio()

# Import MCP related modules
from mcp.server.fastmcp import FastMCP

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Create FastMCP server instance
mcp = FastMCP("document-segmentation-server")


@dataclass
class DocumentSegment:
    """Represents a document segment with metadata"""

    id: str
    title: str
    content: str
    content_type: str  # "introduction", "methodology", "algorithm", "results", etc.
    keywords: List[str]
    char_start: int
    char_end: int
    char_count: int
    relevance_scores: Dict[str, float]  # Scores for different query types
    section_path: str  # e.g., "3.2.1" for nested sections


@dataclass
class DocumentIndex:
    """Document index containing all segments and metadata"""

    document_path: str
    document_type: str  # "academic_paper", "technical_doc", "code_doc", "general"
    segmentation_strategy: str
    total_segments: int
    total_chars: int
    segments: List[DocumentSegment]
    created_at: str


class DocumentAnalyzer:
    """Enhanced document analyzer using semantic content analysis instead of mechanical structure detection"""

    # More precise semantic indicators, weighted by importance
    ALGORITHM_INDICATORS = {
        "high": [
            "algorithm",
            "procedure",
            "method",
            "approach",
            "technique",
            "framework",
        ],
        "medium": ["step", "process", "implementation", "computation", "calculation"],
        "low": ["example", "illustration", "demonstration"],
    }

    TECHNICAL_CONCEPT_INDICATORS = {
        "high": ["formula", "equation", "theorem", "lemma", "proof", "definition"],
        "medium": ["parameter", "variable", "function", "model", "architecture"],
        "low": ["notation", "symbol", "term"],
    }

    IMPLEMENTATION_INDICATORS = {
        "high": ["code", "implementation", "programming", "software", "system"],
        "medium": ["design", "structure", "module", "component", "interface"],
        "low": ["tool", "library", "package"],
    }

    # Semantic features of document types (not just based on titles)
    RESEARCH_PAPER_PATTERNS = [
        r"(?i)\babstract\b.*?\n.*?(introduction|motivation|background)",
        r"(?i)(methodology|method).*?(experiment|evaluation|result)",
        r"(?i)(conclusion|future work|limitation).*?(reference|bibliography)",
        r"(?i)(related work|literature review|prior art)",
    ]

    TECHNICAL_DOC_PATTERNS = [
        r"(?i)(getting started|installation|setup).*?(usage|example)",
        r"(?i)(api|interface|specification).*?(parameter|endpoint)",
        r"(?i)(tutorial|guide|walkthrough).*?(step|instruction)",
        r"(?i)(troubleshooting|faq|common issues)",
    ]

    def analyze_document_type(self, content: str) -> Tuple[str, float]:
        """
        Enhanced document type analysis based on semantic content patterns

        Returns:
            Tuple[str, float]: (document_type, confidence_score)
        """
        content_lower = content.lower()

        # Calculate weighted semantic indicator scores
        algorithm_score = self._calculate_weighted_score(
            content_lower, self.ALGORITHM_INDICATORS
        )
        concept_score = self._calculate_weighted_score(
            content_lower, self.TECHNICAL_CONCEPT_INDICATORS
        )
        implementation_score = self._calculate_weighted_score(
            content_lower, self.IMPLEMENTATION_INDICATORS
        )

        # Detect semantic patterns of document types
        research_pattern_score = self._detect_pattern_score(
            content, self.RESEARCH_PAPER_PATTERNS
        )
        technical_pattern_score = self._detect_pattern_score(
            content, self.TECHNICAL_DOC_PATTERNS
        )

        # Comprehensive evaluation of document type
        total_research_score = (
            algorithm_score + concept_score + research_pattern_score * 2
        )
        total_technical_score = implementation_score + technical_pattern_score * 2

        # Determine document type based on content density and pattern matching
        if research_pattern_score > 0.5 and total_research_score > 3.0:
            return "research_paper", min(0.95, 0.6 + research_pattern_score * 0.35)
        elif algorithm_score > 2.0 and concept_score > 1.5:
            return "algorithm_focused", 0.85
        elif total_technical_score > 2.5:
            return "technical_doc", 0.8
        elif implementation_score > 1.5:
            return "implementation_guide", 0.75
        else:
            return "general_document", 0.5

    def _calculate_weighted_score(
        self, content: str, indicators: Dict[str, List[str]]
    ) -> float:
        """Calculate weighted semantic indicator scores"""
        score = 0.0
        for weight_level, terms in indicators.items():
            weight = {"high": 3.0, "medium": 2.0, "low": 1.0}[weight_level]
            for term in terms:
                if term in content:
                    score += weight * (
                        content.count(term) * 0.5 + 1
                    )  # Consider term frequency
        return score

    def _detect_pattern_score(self, content: str, patterns: List[str]) -> float:
        """Detect semantic pattern matching scores"""
        matches = 0
        for pattern in patterns:
            if re.search(pattern, content, re.DOTALL):
                matches += 1
        return matches / len(patterns)

    def detect_segmentation_strategy(self, content: str, doc_type: str) -> str:
        """
        Intelligently determine the best segmentation strategy based on content semantics rather than mechanical structure
        """
        # Analyze content characteristics
        algorithm_density = self._calculate_algorithm_density(content)
        concept_complexity = self._calculate_concept_complexity(content)
        implementation_detail_level = self._calculate_implementation_detail_level(
            content
        )

        # Select strategy based on document type and content characteristics
        if doc_type == "research_paper" and algorithm_density > 0.3:
            return "semantic_research_focused"
        elif doc_type == "algorithm_focused" or algorithm_density > 0.5:
            return "algorithm_preserve_integrity"
        elif concept_complexity > 0.4 and implementation_detail_level > 0.3:
            return "concept_implementation_hybrid"
        elif len(content) > 15000:  # Long documents
            return "semantic_chunking_enhanced"
        else:
            return "content_aware_segmentation"

    def _calculate_algorithm_density(self, content: str) -> float:
        """Calculate algorithm content density"""
        total_chars = len(content)
        algorithm_chars = 0

        # Identify algorithm blocks
        algorithm_patterns = [
            r"(?i)(algorithm\s+\d+|procedure\s+\d+)",
            r"(?i)(step\s+\d+|phase\s+\d+)",
            r"(?i)(input:|output:|return:|initialize:)",
            r"(?i)(for\s+each|while|if.*then|else)",
            r"(?i)(function|method|procedure).*\(",
        ]

        for pattern in algorithm_patterns:
            matches = re.finditer(pattern, content)
            for match in matches:
                # Estimate algorithm block size (expand forward and backward from match point)
                start = max(0, match.start() - 200)
                end = min(len(content), match.end() + 800)
                algorithm_chars += end - start

        return min(1.0, algorithm_chars / total_chars)

    def _calculate_concept_complexity(self, content: str) -> float:
        """Calculate concept complexity"""
        concept_indicators = self.TECHNICAL_CONCEPT_INDICATORS
        complexity_score = 0.0

        for level, terms in concept_indicators.items():
            weight = {"high": 3.0, "medium": 2.0, "low": 1.0}[level]
            for term in terms:
                complexity_score += content.lower().count(term) * weight

        # Normalize to 0-1 range
        return min(1.0, complexity_score / 100)

    def _calculate_implementation_detail_level(self, content: str) -> float:
        """Calculate implementation detail level"""
        implementation_patterns = [
            r"(?i)(code|implementation|programming)",
            r"(?i)(class|function|method|variable)",
            r"(?i)(import|include|library)",
            r"(?i)(parameter|argument|return)",
            r"(?i)(example|demo|tutorial)",
        ]

        detail_score = 0
        for pattern in implementation_patterns:
            detail_score += len(re.findall(pattern, content))

        return min(1.0, detail_score / 50)


class DocumentSegmenter:
    """Creates intelligent segments from documents"""

    def __init__(self):
        self.analyzer = DocumentAnalyzer()

    def segment_document(self, content: str, strategy: str) -> List[DocumentSegment]:
        """
        Perform intelligent segmentation using the specified strategy
        """
        if strategy == "semantic_research_focused":
            return self._segment_research_paper_semantically(content)
        elif strategy == "algorithm_preserve_integrity":
            return self._segment_preserve_algorithm_integrity(content)
        elif strategy == "concept_implementation_hybrid":
            return self._segment_concept_implementation_hybrid(content)
        elif strategy == "semantic_chunking_enhanced":
            return self._segment_by_enhanced_semantic_chunks(content)
        elif strategy == "content_aware_segmentation":
            return self._segment_content_aware(content)
        else:
            # Compatibility with legacy strategies
            return self._segment_by_enhanced_semantic_chunks(content)

    def _segment_by_headers(self, content: str) -> List[DocumentSegment]:
        """Segment document based on markdown headers"""
        segments = []
        lines = content.split("\n")
        current_segment = []
        current_header = None
        char_pos = 0

        for line in lines:
            line_with_newline = line + "\n"

            # Check if line is a header
            header_match = re.match(r"^(#{1,6})\s+(.+)$", line)

            if header_match:
                # Save previous segment if exists
                if current_segment and current_header:
                    segment_content = "\n".join(current_segment).strip()
                    if segment_content:
                        # Analyze content type and importance
                        content_type = self._classify_content_type(
                            current_header, segment_content
                        )
                        importance_score = (
                            0.8 if content_type in ["algorithm", "formula"] else 0.7
                        )

                        segment = self._create_enhanced_segment(
                            segment_content,
                            current_header,
                            char_pos - len(segment_content.encode("utf-8")),
                            char_pos,
                            importance_score,
                            content_type,
                        )
                        segments.append(segment)

                # Start new segment
                current_header = header_match.group(2).strip()
                current_segment = [line]
            else:
                if current_segment is not None:
                    current_segment.append(line)

            char_pos += len(line_with_newline.encode("utf-8"))

        # Add final segment
        if current_segment and current_header:
            segment_content = "\n".join(current_segment).strip()
            if segment_content:
                # Analyze content type and importance
                content_type = self._classify_content_type(
                    current_header, segment_content
                )
                importance_score = (
                    0.8 if content_type in ["algorithm", "formula"] else 0.7
                )

                segment = self._create_enhanced_segment(
                    segment_content,
                    current_header,
                    char_pos - len(segment_content.encode("utf-8")),
                    char_pos,
                    importance_score,
                    content_type,
                )
                segments.append(segment)

        return segments

    def _segment_preserve_algorithm_integrity(
        self, content: str
    ) -> List[DocumentSegment]:
        """Smart segmentation strategy that preserves algorithm integrity"""
        segments = []

        # 1. Identify algorithm blocks and related descriptions
        algorithm_blocks = self._identify_algorithm_blocks(content)

        # 2. Identify concept definition groups
        concept_groups = self._identify_concept_groups(content)

        # 3. Identify formula derivation chains
        formula_chains = self._identify_formula_chains(content)

        # 4. Merge related content blocks to ensure integrity
        content_blocks = self._merge_related_content_blocks(
            algorithm_blocks, concept_groups, formula_chains, content
        )

        # 5. Convert to DocumentSegment
        for i, block in enumerate(content_blocks):
            segment = self._create_enhanced_segment(
                block["content"],
                block["title"],
                block["start_pos"],
                block["end_pos"],
                block["importance_score"],
                block["content_type"],
            )
            segments.append(segment)

        return segments

    def _segment_research_paper_semantically(
        self, content: str
    ) -> List[DocumentSegment]:
        """Semantic segmentation specifically for research papers"""
        segments = []

        # Identify semantic structure of research papers
        paper_sections = self._identify_research_paper_sections(content)

        for section in paper_sections:
            # Ensure each section contains sufficient context
            enhanced_content = self._enhance_section_with_context(section, content)

            segment = self._create_enhanced_segment(
                enhanced_content["content"],
                enhanced_content["title"],
                enhanced_content["start_pos"],
                enhanced_content["end_pos"],
                enhanced_content["importance_score"],
                enhanced_content["content_type"],
            )
            segments.append(segment)

        return segments

    def _segment_concept_implementation_hybrid(
        self, content: str
    ) -> List[DocumentSegment]:
        """Intelligent segmentation combining concepts and implementation"""
        segments = []

        # Identify concept-implementation correspondence
        concept_impl_pairs = self._identify_concept_implementation_pairs(content)

        for pair in concept_impl_pairs:
            # Merge related concepts and implementations into one segment
            merged_content = self._merge_concept_with_implementation(pair, content)

            segment = self._create_enhanced_segment(
                merged_content["content"],
                merged_content["title"],
                merged_content["start_pos"],
                merged_content["end_pos"],
                merged_content["importance_score"],
                merged_content["content_type"],
            )
            segments.append(segment)

        return segments

    def _segment_by_enhanced_semantic_chunks(
        self, content: str
    ) -> List[DocumentSegment]:
        """Enhanced semantic chunk segmentation"""
        segments = []

        # Use improved semantic boundary detection
        semantic_boundaries = self._detect_semantic_boundaries(content)

        current_start = 0
        for i, boundary in enumerate(semantic_boundaries):
            chunk_content = content[current_start : boundary["position"]]

            if len(chunk_content.strip()) > 200:  # Minimum content threshold
                segment = self._create_enhanced_segment(
                    chunk_content,
                    boundary["suggested_title"],
                    current_start,
                    boundary["position"],
                    boundary["importance_score"],
                    boundary["content_type"],
                )
                segments.append(segment)

            current_start = boundary["position"]

        # Handle the final segment
        if current_start < len(content):
            final_content = content[current_start:]
            if len(final_content.strip()) > 200:
                segment = self._create_enhanced_segment(
                    final_content,
                    "Final Section",
                    current_start,
                    len(content),
                    0.7,
                    "general",
                )
                segments.append(segment)

        return segments

    def _segment_content_aware(self, content: str) -> List[DocumentSegment]:
        """Content-aware intelligent segmentation"""
        segments = []

        # Adaptive segmentation size
        optimal_chunk_size = self._calculate_optimal_chunk_size(content)

        # Segment based on content density
        content_chunks = self._create_content_aware_chunks(content, optimal_chunk_size)

        for chunk in content_chunks:
            segment = self._create_enhanced_segment(
                chunk["content"],
                chunk["title"],
                chunk["start_pos"],
                chunk["end_pos"],
                chunk["importance_score"],
                chunk["content_type"],
            )
            segments.append(segment)

        return segments

    def _segment_academic_paper(self, content: str) -> List[DocumentSegment]:
        """Segment academic paper using semantic understanding"""
        # First try header-based segmentation
        headers = re.findall(r"^(#{1,6})\s+(.+)$", content, re.MULTILINE)
        if len(headers) >= 2:
            return self._segment_by_headers(content)

        # Fallback to semantic detection of academic sections
        sections = self._detect_academic_sections(content)
        segments = []

        for section in sections:
            # Determine importance based on section type
            section_type = section.get("type", "general")
            content_type = (
                section_type
                if section_type
                in ["algorithm", "formula", "introduction", "conclusion"]
                else "general"
            )
            importance_score = {
                "algorithm": 0.95,
                "formula": 0.9,
                "introduction": 0.85,
                "conclusion": 0.8,
            }.get(content_type, 0.7)

            segment = self._create_enhanced_segment(
                section["content"],
                section["title"],
                section["start_pos"],
                section["end_pos"],
                importance_score,
                content_type,
            )
            segments.append(segment)

        return segments

    def _detect_academic_sections(self, content: str) -> List[Dict]:
        """Detect academic paper sections even without clear headers"""
        sections = []

        # Common academic section patterns
        section_patterns = [
            (r"(?i)(abstract|摘要)", "introduction"),
            (r"(?i)(introduction|引言|简介)", "introduction"),
            (r"(?i)(related work|相关工作|背景)", "background"),
            (r"(?i)(method|methodology|approach|方法)", "methodology"),
            (r"(?i)(algorithm|算法)", "algorithm"),
            (r"(?i)(experiment|实验|evaluation|评估)", "experiment"),
            (r"(?i)(result|结果|finding)", "results"),
            (r"(?i)(conclusion|结论|总结)", "conclusion"),
            (r"(?i)(reference|参考文献|bibliography)", "references"),
        ]

        current_pos = 0
        for i, (pattern, section_type) in enumerate(section_patterns):
            match = re.search(pattern, content[current_pos:], re.IGNORECASE)
            if match:
                start_pos = current_pos + match.start()

                # Find end position (next section or end of document)
                next_pos = len(content)
                for next_pattern, _ in section_patterns[i + 1 :]:
                    next_match = re.search(
                        next_pattern, content[start_pos + 100 :], re.IGNORECASE
                    )
                    if next_match:
                        next_pos = start_pos + 100 + next_match.start()
                        break

                section_content = content[start_pos:next_pos].strip()
                if len(section_content) > 50:  # Minimum content length
                    # Calculate importance score and content type
                    importance_score = self._calculate_paragraph_importance(
                        section_content, section_type
                    )
                    content_type = self._classify_content_type(
                        match.group(1), section_content
                    )

                    sections.append(
                        {
                            "title": match.group(1),
                            "content": section_content,
                            "start_pos": start_pos,
                            "end_pos": next_pos,
                            "type": section_type,
                            "importance_score": importance_score,
                            "content_type": content_type,
                        }
                    )

                current_pos = next_pos

        return sections

    def _segment_by_semantic_chunks(self, content: str) -> List[DocumentSegment]:
        """Segment long documents into semantic chunks"""
        # Split into paragraphs first
        paragraphs = [p.strip() for p in content.split("\n\n") if p.strip()]

        segments = []
        current_chunk = []
        current_chunk_size = 0
        chunk_size_limit = 3000  # characters
        overlap_size = 200

        char_pos = 0

        for para in paragraphs:
            para_size = len(para)

            # If adding this paragraph exceeds limit, create a segment
            if current_chunk_size + para_size > chunk_size_limit and current_chunk:
                chunk_content = "\n\n".join(current_chunk)
                # Analyze semantic chunk content type
                content_type = self._classify_paragraph_type(chunk_content)
                importance_score = self._calculate_paragraph_importance(
                    chunk_content, content_type
                )

                segment = self._create_enhanced_segment(
                    chunk_content,
                    f"Section {len(segments) + 1}",
                    char_pos - len(chunk_content.encode("utf-8")),
                    char_pos,
                    importance_score,
                    content_type,
                )
                segments.append(segment)

                # Keep last part for overlap
                overlap_content = (
                    chunk_content[-overlap_size:]
                    if len(chunk_content) > overlap_size
                    else ""
                )
                current_chunk = [overlap_content, para] if overlap_content else [para]
                current_chunk_size = len(overlap_content) + para_size
            else:
                current_chunk.append(para)
                current_chunk_size += para_size

            char_pos += para_size + 2  # +2 for \n\n

        # Add final chunk
        if current_chunk:
            chunk_content = "\n\n".join(current_chunk)
            # Analyze final chunk content type
            content_type = self._classify_paragraph_type(chunk_content)
            importance_score = self._calculate_paragraph_importance(
                chunk_content, content_type
            )

            segment = self._create_enhanced_segment(
                chunk_content,
                f"Section {len(segments) + 1}",
                char_pos - len(chunk_content.encode("utf-8")),
                char_pos,
                importance_score,
                content_type,
            )
            segments.append(segment)

        return segments

    def _segment_by_paragraphs(self, content: str) -> List[DocumentSegment]:
        """Simple paragraph-based segmentation for short documents"""
        paragraphs = [p.strip() for p in content.split("\n\n") if p.strip()]
        segments = []
        char_pos = 0

        for i, para in enumerate(paragraphs):
            if len(para) > 100:  # Only include substantial paragraphs
                # Analyze paragraph type and importance
                content_type = self._classify_paragraph_type(para)
                importance_score = self._calculate_paragraph_importance(
                    para, content_type
                )

                segment = self._create_enhanced_segment(
                    para,
                    f"Paragraph {i + 1}",
                    char_pos,
                    char_pos + len(para.encode("utf-8")),
                    importance_score,
                    content_type,
                )
                segments.append(segment)
            char_pos += len(para.encode("utf-8")) + 2

        return segments

    # =============== Enhanced intelligent segmentation helper methods ===============

    def _identify_algorithm_blocks(self, content: str) -> List[Dict]:
        """Identify algorithm blocks and related descriptions"""
        algorithm_blocks = []

        # Algorithm block identification patterns
        algorithm_patterns = [
            r"(?i)(algorithm\s+\d+|procedure\s+\d+|method\s+\d+).*?(?=algorithm\s+\d+|procedure\s+\d+|method\s+\d+|$)",
            r"(?i)(input:|output:|returns?:|require:|ensure:).*?(?=\n\s*\n|\n\s*(?:input:|output:|returns?:|require:|ensure:)|$)",
            r"(?i)(for\s+each|while|if.*then|repeat.*until).*?(?=\n\s*\n|$)",
            r"(?i)(step\s+\d+|phase\s+\d+).*?(?=step\s+\d+|phase\s+\d+|\n\s*\n|$)",
        ]

        for pattern in algorithm_patterns:
            matches = re.finditer(pattern, content, re.DOTALL)
            for match in matches:
                # Expand context to include complete descriptions
                start = max(0, match.start() - 300)
                end = min(len(content), match.end() + 500)

                # Find natural boundaries
                while start > 0 and content[start] not in "\n.!?":
                    start -= 1
                while end < len(content) and content[end] not in "\n.!?":
                    end += 1

                algorithm_blocks.append(
                    {
                        "start_pos": start,
                        "end_pos": end,
                        "content": content[start:end].strip(),
                        "title": self._extract_algorithm_title(
                            content[match.start() : match.end()]
                        ),
                        "importance_score": 0.95,  # High importance for algorithm blocks
                        "content_type": "algorithm",
                    }
                )

        return algorithm_blocks

    def _identify_concept_groups(self, content: str) -> List[Dict]:
        """Identify concept definition groups"""
        concept_groups = []

        # Concept definition patterns
        concept_patterns = [
            r"(?i)(definition|define|let|denote|given).*?(?=\n\s*\n|definition|define|let|denote|$)",
            r"(?i)(theorem|lemma|proposition|corollary).*?(?=\n\s*\n|theorem|lemma|proposition|corollary|$)",
            r"(?i)(notation|symbol|parameter).*?(?=\n\s*\n|notation|symbol|parameter|$)",
        ]

        for pattern in concept_patterns:
            matches = re.finditer(pattern, content, re.DOTALL)
            for match in matches:
                # Expand context
                start = max(0, match.start() - 200)
                end = min(len(content), match.end() + 300)

                concept_groups.append(
                    {
                        "start_pos": start,
                        "end_pos": end,
                        "content": content[start:end].strip(),
                        "title": self._extract_concept_title(
                            content[match.start() : match.end()]
                        ),
                        "importance_score": 0.85,
                        "content_type": "concept",
                    }
                )

        return concept_groups

    def _identify_formula_chains(self, content: str) -> List[Dict]:
        """Identify formula derivation chains"""
        formula_chains = []

        # Formula patterns
        formula_patterns = [
            r"\$\$.*?\$\$",  # Block-level mathematical formulas
            r"\$[^$]+\$",  # Inline mathematical formulas
            r"(?i)(equation|formula).*?(?=\n\s*\n|equation|formula|$)",
            r"(?i)(where|such that|given that).*?(?=\n\s*\n|where|such that|given that|$)",
        ]

        # Find dense formula regions
        formula_positions = []
        for pattern in formula_patterns:
            matches = re.finditer(pattern, content, re.DOTALL)
            for match in matches:
                formula_positions.append((match.start(), match.end()))

        # Merge nearby formulas into formula chains
        formula_positions.sort()
        if formula_positions:
            current_chain_start = formula_positions[0][0]
            current_chain_end = formula_positions[0][1]

            for start, end in formula_positions[1:]:
                if (
                    start - current_chain_end < 500
                ):  # Merge formulas within 500 characters
                    current_chain_end = end
                else:
                    # Save current chain
                    formula_chains.append(
                        {
                            "start_pos": max(0, current_chain_start - 200),
                            "end_pos": min(len(content), current_chain_end + 200),
                            "content": content[
                                max(0, current_chain_start - 200) : min(
                                    len(content), current_chain_end + 200
                                )
                            ].strip(),
                            "title": "Mathematical Formulation",
                            "importance_score": 0.9,
                            "content_type": "formula",
                        }
                    )
                    current_chain_start = start
                    current_chain_end = end

            # Add the last chain
            formula_chains.append(
                {
                    "start_pos": max(0, current_chain_start - 200),
                    "end_pos": min(len(content), current_chain_end + 200),
                    "content": content[
                        max(0, current_chain_start - 200) : min(
                            len(content), current_chain_end + 200
                        )
                    ].strip(),
                    "title": "Mathematical Formulation",
                    "importance_score": 0.9,
                    "content_type": "formula",
                }
            )

        return formula_chains

    def _merge_related_content_blocks(
        self,
        algorithm_blocks: List[Dict],
        concept_groups: List[Dict],
        formula_chains: List[Dict],
        content: str,
    ) -> List[Dict]:
        """Merge related content blocks to ensure integrity"""
        all_blocks = algorithm_blocks + concept_groups + formula_chains
        all_blocks.sort(key=lambda x: x["start_pos"])

        merged_blocks = []
        i = 0

        while i < len(all_blocks):
            current_block = all_blocks[i]

            # Check if can merge with the next block
            while i + 1 < len(all_blocks):
                next_block = all_blocks[i + 1]

                # If blocks are close or content related, merge them
                if next_block["start_pos"] - current_block[
                    "end_pos"
                ] < 300 or self._are_blocks_related(current_block, next_block):
                    # Merge blocks
                    merged_content = content[
                        current_block["start_pos"] : next_block["end_pos"]
                    ]
                    current_block = {
                        "start_pos": current_block["start_pos"],
                        "end_pos": next_block["end_pos"],
                        "content": merged_content.strip(),
                        "title": f"{current_block['title']} & {next_block['title']}",
                        "importance_score": max(
                            current_block["importance_score"],
                            next_block["importance_score"],
                        ),
                        "content_type": "merged",
                    }
                    i += 1
                else:
                    break

            merged_blocks.append(current_block)
            i += 1

        return merged_blocks

    def _are_blocks_related(self, block1: Dict, block2: Dict) -> bool:
        """Determine if two content blocks are related"""
        # Check content type associations
        related_types = [
            ("algorithm", "formula"),
            ("concept", "algorithm"),
            ("formula", "concept"),
        ]

        for type1, type2 in related_types:
            if (
                block1["content_type"] == type1 and block2["content_type"] == type2
            ) or (block1["content_type"] == type2 and block2["content_type"] == type1):
                return True

        return False

    def _extract_algorithm_title(self, text: str) -> str:
        """Extract title from algorithm text"""
        lines = text.split("\n")[:3]  # First 3 lines
        for line in lines:
            line = line.strip()
            if line and len(line) < 100:  # Reasonable title length
                # Clean title
                title = re.sub(r"[^\w\s-]", "", line)
                if title:
                    return title[:50]  # Limit title length
        return "Algorithm Block"

    def _extract_concept_title(self, text: str) -> str:
        """Extract title from concept text"""
        lines = text.split("\n")[:2]
        for line in lines:
            line = line.strip()
            if line and len(line) < 80:
                title = re.sub(r"[^\w\s-]", "", line)
                if title:
                    return title[:50]
        return "Concept Definition"

    def _create_enhanced_segment(
        self,
        content: str,
        title: str,
        start_pos: int,
        end_pos: int,
        importance_score: float,
        content_type: str,
    ) -> DocumentSegment:
        """Create enhanced document segment"""
        # Generate unique ID
        segment_id = hashlib.md5(
            f"{title}_{start_pos}_{end_pos}_{importance_score}".encode()
        ).hexdigest()[:8]

        # Extract keywords
        keywords = self._extract_enhanced_keywords(content, content_type)

        # Calculate enhanced relevance scores
        relevance_scores = self._calculate_enhanced_relevance_scores(
            content, content_type, importance_score
        )

        return DocumentSegment(
            id=segment_id,
            title=title,
            content=content,
            content_type=content_type,
            keywords=keywords,
            char_start=start_pos,
            char_end=end_pos,
            char_count=len(content),
            relevance_scores=relevance_scores,
            section_path=title,
        )

    def _extract_enhanced_keywords(self, content: str, content_type: str) -> List[str]:
        """Extract enhanced keywords based on content type"""
        words = re.findall(r"\b[a-zA-Z]{3,}\b", content.lower())

        # Adjust stopwords based on content type
        if content_type == "algorithm":
            algorithm_stopwords = {
                "step",
                "then",
                "else",
                "end",
                "begin",
                "start",
                "stop",
            }
            words = [w for w in words if w not in algorithm_stopwords]
        elif content_type == "formula":
            formula_keywords = ["equation", "formula", "where", "given", "such", "that"]
            words.extend(formula_keywords)

        # General stopwords
        general_stopwords = {
            "the",
            "and",
            "for",
            "are",
            "but",
            "not",
            "you",
            "all",
            "can",
            "her",
            "was",
            "one",
            "our",
            "had",
            "but",
            "have",
            "this",
            "that",
            "with",
            "from",
            "they",
            "she",
            "been",
            "were",
            "said",
            "each",
            "which",
            "their",
        }

        keywords = [w for w in set(words) if w not in general_stopwords and len(w) > 3]
        return keywords[:25]  # Increase keyword count

    def _calculate_enhanced_relevance_scores(
        self, content: str, content_type: str, importance_score: float
    ) -> Dict[str, float]:
        """Calculate enhanced relevance scores"""
        content_lower = content.lower()

        base_scores = {
            "concept_analysis": 0.5,
            "algorithm_extraction": 0.5,
            "code_planning": 0.5,
        }

        # Adjust base scores based on content type and importance
        if content_type == "algorithm":
            base_scores["algorithm_extraction"] = importance_score
            base_scores["code_planning"] = importance_score * 0.9
            base_scores["concept_analysis"] = importance_score * 0.7
        elif content_type == "concept":
            base_scores["concept_analysis"] = importance_score
            base_scores["algorithm_extraction"] = importance_score * 0.8
            base_scores["code_planning"] = importance_score * 0.6
        elif content_type == "formula":
            base_scores["algorithm_extraction"] = importance_score
            base_scores["concept_analysis"] = importance_score * 0.8
            base_scores["code_planning"] = importance_score * 0.9
        elif content_type == "merged":
            # Merged content is usually important
            base_scores = {k: importance_score * 0.95 for k in base_scores}

        # Additional bonus based on content density
        algorithm_indicators = ["algorithm", "method", "procedure", "step", "process"]
        concept_indicators = ["definition", "concept", "framework", "approach"]
        implementation_indicators = ["implementation", "code", "function", "design"]

        for query_type, indicators in [
            ("algorithm_extraction", algorithm_indicators),
            ("concept_analysis", concept_indicators),
            ("code_planning", implementation_indicators),
        ]:
            density_bonus = (
                sum(1 for indicator in indicators if indicator in content_lower) * 0.1
            )
            base_scores[query_type] = min(1.0, base_scores[query_type] + density_bonus)

        return base_scores

    # Placeholder methods - can be further implemented later
    def _identify_research_paper_sections(self, content: str) -> List[Dict]:
        """Identify research paper sections - simplified implementation"""
        # Temporarily use improved semantic detection
        return self._detect_academic_sections(content)

    def _enhance_section_with_context(self, section: Dict, content: str) -> Dict:
        """Add context to sections - simplified implementation"""
        return section

    def _identify_concept_implementation_pairs(self, content: str) -> List[Dict]:
        """Identify concept-implementation pairs - simplified implementation"""
        return []

    def _merge_concept_with_implementation(self, pair: Dict, content: str) -> Dict:
        """Merge concepts with implementation - simplified implementation"""
        return pair

    def _detect_semantic_boundaries(self, content: str) -> List[Dict]:
        """Detect semantic boundaries - based on paragraphs and logical separators"""
        boundaries = []

        # Split paragraphs by double line breaks
        paragraphs = content.split("\n\n")
        current_pos = 0

        for i, para in enumerate(paragraphs):
            if len(para.strip()) > 100:  # Valid paragraph
                # Analyze paragraph type
                content_type = self._classify_paragraph_type(para)
                importance_score = self._calculate_paragraph_importance(
                    para, content_type
                )

                boundaries.append(
                    {
                        "position": current_pos + len(para),
                        "suggested_title": self._extract_paragraph_title(para, i + 1),
                        "importance_score": importance_score,
                        "content_type": content_type,
                    }
                )

            current_pos += len(para) + 2  # +2 for \n\n

        return boundaries

    def _classify_paragraph_type(self, paragraph: str) -> str:
        """Classify paragraph type"""
        para_lower = paragraph.lower()

        if "algorithm" in para_lower or "procedure" in para_lower:
            return "algorithm"
        elif "formula" in para_lower or "$$" in paragraph:
            return "formula"
        elif any(
            word in para_lower for word in ["introduction", "overview", "abstract"]
        ):
            return "introduction"
        elif any(word in para_lower for word in ["conclusion", "summary", "result"]):
            return "conclusion"
        else:
            return "general"

    def _calculate_paragraph_importance(
        self, paragraph: str, content_type: str
    ) -> float:
        """Calculate paragraph importance"""
        if content_type == "algorithm":
            return 0.95
        elif content_type == "formula":
            return 0.9
        elif content_type == "introduction":
            return 0.85
        elif content_type == "conclusion":
            return 0.8
        else:
            return 0.7

    def _extract_paragraph_title(self, paragraph: str, index: int) -> str:
        """Extract paragraph title"""
        lines = paragraph.split("\n")
        for line in lines[:2]:
            if line.startswith("#"):
                return line.strip("# ")
            elif len(line) < 80 and line.strip():
                return line.strip()
        return f"Section {index}"

    def _calculate_optimal_chunk_size(self, content: str) -> int:
        """Calculate optimal chunk size"""
        # Dynamically adjust based on content complexity
        complexity = self.analyzer._calculate_concept_complexity(content)
        if complexity > 0.7:
            return 4000  # Complex content needs larger chunks
        elif complexity > 0.4:
            return 3000
        else:
            return 2000

    def _create_content_aware_chunks(self, content: str, chunk_size: int) -> List[Dict]:
        """Create content-aware chunks - simplified implementation"""
        chunks = []
        paragraphs = [p.strip() for p in content.split("\n\n") if p.strip()]

        current_chunk = []
        current_size = 0
        start_pos = 0

        for para in paragraphs:
            para_size = len(para)

            if current_size + para_size > chunk_size and current_chunk:
                chunk_content = "\n\n".join(current_chunk)
                chunks.append(
                    {
                        "content": chunk_content,
                        "title": f"Section {len(chunks) + 1}",
                        "start_pos": start_pos,
                        "end_pos": start_pos + len(chunk_content),
                        "importance_score": 0.7,
                        "content_type": "general",
                    }
                )

                current_chunk = [para]
                current_size = para_size
                start_pos += len(chunk_content) + 2
            else:
                current_chunk.append(para)
                current_size += para_size

        # Add the last chunk
        if current_chunk:
            chunk_content = "\n\n".join(current_chunk)
            chunks.append(
                {
                    "content": chunk_content,
                    "title": f"Section {len(chunks) + 1}",
                    "start_pos": start_pos,
                    "end_pos": start_pos + len(chunk_content),
                    "importance_score": 0.7,
                    "content_type": "general",
                }
            )

        return chunks

    def _create_segment(
        self, content: str, title: str, start_pos: int, end_pos: int
    ) -> DocumentSegment:
        """Create a DocumentSegment with metadata"""
        # Generate unique ID
        segment_id = hashlib.md5(f"{title}_{start_pos}_{end_pos}".encode()).hexdigest()[
            :8
        ]

        # Extract keywords from content
        keywords = self._extract_keywords(content)

        # Determine content type
        content_type = self._classify_content_type(title, content)

        # Calculate relevance scores for different query types
        relevance_scores = self._calculate_relevance_scores(content, content_type)

        return DocumentSegment(
            id=segment_id,
            title=title,
            content=content,
            content_type=content_type,
            keywords=keywords,
            char_start=start_pos,
            char_end=end_pos,
            char_count=len(content),
            relevance_scores=relevance_scores,
            section_path=title,  # Simplified for now
        )

    def _extract_keywords(self, content: str) -> List[str]:
        """Extract relevant keywords from content"""
        # Simple keyword extraction - could be enhanced with NLP
        words = re.findall(r"\b[a-zA-Z]{3,}\b", content.lower())

        # Remove common words
        stopwords = {
            "the",
            "and",
            "for",
            "are",
            "but",
            "not",
            "you",
            "all",
            "can",
            "her",
            "was",
            "one",
            "our",
            "had",
            "but",
            "have",
            "this",
            "that",
            "with",
            "from",
            "they",
            "she",
            "been",
            "were",
            "said",
            "each",
            "which",
            "their",
        }

        keywords = [w for w in set(words) if w not in stopwords and len(w) > 3]
        return keywords[:20]  # Top 20 keywords

    def _classify_content_type(self, title: str, content: str) -> str:
        """Classify the type of content based on title and content"""
        title_lower = title.lower()
        content_lower = content.lower()

        if any(
            word in title_lower for word in ["introduction", "abstract", "overview"]
        ):
            return "introduction"
        elif any(word in title_lower for word in ["method", "approach", "algorithm"]):
            return "methodology"
        elif any(
            word in title_lower for word in ["experiment", "evaluation", "result"]
        ):
            return "experiment"
        elif any(
            word in title_lower for word in ["conclusion", "discussion", "summary"]
        ):
            return "conclusion"
        elif any(word in title_lower for word in ["reference", "bibliography"]):
            return "references"
        elif "algorithm" in content_lower or "procedure" in content_lower:
            return "algorithm"
        else:
            return "general"

    def _calculate_relevance_scores(
        self, content: str, content_type: str
    ) -> Dict[str, float]:
        """Calculate relevance scores for different query types"""
        content_lower = content.lower()

        scores = {
            "concept_analysis": 0.5,
            "algorithm_extraction": 0.5,
            "code_planning": 0.5,
        }

        # Concept analysis relevance
        concept_indicators = [
            "introduction",
            "overview",
            "architecture",
            "system",
            "framework",
            "concept",
            "approach",
        ]
        concept_score = sum(
            1 for indicator in concept_indicators if indicator in content_lower
        ) / len(concept_indicators)
        scores["concept_analysis"] = min(
            1.0, concept_score + (0.8 if content_type == "introduction" else 0)
        )

        # Algorithm extraction relevance
        algorithm_indicators = [
            "algorithm",
            "method",
            "procedure",
            "formula",
            "equation",
            "step",
            "process",
        ]
        algorithm_score = sum(
            1 for indicator in algorithm_indicators if indicator in content_lower
        ) / len(algorithm_indicators)
        scores["algorithm_extraction"] = min(
            1.0, algorithm_score + (0.9 if content_type == "methodology" else 0)
        )

        # Code planning relevance
        code_indicators = [
            "implementation",
            "code",
            "function",
            "class",
            "module",
            "structure",
            "design",
        ]
        code_score = sum(
            1 for indicator in code_indicators if indicator in content_lower
        ) / len(code_indicators)
        scores["code_planning"] = min(
            1.0,
            code_score + (0.7 if content_type in ["methodology", "algorithm"] else 0),
        )

        return scores


# Global variables
DOCUMENT_INDEXES: Dict[str, DocumentIndex] = {}
segmenter = DocumentSegmenter()


def get_segments_dir(paper_dir: str) -> str:
    """Get the segments directory path"""
    return os.path.join(paper_dir, "document_segments")


def ensure_segments_dir_exists(segments_dir: str):
    """Ensure segments directory exists"""
    os.makedirs(segments_dir, exist_ok=True)


def _build_fallback_segments(
    content: str, segmenter: "DocumentSegmenter"
) -> Tuple[List[DocumentSegment], str]:
    """Guarantee a usable segmentation result for downstream planning."""
    fallback_strategies = [
        ("header_fallback", segmenter._segment_by_headers),
        ("academic_fallback", segmenter._segment_academic_paper),
        ("semantic_chunk_fallback", segmenter._segment_by_enhanced_semantic_chunks),
        ("content_aware_fallback", segmenter._segment_content_aware),
        ("paragraph_fallback", segmenter._segment_by_paragraphs),
    ]

    for strategy_name, strategy_fn in fallback_strategies:
        try:
            segments = strategy_fn(content)
        except Exception as exc:
            logger.warning(f"Segmentation fallback {strategy_name} failed: {exc}")
            continue

        usable_segments = [
            segment
            for segment in segments
            if (segment.content or "").strip() and segment.char_count > 0
        ]
        if usable_segments:
            return usable_segments, strategy_name

    raise ValueError("All segmentation strategies produced zero usable segments")


@mcp.tool()
async def analyze_and_segment_document(
    paper_dir: str, force_refresh: bool = False
) -> str:
    """
    Analyze document structure and create intelligent segments

    Args:
        paper_dir: Path to the paper directory
        force_refresh: Whether to force re-analysis even if segments exist

    Returns:
        JSON string with segmentation results
    """
    try:
        # Find markdown file in paper directory
        md_files = [f for f in os.listdir(paper_dir) if f.endswith(".md")]
        if not md_files:
            return json.dumps(
                {
                    "status": "error",
                    "message": f"No markdown file found in {paper_dir}",
                },
                ensure_ascii=False,
                indent=2,
            )

        md_file_path = os.path.join(paper_dir, md_files[0])
        segments_dir = get_segments_dir(paper_dir)
        index_file_path = os.path.join(segments_dir, "document_index.json")

        # Check if analysis already exists and is recent
        if not force_refresh and os.path.exists(index_file_path):
            try:
                with open(index_file_path, "r", encoding="utf-8") as f:
                    existing_index = json.load(f)

                    # Compatibility handling: ensure segments data structure is correct
                    if "segments" in existing_index:
                        segments_data = []
                        for seg_data in existing_index["segments"]:
                            # Ensure all required fields exist
                            segment_dict = dict(seg_data)

                            if "content_type" not in segment_dict:
                                segment_dict["content_type"] = "general"
                            if "keywords" not in segment_dict:
                                segment_dict["keywords"] = []
                            if "relevance_scores" not in segment_dict:
                                segment_dict["relevance_scores"] = {
                                    "concept_analysis": 0.5,
                                    "algorithm_extraction": 0.5,
                                    "code_planning": 0.5,
                                }
                            if "section_path" not in segment_dict:
                                segment_dict["section_path"] = segment_dict.get(
                                    "title", "Unknown"
                                )

                            segments_data.append(DocumentSegment(**segment_dict))

                        existing_index["segments"] = segments_data

                    DOCUMENT_INDEXES[paper_dir] = DocumentIndex(**existing_index)
                return json.dumps(
                    {
                        "status": "success",
                        "message": "Using existing document analysis",
                        "segments_dir": segments_dir,
                        "total_segments": existing_index["total_segments"],
                    },
                    ensure_ascii=False,
                    indent=2,
                )

            except Exception as e:
                logger.error(f"Failed to load existing index: {e}")
                logger.info("Will perform fresh analysis instead")
                # Remove corrupted index file and continue with new analysis
                try:
                    os.remove(index_file_path)
                except Exception as e:
                    pass

        # Read document content
        with open(md_file_path, "r", encoding="utf-8") as f:
            content = f.read()

        # Analyze document
        analyzer = DocumentAnalyzer()
        doc_type, confidence = analyzer.analyze_document_type(content)
        strategy = analyzer.detect_segmentation_strategy(content, doc_type)

        # Create segments
        segments = segmenter.segment_document(content, strategy)
        if not segments:
            logger.warning(
                f"Primary segmentation strategy '{strategy}' produced zero segments; applying deterministic fallback"
            )
            segments, strategy = _build_fallback_segments(content, segmenter)

        # Create document index
        document_index = DocumentIndex(
            document_path=md_file_path,
            document_type=doc_type,
            segmentation_strategy=strategy,
            total_segments=len(segments),
            total_chars=len(content),
            segments=segments,
            created_at=datetime.now().isoformat(),
        )

        # Save segments
        ensure_segments_dir_exists(segments_dir)

        # Save document index
        with open(index_file_path, "w", encoding="utf-8") as f:
            json.dump(
                asdict(document_index), f, ensure_ascii=False, indent=2, default=str
            )

        # Save individual segment files for fallback
        for segment in segments:
            segment_file_path = os.path.join(segments_dir, f"segment_{segment.id}.md")
            with open(segment_file_path, "w", encoding="utf-8") as f:
                f.write(f"# {segment.title}\n\n")
                f.write(f"**Content Type:** {segment.content_type}\n")
                f.write(f"**Keywords:** {', '.join(segment.keywords[:10])}\n\n")
                f.write(segment.content)

        # Store in memory
        DOCUMENT_INDEXES[paper_dir] = document_index

        logger.info(
            f"Document segmentation completed: {len(segments)} segments created"
        )

        return json.dumps(
            {
                "status": "success",
                "message": f"Document analysis completed with {strategy} strategy",
                "document_type": doc_type,
                "segmentation_strategy": strategy,
                "segments_dir": segments_dir,
                "total_segments": len(segments),
                "total_chars": len(content),
            },
            ensure_ascii=False,
            indent=2,
        )

    except Exception as e:
        logger.error(f"Error in analyze_and_segment_document: {e}")
        return json.dumps(
            {"status": "error", "message": f"Failed to analyze document: {str(e)}"},
            ensure_ascii=False,
            indent=2,
        )


@mcp.tool()
async def read_document_segments(
    paper_dir: str,
    query_type: str,
    keywords: List[str] = None,
    max_segments: int = 3,
    max_total_chars: int = None,
) -> str:
    """
    Intelligently retrieve relevant document segments based on query type

    Args:
        paper_dir: Path to the paper directory
        query_type: Type of query - "concept_analysis", "algorithm_extraction", or "code_planning"
        keywords: Optional list of keywords to search for
        max_segments: Maximum number of segments to return
        max_total_chars: Maximum total characters to return

    Returns:
        JSON string with selected segments
    """
    try:
        # Ensure document is analyzed
        if paper_dir not in DOCUMENT_INDEXES:
            segments_dir = get_segments_dir(paper_dir)
            index_file_path = os.path.join(segments_dir, "document_index.json")

            if os.path.exists(index_file_path):
                with open(index_file_path, "r", encoding="utf-8") as f:
                    index_data = json.load(f)
                    # Convert dict back to DocumentIndex with backward compatibility
                    segments_data = []
                    for seg_data in index_data.get("segments", []):
                        # Ensure all required fields exist, provide default values
                        segment_dict = dict(seg_data)

                        # Compatibility handling: add missing fields
                        if "content_type" not in segment_dict:
                            segment_dict["content_type"] = "general"
                        if "keywords" not in segment_dict:
                            segment_dict["keywords"] = []
                        if "relevance_scores" not in segment_dict:
                            segment_dict["relevance_scores"] = {
                                "concept_analysis": 0.5,
                                "algorithm_extraction": 0.5,
                                "code_planning": 0.5,
                            }
                        if "section_path" not in segment_dict:
                            segment_dict["section_path"] = segment_dict.get(
                                "title", "Unknown"
                            )

                        segment = DocumentSegment(**segment_dict)
                        segments_data.append(segment)

                    index_data["segments"] = segments_data
                    DOCUMENT_INDEXES[paper_dir] = DocumentIndex(**index_data)
            else:
                # Auto-analyze if not found
                await analyze_and_segment_document(paper_dir)

        document_index = DOCUMENT_INDEXES[paper_dir]

        # Dynamically calculate character limit
        if max_total_chars is None:
            max_total_chars = _calculate_adaptive_char_limit(document_index, query_type)

        # Score and rank segments with enhanced algorithm
        scored_segments = []
        for segment in document_index.segments:
            # Base relevance score (already enhanced in new system)
            relevance_score = segment.relevance_scores.get(query_type, 0.5)

            # Enhanced keyword matching with position weighting
            if keywords:
                keyword_score = _calculate_enhanced_keyword_score(segment, keywords)
                relevance_score += keyword_score

            # Content completeness bonus
            completeness_bonus = _calculate_completeness_bonus(segment, document_index)
            relevance_score += completeness_bonus

            scored_segments.append((segment, relevance_score))

        # Sort by enhanced relevance score
        scored_segments.sort(key=lambda x: x[1], reverse=True)

        # Intelligent segment selection with integrity preservation
        selected_segments = _select_segments_with_integrity(
            scored_segments, max_segments, max_total_chars, query_type
        )

        total_chars = sum(seg["char_count"] for seg in selected_segments)

        logger.info(
            f"Selected {len(selected_segments)} segments for {query_type} query"
        )

        return json.dumps(
            {
                "status": "success",
                "query_type": query_type,
                "keywords": keywords or [],
                "total_segments_available": len(document_index.segments),
                "segments_selected": len(selected_segments),
                "total_chars": total_chars,
                "max_chars_used": max_total_chars,
                "segments": selected_segments,
            },
            ensure_ascii=False,
            indent=2,
        )

    except Exception as e:
        logger.error(f"Error in read_document_segments: {e}")
        return json.dumps(
            {
                "status": "error",
                "message": f"Failed to read document segments: {str(e)}",
            },
            ensure_ascii=False,
            indent=2,
        )


@mcp.tool()
async def get_document_overview(paper_dir: str) -> str:
    """
    Get overview of document structure and available segments

    Args:
        paper_dir: Path to the paper directory

    Returns:
        JSON string with document overview
    """
    try:
        # Ensure document is analyzed
        if paper_dir not in DOCUMENT_INDEXES:
            await analyze_and_segment_document(paper_dir)

        document_index = DOCUMENT_INDEXES[paper_dir]

        # Create overview
        segment_summaries = []
        for segment in document_index.segments:
            segment_summaries.append(
                {
                    "id": segment.id,
                    "title": segment.title,
                    "content_type": segment.content_type,
                    "char_count": segment.char_count,
                    "keywords": segment.keywords[:5],  # Top 5 keywords
                    "relevance_scores": segment.relevance_scores,
                }
            )

        return json.dumps(
            {
                "status": "success",
                "document_path": document_index.document_path,
                "document_type": document_index.document_type,
                "segmentation_strategy": document_index.segmentation_strategy,
                "total_segments": document_index.total_segments,
                "total_chars": document_index.total_chars,
                "created_at": document_index.created_at,
                "segments_overview": segment_summaries,
            },
            ensure_ascii=False,
            indent=2,
        )

    except Exception as e:
        logger.error(f"Error in get_document_overview: {e}")
        return json.dumps(
            {
                "status": "error",
                "message": f"Failed to get document overview: {str(e)}",
            },
            ensure_ascii=False,
            indent=2,
        )


# =============== Enhanced retrieval system helper methods ===============


def _calculate_adaptive_char_limit(
    document_index: DocumentIndex, query_type: str
) -> int:
    """Dynamically calculate character limit based on document complexity and query type"""
    base_limit = 6000

    # Adjust based on document type
    if document_index.document_type == "research_paper":
        base_limit = 10000
    elif document_index.document_type == "algorithm_focused":
        base_limit = 12000
    elif document_index.segmentation_strategy == "algorithm_preserve_integrity":
        base_limit = 15000

    # Adjust based on query type
    query_multipliers = {
        "algorithm_extraction": 1.5,  # Algorithms need more context
        "concept_analysis": 1.2,
        "code_planning": 1.3,
    }

    multiplier = query_multipliers.get(query_type, 1.0)
    return int(base_limit * multiplier)


def _calculate_enhanced_keyword_score(
    segment: DocumentSegment, keywords: List[str]
) -> float:
    """Calculate enhanced keyword matching score"""
    score = 0.0
    content_lower = segment.content.lower()
    title_lower = segment.title.lower()

    for keyword in keywords:
        keyword_lower = keyword.lower()

        # Title matching has higher weight
        if keyword_lower in title_lower:
            score += 0.3

        # Content matching
        content_matches = content_lower.count(keyword_lower)
        if content_matches > 0:
            # Consider term frequency and position
            frequency_score = min(0.2, content_matches * 0.05)

            # Check if in important position (first 25% of content)
            early_content = content_lower[: len(content_lower) // 4]
            if keyword_lower in early_content:
                frequency_score += 0.1

            score += frequency_score

    return min(0.6, score)  # Limit maximum bonus


def _calculate_completeness_bonus(
    segment: DocumentSegment, document_index: DocumentIndex
) -> float:
    """Calculate content completeness bonus"""
    bonus = 0.0

    # Completeness bonus for algorithm and formula content
    if segment.content_type in ["algorithm", "formula", "merged"]:
        bonus += 0.2

    # Long paragraphs usually contain more complete information
    if segment.char_count > 2000:
        bonus += 0.1
    elif segment.char_count > 4000:
        bonus += 0.15

    # High importance paragraph bonus
    if segment.relevance_scores.get("algorithm_extraction", 0) > 0.8:
        bonus += 0.1

    return min(0.3, bonus)


def _select_segments_with_integrity(
    scored_segments: List[Tuple],
    max_segments: int,
    max_total_chars: int,
    query_type: str,
) -> List[Dict]:
    """Intelligently select segments while maintaining content integrity"""
    selected_segments = []
    total_chars = 0

    # First select the highest scoring segments
    for segment, score in scored_segments:
        if len(selected_segments) >= max_segments:
            break

        if total_chars + segment.char_count <= max_total_chars:
            selected_segments.append(
                {
                    "id": segment.id,
                    "title": segment.title,
                    "content": segment.content,
                    "content_type": segment.content_type,
                    "relevance_score": score,
                    "char_count": segment.char_count,
                }
            )
            total_chars += segment.char_count
        elif len(selected_segments) == 0:
            # If the first segment exceeds the limit, truncate but preserve it
            truncated_content = (
                segment.content[: max_total_chars - 200]
                + "\n\n[Content truncated for length...]"
            )
            selected_segments.append(
                {
                    "id": segment.id,
                    "title": segment.title,
                    "content": truncated_content,
                    "content_type": segment.content_type,
                    "relevance_score": score,
                    "char_count": len(truncated_content),
                }
            )
            break

    # If there's remaining space, try to add relevant small segments
    remaining_chars = max_total_chars - total_chars
    if remaining_chars > 500 and len(selected_segments) < max_segments:
        for segment, score in scored_segments[len(selected_segments) :]:
            if (
                segment.char_count <= remaining_chars
                and len(selected_segments) < max_segments
            ):
                selected_segments.append(
                    {
                        "id": segment.id,
                        "title": segment.title,
                        "content": segment.content,
                        "content_type": segment.content_type,
                        "relevance_score": score,
                        "char_count": segment.char_count,
                    }
                )
                remaining_chars -= segment.char_count

    return selected_segments


if __name__ == "__main__":
    # Run the MCP server
    mcp.run()
