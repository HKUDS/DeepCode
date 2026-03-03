"""
Requirement Analysis Service
Integration with existing requirement analysis workflow

NOTE: This module uses lazy imports for DeepCode modules.
sys.path is configured in main.py at startup.
"""

import json
from typing import Dict, Any


class RequirementService:
    """Service for requirement analysis operations"""

    async def generate_questions(self, initial_requirement: str) -> Dict[str, Any]:
        """Generate guiding questions based on initial requirements"""
        try:
            # Lazy import - DeepCode module found via sys.path set in main.py
            from workflows.agent_orchestration_engine import (
                execute_requirement_analysis_workflow,
            )

            result = await execute_requirement_analysis_workflow(
                user_input=initial_requirement,
                analysis_mode="generate_questions",
                user_answers=None,
                logger=None,
                progress_callback=None,
            )

            if result.get("status") == "success":
                # Parse JSON questions
                questions = json.loads(result.get("result", "[]"))
                return {
                    "status": "success",
                    "questions": questions,
                }
            else:
                return {
                    "status": "error",
                    "error": result.get("error", "Failed to generate questions"),
                }

        except Exception as e:
            return {
                "status": "error",
                "error": str(e),
            }

    async def summarize_requirements(
        self,
        initial_requirement: str,
        user_answers: Dict[str, str],
    ) -> Dict[str, Any]:
        """Summarize requirements based on initial input and user answers"""
        try:
            # Lazy import - DeepCode module found via sys.path set in main.py
            from workflows.agent_orchestration_engine import (
                execute_requirement_analysis_workflow,
            )

            result = await execute_requirement_analysis_workflow(
                user_input=initial_requirement,
                analysis_mode="summarize_requirements",
                user_answers=user_answers,
                logger=None,
                progress_callback=None,
            )

            if result.get("status") == "success":
                return {
                    "status": "success",
                    "summary": result.get("result", ""),
                }
            else:
                return {
                    "status": "error",
                    "error": result.get("error", "Failed to summarize requirements"),
                }

        except Exception as e:
            return {
                "status": "error",
                "error": str(e),
            }

    async def modify_requirements(
        self,
        current_requirements: str,
        modification_feedback: str,
    ) -> Dict[str, Any]:
        """Modify requirements based on user feedback"""
        try:
            # Lazy import - DeepCode module found via sys.path set in main.py
            from workflows.agents.requirement_analysis_agent import (
                RequirementAnalysisAgent,
            )

            agent = RequirementAnalysisAgent()
            await agent.initialize()

            result = await agent.modify_requirements(
                current_requirements=current_requirements,
                modification_feedback=modification_feedback,
            )

            await agent.cleanup()

            return {
                "status": "success",
                "summary": result,
            }

        except Exception as e:
            return {
                "status": "error",
                "error": str(e),
            }


# Global service instance
requirement_service = RequirementService()
