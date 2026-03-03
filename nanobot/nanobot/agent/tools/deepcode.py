"""
DeepCode integration tools for nanobot.

These tools allow nanobot to interact with the DeepCode backend API
for paper-to-code reproduction, chat-based code generation, and task management.

Communication: HTTP requests to DeepCode's FastAPI backend.
In Docker Compose: nanobot -> http://deepcode:8000/api/v1/...
"""

import os
from typing import Any

import httpx

from nanobot.agent.tools.base import Tool


def _get_deepcode_url() -> str:
    """Get DeepCode API base URL from environment."""
    return os.environ.get("DEEPCODE_API_URL", "http://deepcode:8000")


class DeepCodePaper2CodeTool(Tool):
    """Submit a paper (URL or file path) to DeepCode for automatic code reproduction."""

    def __init__(self, api_url: str | None = None):
        self._api_url = api_url or _get_deepcode_url()

    @property
    def name(self) -> str:
        return "deepcode_paper2code"

    @property
    def description(self) -> str:
        return (
            "Submit a research paper to DeepCode for automatic code reproduction. "
            "Accepts a paper URL (e.g. arxiv link) or a local file path. "
            "Returns a task ID for tracking progress. "
            "The code generation process runs in the background and may take 10-60 minutes. "
            "Use deepcode_status to check progress."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "input_source": {
                    "type": "string",
                    "description": "Paper URL (e.g. https://arxiv.org/abs/...) or local file path",
                },
                "input_type": {
                    "type": "string",
                    "enum": ["url", "file"],
                    "description": "Type of input: 'url' for web links, 'file' for local files",
                },
                "enable_indexing": {
                    "type": "boolean",
                    "description": "Enable code reference indexing for enhanced quality (slower but better). Default: false",
                },
            },
            "required": ["input_source", "input_type"],
        }

    async def execute(
        self,
        input_source: str,
        input_type: str = "url",
        enable_indexing: bool = False,
        **kwargs: Any,
    ) -> str:
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    f"{self._api_url}/api/v1/workflows/paper-to-code",
                    json={
                        "input_source": input_source,
                        "input_type": input_type,
                        "enable_indexing": enable_indexing,
                    },
                )
                response.raise_for_status()
                data = response.json()
                task_id = data.get("task_id", "unknown")
                return (
                    f"Paper-to-code task submitted successfully!\n"
                    f"Task ID: {task_id}\n"
                    f"Status: {data.get('status', 'started')}\n"
                    f"Input: {input_source}\n"
                    f"Indexing: {'enabled' if enable_indexing else 'disabled (fast mode)'}\n\n"
                    f"The code generation is running in the background. "
                    f"Use deepcode_status with task_id='{task_id}' to check progress."
                )
        except httpx.ConnectError:
            return "Error: Cannot connect to DeepCode backend. Is the DeepCode service running?"
        except httpx.HTTPStatusError as e:
            return (
                f"Error: DeepCode API returned status {e.response.status_code}: {e.response.text}"
            )
        except Exception as e:
            return f"Error submitting paper to DeepCode: {str(e)}"


class DeepCodeChat2CodeTool(Tool):
    """Submit text requirements to DeepCode for code generation."""

    def __init__(self, api_url: str | None = None):
        self._api_url = api_url or _get_deepcode_url()

    @property
    def name(self) -> str:
        return "deepcode_chat2code"

    @property
    def description(self) -> str:
        return (
            "Submit coding requirements to DeepCode for automatic code generation. "
            "Provide a text description of what you want to build (e.g. web app, algorithm, backend service). "
            "DeepCode will generate a complete implementation. "
            "Returns a task ID for tracking progress."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "requirements": {
                    "type": "string",
                    "description": "Detailed description of coding requirements",
                },
                "enable_indexing": {
                    "type": "boolean",
                    "description": "Enable code reference indexing for enhanced quality. Default: false",
                },
            },
            "required": ["requirements"],
        }

    async def execute(
        self,
        requirements: str,
        enable_indexing: bool = False,
        **kwargs: Any,
    ) -> str:
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    f"{self._api_url}/api/v1/workflows/chat-planning",
                    json={
                        "requirements": requirements,
                        "enable_indexing": enable_indexing,
                    },
                )
                response.raise_for_status()
                data = response.json()
                task_id = data.get("task_id", "unknown")
                return (
                    f"Chat-to-code task submitted successfully!\n"
                    f"Task ID: {task_id}\n"
                    f"Status: {data.get('status', 'started')}\n"
                    f"Requirements: {requirements[:200]}{'...' if len(requirements) > 200 else ''}\n\n"
                    f"The code generation is running in the background. "
                    f"Use deepcode_status with task_id='{task_id}' to check progress."
                )
        except httpx.ConnectError:
            return "Error: Cannot connect to DeepCode backend. Is the DeepCode service running?"
        except httpx.HTTPStatusError as e:
            return (
                f"Error: DeepCode API returned status {e.response.status_code}: {e.response.text}"
            )
        except Exception as e:
            return f"Error submitting requirements to DeepCode: {str(e)}"


