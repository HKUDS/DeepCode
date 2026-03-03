"""
Document Segmentation Agent

A lightweight agent that coordinates with the document segmentation MCP server
to analyze document structure and prepare segments for other agents.
"""

import os
import logging
from typing import Dict, Any, Optional

from mcp_agent.agents.agent import Agent
from utils.llm_utils import get_preferred_llm_class


class DocumentSegmentationAgent:
    """
    Intelligent document segmentation agent with semantic analysis capabilities.

    This enhanced agent provides:
    1. **Semantic Document Classification**: Content-based document type identification
    2. **Adaptive Segmentation Strategy**: Algorithm integrity and semantic coherence preservation
    3. **Planning Agent Optimization**: Segment preparation specifically optimized for downstream agents
    4. **Quality Intelligence Validation**: Advanced metrics for completeness and technical accuracy
    5. **Algorithm Completeness Protection**: Ensures critical algorithms and formulas remain intact

    Key improvements over traditional segmentation:
    - Semantic content analysis vs mechanical structure splitting
    - Dynamic character limits based on content complexity
    - Enhanced relevance scoring for planning agents
    - Algorithm and formula integrity preservation
    - Content type-aware segmentation strategies
    """

    def __init__(self, logger: Optional[logging.Logger] = None):
        self.logger = logger or self._create_default_logger()
        self.mcp_agent = None

    def _create_default_logger(self) -> logging.Logger:
        """Create default logger if none provided"""
        logger = logging.getLogger(f"{__name__}.DocumentSegmentationAgent")
        logger.setLevel(logging.INFO)
        return logger

    async def __aenter__(self):
        """Async context manager entry"""
        await self.initialize()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit"""
        await self.cleanup()

    async def initialize(self):
        """Initialize the MCP agent connection"""
        try:
            self.mcp_agent = Agent(
                name="DocumentSegmentationCoordinator",
                instruction="""You are an intelligent document segmentation coordinator that leverages advanced semantic analysis for optimal document processing.

Your enhanced capabilities include:
1. **Semantic Content Analysis**: Coordinate intelligent document type classification based on content semantics rather than structural patterns
2. **Algorithm Integrity Protection**: Ensure algorithm blocks, formulas, and related content maintain logical coherence
3. **Adaptive Segmentation Strategy**: Select optimal segmentation approaches (semantic_research_focused, algorithm_preserve_integrity, concept_implementation_hybrid, etc.)
4. **Quality Intelligence Validation**: Assess segmentation quality using enhanced metrics for completeness, relevance, and technical accuracy
5. **Planning Agent Optimization**: Ensure segments are specifically optimized for ConceptAnalysisAgent, AlgorithmAnalysisAgent, and CodePlannerAgent needs

**Key Principles**:
- Prioritize content semantics over mechanical structure
- Preserve algorithm and formula completeness
- Optimize for downstream agent token efficiency
- Ensure technical content integrity
- Provide actionable quality assessments

Use the enhanced document-segmentation tools to deliver superior segmentation results that significantly improve planning agent performance.""",
                server_names=["document-segmentation"],
            )

            # Initialize the agent context
            await self.mcp_agent.__aenter__()

            # Attach LLM
            self.llm = await self.mcp_agent.attach_llm(get_preferred_llm_class())

            self.logger.info("DocumentSegmentationAgent initialized successfully")

        except Exception as e:
            self.logger.error(f"Failed to initialize DocumentSegmentationAgent: {e}")
            raise

    async def cleanup(self):
        """Cleanup resources"""
        if self.mcp_agent:
            try:
                await self.mcp_agent.__aexit__(None, None, None)
            except Exception as e:
                self.logger.warning(f"Error during cleanup: {e}")

    async def analyze_and_prepare_document(
        self, paper_dir: str, force_refresh: bool = False
    ) -> Dict[str, Any]:
        """
        Perform intelligent semantic analysis and create optimized document segments.

        This method coordinates with the enhanced document segmentation server to:
        - Classify document type using semantic content analysis
        - Select optimal segmentation strategy (semantic_research_focused, algorithm_preserve_integrity, etc.)
        - Preserve algorithm and formula integrity
        - Optimize segments for downstream planning agents

        Args:
            paper_dir: Path to the paper directory
            force_refresh: Whether to force re-analysis with latest algorithms

        Returns:
            Dict containing enhanced analysis results and intelligent segment information
        """
        try:
            self.logger.info(f"Starting document analysis for: {paper_dir}")

            # Check if markdown file exists
            md_files = [f for f in os.listdir(paper_dir) if f.endswith(".md")]
            if not md_files:
                raise ValueError(f"No markdown file found in {paper_dir}")

            # Use the enhanced document segmentation tool
            message = f"""Please perform intelligent semantic analysis and segmentation for the document in directory: {paper_dir}

