"""
User-in-Loop Plugin System - Base Classes

This module provides a plugin-based architecture for adding user interaction
points to workflows without modifying core workflow code.

Design Philosophy:
- Plugins are registered at specific "hook points" in the workflow
- Each plugin decides if it should trigger based on context
- Plugins are completely optional and can be enabled/disabled via config
- Zero changes to core workflow code - just call `await plugins.run_hook(...)`

Usage:
    from workflows.plugins import PluginRegistry, InteractionPoint

    # Initialize registry with interaction callback
    plugins = PluginRegistry(interaction_callback=my_callback)

    # In workflow, call hooks at specific points
    context = await plugins.run_hook(
        InteractionPoint.BEFORE_PLANNING,
        context={"user_input": user_input, "task_id": task_id}
    )
"""

import asyncio
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Awaitable
import logging


class InteractionPoint(Enum):
    """
    Defines hook points where plugins can be inserted in the workflow.

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
    data: Dict[str, Any]  # Interaction-specific data
    options: Dict[str, str] = field(default_factory=dict)  # Available actions
    required: bool = False  # If True, cannot be skipped
    timeout_seconds: int = 300  # Timeout for response (5 min default)


@dataclass
class InteractionResponse:
    """Data structure for user's response to interaction"""

    action: str  # User's action (e.g., "confirm", "modify", "skip")
    data: Dict[str, Any] = field(default_factory=dict)  # Response data
    skipped: bool = False  # True if user chose to skip