class DeepCodeStatusTool(Tool):
    """Check the status and progress of a DeepCode task."""

    def __init__(self, api_url: str | None = None):
        self._api_url = api_url or _get_deepcode_url()

    @property
    def name(self) -> str:
        return "deepcode_status"

    @property
    def description(self) -> str:
        return (
            "Check the status and progress of a DeepCode code generation task. "
            "Provide the task_id returned by deepcode_paper2code or deepcode_chat2code. "
            "Returns current status, progress percentage, and result when complete."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "task_id": {
                    "type": "string",
                    "description": "The task ID to check status for",
                },
            },
            "required": ["task_id"],
        }

    async def execute(self, task_id: str, **kwargs: Any) -> str:
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.get(f"{self._api_url}/api/v1/workflows/status/{task_id}")
                response.raise_for_status()
                data = response.json()

                status = data.get("status", "unknown")
                progress = data.get("progress", 0)
                message = data.get("message", "")
                result = data.get("result")
                error = data.get("error")

                lines = [
                    f"Task ID: {task_id}",
                    f"Status: {status}",
                    f"Progress: {progress}%",
                ]

                if message:
                    lines.append(f"Message: {message}")

                if status == "completed" and result:
                    lines.append(f"\nResult:\n{result}")
                elif status == "error" and error:
                    lines.append(f"\nError: {error}")
                elif status == "waiting_for_input":
                    interaction = data.get("pending_interaction")
                    if interaction:
                        lines.append("\nWaiting for user input:")
                        lines.append(f"  Type: {interaction.get('type', 'unknown')}")
                        lines.append(f"  Title: {interaction.get('title', '')}")
                        lines.append(f"  Description: {interaction.get('description', '')}")

                return "\n".join(lines)

        except httpx.ConnectError:
            return "Error: Cannot connect to DeepCode backend. Is the DeepCode service running?"
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                return f"Error: Task '{task_id}' not found. It may have expired."
            return (
                f"Error: DeepCode API returned status {e.response.status_code}: {e.response.text}"
            )
        except Exception as e:
            return f"Error checking task status: {str(e)}"


class DeepCodeListTasksTool(Tool):
    """List active and recent DeepCode tasks."""

    def __init__(self, api_url: str | None = None):
        self._api_url = api_url or _get_deepcode_url()

    @property
    def name(self) -> str:
        return "deepcode_list_tasks"

    @property
    def description(self) -> str:
        return (
            "List all active and recent DeepCode code generation tasks. "
            "Shows task IDs, status, progress, and results summary."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of recent tasks to show. Default: 10",
                    "minimum": 1,
                    "maximum": 50,
                },
            },
        }

    async def execute(self, limit: int = 10, **kwargs: Any) -> str:
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                # Fetch active tasks
                active_resp = await client.get(f"{self._api_url}/api/v1/workflows/active")
                active_resp.raise_for_status()
                active_data = active_resp.json()

                # Fetch recent tasks
                recent_resp = await client.get(
                    f"{self._api_url}/api/v1/workflows/recent",
                    params={"limit": limit},
                )
                recent_resp.raise_for_status()
                recent_data = recent_resp.json()

                lines = []

                # Active tasks
                active_tasks = active_data.get("tasks", [])
                if active_tasks:
                    lines.append(f"=== Active Tasks ({len(active_tasks)}) ===")
                    for task in active_tasks:
                        lines.append(
                            f"  [{task.get('status', '?')}] {task.get('task_id', '?')} "
                            f"- {task.get('progress', 0)}% - {task.get('message', '')}"
                        )
                    lines.append("")

                # Recent tasks
                recent_tasks = recent_data.get("tasks", [])
                if recent_tasks:
                    lines.append(f"=== Recent Tasks ({len(recent_tasks)}) ===")
                    for task in recent_tasks:
                        status_icon = {
                            "completed": "done",
                            "error": "error",
                            "running": "running",
                            "cancelled": "cancelled",
                        }.get(task.get("status", ""), "?")
                        lines.append(
                            f"  [{status_icon}] {task.get('task_id', '?')} "
                            f"- {task.get('status', '?')} - {task.get('message', '')}"
                        )

                if not lines:
                    return "No DeepCode tasks found."

                return "\n".join(lines)

        except httpx.ConnectError:
            return "Error: Cannot connect to DeepCode backend. Is the DeepCode service running?"
        except Exception as e:
            return f"Error listing tasks: {str(e)}"


