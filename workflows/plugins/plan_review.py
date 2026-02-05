"""
Plan Review Plugin

This plugin triggers after planning to let users review and modify
the implementation plan before code generation begins.

Flow:
1. AI generates implementation plan
2. Plugin presents plan to user
3. User can: Confirm / Request modifications / Cancel
4. If modifications requested, AI updates the plan
5. Code generation proceeds with approved plan
"""

from typing import Any, Dict, Optional
from .base import (
    InteractionPlugin,
    InteractionPoint,
    InteractionRequest,
    InteractionResponse,
)


class PlanReviewPlugin(InteractionPlugin):
    """
    Plugin for reviewing and modifying implementation plans.

    This allows users to:
    - Review the generated YAML implementation plan
    - Confirm to proceed with code generation
    - Request modifications to the plan
    - Cancel the workflow entirely

    The confirmed/modified plan is then used for code generation.
    """

    name = "plan_review"
    description = "Review and optionally modify the implementation plan"
    hook_point = InteractionPoint.AFTER_PLANNING
    priority = 10

    def __init__(self, enabled: bool = True, config: Optional[Dict] = None):
        super().__init__(enabled, config)
        self._max_modification_rounds = (
            config.get("max_modification_rounds", 3) if config else 3
        )

    async def should_trigger(self, context: Dict[str, Any]) -> bool:
        """
        Trigger if:
        - A plan has been generated
        - Plan review is not disabled
        - Haven't already reviewed/approved the plan
        """
        # Check if disabled
        if context.get("skip_plan_review", False):
            return False

        # Check if already reviewed
        if context.get("plan_approved", False):
            return False

        # Check if we have a plan to review
        plan = context.get("implementation_plan") or context.get("planning_result")
        if not plan:
            # Try to read from file
            plan_path = context.get("initial_plan_path")
            if plan_path:
                try:
                    with open(plan_path, "r", encoding="utf-8") as f:
                        plan = f.read()
                        context["implementation_plan"] = plan
                except Exception:
                    return False
            else:
                return False

        return len(str(plan).strip()) > 0

    async def create_interaction(self, context: Dict[str, Any]) -> InteractionRequest:
        """Create plan review interaction."""
        plan = context.get("implementation_plan") or context.get("planning_result", "")
        modification_round = context.get("plan_modification_round", 0)

        # Prepare plan summary
        plan_lines = str(plan).split("\n")
        plan_preview = "\n".join(plan_lines[:50])  # First 50 lines as preview
        if len(plan_lines) > 50:
            plan_preview += f"\n... ({len(plan_lines) - 50} more lines)"

        description = "Review the implementation plan below. You can approve it, request changes, or cancel."
        if modification_round > 0:
            description = f"Plan has been modified (round {modification_round}). Please review again."

        return InteractionRequest(
            interaction_type="plan_review",
            title="🔍 Review Implementation Plan",
            description=description,
            data={
                "plan": plan,
                "plan_preview": plan_preview,
                "plan_path": context.get("initial_plan_path"),
                "modification_round": modification_round,
                "max_rounds": self._max_modification_rounds,
            },
            options={
                "confirm": "✓ Approve & Continue",
                "modify": "✎ Request Changes",
                "cancel": "✕ Cancel Workflow",
            },
            required=False,  # Can be skipped (auto-approve)
            timeout_seconds=600,  # 10 minutes for review
        )

    async def process_response(
        self, response: InteractionResponse, context: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Process user's plan review response."""
        action = response.action.lower()

        if action == "confirm":
            # Plan approved, proceed
            context["plan_approved"] = True
            self.logger.info("Implementation plan approved by user")

        elif action == "modify":
            # User wants modifications
            feedback = response.data.get("feedback", "")
            modification_round = context.get("plan_modification_round", 0) + 1

            if modification_round > self._max_modification_rounds:
                self.logger.warning(
                    f"Max modification rounds ({self._max_modification_rounds}) reached"
                )
                context["plan_approved"] = True
                context["plan_modification_warning"] = (
                    "Maximum modification rounds reached"
                )
                return context

            # Modify the plan based on feedback
            try:
                modified_plan = await self._modify_plan(
                    context.get("implementation_plan", ""), feedback, context
                )

                context["implementation_plan"] = modified_plan
                context["planning_result"] = modified_plan
                context["plan_modification_round"] = modification_round
                context["last_modification_feedback"] = feedback

                # Save modified plan to file
                plan_path = context.get("initial_plan_path")
                if plan_path:
                    with open(plan_path, "w", encoding="utf-8") as f:
                        f.write(modified_plan)

                self.logger.info(f"Plan modified (round {modification_round})")

                # Note: The workflow should loop back to show the modified plan
                # This is handled by NOT setting plan_approved = True

            except Exception as e:
                self.logger.error(f"Failed to modify plan: {e}")
                context["plan_modification_error"] = str(e)
                # Auto-approve to continue
                context["plan_approved"] = True

        elif action == "cancel":
            # User wants to cancel
            context["workflow_cancelled"] = True
            context["cancel_reason"] = response.data.get(
                "reason", "User cancelled at plan review"
            )
            self.logger.info("Workflow cancelled by user at plan review")

        else:
            # Unknown action, treat as confirm
            self.logger.warning(f"Unknown action '{action}', treating as confirm")
            context["plan_approved"] = True

        return context

    async def _modify_plan(
        self, current_plan: str, feedback: str, context: Dict[str, Any]
    ) -> str:
        """
        Modify the implementation plan based on user feedback.
        Uses RequirementAnalysisAgent's modify_requirements method.
        """
        try:
            from workflows.agents.requirement_analysis_agent import (
                RequirementAnalysisAgent,
            )

            async with RequirementAnalysisAgent() as agent:
                modified = await agent.modify_requirements(current_plan, feedback)
                return modified

        except Exception as e:
            self.logger.error(f"Plan modification failed: {e}")
            # Return original plan with feedback appended as comment
            return f"""{current_plan}

# ==========================================
# User Modification Request (not applied automatically):
# {feedback}
# ==========================================
"""

    async def on_skip(self, context: Dict[str, Any]) -> Dict[str, Any]:
        """Handle skip - auto-approve the plan."""
        context["plan_approved"] = True
        context["plan_auto_approved"] = True
        self.logger.info("Plan auto-approved (user skipped review)")
        return context

    async def on_timeout(self, context: Dict[str, Any]) -> Dict[str, Any]:
        """Handle timeout - auto-approve."""
        self.logger.warning("Plan review timed out, auto-approving")
        return await self.on_skip(context)
