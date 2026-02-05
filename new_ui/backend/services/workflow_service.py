"""
Workflow Service - Integration with existing DeepCode workflows

NOTE: This module uses lazy imports for DeepCode modules (workflows, mcp_agent).
sys.path is configured in main.py at startup. Background tasks share the same
sys.path, so DeepCode modules will be found correctly as long as there are
no naming conflicts (config.py -> settings.py, utils/ -> app_utils/).
"""

import asyncio
import uuid
import os
from datetime import datetime
from typing import Optional, Dict, Any, Callable, List
from dataclasses import dataclass, field

from settings import CONFIG_PATH, PROJECT_ROOT


@dataclass
class WorkflowTask:
    """Represents a running workflow task"""

    task_id: str
    status: str = "pending"  # pending | running | waiting_for_input | completed | error | cancelled
    progress: int = 0
    message: str = ""
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    cancel_event: asyncio.Event = field(default_factory=asyncio.Event)
    # User-in-Loop support
    pending_interaction: Optional[Dict[str, Any]] = (
        None  # Current interaction request waiting for user
    )


class WorkflowService:
    """Service for managing workflow execution"""

    def __init__(self):
        self._tasks: Dict[str, WorkflowTask] = {}
        # Changed: Each task can have multiple subscriber queues
        self._subscribers: Dict[str, List[asyncio.Queue]] = {}
        # User-in-Loop plugin integration (lazy loaded)
        self._plugin_integration = None
        self._plugin_enabled = True  # Can be disabled via config

    def _get_plugin_integration(self):
        """Lazy load the plugin integration system."""
        if self._plugin_integration is None and self._plugin_enabled:
            try:
                from workflows.plugins.integration import WorkflowPluginIntegration

                self._plugin_integration = WorkflowPluginIntegration(self)
                print("[WorkflowService] Plugin integration initialized")
            except ImportError as e:
                print(f"[WorkflowService] Plugin system not available: {e}")
                self._plugin_enabled = False
        return self._plugin_integration

    def create_task(self) -> WorkflowTask:
        """Create a new workflow task"""
        task_id = str(uuid.uuid4())
        task = WorkflowTask(task_id=task_id)
        self._tasks[task_id] = task
        self._subscribers[task_id] = []
        return task

    def get_task(self, task_id: str) -> Optional[WorkflowTask]:
        """Get task by ID"""
        return self._tasks.get(task_id)

    def subscribe(self, task_id: str) -> Optional[asyncio.Queue]:
        """Subscribe to a task's progress updates. Returns a new queue for this subscriber."""
        if task_id not in self._subscribers:
            print(f"[Subscribe] Failed: task={task_id[:8]}... not found in subscribers")
            return None
        queue = asyncio.Queue()
        self._subscribers[task_id].append(queue)
        print(
            f"[Subscribe] Success: task={task_id[:8]}... total_subscribers={len(self._subscribers[task_id])}"
        )
        return queue

    def unsubscribe(self, task_id: str, queue: asyncio.Queue):
        """Unsubscribe from a task's progress updates."""
        if task_id in self._subscribers and queue in self._subscribers[task_id]:
            self._subscribers[task_id].remove(queue)
            print(
                f"[Unsubscribe] task={task_id[:8]}... remaining={len(self._subscribers[task_id])}"
            )

    async def _broadcast(self, task_id: str, message: Dict[str, Any]):
        """Broadcast a message to all subscribers of a task."""
        if task_id in self._subscribers:
            subscriber_count = len(self._subscribers[task_id])
            print(
                f"[Broadcast] task={task_id[:8]}... type={message.get('type')} subscribers={subscriber_count}"
            )
            for queue in self._subscribers[task_id]:
                try:
                    await queue.put(message)
                except Exception as e:
                    print(f"[Broadcast] Failed to send to queue: {e}")
        else:
            print(
                f"[Broadcast] No subscribers for task={task_id[:8]}... type={message.get('type')}"
            )

    def get_progress_queue(self, task_id: str) -> Optional[asyncio.Queue]:
        """Get progress queue for a task (deprecated, use subscribe instead)"""
        # For backwards compatibility, create a subscriber queue
        return self.subscribe(task_id)

    async def _create_progress_callback(
        self, task_id: str
    ) -> Callable[[int, str], None]:
        """Create a progress callback that broadcasts to all subscribers"""
        task = self._tasks.get(task_id)

        def callback(progress: int, message: str):
            if task:
                task.progress = progress
                task.message = message

            # Broadcast to all subscribers
            asyncio.create_task(
                self._broadcast(
                    task_id,
                    {
                        "type": "progress",
                        "task_id": task_id,
                        "progress": progress,
                        "message": message,
                        "timestamp": datetime.utcnow().isoformat(),
                    },
                )
            )

        return callback

    async def execute_paper_to_code(
        self,
        task_id: str,
        input_source: str,
        input_type: str,
        enable_indexing: bool = False,
    ) -> Dict[str, Any]:
        """Execute paper-to-code workflow"""
        # Lazy imports - DeepCode modules found via sys.path set in main.py
        from mcp_agent.app import MCPApp
        from workflows.agent_orchestration_engine import (
            execute_multi_agent_research_pipeline,
        )

        task = self._tasks.get(task_id)
        if not task:
            return {"status": "error", "error": "Task not found"}

        task.status = "running"
        task.started_at = datetime.utcnow()

        try:
            progress_callback = await self._create_progress_callback(task_id)

            # Change to project root directory for MCP server paths to work correctly
            original_cwd = os.getcwd()
            os.chdir(PROJECT_ROOT)

            # Create MCP app context with explicit config path
            app = MCPApp(name="paper_to_code", settings=str(CONFIG_PATH))

            async with app.run() as agent_app:
                logger = agent_app.logger
                context = agent_app.context

                # Add current working directory to filesystem server args
                context.config.mcp.servers["filesystem"].args.extend([os.getcwd()])

                # Execute the pipeline
                result = await execute_multi_agent_research_pipeline(
                    input_source,
                    logger,
                    progress_callback,
                    enable_indexing=enable_indexing,
                )

                task.status = "completed"
                task.progress = 100
                task.result = {
                    "status": "success",
                    "repo_result": result,
                }
                task.completed_at = datetime.utcnow()

                # Broadcast completion signal to all subscribers
                await self._broadcast(
                    task_id,
                    {
                        "type": "complete",
                        "task_id": task_id,
                        "status": "success",
                        "result": task.result,
                    },
                )
                # Give WebSocket handlers time to receive the completion message
                await asyncio.sleep(0.5)

                return task.result

        except Exception as e:
            task.status = "error"
            task.error = str(e)
            task.completed_at = datetime.utcnow()

            # Broadcast error signal to all subscribers
            await self._broadcast(
                task_id,
                {
                    "type": "error",
                    "task_id": task_id,
                    "error": str(e),
                },
            )

            return {"status": "error", "error": str(e)}

        finally:
            # Restore original working directory
            os.chdir(original_cwd)

    async def execute_chat_planning(
        self,
        task_id: str,
        requirements: str,
        enable_indexing: bool = False,
        enable_user_interaction: bool = True,  # Enable User-in-Loop by default
    ) -> Dict[str, Any]:
        """Execute chat-based planning workflow"""
        # Lazy imports - DeepCode modules found via sys.path set in main.py
        from mcp_agent.app import MCPApp
        from workflows.agent_orchestration_engine import (
            execute_chat_based_planning_pipeline,
        )

        task = self._tasks.get(task_id)
        if not task:
            return {"status": "error", "error": "Task not found"}

        task.status = "running"
        task.started_at = datetime.utcnow()

        try:
            progress_callback = await self._create_progress_callback(task_id)

            # Change to project root directory for MCP server paths to work correctly
            original_cwd = os.getcwd()
            os.chdir(PROJECT_ROOT)

            # Create MCP app context with explicit config path
            app = MCPApp(name="chat_planning", settings=str(CONFIG_PATH))

            async with app.run() as agent_app:
                logger = agent_app.logger
                context = agent_app.context

                # Add current working directory to filesystem server args
                context.config.mcp.servers["filesystem"].args.extend([os.getcwd()])

                # --- User-in-Loop: Before Planning Hook ---
                final_requirements = requirements
                plugin_integration = self._get_plugin_integration()

                if enable_user_interaction and plugin_integration:
                    try:
                        from workflows.plugins import InteractionPoint

                        # Create plugin context
                        plugin_context = plugin_integration.create_context(
                            task_id=task_id,
                            user_input=requirements,
                            requirements=requirements,
                            enable_indexing=enable_indexing,
                        )

                        # Run BEFORE_PLANNING plugins (requirement analysis)
                        plugin_context = await plugin_integration.run_hook(
                            InteractionPoint.BEFORE_PLANNING, plugin_context
                        )

                        # Check if workflow was cancelled by user
                        if plugin_context.get("workflow_cancelled"):
                            task.status = "cancelled"
                            task.completed_at = datetime.utcnow()
                            return {
                                "status": "cancelled",
                                "reason": plugin_context.get(
                                    "cancel_reason", "Cancelled by user"
                                ),
                            }

                        # Use potentially enhanced requirements
                        final_requirements = plugin_context.get(
                            "requirements", requirements
                        )
                        print(
                            f"[WorkflowService] Requirements after plugin: {len(final_requirements)} chars"
                        )

                    except Exception as plugin_error:
                        print(
                            f"[WorkflowService] Plugin error (continuing without): {plugin_error}"
                        )
                        # Continue without plugin enhancement

                # Execute the pipeline with (possibly enhanced) requirements
                result = await execute_chat_based_planning_pipeline(
                    final_requirements,
                    logger,
                    progress_callback,
                    enable_indexing=enable_indexing,
                )

                task.status = "completed"
                task.progress = 100
                task.result = {
                    "status": "success",
                    "repo_result": result,
                }
                task.completed_at = datetime.utcnow()

                # Broadcast completion signal to all subscribers
                await self._broadcast(
                    task_id,
                    {
                        "type": "complete",
                        "task_id": task_id,
                        "status": "success",
                        "result": task.result,
                    },
                )
                # Give WebSocket handlers time to receive the completion message
                await asyncio.sleep(0.5)

                return task.result

        except Exception as e:
            task.status = "error"
            task.error = str(e)
            task.completed_at = datetime.utcnow()

            # Broadcast error signal to all subscribers
            await self._broadcast(
                task_id,
                {
                    "type": "error",
                    "task_id": task_id,
                    "error": str(e),
                },
            )

            return {"status": "error", "error": str(e)}

        finally:
            # Restore original working directory
            os.chdir(original_cwd)

    def cancel_task(self, task_id: str) -> bool:
        """Cancel a running task"""
        task = self._tasks.get(task_id)
        if task and task.status == "running":
            task.cancel_event.set()
            task.status = "cancelled"
            return True
        return False

    def cleanup_task(self, task_id: str):
        """Clean up task resources"""
        if task_id in self._tasks:
            del self._tasks[task_id]
        if task_id in self._subscribers:
            del self._subscribers[task_id]

    def get_active_tasks(self) -> List[WorkflowTask]:
        """Get all tasks that are currently running"""
        return [task for task in self._tasks.values() if task.status == "running"]

    def get_recent_tasks(self, limit: int = 10) -> List[WorkflowTask]:
        """Get recent tasks sorted by start time (newest first)"""
        tasks = list(self._tasks.values())
        # Sort by started_at descending (newest first)
        tasks.sort(key=lambda t: t.started_at or datetime.min, reverse=True)
        return tasks[:limit]


# Global service instance
workflow_service = WorkflowService()