Use the analyze_and_segment_document tool with these parameters:
- paper_dir: {paper_dir}
- force_refresh: {force_refresh}

**Focus on these enhanced objectives**:
1. **Semantic Document Classification**: Identify document type using content semantics (research_paper, algorithm_focused, technical_doc, etc.)
2. **Intelligent Segmentation Strategy**: Select the optimal strategy based on content analysis:
   - `semantic_research_focused` for research papers with high algorithm density
   - `algorithm_preserve_integrity` for algorithm-heavy documents
   - `concept_implementation_hybrid` for mixed concept/implementation content
3. **Algorithm Completeness**: Ensure algorithm blocks, formulas, and related descriptions remain logically connected
4. **Planning Agent Optimization**: Create segments that maximize effectiveness for ConceptAnalysisAgent, AlgorithmAnalysisAgent, and CodePlannerAgent

After segmentation, get a document overview and provide:
- Quality assessment of semantic segmentation approach
- Algorithm/formula integrity verification
- Recommendations for planning agent optimization
- Technical content completeness evaluation"""

            result = await self.llm.generate_str(message=message)

            self.logger.info("Document analysis completed successfully")

            # Parse the result and return structured information
            return {
                "status": "success",
                "paper_dir": paper_dir,
                "analysis_result": result,
                "segments_available": True,
            }

        except Exception as e:
            self.logger.error(f"Error in document analysis: {e}")
            return {
                "status": "error",
                "paper_dir": paper_dir,
                "error_message": str(e),
                "segments_available": False,
            }

    async def get_document_overview(self, paper_dir: str) -> Dict[str, Any]:
        """
        Get overview of document structure and segments.

        Args:
            paper_dir: Path to the paper directory

        Returns:
            Dict containing document overview information
        """
        try:
            message = f"""Please provide an intelligent overview of the enhanced document segmentation for: {paper_dir}

Use the get_document_overview tool to retrieve:
- **Semantic Document Classification**: Document type and confidence score
- **Adaptive Segmentation Strategy**: Strategy used and reasoning
- **Segment Intelligence**: Total segments with enhanced metadata
- **Content Type Distribution**: Breakdown by algorithm, concept, formula, implementation content
- **Quality Intelligence Assessment**: Completeness, coherence, and planning agent optimization

Provide a comprehensive analysis focusing on:
1. Semantic vs structural segmentation quality
2. Algorithm and formula integrity preservation
3. Segment relevance for downstream planning agents
4. Technical content distribution and completeness"""

            result = await self.llm.generate_str(message=message)

            return {
                "status": "success",
                "paper_dir": paper_dir,
                "overview_result": result,
            }

        except Exception as e:
            self.logger.error(f"Error getting document overview: {e}")
            return {"status": "error", "paper_dir": paper_dir, "error_message": str(e)}

    async def validate_segmentation_quality(self, paper_dir: str) -> Dict[str, Any]:
        """
        Validate the quality of document segmentation.

        Args:
            paper_dir: Path to the paper directory

        Returns:
            Dict containing validation results
        """
        try:
            # Get overview first
            overview_result = await self.get_document_overview(paper_dir)

            if overview_result["status"] != "success":
                return overview_result

            # Analyze enhanced segmentation quality
            message = f"""Based on the intelligent document overview for {paper_dir}, please evaluate the enhanced segmentation quality using advanced criteria.

