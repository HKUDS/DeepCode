"""
User Requirement Analysis Agent

Responsible for analyzing user initial requirements, generating guiding questions,
and summarizing detailed requirement documents based on user responses.
This Agent seamlessly integrates with existing chat workflows to provide more precise requirement understanding.
"""

import json
import logging
from typing import Dict, List, Optional

from mcp_agent.agents.agent import Agent
from utils.llm_utils import get_preferred_llm_class


class RequirementAnalysisAgent:
    """
    User Requirement Analysis Agent

    Core Functions:
    1. Generate 5-8 guiding questions based on user initial requirements
    2. Collect user responses and analyze requirement completeness
    3. Generate detailed requirement documents for subsequent workflows
    4. Support skipping questions to directly enter implementation process

    Design Philosophy:ß
    - Intelligent question generation covering functionality, technology, performance, UI, deployment dimensions
    - Flexible user interaction supporting partial answers or complete skipping
    - Structured requirement output for easy understanding by code generation agents
    """

    def __init__(self, logger: Optional[logging.Logger] = None):
        """
        Initialize requirement analysis agent
        Args:
            logger: Logger instance
        """
        self.logger = logger or self._create_default_logger()
        self.mcp_agent = None
        self.llm = None

    def _create_default_logger(self) -> logging.Logger:
        """Create default logger"""
        logger = logging.getLogger(f"{__name__}.RequirementAnalysisAgent")
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
        """Initialize MCP Agent connection and LLM"""
        try:
            self.mcp_agent = Agent(
                name="RequirementAnalysisAgent",
                instruction="""You are a professional requirement analysis expert, skilled at guiding users to provide more detailed project requirements through precise questions.

Your core capabilities:
1. **Intelligent Question Generation**: Based on user initial descriptions, generate 5-8 key questions covering functional requirements, technology selection, performance requirements, user interface, deployment environment, etc.
2. **Requirement Understanding Analysis**: Deep analysis of user's real intentions and implicit requirements
3. **Structured Requirement Output**: Integrate scattered requirement information into clear technical specification documents

Question Generation Principles:
- Questions should be specific and clear, avoiding overly broad scope
- Cover key decision points for technical implementation
- Consider project feasibility and complexity
- Help users think about important details they might have missed

Requirement Summary Principles:
- Maintain user's original intent unchanged
- Supplement key information for technical implementation
- Provide clear functional module division
- Give reasonable technical architecture suggestions""",
                server_names=[],  # No MCP servers needed, only use LLM
            )

            # Initialize agent context
            await self.mcp_agent.__aenter__()

            # Attach LLM
            self.llm = await self.mcp_agent.attach_llm(get_preferred_llm_class())

            self.logger.info("RequirementAnalysisAgent initialized successfully")

        except Exception as e:
            self.logger.error(f"RequirementAnalysisAgent initialization failed: {e}")
            raise

    async def cleanup(self):
        """Clean up resources"""
        if self.mcp_agent:
            try:
                await self.mcp_agent.__aexit__(None, None, None)
            except Exception as e:
                self.logger.warning(f"Error during resource cleanup: {e}")

    async def generate_guiding_questions(self, user_input: str) -> List[Dict[str, str]]:
        """
        Generate guiding questions based on user initial requirements

        Args:
            user_input: User's initial requirement description

        Returns:
            List[Dict]: Question list, each question contains category, question, importance and other fields
        """
        try:
            self.logger.info("Starting to generate AI precise guiding questions")

            # Build more precise prompt
            prompt = f"""Based on user's project requirements, generate precise guiding questions to help refine requirements.

User Requirements: {user_input}

Please analyze user requirements and generate 1-3 most critical targeted questions focusing on the most important aspects for this specific project

Return format (pure JSON array, no other text):
[
  {{
    "category": "Functional Requirements",
    "question": "Specific question content",
    "importance": "High",
    "hint": "Question hint"
  }}
]

Requirements: Questions should be specific and practical, avoiding general discussions."""

            from mcp_agent.workflows.llm.augmented_llm import RequestParams

            params = RequestParams(
                max_tokens=3000,
                temperature=0.5,  # Lower temperature for more stable JSON output
            )

            self.logger.info(
                f"Calling LLM to generate precise questions, input length: {len(user_input)}"
            )

            result = await self.llm.generate_str(message=prompt, request_params=params)

            self.logger.info(
                f"LLM returned result length: {len(result) if result else 0}"
            )

            if not result or not result.strip():
                self.logger.error("LLM returned empty result")
                raise ValueError("LLM returned empty result")

            self.logger.info(f"LLM returned result: {result[:500]}...")

            # Clean result and extract JSON part
            result_cleaned = result.strip()

            # Try to find JSON array
            import re

            json_pattern = r"\[\s*\{.*?\}\s*\]"
            json_match = re.search(json_pattern, result_cleaned, re.DOTALL)

            if json_match:
                json_str = json_match.group()
                self.logger.info(f"Extracted JSON: {json_str[:200]}...")
            else:
                # If complete JSON not found, try direct parsing
                json_str = result_cleaned

            # Parse JSON result
            try:
                questions = json.loads(json_str)
                if isinstance(questions, list) and len(questions) > 0:
                    self.logger.info(
                        f"✅ Successfully generated {len(questions)} AI precise guiding questions"
                    )
                    return questions
                else:
                    raise ValueError("Returned result is not a valid question list")

            except json.JSONDecodeError as e:
                self.logger.error(f"JSON parsing failed: {e}")
                self.logger.error(f"Original result: {result}")

                # Try more lenient JSON extraction
                lines = result.split("\n")
                json_lines = []
                in_json = False

                for line in lines:
                    if "[" in line:
                        in_json = True
                    if in_json:
                        json_lines.append(line)
                    if "]" in line and in_json:
                        break

                if json_lines:
                    try:
                        json_attempt = "\n".join(json_lines)
                        questions = json.loads(json_attempt)
                        if isinstance(questions, list) and len(questions) > 0:
                            self.logger.info(
                                f"✅ Generated {len(questions)} questions through lenient parsing"
                            )
                            return questions
                    except Exception:
                        pass

                # If JSON parsing fails, raise an error
                self.logger.error("JSON parsing completely failed")
                raise ValueError("Failed to parse AI generated questions")

        except Exception as e:
            self.logger.error(f"Failed to generate guiding questions: {e}")
            # Re-raise the exception instead of falling back to default questions
            raise

    async def summarize_detailed_requirements(
        self, initial_input: str, answers: Dict[str, str]
    ) -> str:
        """
        Generate detailed requirement document based on initial input and user answers

        Args:
            initial_input: User's initial requirement description
            answers: User's answer dictionary {question_id: answer}

        Returns:
            str: Detailed requirement document
        """
        try:
            self.logger.info("Starting to generate AI detailed requirement summary")

            # Build answer content
            answers_text = ""
            if answers:
                for question_id, answer in answers.items():
                    if answer and answer.strip():
                        answers_text += f"• {answer}\n"

            if not answers_text:
                answers_text = "User chose to skip questions, generating based on initial requirements"

            prompt = f"""Based on user requirements and responses, generate a concise project requirement document.

Initial Requirements: {initial_input}

Additional Information:
{answers_text}

Please generate a focused requirement document including:

## Project Overview
Brief description of project's core goals and value proposition

## Functional Requirements
Detailed list of required features and functional modules:
- Core functionalities
- User interactions and workflows
- Data processing requirements
- Integration needs

## Technical Architecture
Recommended technical design including:
- Technology stack and frameworks
- System architecture design
- Database and data storage solutions
- API design considerations
- Security requirements

## Performance & Scalability
- Expected user scale and performance requirements
- Scalability considerations and constraints

Requirements: Focus on what needs to be built and how to build it technically. Be concise but comprehensive - avoid unnecessary implementation details."""

            from mcp_agent.workflows.llm.augmented_llm import RequestParams

            params = RequestParams(max_tokens=4000, temperature=0.3)

            self.logger.info(
                f"Calling LLM to generate requirement summary, initial requirement length: {len(initial_input)}"
            )

            result = await self.llm.generate_str(message=prompt, request_params=params)

            if not result or not result.strip():
                self.logger.error("LLM returned empty requirement summary")
                raise ValueError("LLM returned empty requirement summary")

            self.logger.info(
                f"✅ Requirement summary generation completed, length: {len(result)}"
            )
            return result.strip()

        except Exception as e:
            self.logger.error(f"Requirement summary failed: {e}")
            # Return basic requirement document
            return f"""## Project Overview
Based on user requirements: {initial_input}

## Functional Requirements
Core functionality needed: {initial_input}

## Technical Architecture
- Select appropriate technology stack based on project requirements
- Adopt modular architecture design
- Consider database and data storage solutions
- Implement necessary security measures

## Performance & Scalability
- Design for expected user scale
- Consider scalability and performance requirements

Note: Due to technical issues, this is a simplified requirement document. Manual supplementation of detailed information is recommended."""

    async def modify_requirements(
        self, current_requirements: str, modification_feedback: str
    ) -> str:
        """
        Modify existing requirement document based on user feedback

        Args:
            current_requirements: Current requirement document content
            modification_feedback: User's modification requests and feedback

        Returns:
            str: Modified requirement document
        """
        try:
            self.logger.info("Starting to modify requirements based on user feedback")

            # Build modification prompt
            prompt = f"""Based on the current requirement document and user's modification requests, generate an updated requirement document.

Current Requirements Document:
{current_requirements}

User's Modification Requests:
{modification_feedback}

CRITICAL REQUIREMENT: You MUST generate a complete, well-structured requirement document regardless of how complete or incomplete the user's modification requests are. Even if the user only provides minimal or unclear feedback, you must still produce a comprehensive requirement document following the exact format below.

Generate an updated requirement document that incorporates any reasonable interpretation of the user's requested changes while maintaining the EXACT structure and format:

## Project Overview
Brief description of project's core goals and value proposition

## Functional Requirements
Detailed list of required features and functional modules:
- Core functionalities
- User interactions and workflows
- Data processing requirements
- Integration needs

## Technical Architecture
Recommended technical design including:
- Technology stack and frameworks
- System architecture design
- Database and data storage solutions
- API design considerations
- Security requirements

## Performance & Scalability
- Expected user scale and performance requirements
- Scalability considerations and constraints

MANDATORY REQUIREMENTS:
1. ALWAYS return a complete document with ALL sections above, regardless of user input completeness
2. If user feedback is unclear or incomplete, make reasonable assumptions based on the current requirements
3. Incorporate any clear user requests while filling in missing details intelligently
4. Maintain consistency and coherence throughout the document
5. Ensure all technical suggestions are feasible and practical
6. NEVER return an incomplete or partial document - always provide full sections
7. Keep the same professional structure and format in all cases"""

            from mcp_agent.workflows.llm.augmented_llm import RequestParams

            params = RequestParams(max_tokens=4000, temperature=0.3)

            self.logger.info(
                f"Calling LLM to modify requirements, feedback length: {len(modification_feedback)}"
            )

            result = await self.llm.generate_str(message=prompt, request_params=params)

            if not result or not result.strip():
                self.logger.error("LLM returned empty modified requirements")
                raise ValueError("LLM returned empty modified requirements")

            self.logger.info(
                f"✅ Requirements modification completed, length: {len(result)}"
            )
            return result.strip()

        except Exception as e:
            self.logger.error(f"Requirements modification failed: {e}")
            # Return current requirements with a note about the modification attempt
            return f"""{current_requirements}

---
**Note:** Automatic modification failed due to technical issues. The original requirements are shown above. Please manually incorporate the following requested changes:

{modification_feedback}"""
