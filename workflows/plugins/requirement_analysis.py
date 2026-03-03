"""
Requirement Analysis Plugin

This plugin triggers before planning to gather more detailed requirements
from the user through guided questions.

Flow:
1. User submits initial requirements
2. Plugin generates 1-3 targeted questions
3. User answers questions (or skips)
4. Plugin creates enhanced requirements document
5. Enhanced requirements passed to planning phase
"""

from typing import Any, Dict, Optional
from .base import (
    InteractionPlugin,
    InteractionPoint,
    InteractionRequest,
    InteractionResponse,
)


class RequirementAnalysisPlugin(InteractionPlugin):
    """
    Plugin for enhanced requirement gathering through AI-generated questions.

    This plugin uses the existing RequirementAnalysisAgent to:
    1. Generate targeted questions based on initial requirements
    2. Collect user answers
    3. Create a detailed requirements document

    The enhanced requirements lead to better implementation plans and code.
    """

    name = "requirement_analysis"
    description = "Gather detailed requirements through guided questions"
    hook_point = InteractionPoint.BEFORE_PLANNING
    priority = 10  # High priority - runs first

    def __init__(self, enabled: bool = True, config: Optional[Dict] = None):
        super().__init__(enabled, config)
        self._agent = None

    async def _get_agent(self):
        """Lazy load RequirementAnalysisAgent."""
        if self._agent is None:
            from workflows.agents.requirement_analysis_agent import (
                RequirementAnalysisAgent,
            )

            self._agent = RequirementAnalysisAgent()
            await self._agent.initialize()
        return self._agent

    async def _cleanup_agent(self):
        """Clean up agent resources."""
        if self._agent is not None:
            await self._agent.cleanup()
            self._agent = None

    async def should_trigger(self, context: Dict[str, Any]) -> bool:
        """
        Trigger if:
        - User has provided initial requirements
        - Requirement analysis is not disabled in config
        - User hasn't already answered questions for this session
        """
        # Check if disabled in context
        if context.get("skip_requirement_analysis", False):
            return False

        # Check if already processed
        if context.get("requirements_enhanced", False):
            return False

        # Check if we have user input to analyze
        user_input = context.get("user_input") or context.get("requirements")
        if not user_input or len(user_input.strip()) < 10:
            return False

        return True

    async def create_interaction(self, context: Dict[str, Any]) -> InteractionRequest:
        """Generate questions based on user's initial requirements."""
        user_input = context.get("user_input") or context.get("requirements", "")

        try:
            agent = await self._get_agent()
            questions = await agent.generate_guiding_questions(user_input)

            return InteractionRequest(
                interaction_type="requirement_questions",
                title="📋 Let's clarify your requirements",
                description="Answer these questions to help generate better code (or skip to continue)",
                data={
                    "questions": questions,
                    "original_input": user_input,
                },
                options={
                    "submit": "Submit Answers",
                    "skip": "Skip and Continue",
                },
                required=False,
                timeout_seconds=300,  # 5 minutes
            )
        except Exception as e:
            self.logger.error(f"Failed to generate questions: {e}")
            # Return a simple fallback interaction
            return InteractionRequest(
                interaction_type="requirement_questions",
                title="📋 Add more details?",
                description="Would you like to provide any additional details about your requirements?",
                data={
                    "questions": [
                        {
                            "id": "additional_details",
                            "category": "General",
                            "question": "Is there anything else you'd like to add about your project requirements?",
                            "importance": "Medium",
                            "hint": "Any technical preferences, constraints, or specific features",
                        }
                    ],
                    "original_input": user_input,
                },
                options={
                    "submit": "Submit",
                    "skip": "Skip",
                },
                required=False,
                timeout_seconds=300,
            )

    async def process_response(
        self, response: InteractionResponse, context: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Process user's answers and create enhanced requirements."""
        user_input = context.get("user_input") or context.get("requirements", "")
        answers = response.data.get("answers", {})

        if not answers:
            # No answers provided, use original input
            context["requirements_enhanced"] = True
            return context

        try:
            agent = await self._get_agent()

            # Generate detailed requirements document
            enhanced_requirements = await agent.summarize_detailed_requirements(
                user_input, answers
            )

            # Update context with enhanced requirements
            context["original_requirements"] = user_input
            context["user_answers"] = answers
            context["requirements"] = enhanced_requirements
            context["user_input"] = enhanced_requirements  # For chat pipeline
            context["requirements_enhanced"] = True

            self.logger.info("Requirements enhanced with user answers")

        except Exception as e:
            self.logger.error(f"Failed to enhance requirements: {e}")
            # Keep original requirements
            context["requirements_enhanced"] = True

        finally:
            await self._cleanup_agent()

        return context

    async def on_skip(self, context: Dict[str, Any]) -> Dict[str, Any]:
        """Handle skip - mark as processed but don't modify requirements."""
        context["requirements_enhanced"] = True
        context["requirements_skipped"] = True
        await self._cleanup_agent()
        return context

    async def on_timeout(self, context: Dict[str, Any]) -> Dict[str, Any]:
        """Handle timeout - same as skip."""
        self.logger.warning(
            "Requirement analysis timed out, continuing with original requirements"
        )
        return await self.on_skip(context)
