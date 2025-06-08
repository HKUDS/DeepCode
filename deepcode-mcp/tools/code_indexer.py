"""
Code Indexer - Build relationships between existing codebase and target project structure

This tool analyzes existing repositories in code_base directory and creates intelligent
mappings to target project structure using LLM-powered analysis.

Features:
- Recursive file traversal
- LLM-powered code similarity analysis  
- JSON-based relationship storage
- Configurable matching strategies
- Progress tracking and error handling
"""

import os
import re
import json
import asyncio
from pathlib import Path
from typing import Dict, List, Any, Optional, Tuple
from dataclasses import dataclass, asdict
import logging
from datetime import datetime

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
    
    def __init__(self, code_base_path: str, target_structure: str, output_dir: str = "indexes"):
        self.code_base_path = Path(code_base_path)
        self.target_structure = target_structure
        self.output_dir = Path(output_dir)
        self.llm = None
        self.logger = self._setup_logger()
        
        # Create output directory if it doesn't exist
        self.output_dir.mkdir(exist_ok=True)
        
        # Supported file extensions for analysis
        self.supported_extensions = {
            '.py', '.js', '.ts', '.java', '.cpp', '.c', '.h', '.hpp',
            '.cs', '.php', '.rb', '.go', '.rs', '.scala', '.kt',
            '.swift', '.m', '.mm', '.r', '.matlab', '.sql', '.sh',
            '.bat', '.ps1', '.yaml', '.yml', '.json', '.xml', '.toml'
        }

    def _setup_logger(self) -> logging.Logger:
        """Setup logging configuration"""
        logger = logging.getLogger('CodeIndexer')
        logger.setLevel(logging.INFO)
        
        handler = logging.StreamHandler()
        formatter = logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
        )
        handler.setFormatter(formatter)
        logger.addHandler(handler)
        
        return logger

    async def llm_model_func(self, prompt, system_prompt="You are a code analysis expert. Provide precise, structured analysis of code relationships and similarities.", history_messages=[], keyword_extraction=False, **kwargs
    ) -> str:
        from lightrag.llm.openai import openai_complete_if_cache
        return await openai_complete_if_cache(
            "gpt-4o-mini",
            prompt,
            system_prompt=system_prompt,
            history_messages=history_messages,
            api_key="sk-ZDiJP6MOI3yOr6iL7vOOJ7ohwdhbbuL2jcZe3KDmYMq6nWQ2",
            base_url="https://api.nuwaapi.com/v1",
            **kwargs,
        )
    # async def llm_model_func(self, prompt: str, max_tokens: int = 4000) -> str:
    #     """
    #     LLM model function for making AI analysis calls
        
    #     Args:
    #         prompt: The analysis prompt to send to LLM
    #         max_tokens: Maximum tokens for response
            
    #     Returns:
    #         LLM response text
    #     """
    #     from lightrag.llm.openai import openai_complete_if_cache
    #     try:
    #         if self.llm is None:
    #             self.llm = lambda prompt, system_prompt=None, history_messages=[], **kwargs: openai_complete_if_cache(
    #                 "gpt-4o-mini",
    #                 prompt,
    #                 system_prompt=system_prompt,
    #                 history_messages=history_messages,
    #                 api_key="sk-ZDiJP6MOI3yOr6iL7vOOJ7ohwdhbbuL2jcZe3KDmYMq6nWQ2",
    #                 base_url="https://api.nuwaapi.com/v1",
    #                 **kwargs,
    #             ),
            
    #         # request_params = RequestParams(
    #         #     max_tokens=max_tokens,
    #         #     temperature=0.3,  # Lower temperature for more consistent analysis
    #         #     system_prompt="You are a code analysis expert. Provide precise, structured analysis of code relationships and similarities."
    #         # )
            
    #         print(prompt)
    #         response = await self.llm(
    #             prompt=prompt,
    #             system_prompt="You are a code analysis expert. Provide precise, structured analysis of code relationships and similarities."
    #         )
            
    #         return response
            
    #     except Exception as e:
    #         self.logger.error(f"LLM call failed: {e}")
    #         return f"Error in LLM analysis: {str(e)}"

    def get_all_repo_files(self, repo_path: Path) -> List[Path]:
        """Recursively get all supported files in a repository"""
        files = []
        
        try:
            for root, dirs, filenames in os.walk(repo_path):
                # Skip common non-code directories
                dirs[:] = [d for d in dirs if not d.startswith('.') and d not in 
                          {'__pycache__', 'node_modules', 'target', 'build', 'dist', 'venv', 'env'}]
                
                for filename in filenames:
                    file_path = Path(root) / filename
                    if file_path.suffix.lower() in self.supported_extensions:
                        files.append(file_path)
                        
        except Exception as e:
            self.logger.error(f"Error traversing {repo_path}: {e}")
            
        return files

    async def analyze_file_content(self, file_path: Path) -> FileSummary:
        """Analyze a single file and create summary"""
        try:
            with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                content = f.read()
            
            # Get file stats
            stats = file_path.stat()
            lines_of_code = len([line for line in content.split('\n') if line.strip()])
            
            # Create analysis prompt
            analysis_prompt = f"""
            Analyze this code file and provide a structured summary:
            
            File: {file_path.name}
            Content:
            ```
            {content[:3000]}{'...' if len(content) > 3000 else ''}
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
            
            # Get LLM analysis
            llm_response = await self.llm_model_func(analysis_prompt, max_tokens=1000)
            
            
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
                    "summary": "File analysis failed - JSON parsing error"
                }
            
            return FileSummary(
                file_path=str(file_path.relative_to(self.code_base_path)),
                file_type=analysis_data.get("file_type", "unknown"),
                main_functions=analysis_data.get("main_functions", []),
                key_concepts=analysis_data.get("key_concepts", []),
                dependencies=analysis_data.get("dependencies", []),
                summary=analysis_data.get("summary", "No summary available"),
                lines_of_code=lines_of_code,
                last_modified=datetime.fromtimestamp(stats.st_mtime).isoformat()
            )
            
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
                last_modified=""
            )

    async def find_relationships(self, file_summary: FileSummary) -> List[FileRelationship]:
        """Find relationships between a repo file and target structure"""
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
        
        Only include relationships with confidence > 0.3. Focus on concrete, actionable connections.
        """
        
        try:
            llm_response = await self.llm_model_func(relationship_prompt, max_tokens=1500)
            
            match = re.search(r"\{.*\}", llm_response, re.DOTALL)
            relationship_data = json.loads(match.group(0))
            
            relationships = []
            for rel_data in relationship_data.get("relationships", []):
                relationship = FileRelationship(
                    repo_file_path=file_summary.file_path,
                    target_file_path=rel_data.get("target_file_path", ""),
                    relationship_type=rel_data.get("relationship_type", "reference"),
                    confidence_score=float(rel_data.get("confidence_score", 0.0)),
                    helpful_aspects=rel_data.get("helpful_aspects", []),
                    potential_contributions=rel_data.get("potential_contributions", []),
                    usage_suggestions=rel_data.get("usage_suggestions", "")
                )
                relationships.append(relationship)
                
            return relationships
            
        except Exception as e:
            self.logger.error(f"Error finding relationships for {file_summary.file_path}: {e}")
            return []

    async def process_repository(self, repo_path: Path) -> RepoIndex:
        """Process a single repository and create complete index"""
        repo_name = repo_path.name
        self.logger.info(f"Processing repository: {repo_name}")
        
        # Get all files in repository
        all_files = self.get_all_repo_files(repo_path)
        self.logger.info(f"Found {len(all_files)} files to analyze in {repo_name}")
        
        # Analyze each file
        file_summaries = []
        all_relationships = []
        
        for i, file_path in enumerate(all_files, 1):
            self.logger.info(f"Analyzing file {i}/{len(all_files)}: {file_path.name}")
            
            # Get file summary
            file_summary = await self.analyze_file_content(file_path)
            file_summaries.append(file_summary)
            
            # Find relationships
            relationships = await self.find_relationships(file_summary)
            all_relationships.extend(relationships)
            
            # Add small delay to avoid overwhelming the LLM API
            await asyncio.sleep(0.1)
        
        # Create repository index
        repo_index = RepoIndex(
            repo_name=repo_name,
            total_files=len(all_files),
            file_summaries=file_summaries,
            relationships=all_relationships,
            analysis_metadata={
                "analysis_date": datetime.now().isoformat(),
                "target_structure_analyzed": self.target_structure[:200] + "...",
                "total_relationships_found": len(all_relationships),
                "high_confidence_relationships": len([r for r in all_relationships if r.confidence_score > 0.7]),
                "analyzer_version": "1.0.0"
            }
        )
        
        return repo_index

    async def build_all_indexes(self) -> Dict[str, str]:
        """Build indexes for all repositories in code_base"""
        if not self.code_base_path.exists():
            raise FileNotFoundError(f"Code base path does not exist: {self.code_base_path}")
        
        # Get all repository directories
        repo_dirs = [d for d in self.code_base_path.iterdir() 
                    if d.is_dir() and not d.name.startswith('.')]
        
        if not repo_dirs:
            raise ValueError(f"No repositories found in {self.code_base_path}")
        
        self.logger.info(f"Found {len(repo_dirs)} repositories to process")
        
        # Process each repository
        output_files = {}
        
        for repo_dir in repo_dirs:
            try:
                # Process repository
                repo_index = await self.process_repository(repo_dir)
                
                # Save to JSON file
                output_file = self.output_dir / f"{repo_index.repo_name}_index.json"
                
                with open(output_file, 'w', encoding='utf-8') as f:
                    json.dump(asdict(repo_index), f, indent=2, ensure_ascii=False)
                
                output_files[repo_index.repo_name] = str(output_file)
                self.logger.info(f"Saved index for {repo_index.repo_name} to {output_file}")
                
            except Exception as e:
                self.logger.error(f"Failed to process repository {repo_dir.name}: {e}")
                continue
        
        return output_files

    def generate_summary_report(self, output_files: Dict[str, str]) -> str:
        """Generate a summary report of all indexes created"""
        report_path = self.output_dir / "indexing_summary.json"
        
        summary_data = {
            "indexing_completion_time": datetime.now().isoformat(),
            "total_repositories_processed": len(output_files),
            "output_files": output_files,
            "target_structure": self.target_structure,
            "code_base_path": str(self.code_base_path)
        }
        
        with open(report_path, 'w', encoding='utf-8') as f:
            json.dump(summary_data, f, indent=2, ensure_ascii=False)
        
        return str(report_path)


async def main():
    """Main function to run the code indexer"""
    # Configuration
    code_base_path = "deepcode-mcp/agent_folders/papers/paper_3/code_base"
    
    # Target structure from the attached file
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
    
    # Create indexer
    indexer = CodeIndexer(
        code_base_path=code_base_path,
        target_structure=target_structure,
        output_dir="deepcode-mcp/agent_folders/papers/paper_3/indexes"
    )
    
    try:
        # Build all indexes
        output_files = await indexer.build_all_indexes()
        
        # Generate summary report
        summary_report = indexer.generate_summary_report(output_files)
        
        print(f"\n✅ Indexing completed successfully!")
        print(f"📊 Processed {len(output_files)} repositories")
        print(f"📁 Output files:")
        for repo_name, file_path in output_files.items():
            print(f"   - {repo_name}: {file_path}")
        print(f"📋 Summary report: {summary_report}")
        
    except Exception as e:
        print(f"❌ Indexing failed: {e}")


if __name__ == "__main__":
    asyncio.run(main()) 