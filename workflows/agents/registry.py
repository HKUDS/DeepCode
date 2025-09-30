from mcp_agent.agents.agent import Agent
from mcp_agent.workflows.parallel.parallel_llm import ParallelLLM

from prompts.code_prompts import PAPER_INPUT_ANALYZER_PROMPT, PAPER_DOWNLOADER_PROMPT, PAPER_REFERENCE_ANALYZER_PROMPT, \
    GITHUB_DOWNLOAD_PROMPT, PAPER_ALGORITHM_ANALYSIS_PROMPT, CODE_PLANNING_PROMPT
from prompts.router_prompt import PAPER_CONCEPT_ANALYSIS_PROMPT, STRUCTURE_GENERATOR_PROMPT, CODE_IMPLEMENTATION_PROMPT
from tools.code_indexer import get_preferred_llm_class

analyzer_agent = Agent(
            name="ResearchAnalyzerAgent",
            instruction=PAPER_INPUT_ANALYZER_PROMPT,
            server_names=[],
        )
processor_agent = Agent(
        name="ResourceProcessorAgent",
        instruction=PAPER_DOWNLOADER_PROMPT,
        server_names=["filesystem", "file-downloader"],
    )
reference_analysis_agent = Agent(
        name="ReferenceAnalysisAgent",
        instruction=PAPER_REFERENCE_ANALYZER_PROMPT,
        server_names=["filesystem", "fetch"],
    )
github_download_agent = Agent(
        name="GithubRepositoryDownloadAgent",
        instruction =GITHUB_DOWNLOAD_PROMPT,
        server_names=[],
)
concept_analysis_agent = Agent(
        name="ConceptAnalysisAgent",
        instruction=PAPER_CONCEPT_ANALYSIS_PROMPT,
        server_names=["filesystem"],
    )
algorithm_analysis_agent = Agent(
        name="AlgorithmAnalysisAgent",
        instruction=PAPER_ALGORITHM_ANALYSIS_PROMPT,
        server_names=["filesystem"],
    )
code_planner_agent = Agent(
        name="CodePlannerAgent",
        instruction=CODE_PLANNING_PROMPT,
        server_names=[],
    )

code_aggregator_agent = ParallelLLM(
        fan_in_agent=code_planner_agent,
        fan_out_agents=[concept_analysis_agent, algorithm_analysis_agent],
        llm_factory=get_preferred_llm_class(),
    )
structure_agent = Agent(
            name="StructureGeneratorAgent",
            instruction=STRUCTURE_GENERATOR_PROMPT,
            server_names=["command-executor"],
        )
code_implement_agent = Agent(
            name="CodeImplementAgent",
            instruction=CODE_IMPLEMENTATION_PROMPT,
            server_names=['']
)
registry = {
    "ResourceProcessorAgent":processor_agent,
    "ResearchAnalyzerAgent":analyzer_agent,
    "ReferenceAnalysisAgent":reference_analysis_agent,
    "GithubRepositoryDownloadAgent":github_download_agent,
    "ConceptAnalysisAgent":concept_analysis_agent,
    "CodePlannerAgent":code_planner_agent,
    "StructureGeneratorAgent":structure_agent,
    "AlgorithmAnalysisAgent":algorithm_analysis_agent,
    "CodePlannerAgent":code_planner_agent,
    "StructureGeneratorAgent":structure_agent,
    "CodeImplementAgent":code_implement_agent,

}
