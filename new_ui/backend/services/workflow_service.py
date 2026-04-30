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
import shutil
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any, Callable, List
from dataclasses import dataclass, field

from settings import CONFIG_PATH, PROJECT_ROOT
from services.session_service import session_store


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
    # Session integration: every WorkflowTask belongs to exactly one
    # session. ``task_short_id`` mirrors the 8-char id used by
    # workflows.environment for task_dir naming and log routing.
    session_id: Optional[str] = None
    task_kind: str = "paper"  # paper | chat | url | repo | requirement
    task_short_id: Optional[str] = None
    task_dir: Optional[str] = None


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

    def create_task(
        self,
        session_id: Optional[str] = None,
        *,
        task_kind: str = "paper",
        title: Optional[str] = None,
    ) -> WorkflowTask:
        """Create a new workflow task, attached to a session.

        When ``session_id`` is omitted a fresh session is created so
        every task is reachable from the session listing endpoint.
        """
        task_id = str(uuid.uuid4())
        if session_id is None or session_store.get_session(session_id) is None:
            session = session_store.create_session(title=title or "")
            session_id = session.session_id
        task = WorkflowTask(
            task_id=task_id,
            session_id=session_id,
            task_kind=task_kind,
        )
        self._tasks[task_id] = task
        self._subscribers[task_id] = []
        return task

    def get_task(self, task_id: str) -> Optional[WorkflowTask]:
        """Get task by ID"""
        return self._tasks.get(task_id)

    def get_task_by_any_id(self, task_id: str) -> Optional[WorkflowTask]:
        """Get a task by full UUID, short id, or restored short task id.

        UI clients may hold the full UUID from the original create response,
        while hydrated tasks after a backend restart are keyed by the 8-char
        task id persisted in the session store.
        """
        if not task_id:
            return None
        task = self._tasks.get(task_id)
        if task is not None:
            return task
        short = str(task_id)[:8]
        task = self._tasks.get(short)
        if task is not None:
            return task
        return next(
            (
                candidate
                for candidate in self._tasks.values()
                if candidate.task_short_id == task_id
                or candidate.task_short_id == short
                or candidate.task_id[:8] == short
            ),
            None,
        )

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

        def callback(progress: int, message: str, error: Optional[str] = None):
            if task:
                if task.cancel_event.is_set() or task.status == "cancelled":
                    raise asyncio.CancelledError("Workflow cancelled by user")
                task.progress = progress
                task.message = message
                if error:
                    task.error = error

            # Broadcast to all subscribers
            asyncio.create_task(
                self._broadcast(
                    task_id,
                    {
                        "type": "progress",
                        "task_id": task_id,
                        "progress": progress,
                        "message": message,
                        "error": error,
                        "timestamp": datetime.utcnow().isoformat(),
                    },
                )
            )

        return callback

    def _create_plan_review_callback(
        self,
        task_id: str,
        *,
        enabled: bool = True,
    ) -> Optional[Callable[[Dict[str, Any]], Any]]:
        """Create a task-scoped plan review callback for the core workflow."""
        if not enabled:
            return None

        async def callback(request: Dict[str, Any]) -> Dict[str, Any]:
            plugin_integration = self._get_plugin_integration()
            if not plugin_integration:
                return {
                    "action": "skip",
                    "skipped": True,
                    "reason": "plugin_integration_unavailable",
                }

            from workflows.plugins.base import InteractionRequest

            interaction = InteractionRequest(
                interaction_type=request.get("interaction_type", "plan_review"),
                title=request.get("title", "Review Implementation Plan"),
                description=request.get("description", ""),
                data=request.get("data", {}),
                options=request.get("options", {}),
                required=bool(request.get("required", False)),
                timeout_seconds=int(request.get("timeout_seconds", 1800)),
            )
            response = await plugin_integration.request_interaction(
                task_id, interaction
            )
            data = dict(response.data or {})
            return {
                "action": response.action,
                "data": data,
                "skipped": response.skipped,
                **data,
            }

        return callback

    async def _mark_task_cancelled(
        self,
        task_id: str,
        reason: str,
    ) -> Dict[str, Any]:
        task = self._tasks.get(task_id)
        if task:
            task.status = "cancelled"
            task.message = reason
            task.error = None
            task.pending_interaction = None
            task.completed_at = datetime.utcnow()

        plugin_integration = self._plugin_integration
        if plugin_integration:
            plugin_integration.cancel_interaction(task_id)

        result = {"status": "cancelled", "reason": reason}
        await self._broadcast(
            task_id,
            {
                "type": "cancelled",
                "task_id": task_id,
                "status": "cancelled",
                "reason": reason,
                "timestamp": datetime.utcnow().isoformat(),
            },
        )
        await asyncio.sleep(0.5)
        return result

    async def execute_paper_to_code(
        self,
        task_id: str,
        input_source: str,
        input_type: str,
        enable_indexing: bool = False,
        enable_user_interaction: bool = True,
    ) -> Dict[str, Any]:
        """Execute paper-to-code workflow"""
        # Lazy imports - DeepCode modules found via sys.path set in main.py
        from core.compat import MCPApp
        from core.observability import bind_task, pop_task, set_session
        from workflows.agent_orchestration_engine import (
            execute_multi_agent_research_pipeline,
        )
        from workflows.plan_review_runtime import PlanReviewCancelled

        task = self._tasks.get(task_id)
        if not task:
            return {"status": "error", "error": "Task not found"}

        task.status = "running"
        task.started_at = datetime.utcnow()

        # Bind task_id into the async context so every loguru call and
        # provider/MCP record made downstream is automatically attributed
        # to this task (no business-code change needed).
        short_task_id = str(task_id)[:8] if task_id else None
        task.task_short_id = short_task_id
        task.task_kind = self._infer_task_kind(input_type, task.task_kind)
        task_token = bind_task(short_task_id)
        session_id = getattr(task, "session_id", None)
        session_token = set_session(session_id) if session_id else None

        # Record the user-facing intent in the session transcript so the
        # session listing UI / CLI shows a meaningful preview.
        if session_id:
            try:
                session_store.append_message(
                    session_id,
                    role="user",
                    content=f"[{task.task_kind}] {input_source}",
                    task_id_ref=short_task_id,
                    metadata={"input_type": input_type},
                )
            except Exception:
                pass

        try:
            progress_callback = await self._create_progress_callback(task_id)

            # Change to project root directory for MCP server paths to work correctly
            original_cwd = os.getcwd()
            os.chdir(PROJECT_ROOT)

            # Create MCP app context with explicit config path
            app = MCPApp(name="paper_to_code", settings=str(CONFIG_PATH))

            async with app.run() as agent_app:
                logger = agent_app.logger
                # NOTE: filesystem MCP allowed-dirs are now managed by
                # workflows.environment.prepare_workflow_environment(). Do not
                # patch agent_app.context.config.mcp.servers["filesystem"].args here.

                # Execute the pipeline (task_id forwarded for cross-tab isolation)
                result = await execute_multi_agent_research_pipeline(
                    input_source,
                    logger,
                    progress_callback,
                    enable_indexing=enable_indexing,
                    task_id=str(task_id)[:8] if task_id else None,
                    plan_review_callback=self._create_plan_review_callback(
                        task_id, enabled=enable_user_interaction
                    ),
                )

                result_status = self._pipeline_status(result)
                task.status = result_status
                task.progress = 100 if result_status == "completed" else 95
                task.result = self._build_workflow_result(result, result_status)
                task.completed_at = datetime.utcnow()
                self._record_session_outcome(
                    task,
                    role="assistant",
                    body=self._pipeline_summary(result),
                    metadata=self._pipeline_metadata(result),
                )

                # Broadcast completion signal to all subscribers
                await self._broadcast(
                    task_id,
                    {
                        "type": "complete",
                        "task_id": task_id,
                        "status": result_status,
                        "result": task.result,
                        "timestamp": datetime.utcnow().isoformat(),
                    },
                )
                # Give WebSocket handlers time to receive the completion message
                await asyncio.sleep(0.5)

                return task.result

        except (PlanReviewCancelled, asyncio.CancelledError) as e:
            reason = getattr(e, "reason", None) or str(e) or "Workflow cancelled"
            return await self._mark_task_cancelled(task_id, reason)

        except Exception as e:
            task.status = "error"
            task.error = str(e)
            task.completed_at = datetime.utcnow()
            self._record_session_outcome(
                task, role="system", body=f"Workflow failed: {e}"
            )

            # Broadcast error signal to all subscribers
            await self._broadcast(
                task_id,
                {
                    "type": "error",
                    "task_id": task_id,
                    "error": str(e),
                        "timestamp": datetime.utcnow().isoformat(),
                },
            )

            return {"status": "error", "error": str(e)}

        finally:
            # Restore original working directory
            os.chdir(original_cwd)
            # Detach task / session context vars
            if session_token is not None:
                from core.observability import pop_session as _pop_session

                _pop_session(session_token)
            pop_task(task_token)

    async def execute_chat_planning(
        self,
        task_id: str,
        requirements: str,
        enable_indexing: bool = False,
        enable_user_interaction: bool = True,  # Enable User-in-Loop by default
    ) -> Dict[str, Any]:
        """Execute chat-based planning workflow"""
        # Lazy imports - DeepCode modules found via sys.path set in main.py
        from core.compat import MCPApp
        from core.observability import bind_task, pop_task, set_session
        from workflows.agent_orchestration_engine import (
            execute_chat_based_planning_pipeline,
        )
        from workflows.plan_review_runtime import PlanReviewCancelled

        task = self._tasks.get(task_id)
        if not task:
            return {"status": "error", "error": "Task not found"}

        task.status = "running"
        task.started_at = datetime.utcnow()

        short_task_id = str(task_id)[:8] if task_id else None
        task.task_short_id = short_task_id
        task.task_kind = "chat"
        task_token = bind_task(short_task_id)
        session_id = getattr(task, "session_id", None)
        session_token = set_session(session_id) if session_id else None

        if session_id:
            try:
                session_store.append_message(
                    session_id,
                    role="user",
                    content=requirements,
                    task_id_ref=short_task_id,
                    metadata={"input_type": "chat"},
                )
            except Exception:
                pass

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

                _fs = context.config.mcp.servers.get("filesystem")
                if _fs is not None and os.getcwd() not in _fs.args:
                    _fs.args.append(os.getcwd())

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
                    task_id=str(task_id)[:8] if task_id else None,
                    plan_review_callback=self._create_plan_review_callback(
                        task_id, enabled=enable_user_interaction
                    ),
                )

                result_status = self._pipeline_status(result)
                task.status = result_status
                task.progress = 100 if result_status == "completed" else 95
                task.result = self._build_workflow_result(result, result_status)
                task.completed_at = datetime.utcnow()
                self._record_session_outcome(
                    task,
                    role="assistant",
                    body=self._pipeline_summary(result),
                    metadata=self._pipeline_metadata(result),
                )

                # Broadcast completion signal to all subscribers
                await self._broadcast(
                    task_id,
                    {
                        "type": "complete",
                        "task_id": task_id,
                        "status": result_status,
                        "result": task.result,
                        "timestamp": datetime.utcnow().isoformat(),
                    },
                )
                # Give WebSocket handlers time to receive the completion message
                await asyncio.sleep(0.5)

                return task.result

        except (PlanReviewCancelled, asyncio.CancelledError) as e:
            reason = getattr(e, "reason", None) or str(e) or "Workflow cancelled"
            return await self._mark_task_cancelled(task_id, reason)

        except Exception as e:
            task.status = "error"
            task.error = str(e)
            task.completed_at = datetime.utcnow()
            self._record_session_outcome(
                task, role="system", body=f"Workflow failed: {e}"
            )

            # Broadcast error signal to all subscribers
            await self._broadcast(
                task_id,
                {
                    "type": "error",
                    "task_id": task_id,
                    "error": str(e),
                        "timestamp": datetime.utcnow().isoformat(),
                },
            )

            return {"status": "error", "error": str(e)}

        finally:
            # Restore original working directory
            os.chdir(original_cwd)
            if session_token is not None:
                from core.observability import pop_session as _pop_session

                _pop_session(session_token)
            pop_task(task_token)

    def cancel_task(self, task_id: str) -> bool:
        """Cancel a running task"""
        task = self.get_task_by_any_id(task_id)
        if task and task.status in {"running", "waiting_for_input"}:
            task.cancel_event.set()
            task.status = "cancelled"
            task.message = "Workflow cancelled by user"
            task.pending_interaction = None
            task.completed_at = datetime.utcnow()
            self._record_session_outcome(
                task, role="system", body="Workflow cancelled by user"
            )
            plugin_integration = self._plugin_integration
            if plugin_integration:
                plugin_integration.cancel_interaction(task_id)
            asyncio.create_task(
                self._broadcast(
                    task.task_id,
                    {
                        "type": "cancelled",
                        "task_id": task.task_id,
                        "status": "cancelled",
                        "reason": task.message,
                        "timestamp": datetime.utcnow().isoformat(),
                    },
                )
            )
            return True
        return False

    def cleanup_task(self, task_id: str):
        """Clean up task resources"""
        if task_id in self._tasks:
            del self._tasks[task_id]
        if task_id in self._subscribers:
            del self._subscribers[task_id]

    def delete_session_cascade(self, session_id: str) -> Dict[str, Any]:
        """Delete a session and its task workspaces when no task is active.

        Raw uploaded files under ``uploads/`` are intentionally preserved because
        they may be shared across sessions. Only task directories recorded in the
        session store and located under ``deepcode_lab/tasks`` are removed.
        """
        session = session_store.get_session(session_id)
        if session is None:
            return {"status": "not_found", "session_id": session_id}

        blocking_statuses = {"pending", "running", "waiting_for_input"}
        task_ids = {task.task_id for task in session.tasks}
        running_tasks = [
            {
                "task_id": task.task_id,
                "task_short_id": task.task_short_id,
                "status": task.status,
            }
            for task in self._tasks.values()
            if task.status in blocking_statuses
            and (
                task.session_id == session_id
                or task.task_short_id in task_ids
                or task.task_id in task_ids
            )
        ]
        stored_blocking_tasks = [
            {
                "task_id": task.task_id,
                "status": task.status,
            }
            for task in session.tasks
            if task.status in blocking_statuses
        ]
        if running_tasks or stored_blocking_tasks:
            return {
                "status": "blocked",
                "session_id": session_id,
                "reason": "Session has running or pending tasks.",
                "running_tasks": running_tasks,
                "stored_blocking_tasks": stored_blocking_tasks,
            }

        allowed_tasks_root = (PROJECT_ROOT / "deepcode_lab" / "tasks").resolve()
        deleted_task_dirs: List[str] = []
        missing_task_dirs: List[str] = []
        skipped_task_dirs: List[str] = []

        for stored in session.tasks:
            task_dir_raw = (stored.task_dir or "").strip()
            if not task_dir_raw:
                continue
            task_dir = Path(task_dir_raw).expanduser()
            if not task_dir.is_absolute():
                task_dir = PROJECT_ROOT / task_dir
            task_dir = task_dir.resolve()

            if not self._is_safe_task_dir(task_dir, allowed_tasks_root):
                skipped_task_dirs.append(str(task_dir))
                continue
            if not task_dir.exists():
                missing_task_dirs.append(str(task_dir))
                continue
            shutil.rmtree(task_dir)
            deleted_task_dirs.append(str(task_dir))

        for key, task in list(self._tasks.items()):
            if (
                task.session_id == session_id
                or task.task_short_id in task_ids
                or task.task_id in task_ids
            ):
                self._tasks.pop(key, None)
                self._subscribers.pop(key, None)

        deleted = session_store.delete_session(session_id)
        if not deleted:
            return {
                "status": "not_found",
                "session_id": session_id,
                "deleted_task_dirs": deleted_task_dirs,
                "missing_task_dirs": missing_task_dirs,
                "skipped_task_dirs": skipped_task_dirs,
            }

        return {
            "status": "deleted",
            "session_id": session_id,
            "deleted_task_dirs": deleted_task_dirs,
            "missing_task_dirs": missing_task_dirs,
            "skipped_task_dirs": skipped_task_dirs,
            "uploads_deleted": False,
        }

    def get_active_tasks(self) -> List[WorkflowTask]:
        """Get all tasks that are currently running"""
        return [
            task
            for task in self._tasks.values()
            if task.status in {"running", "waiting_for_input"}
        ]

    def get_recent_tasks(self, limit: int = 10) -> List[WorkflowTask]:
        """Get recent tasks sorted by start time (newest first)"""
        tasks = list(self._tasks.values())
        # Sort by started_at descending (newest first)
        tasks.sort(key=lambda t: t.started_at or datetime.min, reverse=True)
        return tasks[:limit]

    # ------------------------------------------------------------------
    # Session integration helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _is_safe_task_dir(task_dir: Path, allowed_tasks_root: Path) -> bool:
        if task_dir == allowed_tasks_root:
            return False
        try:
            task_dir.relative_to(allowed_tasks_root)
        except ValueError:
            return False
        return True

    @staticmethod
    def _infer_task_kind(input_type: str, fallback: str) -> str:
        mapping = {
            "file": "paper",
            "url": "url",
            "chat": "chat",
            "requirement": "requirement",
            "repo": "repo",
        }
        return mapping.get((input_type or "").lower(), fallback)

    @staticmethod
    def _pipeline_status(result: Any) -> str:
        if isinstance(result, dict):
            status = str(result.get("status") or "completed")
            if status == "success":
                return "completed"
            return status
        return "completed"

    @staticmethod
    def _pipeline_summary(result: Any) -> str:
        if isinstance(result, dict):
            return str(result.get("summary") or result)
        return str(result)

    @staticmethod
    def _pipeline_metadata(result: Any) -> Dict[str, Any]:
        if not isinstance(result, dict):
            return {}
        metadata: Dict[str, Any] = {}
        implementation = result.get("implementation")
        if isinstance(implementation, dict):
            metadata["implementation"] = implementation
        return metadata

    @staticmethod
    def _build_workflow_result(result: Any, result_status: str) -> Dict[str, Any]:
        payload = {
            "status": "success" if result_status == "completed" else result_status,
            "repo_result": result,
        }
        if isinstance(result, dict) and isinstance(result.get("implementation"), dict):
            payload["implementation"] = result["implementation"]
        return payload

    def _record_session_outcome(
        self,
        task: "WorkflowTask",
        *,
        role: str,
        body: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Append the task's final state to its session transcript."""
        sid = getattr(task, "session_id", None)
        if not sid:
            return
        try:
            session_store.append_message(
                sid,
                role=role,
                content=body[:8000],
                task_id_ref=task.task_short_id or task.task_id[:8],
                metadata={
                    "status": task.status,
                    "task_kind": task.task_kind,
                    **(metadata or {}),
                },
            )
            session_store.update_task_status(
                sid,
                task.task_short_id or task.task_id[:8],
                task.status,
                metadata=metadata,
            )
        except Exception:
            pass

    def hydrate_from_sessions(self) -> int:
        """Rebuild :class:`WorkflowTask` rows from the on-disk session store.

        Called by the FastAPI lifespan hook so a backend restart does
        not erase the user's task history. Live task progress cannot be
        recovered (the original asyncio coroutine is gone), so any
        previously running task is marked as ``interrupted``.
        """
        restored = 0
        for session, stored in session_store.list_attached_tasks():
            short = stored.task_id
            if short in self._tasks:
                continue
            status = stored.status
            if status in {"pending", "running", "waiting_for_input"}:
                status = "interrupted"
                try:
                    session_store.update_task_status(
                        session.session_id,
                        stored.task_id,
                        status,
                        metadata={
                            "interrupted": True,
                            "reason": "Backend restarted before the workflow completed.",
                        },
                    )
                except Exception:
                    pass
            task = WorkflowTask(
                task_id=short,
                status=status,
                session_id=session.session_id,
                task_kind=stored.task_kind,
                task_short_id=short,
                task_dir=stored.task_dir,
                message=f"Restored from session {session.session_id}",
            )
            self._tasks[short] = task
            self._subscribers[short] = []
            restored += 1
        return restored


# Global service instance
workflow_service = WorkflowService()