class InteractionPlugin(ABC):
    """
    Base class for User-in-Loop plugins.

    Each plugin implements:
    1. should_trigger() - Decides if plugin should run based on context
    2. create_interaction() - Creates the interaction request
    3. process_response() - Handles user's response and updates context

    Example:
        class MyPlugin(InteractionPlugin):
            name = "my_plugin"
            hook_point = InteractionPoint.AFTER_PLANNING

            async def should_trigger(self, context):
                return context.get("enable_my_plugin", True)

            async def create_interaction(self, context):
                return InteractionRequest(...)

            async def process_response(self, response, context):
                context["my_result"] = response.data
                return context
    """

    # Plugin metadata - override in subclass
    name: str = "base_plugin"
    description: str = "Base plugin"
    hook_point: InteractionPoint = InteractionPoint.BEFORE_PLANNING
    priority: int = 100  # Lower number = higher priority (runs first)

    def __init__(self, enabled: bool = True, config: Optional[Dict] = None):
        self.enabled = enabled
        self.config = config or {}
        self.logger = logging.getLogger(f"plugin.{self.name}")

    @abstractmethod
    async def should_trigger(self, context: Dict[str, Any]) -> bool:
        """
        Determine if this plugin should trigger.

        Args:
            context: Current workflow context

        Returns:
            True if plugin should run, False to skip
        """
        pass

    @abstractmethod
    async def create_interaction(self, context: Dict[str, Any]) -> InteractionRequest:
        """
        Create the interaction request to send to user.

        Args:
            context: Current workflow context

        Returns:
            InteractionRequest with data for user interface
        """
        pass

    @abstractmethod
    async def process_response(
        self, response: InteractionResponse, context: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Process user's response and update context.

        Args:
            response: User's response
            context: Current workflow context

        Returns:
            Updated context dictionary
        """
        pass

    async def on_skip(self, context: Dict[str, Any]) -> Dict[str, Any]:
        """
        Called when user skips the interaction.
        Override to provide default behavior.

        Args:
            context: Current workflow context

        Returns:
            Updated context (default: unchanged)
        """
        self.logger.info(f"Plugin {self.name} skipped by user")
        return context

    async def on_timeout(self, context: Dict[str, Any]) -> Dict[str, Any]:
        """
        Called when interaction times out.
        Override to provide timeout behavior.

        Args:
            context: Current workflow context

        Returns:
            Updated context (default: same as skip)
        """
        self.logger.warning(f"Plugin {self.name} timed out")
        return await self.on_skip(context)


# Type alias for interaction callback
InteractionCallback = Callable[
    [str, InteractionRequest],  # (task_id, request)
    Awaitable[InteractionResponse],  # Returns response
]


class PluginRegistry:
    """
    Registry for managing and executing User-in-Loop plugins.

    Features:
    - Register plugins at specific hook points
    - Enable/disable plugins dynamically
    - Execute all plugins at a hook point in priority order
    - Handle interaction callbacks to frontend

    Usage:
        # Create registry
        registry = PluginRegistry()

        # Register plugins
        registry.register(RequirementAnalysisPlugin())
        registry.register(PlanReviewPlugin(enabled=False))

        # Set interaction callback (connects to WebSocket/API)
        registry.set_interaction_callback(my_callback)

        # Run hooks in workflow
        context = await registry.run_hook(InteractionPoint.BEFORE_PLANNING, context)
    """

    def __init__(self, interaction_callback: Optional[InteractionCallback] = None):
        self._plugins: Dict[InteractionPoint, List[InteractionPlugin]] = {
            point: [] for point in InteractionPoint
        }
        self._interaction_callback = interaction_callback
        self.logger = logging.getLogger("plugin.registry")

    def register(self, plugin: InteractionPlugin) -> None:
        """Register a plugin at its hook point."""
        hook_point = plugin.hook_point
        self._plugins[hook_point].append(plugin)
        # Sort by priority (lower number first)
        self._plugins[hook_point].sort(key=lambda p: p.priority)
        self.logger.info(f"Registered plugin '{plugin.name}' at {hook_point.value}")

    def unregister(self, plugin_name: str) -> bool:
        """Unregister a plugin by name."""
        for hook_point, plugins in self._plugins.items():
            for plugin in plugins:
                if plugin.name == plugin_name:
                    plugins.remove(plugin)
                    self.logger.info(f"Unregistered plugin '{plugin_name}'")
                    return True
        return False

    def enable(self, plugin_name: str) -> bool:
        """Enable a plugin by name."""
        for plugins in self._plugins.values():
            for plugin in plugins:
                if plugin.name == plugin_name:
                    plugin.enabled = True
                    self.logger.info(f"Enabled plugin '{plugin_name}'")
                    return True
        return False

    def disable(self, plugin_name: str) -> bool:
        """Disable a plugin by name."""
        for plugins in self._plugins.values():
            for plugin in plugins:
                if plugin.name == plugin_name:
                    plugin.enabled = False
                    self.logger.info(f"Disabled plugin '{plugin_name}'")
                    return True
        return False

    def set_interaction_callback(self, callback: InteractionCallback) -> None:
        """Set the callback function for user interactions."""
        self._interaction_callback = callback

    def get_plugins(self, hook_point: InteractionPoint) -> List[InteractionPlugin]:
        """Get all plugins registered at a hook point."""
        return self._plugins.get(hook_point, [])

    async def run_hook(
        self,
        hook_point: InteractionPoint,
        context: Dict[str, Any],
        task_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Execute all enabled plugins at a hook point.

        Plugins are executed in priority order. Each plugin can:
        - Modify the context
        - Request user interaction
        - Be skipped by the user

        Args:
            hook_point: The hook point to execute
            context: Current workflow context
            task_id: Task ID for interaction callbacks

        Returns:
            Updated context after all plugins have run
        """
        plugins = self._plugins.get(hook_point, [])

        if not plugins:
            self.logger.debug(f"No plugins registered at {hook_point.value}")
            return context

        self.logger.info(
            f"Running hook {hook_point.value} with {len(plugins)} plugin(s)"
        )

        for plugin in plugins:
            if not plugin.enabled:
                self.logger.debug(f"Plugin '{plugin.name}' is disabled, skipping")
                continue

            try:
                # Check if plugin should trigger
                if not await plugin.should_trigger(context):
                    self.logger.debug(f"Plugin '{plugin.name}' chose not to trigger")
                    continue

                self.logger.info(f"Running plugin '{plugin.name}'")

                # Create interaction request
                interaction = await plugin.create_interaction(context)

                # If we have a callback, request user interaction
                if self._interaction_callback and task_id:
                    try:
                        response = await asyncio.wait_for(
                            self._interaction_callback(task_id, interaction),
                            timeout=interaction.timeout_seconds,
                        )

                        if response.skipped:
                            context = await plugin.on_skip(context)
                        else:
                            context = await plugin.process_response(response, context)

                    except asyncio.TimeoutError:
                        self.logger.warning(
                            f"Plugin '{plugin.name}' interaction timed out"
                        )
                        context = await plugin.on_timeout(context)
                else:
                    # No callback - auto-skip non-required interactions
                    if not interaction.required:
                        self.logger.info(
                            f"No callback, auto-skipping plugin '{plugin.name}'"
                        )
                        context = await plugin.on_skip(context)
                    else:
                        raise RuntimeError(
                            f"Plugin '{plugin.name}' requires interaction but no callback provided"
                        )

            except Exception as e:
                self.logger.error(f"Plugin '{plugin.name}' failed: {e}")
                # Continue with other plugins
                continue

        return context


# Global default registry
_default_registry: Optional[PluginRegistry] = None


def get_default_registry(auto_register: bool = True) -> PluginRegistry:
    """
    Get or create the default plugin registry.

    Args:
        auto_register: If True, auto-register default plugins. Set to False to avoid
                       circular imports when called from plugin modules.
    """
    global _default_registry
    if _default_registry is None:
        _default_registry = PluginRegistry()

        if auto_register:
            # Lazy import to avoid circular imports
            try:
                from .requirement_analysis import RequirementAnalysisPlugin
                from .plan_review import PlanReviewPlugin

                _default_registry.register(RequirementAnalysisPlugin())
                _default_registry.register(PlanReviewPlugin())
            except ImportError as e:
                logging.getLogger("plugin.registry").warning(
                    f"Could not auto-register default plugins: {e}"
                )

    return _default_registry


def reset_registry() -> None:
    """Reset the default registry (useful for testing)."""
    global _default_registry
    _default_registry = None