class DeepCodeCancelTool(Tool):
    """Cancel a running DeepCode task."""

    def __init__(self, api_url: str | None = None):
        self._api_url = api_url or _get_deepcode_url()

    @property
    def name(self) -> str:
        return "deepcode_cancel"

    @property
    def description(self) -> str:
        return "Cancel a running DeepCode code generation task."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "task_id": {
                    "type": "string",
                    "description": "The task ID to cancel",
                },
            },
            "required": ["task_id"],
        }

    async def execute(self, task_id: str, **kwargs: Any) -> str:
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.post(f"{self._api_url}/api/v1/workflows/cancel/{task_id}")
                response.raise_for_status()
                return f"Task '{task_id}' has been cancelled successfully."
        except httpx.ConnectError:
            return "Error: Cannot connect to DeepCode backend. Is the DeepCode service running?"
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 400:
                return f"Error: Task '{task_id}' not found or cannot be cancelled."
            return (
                f"Error: DeepCode API returned status {e.response.status_code}: {e.response.text}"
            )
        except Exception as e:
            return f"Error cancelling task: {str(e)}"


class DeepCodeRespondTool(Tool):
    """Respond to a DeepCode User-in-Loop interaction request."""

    def __init__(self, api_url: str | None = None):
        self._api_url = api_url or _get_deepcode_url()

    @property
    def name(self) -> str:
        return "deepcode_respond"

    @property
    def description(self) -> str:
        return (
            "Respond to a DeepCode User-in-Loop interaction. "
            "When a DeepCode task is waiting for user input (e.g. requirement clarification, "
            "plan review), use this tool to submit the user's response. "
            "First check deepcode_status to see the pending interaction details."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "task_id": {
                    "type": "string",
                    "description": "The task ID that is waiting for input",
                },
                "action": {
                    "type": "string",
                    "enum": ["submit", "confirm", "modify", "skip", "cancel"],
                    "description": "User's action: submit answers, confirm plan, modify, skip, or cancel",
                },
                "data": {
                    "type": "object",
                    "description": "Response data (e.g. answers to questions, modification feedback)",
                },
                "skipped": {
                    "type": "boolean",
                    "description": "Whether the user chose to skip this interaction. Default: false",
                },
            },
            "required": ["task_id", "action"],
        }

    async def execute(
        self,
        task_id: str,
        action: str,
        data: dict | None = None,
        skipped: bool = False,
        **kwargs: Any,
    ) -> str:
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    f"{self._api_url}/api/v1/workflows/respond/{task_id}",
                    json={
                        "action": action,
                        "data": data or {},
                        "skipped": skipped,
                    },
                )
                response.raise_for_status()
                response.json()  # validate JSON response
                return (
                    f"Response submitted successfully!\n"
                    f"Task ID: {task_id}\n"
                    f"Action: {action}\n"
                    f"The workflow will now continue."
                )
        except httpx.ConnectError:
            return "Error: Cannot connect to DeepCode backend. Is the DeepCode service running?"
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 400:
                detail = e.response.json().get("detail", "Unknown error")
                return f"Error: {detail}"
            return (
                f"Error: DeepCode API returned status {e.response.status_code}: {e.response.text}"
            )
        except Exception as e:
            return f"Error responding to interaction: {str(e)}"


# ============================================================
# Helper: create all DeepCode tools at once
# ============================================================


def create_all_tools(api_url: str | None = None) -> list[Tool]:
    """
    Create all DeepCode tools with the given API URL.

    Usage in AgentLoop._register_default_tools():
        deepcode_url = os.environ.get("DEEPCODE_API_URL")
        if deepcode_url:
            from nanobot.agent.tools.deepcode import create_all_tools
            for tool in create_all_tools(api_url=deepcode_url):
                self.tools.register(tool)
    """
    url = api_url or _get_deepcode_url()
    return [
        DeepCodePaper2CodeTool(api_url=url),
        DeepCodeChat2CodeTool(api_url=url),
        DeepCodeStatusTool(api_url=url),
        DeepCodeListTasksTool(api_url=url),
        DeepCodeCancelTool(api_url=url),
        DeepCodeRespondTool(api_url=url),
    ]