**Enhanced Quality Assessment Factors**:
1. **Semantic Coherence**: Do segments maintain logical content boundaries vs mechanical structural splits?
2. **Algorithm Integrity**: Are algorithm blocks, formulas, and related explanations kept together?
3. **Content Type Optimization**: Are different content types (algorithm, concept, formula, implementation) properly identified and scored?
4. **Planning Agent Effectiveness**: Will ConceptAnalysisAgent, AlgorithmAnalysisAgent, and CodePlannerAgent receive optimal information?
5. **Dynamic Sizing**: Are segments adaptively sized based on content complexity rather than fixed limits?
6. **Technical Completeness**: Are critical technical details preserved without fragmentation?

**Provide specific recommendations for**:
- Semantic segmentation improvements
- Algorithm/formula integrity enhancements
- Planning agent optimization opportunities
- Content distribution balance adjustments"""

            validation_result = await self.llm.generate_str(message=message)

            return {
                "status": "success",
                "paper_dir": paper_dir,
                "validation_result": validation_result,
                "overview_data": overview_result,
            }

        except Exception as e:
            self.logger.error(f"Error validating segmentation quality: {e}")
            return {"status": "error", "paper_dir": paper_dir, "error_message": str(e)}


async def run_document_segmentation_analysis(
    paper_dir: str, logger: Optional[logging.Logger] = None, force_refresh: bool = False
) -> Dict[str, Any]:
    """
    Convenience function to run document segmentation analysis.

    Args:
        paper_dir: Path to the paper directory
        logger: Optional logger instance
        force_refresh: Whether to force re-analysis

    Returns:
        Dict containing analysis results
    """
    async with DocumentSegmentationAgent(logger=logger) as agent:
        # Analyze and prepare document
        analysis_result = await agent.analyze_and_prepare_document(
            paper_dir, force_refresh=force_refresh
        )

        if analysis_result["status"] == "success":
            # Validate segmentation quality
            validation_result = await agent.validate_segmentation_quality(paper_dir)
            analysis_result["validation"] = validation_result

        return analysis_result


# Utility function for integration with existing workflow
async def prepare_document_segments(
    paper_dir: str, logger: Optional[logging.Logger] = None
) -> Dict[str, Any]:
    """
    Prepare intelligent document segments optimized for planning agents.

    This enhanced function leverages semantic analysis to create segments that:
    - Preserve algorithm and formula integrity
    - Optimize for ConceptAnalysisAgent, AlgorithmAnalysisAgent, and CodePlannerAgent
    - Use adaptive character limits based on content complexity
    - Maintain technical content completeness

    Called from the orchestration engine (Phase 3.5) to prepare documents
    before the planning phase with superior segmentation quality.

    Args:
        paper_dir: Path to the paper directory containing markdown file
        logger: Optional logger instance for tracking

    Returns:
        Dict containing enhanced preparation results and intelligent metadata
    """
    try:
        logger = logger or logging.getLogger(__name__)
        logger.info(f"Preparing document segments for: {paper_dir}")

        # Run analysis
        result = await run_document_segmentation_analysis(
            paper_dir=paper_dir,
            logger=logger,
            force_refresh=False,  # Use cached analysis if available
        )

        if result["status"] == "success":
            logger.info("Document segments prepared successfully")

            # Create metadata for downstream agents
            segments_dir = os.path.join(paper_dir, "document_segments")

            return {
                "status": "success",
                "paper_dir": paper_dir,
                "segments_dir": segments_dir,
                "segments_ready": True,
                "analysis_summary": result.get("analysis_result", ""),
                "validation_summary": result.get("validation", {}).get(
                    "validation_result", ""
                ),
            }
        else:
            logger.error(
                f"Document segmentation failed: {result.get('error_message', 'Unknown error')}"
            )
            return {
                "status": "error",
                "paper_dir": paper_dir,
                "segments_ready": False,
                "error_message": result.get(
                    "error_message", "Document segmentation failed"
                ),
            }

    except Exception as e:
        logger.error(f"Error preparing document segments: {e}")
        return {
            "status": "error",
            "paper_dir": paper_dir,
            "segments_ready": False,
            "error_message": str(e),
        }
