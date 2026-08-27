"""
User-in-loop interaction handlers - base classes

This module provides hook-based handlers for adding user interaction
points to workflows without modifying core workflow code.

Design Philosophy:
- Handlers are registered at specific workflow interaction points
- Each handler decides if it should trigger based on context
- Handlers are optional and can be enabled or disabled via config
- Workflows call `await interactions.run_hook(...)` at an interaction point

Usage:
    from workflows.interactions import InteractionRegistry, InteractionPoint

    # Initialize registry with interaction callback
    interactions = InteractionRegistry(interaction_callback=my_callback)

    # In workflow, call hooks at specific points
    context = await interactions.run_hook(
        InteractionPoint.BEFORE_PLANNING,
        context={"user_input": user_input, "task_id": task_id}
    )
"""

import asyncio
import logging
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class InteractionPoint(Enum):
    """
    Defines points where interaction handlers can run in the workflow.

    Hook points are named by their position relative to workflow phases:
    - BEFORE_* : Before a phase starts
    - AFTER_*  : After a phase completes
    """

    # Chat Planning Pipeline hooks
    BEFORE_PLANNING = "before_planning"  # Before generating implementation plan
    AFTER_PLANNING = "after_planning"  # After plan is generated, before implementation

    # Paper-to-Code Pipeline hooks
    BEFORE_RESEARCH_ANALYSIS = "before_research_analysis"  # Before analyzing paper
    AFTER_RESEARCH_ANALYSIS = "after_research_analysis"  # After paper analysis
    AFTER_CODE_PLANNING = "after_code_planning"  # After code plan generated

    # Common hooks
    BEFORE_IMPLEMENTATION = "before_implementation"  # Before code generation starts
    AFTER_IMPLEMENTATION = "after_implementation"  # After code is generated


@dataclass
class InteractionRequest:
    """Data structure for requesting user interaction"""

    interaction_type: str  # Type of interaction (e.g., "questions", "plan_review")
    title: str  # Display title
    description: str  # Description for user
    data: dict[str, Any]  # Interaction-specific data
    options: dict[str, str] = field(default_factory=dict)  # Available actions
    required: bool = False  # If True, cannot be skipped
    timeout_seconds: int = 300  # Timeout for response (5 min default)


@dataclass
class InteractionResponse:
    """Data structure for user's response to interaction"""

    action: str  # User's action (e.g., "confirm", "modify", "skip")
    data: dict[str, Any] = field(default_factory=dict)  # Response data
    skipped: bool = False  # True if user chose to skip


class InteractionHandler(ABC):
    """
    Base class for user-in-loop interaction handlers.

    Each handler implements:
    1. should_trigger() - Decides if the handler should run based on context
    2. create_interaction() - Creates the interaction request
    3. process_response() - Handles user's response and updates context

    Example:
        class MyHandler(InteractionHandler):
            name = "my_handler"
            hook_point = InteractionPoint.AFTER_PLANNING

            async def should_trigger(self, context):
                return context.get("enable_my_handler", True)

            async def create_interaction(self, context):
                return InteractionRequest(...)

            async def process_response(self, response, context):
                context["my_result"] = response.data
                return context
    """

    # Handler metadata - override in subclasses.
    name: str = "base_handler"
    description: str = "Base interaction handler"
    hook_point: InteractionPoint = InteractionPoint.BEFORE_PLANNING
    priority: int = 100  # Lower number = higher priority (runs first)

    def __init__(self, enabled: bool = True, config: dict | None = None):
        self.enabled = enabled
        self.config = config or {}
        self.logger = logging.getLogger(f"workflow.interactions.{self.name}")

    @abstractmethod
    async def should_trigger(self, context: dict[str, Any]) -> bool:
        """
        Determine if this handler should trigger.

        Args:
            context: Current workflow context

        Returns:
            True if the handler should run, False to skip
        """

    @abstractmethod
    async def create_interaction(self, context: dict[str, Any]) -> InteractionRequest:
        """
        Create the interaction request to send to user.

        Args:
            context: Current workflow context

        Returns:
            InteractionRequest with data for user interface
        """

    @abstractmethod
    async def process_response(
        self, response: InteractionResponse, context: dict[str, Any]
    ) -> dict[str, Any]:
        """
        Process user's response and update context.

        Args:
            response: User's response
            context: Current workflow context

        Returns:
            Updated context dictionary
        """

    async def on_skip(self, context: dict[str, Any]) -> dict[str, Any]:
        """
        Called when user skips the interaction.
        Override to provide default behavior.

        Args:
            context: Current workflow context

        Returns:
            Updated context (default: unchanged)
        """
        self.logger.info(f"Interaction handler {self.name} skipped by user")
        return context

    async def on_timeout(self, context: dict[str, Any]) -> dict[str, Any]:
        """
        Called when interaction times out.
        Override to provide timeout behavior.

        Args:
            context: Current workflow context

        Returns:
            Updated context (default: same as skip)
        """
        self.logger.warning(f"Interaction handler {self.name} timed out")
        return await self.on_skip(context)


# Type alias for interaction callback
InteractionCallback = Callable[
    [str, InteractionRequest],  # (task_id, request)
    Awaitable[InteractionResponse],  # Returns response
]


