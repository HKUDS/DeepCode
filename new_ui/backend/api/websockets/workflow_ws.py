"""
Workflow WebSocket Handler
Provides real-time progress updates for running workflows
"""

import asyncio
from datetime import datetime
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from services.workflow_service import workflow_service


router = APIRouter()


class ConnectionManager:
    """Manages WebSocket connections for workflow updates"""

    def __init__(self):
        self.active_connections: dict[str, list[WebSocket]] = {}

    async def connect(self, websocket: WebSocket, task_id: str):
        await websocket.accept()
        if task_id not in self.active_connections:
            self.active_connections[task_id] = []
        self.active_connections[task_id].append(websocket)

    def disconnect(self, websocket: WebSocket, task_id: str):
        if task_id in self.active_connections:
            if websocket in self.active_connections[task_id]:
                self.active_connections[task_id].remove(websocket)
            if not self.active_connections[task_id]:
                del self.active_connections[task_id]

    async def broadcast(self, task_id: str, message: dict):
        if task_id in self.active_connections:
            for connection in self.active_connections[task_id]:
                try:
                    await connection.send_json(message)
                except Exception:
                    pass


manager = ConnectionManager()


@router.websocket("/workflow/{task_id}")
async def workflow_websocket(websocket: WebSocket, task_id: str):
    """
    WebSocket endpoint for real-time workflow progress updates.

    Connect to receive:
    - progress: Workflow step progress updates
    - complete: Workflow completion notification
    - error: Error notifications

    Message format:
    {
        "type": "progress" | "complete" | "error",
        "task_id": str,
        "progress": int,  # 0-100
        "message": str,
        "timestamp": str,
        "result": dict | null,  # Only for complete type
        "error": str | null  # Only for error type
    }
    """
    await manager.connect(websocket, task_id)
    print(f"[WorkflowWS] Connected: task={task_id[:8]}...")

    # Subscribe to get our own queue for this task
    queue = workflow_service.subscribe(task_id)
    task = workflow_service.get_task(task_id)
    print(
        f"[WorkflowWS] Subscribed: task={task_id[:8]}... queue={queue is not None} task={task is not None}"
    )

    if not task:
        await websocket.send_json(
            {
                "type": "error",
                "task_id": task_id,
                "error": "Task not found",
                "timestamp": datetime.utcnow().isoformat(),
            }
        )
        await websocket.close()
        return

    # Send current status
    await websocket.send_json(
        {
            "type": "status",
            "task_id": task_id,
            "status": task.status,
            "progress": task.progress,
            "message": task.message,
            "timestamp": datetime.utcnow().isoformat(),
        }
    )

    # Send pending interaction if any (fixes race condition where interaction_required
    # was broadcast before WebSocket connected)
    if task.pending_interaction:
        print(f"[WorkflowWS] Sending missed pending interaction: task={task_id[:8]}...")
        await websocket.send_json(
            {
                "type": "interaction_required",
                "task_id": task_id,
                "interaction_type": task.pending_interaction.get("type"),
                "title": task.pending_interaction.get("title"),
                "description": task.pending_interaction.get("description"),
                "data": task.pending_interaction.get("data"),
                "options": task.pending_interaction.get("options"),
                "required": task.pending_interaction.get("required"),
                "timestamp": datetime.utcnow().isoformat(),
            }
        )

    try:
        # If task is already completed, send final status and close
        if task.status in ("completed", "error", "cancelled"):
            if task.status == "completed":
                await websocket.send_json(
                    {
                        "type": "complete",
                        "task_id": task_id,
                        "result": task.result,
                        "timestamp": datetime.utcnow().isoformat(),
                    }
                )
            elif task.status == "error":
                await websocket.send_json(
                    {
                        "type": "error",
                        "task_id": task_id,
                        "error": task.error,
                        "timestamp": datetime.utcnow().isoformat(),
                    }
                )
            # Close WebSocket (don't cleanup immediately - keep task for status queries)
            await websocket.close()
            return

        # Stream progress updates
        if queue:
            while True:
                try:
                    # Wait for progress update with timeout
                    message = await asyncio.wait_for(queue.get(), timeout=60.0)
                    msg_type = message.get("type")
                    print(
                        f"[WorkflowWS] Sending: task={task_id[:8]}... type={msg_type}"
                    )
                    await websocket.send_json(message)

                    # Check if workflow is complete
                    if msg_type in ("complete", "error"):
                        print(
                            f"[WorkflowWS] Workflow finished: task={task_id[:8]}... type={msg_type}"
                        )
                        # Wait a bit before closing to ensure frontend processes the message
                        await asyncio.sleep(0.5)
                        await websocket.close()
                        break

                except asyncio.TimeoutError:
                    # Send heartbeat
                    await websocket.send_json(
                        {
                            "type": "heartbeat",
                            "task_id": task_id,
                            "timestamp": datetime.utcnow().isoformat(),
                        }
                    )

    except WebSocketDisconnect:
        pass
    finally:
        manager.disconnect(websocket, task_id)
        # Unsubscribe from task updates
        if queue:
            workflow_service.unsubscribe(task_id, queue)
