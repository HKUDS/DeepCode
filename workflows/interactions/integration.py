"""
Workflow Interaction Integration Helper

This module shows how to integrate user-in-loop interaction handlers
into existing workflows with minimal code changes.

The key idea is to add ONE LINE at each hook point:
    context = await interactions.run_hook(InteractionPoint.XXX, context, task_id)

Example integration in execute_chat_based_planning_pipeline:

    # Before (original code):
    planning_result = await run_chat_planning_agent(user_input, logger)

    # After (with interactions):
    context = {"user_input": user_input, "task_id": task_id}
    context = await interactions.run_hook(InteractionPoint.BEFORE_PLANNING, context, task_id)
    user_input = context.get("requirements", user_input)  # May be enhanced

    planning_result = await run_chat_planning_agent(user_input, logger)

    context["planning_result"] = planning_result
    context = await interactions.run_hook(InteractionPoint.AFTER_PLANNING, context, task_id)

    if context.get("workflow_cancelled"):
        return {"status": "cancelled", "reason": context.get("cancel_reason")}
"""

import asyncio
from collections.abc import Callable
from datetime import datetime
from typing import Any

from .base import (
    InteractionPoint,
    InteractionRegistry,
    InteractionRequest,
    InteractionResponse,
    get_default_registry,
)


class WorkflowInteractionIntegration:
    """
    Helper class for integrating interaction handlers with workflow execution.

    This class bridges interaction handlers with the workflow service,
    handling the communication between backend and frontend.

    Usage in workflow_service.py:

        from workflows.interactions.integration import WorkflowInteractionIntegration

        class WorkflowService:
            def __init__(self):
                self._interaction_integration = WorkflowInteractionIntegration(self)

            async def execute_chat_planning(self, task_id, requirements, ...):
                # Get context with interaction support
                context = self._interaction_integration.create_context(
                    task_id=task_id,
                    user_input=requirements,
                )

                # Run before-planning handlers
                context = await self._interaction_integration.run_hook(
                    InteractionPoint.BEFORE_PLANNING,
                    context
                )

                # Continue with (possibly enhanced) requirements
                requirements = context.get("requirements", requirements)
                ...
    """

    def __init__(
        self, workflow_service: Any, registry: InteractionRegistry | None = None
    ):
        """
        Initialize workflow interaction integration.

        Args:
            workflow_service: The WorkflowService instance
            registry: Optional custom interaction registry
        """
        self._workflow_service = workflow_service
        self._registry = registry or get_default_registry()

        # Set up interaction callback
        self._registry.set_interaction_callback(self._handle_interaction)

        # Pending interactions (task_id -> response_future)
        self._pending_interactions: dict[str, asyncio.Future] = {}

    def create_context(self, task_id: str, **kwargs) -> dict[str, Any]:
        """Create a workflow context with interaction-handler support."""
        return {
            "task_id": task_id,
            "timestamp": datetime.utcnow().isoformat(),
            **kwargs,
        }

    async def run_hook(
        self,
        hook_point: InteractionPoint,
        context: dict[str, Any],
    ) -> dict[str, Any]:
        """
        Run handlers at an interaction point.

        This is the main entry point for interaction execution.
        """
        task_id = context.get("task_id")
        return await self._registry.run_hook(hook_point, context, task_id)

    async def request_interaction(
        self,
        task_id: str,
        request: InteractionRequest,
    ) -> InteractionResponse:
        """Request one user interaction without running handler mutation logic."""
        return await self._handle_interaction(task_id, request)

    async def _handle_interaction(
        self,
        task_id: str,
        request: InteractionRequest,
    ) -> InteractionResponse:
        """
        Handle an interaction request from a handler.

        This method:
        1. Publishes the interaction request through the workflow event sink
        2. Waits for user response (via submit_response)
        3. Returns the response to the handler
        """
        # Update task status
        task = self._workflow_service.get_task(task_id)
        if task:
            task.status = "waiting_for_input"
            task.pending_interaction = {
                "type": request.interaction_type,
                "title": request.title,
                "description": request.description,
                "data": request.data,
                "options": request.options,
                "required": request.required,
            }

        # Create future for response (use get_running_loop for Python 3.10+ compatibility)
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = asyncio.get_event_loop()
        response_future: asyncio.Future = loop.create_future()
        self._pending_interactions[task_id] = response_future

        # Publish through the service-owned event sink.
        await self._workflow_service._broadcast(
            task_id,
            {
                "type": "interaction_required",
                "task_id": task_id,
                "interaction_type": request.interaction_type,
                "title": request.title,
                "description": request.description,
                "data": request.data,
                "options": request.options,
                "required": request.required,
                "timestamp": datetime.utcnow().isoformat(),
            },
        )

        try:
            # Wait for response
            response = await asyncio.wait_for(
                response_future, timeout=request.timeout_seconds
            )
            return response

        except TimeoutError:
            # Return timeout response
            return InteractionResponse(
                action="timeout",
                data={},
                skipped=True,
            )
        finally:
            # Clean up
            self._pending_interactions.pop(task_id, None)
            if task:
                task.status = "running"
                task.pending_interaction = None

    def submit_response(
        self,
        task_id: str,
        action: str,
        data: dict[str, Any] | None = None,
        skipped: bool = False,
    ) -> bool:
        """
        Submit user's response to a pending interaction.

        Called by the API endpoint when user responds.

        Args:
            task_id: The task ID
            action: User's action (e.g., "confirm", "modify", "skip")
            data: Response data
            skipped: Whether user chose to skip

        Returns:
            True if response was submitted, False if no pending interaction
        """
        future = self._pending_interactions.get(task_id)
        if future and not future.done():
            response = InteractionResponse(
                action=action,
                data=data or {},
                skipped=skipped,
            )
            future.set_result(response)
            return True
        return False

    def has_pending_interaction(self, task_id: str) -> bool:
        """Check if a task has a pending interaction."""
        return task_id in self._pending_interactions

    def cancel_interaction(self, task_id: str) -> bool:
        """Cancel a pending interaction (e.g., when task is cancelled)."""
        future = self._pending_interactions.get(task_id)
        if future and not future.done():
            future.cancel()
            self._pending_interactions.pop(task_id, None)
            return True
        return False


def create_interaction_wrapper(
    original_function: Callable,
    before_hooks: list[InteractionPoint],
    after_hooks: list[InteractionPoint],
    integration: WorkflowInteractionIntegration,
) -> Callable:
    """
    Create a wrapper that adds interaction points around an existing function.

    This is useful for wrapping existing workflow functions without
    modifying their code.

    Example:
        # Original function
        async def execute_planning(requirements, logger):
            ...

        # Wrap with interaction handlers
        execute_planning_with_interactions = create_interaction_wrapper(
            execute_planning,
            before_hooks=[InteractionPoint.BEFORE_PLANNING],
            after_hooks=[InteractionPoint.AFTER_PLANNING],
            integration=interaction_integration,
        )
    """

    async def wrapper(*args, task_id: str = None, **kwargs):
        context = integration.create_context(
            task_id=task_id or "unknown",
            args=args,
            kwargs=kwargs,
        )

        # Run before hooks
        for hook in before_hooks:
            context = await integration.run_hook(hook, context)
            if context.get("workflow_cancelled"):
                return {"status": "cancelled", "reason": context.get("cancel_reason")}

        # Execute original function
        result = await original_function(*args, **kwargs)

        # Run after hooks
        context["result"] = result
        for hook in after_hooks:
            context = await integration.run_hook(hook, context)
            if context.get("workflow_cancelled"):
                return {"status": "cancelled", "reason": context.get("cancel_reason")}

        return result

    return wrapper