class InteractionRegistry:
    """
    Registry for managing and executing user-in-loop interaction handlers.

    Features:
    - Register handlers at specific interaction points
    - Enable or disable handlers dynamically
    - Execute handlers at an interaction point in priority order
    - Handle interaction callbacks to the application layer

    Usage:
        # Create registry
        registry = InteractionRegistry()

        # Register handlers
        registry.register(RequirementAnalysisHandler())
        registry.register(PlanReviewHandler(enabled=False))

        # Set interaction callback (connects to an application event sink)
        registry.set_interaction_callback(my_callback)

        # Run hooks in workflow
        context = await registry.run_hook(InteractionPoint.BEFORE_PLANNING, context)
    """

    def __init__(self, interaction_callback: InteractionCallback | None = None):
        self._handlers: dict[InteractionPoint, list[InteractionHandler]] = {
            point: [] for point in InteractionPoint
        }
        self._interaction_callback = interaction_callback
        self.logger = logging.getLogger("workflow.interactions")

    def register(self, handler: InteractionHandler) -> None:
        """Register a handler at its interaction point."""
        hook_point = handler.hook_point
        self._handlers[hook_point].append(handler)
        # Sort by priority (lower number first)
        self._handlers[hook_point].sort(key=lambda item: item.priority)
        self.logger.info(f"Registered handler '{handler.name}' at {hook_point.value}")

    def unregister(self, handler_name: str) -> bool:
        """Unregister a handler by name."""
        for handlers in self._handlers.values():
            for handler in handlers:
                if handler.name == handler_name:
                    handlers.remove(handler)
                    self.logger.info(f"Unregistered handler '{handler_name}'")
                    return True
        return False

    def enable(self, handler_name: str) -> bool:
        """Enable a handler by name."""
        for handlers in self._handlers.values():
            for handler in handlers:
                if handler.name == handler_name:
                    handler.enabled = True
                    self.logger.info(f"Enabled handler '{handler_name}'")
                    return True
        return False

    def disable(self, handler_name: str) -> bool:
        """Disable a handler by name."""
        for handlers in self._handlers.values():
            for handler in handlers:
                if handler.name == handler_name:
                    handler.enabled = False
                    self.logger.info(f"Disabled handler '{handler_name}'")
                    return True
        return False

    def set_interaction_callback(self, callback: InteractionCallback) -> None:
        """Set the callback function for user interactions."""
        self._interaction_callback = callback

    def get_handlers(self, hook_point: InteractionPoint) -> list[InteractionHandler]:
        """Get handlers registered at an interaction point."""
        return self._handlers.get(hook_point, [])

    async def run_hook(
        self,
        hook_point: InteractionPoint,
        context: dict[str, Any],
        task_id: str | None = None,
    ) -> dict[str, Any]:
        """
        Execute all enabled handlers at an interaction point.

        Handlers are executed in priority order. Each handler can:
        - Modify the context
        - Request user interaction
        - Be skipped by the user

        Args:
            hook_point: The hook point to execute
            context: Current workflow context
            task_id: Task ID for interaction callbacks

        Returns:
            Updated context after all handlers have run
        """
        handlers = self._handlers.get(hook_point, [])

        if not handlers:
            self.logger.debug(f"No handlers registered at {hook_point.value}")
            return context

        self.logger.info(
            f"Running hook {hook_point.value} with {len(handlers)} handler(s)"
        )

        for handler in handlers:
            if not handler.enabled:
                self.logger.debug(f"Handler '{handler.name}' is disabled, skipping")
                continue

            try:
                if not await handler.should_trigger(context):
                    self.logger.debug(f"Handler '{handler.name}' chose not to trigger")
                    continue

                self.logger.info(f"Running handler '{handler.name}'")

                interaction = await handler.create_interaction(context)

                # If we have a callback, request user interaction
                if self._interaction_callback and task_id:
                    try:
                        response = await asyncio.wait_for(
                            self._interaction_callback(task_id, interaction),
                            timeout=interaction.timeout_seconds,
                        )

                        if response.skipped:
                            context = await handler.on_skip(context)
                        else:
                            context = await handler.process_response(response, context)

                    except TimeoutError:
                        self.logger.warning(
                            f"Handler '{handler.name}' interaction timed out"
                        )
                        context = await handler.on_timeout(context)
                else:
                    # No callback - auto-skip non-required interactions
                    if not interaction.required:
                        self.logger.info(
                            f"No callback, auto-skipping handler '{handler.name}'"
                        )
                        context = await handler.on_skip(context)
                    else:
                        raise RuntimeError(
                            f"Handler '{handler.name}' requires interaction but no "
                            "callback was provided"
                        )

            except Exception as e:
                self.logger.error(f"Handler '{handler.name}' failed: {e}")
                # Continue with the remaining independent handlers.
                continue

        return context


# Global default registry
_default_registry: InteractionRegistry | None = None


def get_default_registry(auto_register: bool = True) -> InteractionRegistry:
    """
    Get or create the default interaction registry.

    Args:
        auto_register: If True, auto-register default handlers. Set to False to
                       avoid circular imports when called from handler modules.
    """
    global _default_registry
    if _default_registry is None:
        _default_registry = InteractionRegistry()

        if auto_register:
            # Lazy import to avoid circular imports
            try:
                from .plan_review import PlanReviewHandler
                from .requirement_analysis import RequirementAnalysisHandler

                _default_registry.register(RequirementAnalysisHandler())
                _default_registry.register(PlanReviewHandler())
            except ImportError as e:
                logging.getLogger("workflow.interactions").warning(
                    f"Could not auto-register default handlers: {e}"
                )

    return _default_registry


def reset_registry() -> None:
    """Reset the default registry (useful for testing)."""
    global _default_registry
    _default_registry = None
