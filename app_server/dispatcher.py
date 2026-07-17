"""JSON-RPC method dispatch; transport validation ends at this boundary."""

from __future__ import annotations

from collections.abc import Callable
from importlib.metadata import PackageNotFoundError, version
from typing import Any

from app_server.connection import ConnectionState
from app_server.errors import (
    InvalidParams,
    MethodNotFound,
    NotInitialized,
    ProtocolMismatch,
)
from app_server.protocol import methods as rpc_methods
from app_server.protocol.codec import DEFAULT_MAX_MESSAGE_BYTES
from app_server.protocol.models import Request
from core.application.application import DeepCodeApplication
from core.application.views import (
    approval_view,
    artifact_view,
    automation_run_view,
    automation_view,
    event_view,
    file_content_view,
    file_diff_view,
    file_entry_view,
    git_status_view,
    item_view,
    hook_info_view,
    mcp_inventory_view,
    project_view,
    skill_detail_view,
    skill_info_view,
    thread_view,
    terminal_info_view,
    test_command_view,
    turn_view,
    workflow_view,
    worktree_result_view,
)
from core.domain.approval import ApprovalStatus
from core.domain.automation import (
    AutomationScheduleKind,
    AutomationStatus,
)
from core.domain.project import TrustState
from core.domain.thread import ThreadMode


PROTOCOL_VERSION = "1.0"
try:
    SERVER_VERSION = version("deepcode-hku")
except PackageNotFoundError:  # source checkout without an editable install
    SERVER_VERSION = "1.2.0"


class Params:
    def __init__(self, values: dict[str, Any]) -> None:
        self.values = values

    def only(self, *allowed: str) -> "Params":
        unknown = set(self.values) - set(allowed)
        if unknown:
            raise InvalidParams(f"unknown parameter(s): {', '.join(sorted(unknown))}")
        return self

    def string(
        self, name: str, *, required: bool = True, allow_empty: bool = False
    ) -> str | None:
        value = self.values.get(name)
        if value is None and not required:
            return None
        if not isinstance(value, str) or (not allow_empty and not value.strip()):
            requirement = "a string" if allow_empty else "a non-empty string"
            raise InvalidParams(f"{name} must be {requirement}")
        return value

    def integer(
        self,
        name: str,
        *,
        default: int,
        minimum: int = 0,
        maximum: int = 1000,
    ) -> int:
        value = self.values.get(name, default)
        if isinstance(value, bool) or not isinstance(value, int):
            raise InvalidParams(f"{name} must be an integer")
        if not minimum <= value <= maximum:
            raise InvalidParams(f"{name} must be between {minimum} and {maximum}")
        return value

    def optional_integer(
        self,
        name: str,
        *,
        minimum: int = 0,
        maximum: int = 1000,
    ) -> int | None:
        if name not in self.values or self.values[name] is None:
            return None
        value = self.values[name]
        if isinstance(value, bool) or not isinstance(value, int):
            raise InvalidParams(f"{name} must be an integer")
        if not minimum <= value <= maximum:
            raise InvalidParams(f"{name} must be between {minimum} and {maximum}")
        return value

    def boolean(self, name: str, *, default: bool = False) -> bool:
        value = self.values.get(name, default)
        if not isinstance(value, bool):
            raise InvalidParams(f"{name} must be a boolean")
        return value

    def object(self, name: str, *, required: bool = True) -> dict[str, Any] | None:
        value = self.values.get(name)
        if value is None and not required:
            return None
        if not isinstance(value, dict):
            raise InvalidParams(f"{name} must be an object")
        return value

    def nullable_string(self, name: str) -> str | None:
        if name not in self.values:
            raise InvalidParams(f"{name} is required")
        value = self.values[name]
        if value is None:
            return None
        if not isinstance(value, str) or not value.strip():
            raise InvalidParams(f"{name} must be a non-empty string or null")
        return value


Handler = Callable[[Params], Any]


class Dispatcher:
    def __init__(
        self,
        application: DeepCodeApplication,
        connection: ConnectionState,
        *,
        max_message_bytes: int = DEFAULT_MAX_MESSAGE_BYTES,
    ) -> None:
        self.application = application
        self.connection = connection
        self.max_message_bytes = max_message_bytes
        self._handlers: dict[str, Handler] = {
            rpc_methods.INITIALIZE: self._initialize,
            rpc_methods.SHUTDOWN: self._shutdown,
            rpc_methods.PROJECT_LIST: self._project_list,
            rpc_methods.PROJECT_ADD: self._project_add,
            rpc_methods.PROJECT_READ: self._project_read,
            rpc_methods.PROJECT_UPDATE: self._project_update,
            rpc_methods.PROJECT_REMOVE: self._project_remove,
            rpc_methods.SETTINGS_READ: self._settings_read,
            rpc_methods.SETTINGS_UPDATE: self._settings_update,
            rpc_methods.SKILLS_LIST: self._skills_list,
            rpc_methods.SKILL_READ: self._skill_read,
            rpc_methods.HOOKS_LIST: self._hooks_list,
            rpc_methods.MCP_LIST: self._mcp_list,
            rpc_methods.MCP_UPSERT: self._mcp_upsert,
            rpc_methods.MCP_REMOVE: self._mcp_remove,
            rpc_methods.DIAGNOSTICS_READ: self._diagnostics_read,
            rpc_methods.AUTOMATION_LIST: self._automation_list,
            rpc_methods.AUTOMATION_CREATE: self._automation_create,
            rpc_methods.AUTOMATION_UPDATE: self._automation_update,
            rpc_methods.AUTOMATION_REMOVE: self._automation_remove,
            rpc_methods.AUTOMATION_RUN: self._automation_run,
            rpc_methods.AUTOMATION_RUNS: self._automation_runs,
            rpc_methods.THREAD_START: self._thread_start,
            rpc_methods.THREAD_RESUME: self._thread_resume,
            rpc_methods.THREAD_LIST: self._thread_list,
            rpc_methods.THREAD_READ: self._thread_read,
            rpc_methods.THREAD_RENAME: self._thread_rename,
            rpc_methods.THREAD_MODEL: self._thread_model,
            rpc_methods.THREAD_ARCHIVE: self._thread_archive,
            rpc_methods.THREAD_FORK: self._thread_fork,
            rpc_methods.TURN_START: self._turn_start,
            rpc_methods.TURN_ENQUEUE: self._turn_enqueue,
            rpc_methods.TURN_READ: self._turn_read,
            rpc_methods.TURN_INTERRUPT: self._turn_interrupt,
            rpc_methods.WORKFLOW_START: self._workflow_start,
            rpc_methods.WORKFLOW_READ: self._workflow_read,
            rpc_methods.WORKFLOW_LIST: self._workflow_list,
            rpc_methods.WORKFLOW_INTERRUPT: self._workflow_interrupt,
            rpc_methods.WORKFLOW_RETRY: self._workflow_retry,
            rpc_methods.WORKFLOW_RESPOND: self._workflow_respond,
            rpc_methods.ARTIFACT_LIST: self._artifact_list,
            rpc_methods.ARTIFACT_READ: self._artifact_read,
            rpc_methods.APPROVAL_RESPOND: self._approval_respond,
            rpc_methods.EVENT_REPLAY: self._event_replay,
            rpc_methods.FILE_LIST: self._file_list,
            rpc_methods.FILE_READ: self._file_read,
            rpc_methods.FILE_WRITE: self._file_write,
            rpc_methods.GIT_STATUS: self._git_status,
            rpc_methods.GIT_DIFF: self._git_diff,
            rpc_methods.GIT_DISCARD: self._git_discard,
            rpc_methods.GIT_WORKTREE_CREATE: self._git_worktree_create,
            rpc_methods.GIT_WORKTREE_REMOVE: self._git_worktree_remove,
            rpc_methods.TERMINAL_CREATE: self._terminal_create,
            rpc_methods.TERMINAL_WRITE: self._terminal_write,
            rpc_methods.TERMINAL_RESIZE: self._terminal_resize,
            rpc_methods.TERMINAL_CLOSE: self._terminal_close,
            rpc_methods.TEST_DISCOVER: self._test_discover,
            rpc_methods.TEST_RUN: self._test_run,
        }

    @property
    def methods(self) -> tuple[str, ...]:
        return tuple(sorted(self._handlers))

    def dispatch(self, request: Request) -> Any:
        handler = self._handlers.get(request.method)
        if handler is None:
            raise MethodNotFound(request.method)
        if request.method != rpc_methods.INITIALIZE and not self.connection.initialized:
            raise NotInitialized()
        return handler(Params(request.params))

    def _initialize(self, params: Params) -> dict[str, Any]:
        params.only("protocolVersion", "clientInfo")
        requested = params.string("protocolVersion")
        if requested != PROTOCOL_VERSION:
            raise ProtocolMismatch(str(requested), PROTOCOL_VERSION)
        client_info = Params(params.object("clientInfo") or {}).only("name", "version")
        client_name = client_info.string("name")
        client_version = client_info.string("version")
        self.connection.initialize(str(client_name))
        self.application.automations.start_scheduler()
        return {
            "protocolVersion": PROTOCOL_VERSION,
            "serverInfo": {"name": "deepcode-app-server", "version": SERVER_VERSION},
            "clientInfo": {"name": client_name, "version": client_version},
            "capabilities": {
                "methods": list(self.methods),
                "eventReplay": True,
                "liveEvents": True,
                "maxMessageBytes": self.max_message_bytes,
            },
        }

    def _shutdown(self, params: Params) -> dict[str, bool]:
        params.only()
        self.connection.shutting_down = True
        return {"accepted": True}

    def _project_list(self, params: Params) -> dict[str, Any]:
        params.only("limit", "offset")
        self.application.threads.reconcile()
        projects = self.application.projects.list(
            limit=params.integer("limit", default=100, minimum=1, maximum=500),
            offset=params.integer("offset", default=0, maximum=1_000_000),
        )
        return {"projects": [project_view(project) for project in projects]}

    def _project_add(self, params: Params) -> dict[str, Any]:
        params.only("path", "displayName", "trustState")
        raw_trust = params.string("trustState", required=False) or TrustState.UNTRUSTED
        try:
            trust = TrustState(raw_trust)
        except ValueError as exc:
            raise InvalidParams("trustState must be untrusted or trusted") from exc
        project = self.application.projects.add(
            str(params.string("path")),
            display_name=params.string("displayName", required=False),
            trust_state=trust,
        )
        return {"project": project_view(project)}

    def _project_read(self, params: Params) -> dict[str, Any]:
        params.only("projectId")
        project = self.application.projects.read(str(params.string("projectId")))
        return {"project": project_view(project)}

    def _project_update(self, params: Params) -> dict[str, Any]:
        params.only("projectId", "displayName", "trustState", "settings")
        raw_trust = params.string("trustState", required=False)
        try:
            trust = TrustState(raw_trust) if raw_trust is not None else None
        except ValueError as exc:
            raise InvalidParams("trustState must be untrusted or trusted") from exc
        project = self.application.projects.update(
            str(params.string("projectId")),
            display_name=params.string("displayName", required=False),
            trust_state=trust,
            settings=params.object("settings", required=False),
        )
        return {"project": project_view(project)}

    def _project_remove(self, params: Params) -> dict[str, bool]:
        params.only("projectId")
        return {
            "removed": self.application.projects.remove(str(params.string("projectId")))
        }

    def _settings_read(self, params: Params) -> dict[str, Any]:
        params.only("projectId")
        return {
            "settings": self.application.settings.read(
                params.string("projectId", required=False)
            )
        }

    def _settings_update(self, params: Params) -> dict[str, Any]:
        params.only("patch", "scope", "projectId")
        settings = self.application.settings.update(
            dict(params.object("patch") or {}),
            scope=params.string("scope", required=False) or "user",
            project_id=params.string("projectId", required=False),
        )
        return {"settings": settings}

    def _skills_list(self, params: Params) -> dict[str, Any]:
        params.only("projectId")
        discovery = self.application.extensions.skills(str(params.string("projectId")))
        return {
            "skills": [skill_info_view(skill) for skill in discovery.skills],
            "warnings": list(discovery.warnings),
        }

    def _skill_read(self, params: Params) -> dict[str, Any]:
        params.only("projectId", "name")
        detail = self.application.extensions.skill(
            str(params.string("projectId")),
            str(params.string("name")),
        )
        return {"skill": skill_detail_view(detail)}

    def _hooks_list(self, params: Params) -> dict[str, Any]:
        params.only("projectId")
        discovery = self.application.extensions.hooks(str(params.string("projectId")))
        return {
            "hooks": [hook_info_view(hook) for hook in discovery.hooks],
            "warnings": list(discovery.warnings),
            "truncated": discovery.truncated,
        }

    def _mcp_list(self, params: Params) -> dict[str, Any]:
        params.only("projectId")
        project_id = params.string("projectId", required=False)
        return mcp_inventory_view(self.application.mcp.list(project_id))

    def _mcp_upsert(self, params: Params) -> dict[str, Any]:
        params.only("projectId", "scope", "name", "server")
        inventory = self.application.mcp.upsert(
            project_id=params.string("projectId", required=False),
            scope=str(params.string("scope")),
            name=str(params.string("name")),
            patch=dict(params.object("server") or {}),
        )
        return mcp_inventory_view(inventory)

    def _mcp_remove(self, params: Params) -> dict[str, Any]:
        params.only("projectId", "scope", "name")
        inventory = self.application.mcp.remove(
            project_id=params.string("projectId", required=False),
            scope=str(params.string("scope")),
            name=str(params.string("name")),
        )
        return mcp_inventory_view(inventory)

    def _diagnostics_read(self, params: Params) -> dict[str, Any]:
        params.only("projectId")
        return {
            "diagnostics": self.application.diagnostics.read(
                params.string("projectId", required=False)
            )
        }

    def _automation_list(self, params: Params) -> dict[str, Any]:
        params.only("projectId")
        inventory = self.application.automations.list(
            params.string("projectId", required=False)
        )
        return {
            "automations": [
                automation_view(automation) for automation in inventory.automations
            ],
            "latestRuns": [automation_run_view(run) for run in inventory.latest_runs],
            "schedulerActive": inventory.scheduler_active,
            "executionMode": "while_app_running",
        }

    def _automation_create(self, params: Params) -> dict[str, Any]:
        params.only(
            "projectId",
            "name",
            "prompt",
            "scheduleKind",
            "intervalSeconds",
            "enabled",
        )
        try:
            schedule_kind = AutomationScheduleKind(str(params.string("scheduleKind")))
        except ValueError as exc:
            raise InvalidParams("scheduleKind must be manual or interval") from exc
        created = self.application.automations.create(
            project_id=str(params.string("projectId")),
            name=str(params.string("name")),
            prompt=str(params.string("prompt")),
            schedule_kind=schedule_kind,
            interval_seconds=params.optional_integer(
                "intervalSeconds",
                minimum=60,
                maximum=31_622_400,
            ),
            enabled=params.boolean("enabled", default=True),
        )
        return {
            "automation": automation_view(created.automation),
            "thread": thread_view(created.thread),
        }

    def _automation_update(self, params: Params) -> dict[str, Any]:
        params.only(
            "automationId",
            "name",
            "prompt",
            "status",
            "scheduleKind",
            "intervalSeconds",
        )
        raw_status = params.string("status", required=False)
        raw_schedule = params.string("scheduleKind", required=False)
        try:
            status = AutomationStatus(raw_status) if raw_status is not None else None
            schedule_kind = (
                AutomationScheduleKind(raw_schedule)
                if raw_schedule is not None
                else None
            )
        except ValueError as exc:
            raise InvalidParams("invalid automation status or scheduleKind") from exc
        automation = self.application.automations.update(
            str(params.string("automationId")),
            name=params.string("name", required=False),
            prompt=params.string("prompt", required=False),
            status=status,
            schedule_kind=schedule_kind,
            interval_seconds=params.optional_integer(
                "intervalSeconds",
                minimum=60,
                maximum=31_622_400,
            ),
        )
        return {"automation": automation_view(automation)}

    def _automation_remove(self, params: Params) -> dict[str, bool]:
        params.only("automationId")
        return {
            "removed": self.application.automations.remove(
                str(params.string("automationId"))
            )
        }

    def _automation_run(self, params: Params) -> dict[str, Any]:
        params.only("automationId")
        execution = self.application.automations.run_now(
            str(params.string("automationId"))
        )
        return {
            "run": automation_run_view(execution.run),
            "turn": (turn_view(execution.turn) if execution.turn is not None else None),
        }

    def _automation_runs(self, params: Params) -> dict[str, Any]:
        params.only("automationId", "limit")
        runs = self.application.automations.list_runs(
            str(params.string("automationId")),
            limit=params.integer("limit", default=100, minimum=1, maximum=500),
        )
        return {"runs": [automation_run_view(run) for run in runs]}

    def _thread_start(self, params: Params) -> dict[str, Any]:
        params.only(
            "projectId",
            "title",
            "mode",
            "model",
            "workspacePath",
            "parentThreadId",
        )
        raw_mode = params.string("mode", required=False) or ThreadMode.CODE
        try:
            mode = ThreadMode(raw_mode)
        except ValueError as exc:
            raise InvalidParams("unsupported thread mode") from exc
        thread = self.application.threads.start(
            str(params.string("projectId")),
            title=str(params.string("title")),
            mode=mode,
            model=params.string("model", required=False),
            workspace_path=params.string("workspacePath", required=False),
            parent_thread_id=params.string("parentThreadId", required=False),
        )
        return {"thread": thread_view(thread)}

    def _thread_list(self, params: Params) -> dict[str, Any]:
        params.only("projectId", "cwd", "includeArchived", "limit", "offset")
        threads = self.application.threads.list(
            params.string("projectId", required=False),
            cwd=params.string("cwd", required=False),
            include_archived=params.boolean("includeArchived"),
            limit=params.integer("limit", default=100, minimum=1, maximum=500),
            offset=params.integer("offset", default=0, maximum=1_000_000),
        )
        return {"threads": [thread_view(thread) for thread in threads]}

    def _thread_resume(self, params: Params) -> dict[str, Any]:
        params.only("sessionId", "workspacePath")
        thread = self.application.threads.resume(
            str(params.string("sessionId")),
            workspace_path=params.string("workspacePath", required=False),
        )
        return {"thread": thread_view(thread)}

    def _thread_read(self, params: Params) -> dict[str, Any]:
        params.only("threadId")
        thread = self.application.threads.read(str(params.string("threadId")))
        return {"thread": thread_view(thread)}

    def _thread_rename(self, params: Params) -> dict[str, Any]:
        params.only("threadId", "title")
        thread = self.application.threads.rename(
            str(params.string("threadId")), str(params.string("title"))
        )
        return {"thread": thread_view(thread)}

    def _thread_model(self, params: Params) -> dict[str, Any]:
        params.only("threadId", "model")
        thread = self.application.threads.set_model(
            str(params.string("threadId")),
            params.nullable_string("model"),
        )
        return {"thread": thread_view(thread)}

    def _thread_archive(self, params: Params) -> dict[str, Any]:
        params.only("threadId")
        thread = self.application.threads.archive(str(params.string("threadId")))
        return {"thread": thread_view(thread)}

    def _thread_fork(self, params: Params) -> dict[str, Any]:
        params.only("threadId", "title")
        thread = self.application.threads.fork(
            str(params.string("threadId")),
            title=params.string("title", required=False),
        )
        return {"thread": thread_view(thread)}

    def _turn_start(self, params: Params) -> dict[str, Any]:
        params.only("threadId", "prompt")
        snapshot = self.application.turns.start(
            str(params.string("threadId")),
            prompt=str(params.string("prompt")),
        )
        return self._turn_snapshot(snapshot)

    def _turn_enqueue(self, params: Params) -> dict[str, Any]:
        params.only("threadId", "prompt")
        snapshot = self.application.turns.enqueue(
            str(params.string("threadId")),
            prompt=str(params.string("prompt")),
        )
        return self._turn_snapshot(snapshot)

    def _turn_read(self, params: Params) -> dict[str, Any]:
        params.only("turnId")
        return self._turn_snapshot(
            self.application.turns.read(str(params.string("turnId")))
        )

    def _turn_interrupt(self, params: Params) -> dict[str, Any]:
        params.only("turnId")
        accepted, turn = self.application.turns.interrupt(str(params.string("turnId")))
        return {"accepted": accepted, "turn": turn_view(turn)}

    def _workflow_start(self, params: Params) -> dict[str, Any]:
        params.only("threadId", "kind", "sourceType", "source", "options")
        snapshot = self.application.workflows.start(
            str(params.string("threadId")),
            kind=str(params.string("kind")),
            source_type=str(params.string("sourceType")),
            source=str(params.string("source")),
            options=params.object("options", required=False),
        )
        return self._workflow_snapshot(snapshot)

    def _workflow_read(self, params: Params) -> dict[str, Any]:
        params.only("workflowRunId")
        return self._workflow_snapshot(
            self.application.workflows.read(str(params.string("workflowRunId")))
        )

    def _workflow_list(self, params: Params) -> dict[str, Any]:
        params.only("threadId")
        runs = self.application.workflows.list_for_thread(
            str(params.string("threadId"))
        )
        return {"workflows": [workflow_view(run) for run in runs]}

    def _workflow_interrupt(self, params: Params) -> dict[str, Any]:
        params.only("workflowRunId")
        accepted, run = self.application.workflows.interrupt(
            str(params.string("workflowRunId"))
        )
        return {"accepted": accepted, "workflow": workflow_view(run)}

    def _workflow_retry(self, params: Params) -> dict[str, Any]:
        params.only("workflowRunId")
        return self._workflow_snapshot(
            self.application.workflows.retry(str(params.string("workflowRunId")))
        )

    def _workflow_respond(self, params: Params) -> dict[str, Any]:
        params.only("workflowRunId", "interactionId", "response")
        run = self.application.workflows.respond(
            str(params.string("workflowRunId")),
            interaction_id=str(params.string("interactionId")),
            response=params.object("response") or {},
        )
        return {"workflow": workflow_view(run)}

    def _artifact_list(self, params: Params) -> dict[str, Any]:
        params.only("threadId")
        artifacts = self.application.workflows.list_artifacts(
            str(params.string("threadId"))
        )
        return {"artifacts": [artifact_view(artifact) for artifact in artifacts]}

    def _artifact_read(self, params: Params) -> dict[str, Any]:
        params.only("artifactId", "maxBytes")
        content = self.application.workflows.read_artifact(
            str(params.string("artifactId")),
            max_bytes=params.integer(
                "maxBytes", default=128 * 1024, minimum=1, maximum=128 * 1024
            ),
        )
        return {
            "artifact": artifact_view(content.artifact),
            "content": content.content,
            "truncated": content.truncated,
            "directory": content.directory,
        }

    def _approval_respond(self, params: Params) -> dict[str, Any]:
        params.only("approvalId", "decision", "message")
        raw_decision = str(params.string("decision"))
        try:
            decision = ApprovalStatus(raw_decision)
        except ValueError as exc:
            raise InvalidParams(
                "decision must be approved_once, approved_session, or denied"
            ) from exc
        if decision not in {
            ApprovalStatus.APPROVED_ONCE,
            ApprovalStatus.APPROVED_SESSION,
            ApprovalStatus.DENIED,
        }:
            raise InvalidParams(
                "decision must be approved_once, approved_session, or denied"
            )
        approval = self.application.approvals.respond(
            str(params.string("approvalId")),
            decision=decision,
            message=params.string("message", required=False),
        )
        return {"approval": approval_view(approval)}

    def _event_replay(self, params: Params) -> dict[str, Any]:
        params.only("threadId", "after", "limit")
        thread_id = str(params.string("threadId"))
        self.application.threads.read(thread_id)
        page = self.application.events.replay_page(
            thread_id,
            after=params.integer("after", default=0, maximum=2**63 - 1),
            limit=params.integer("limit", default=500, minimum=1, maximum=1000),
        )
        return {
            "events": [event_view(event) for event in page.events],
            "nextAfter": page.next_after,
            "hasMore": page.has_more,
        }

    def _file_list(self, params: Params) -> dict[str, Any]:
        params.only("threadId", "path", "depth", "limit")
        entries, truncated = self.application.files.list(
            str(params.string("threadId")),
            path=str(params.string("path", required=False, allow_empty=True) or ""),
            depth=params.integer("depth", default=2, minimum=1, maximum=8),
            limit=params.integer("limit", default=750, minimum=1, maximum=750),
        )
        return {
            "entries": [file_entry_view(entry) for entry in entries],
            "truncated": truncated,
        }

    def _file_read(self, params: Params) -> dict[str, Any]:
        params.only("threadId", "path", "maxBytes")
        content = self.application.files.read(
            str(params.string("threadId")),
            str(params.string("path")),
            max_bytes=params.integer(
                "maxBytes", default=128 * 1024, minimum=1, maximum=128 * 1024
            ),
        )
        return {"file": file_content_view(content)}

    def _file_write(self, params: Params) -> dict[str, Any]:
        params.only("threadId", "path", "content", "expectedSha256")
        content = self.application.files.write(
            str(params.string("threadId")),
            str(params.string("path")),
            str(params.string("content", allow_empty=True)),
            expected_sha256=params.string("expectedSha256", required=False),
        )
        return {"file": file_content_view(content)}

    def _git_status(self, params: Params) -> dict[str, Any]:
        params.only("threadId")
        return {
            "status": git_status_view(
                self.application.git.status(str(params.string("threadId")))
            )
        }

    def _git_diff(self, params: Params) -> dict[str, Any]:
        params.only("threadId", "scope", "path")
        diffs = self.application.git.diff(
            str(params.string("threadId")),
            scope=params.string("scope", required=False) or "all",
            path=params.string("path", required=False),
        )
        return {"files": [file_diff_view(file_diff) for file_diff in diffs]}

    def _git_discard(self, params: Params) -> dict[str, Any]:
        params.only("threadId", "path", "expectedRevision")
        path = self.application.git.discard(
            str(params.string("threadId")),
            str(params.string("path")),
            str(params.string("expectedRevision")),
        )
        return {"discarded": True, "path": path}

    def _git_worktree_create(self, params: Params) -> dict[str, Any]:
        params.only("threadId")
        return worktree_result_view(
            self.application.worktrees.create(str(params.string("threadId")))
        )

    def _git_worktree_remove(self, params: Params) -> dict[str, Any]:
        params.only("threadId", "disposition", "force", "deleteBranch")
        return worktree_result_view(
            self.application.worktrees.remove(
                str(params.string("threadId")),
                disposition=str(params.string("disposition")),
                force=params.boolean("force"),
                delete_branch=params.boolean("deleteBranch"),
            )
        )

    def _terminal_create(self, params: Params) -> dict[str, Any]:
        params.only("threadId", "columns", "rows")
        info = self.application.terminals.create(
            str(params.string("threadId")),
            columns=params.integer("columns", default=100, minimum=20, maximum=500),
            rows=params.integer("rows", default=30, minimum=5, maximum=200),
        )
        return {"terminal": terminal_info_view(info)}

    def _terminal_write(self, params: Params) -> dict[str, Any]:
        params.only("threadId", "terminalId", "data")
        written = self.application.terminals.write(
            str(params.string("threadId")),
            str(params.string("terminalId")),
            str(params.string("data", allow_empty=True)),
        )
        return {"written": written}

    def _terminal_resize(self, params: Params) -> dict[str, Any]:
        params.only("threadId", "terminalId", "columns", "rows")
        info = self.application.terminals.resize(
            str(params.string("threadId")),
            str(params.string("terminalId")),
            columns=params.integer("columns", default=100, minimum=20, maximum=500),
            rows=params.integer("rows", default=30, minimum=5, maximum=200),
        )
        return {"terminal": terminal_info_view(info)}

    def _terminal_close(self, params: Params) -> dict[str, Any]:
        params.only("threadId", "terminalId")
        return {
            "accepted": self.application.terminals.close(
                str(params.string("threadId")), str(params.string("terminalId"))
            )
        }

    def _test_discover(self, params: Params) -> dict[str, Any]:
        params.only("threadId")
        commands = self.application.tests.discover(str(params.string("threadId")))
        return {"commands": [test_command_view(command) for command in commands]}

    def _test_run(self, params: Params) -> dict[str, Any]:
        params.only("threadId", "turnId", "commandId", "timeoutSeconds")
        result = self.application.tests.run(
            str(params.string("threadId")),
            str(params.string("turnId")),
            str(params.string("commandId")),
            timeout_seconds=params.integer(
                "timeoutSeconds", default=300, minimum=1, maximum=1800
            ),
        )
        return {
            "item": item_view(result.item),
            "command": test_command_view(result.command),
            "exitCode": result.exit_code,
            "timedOut": result.timed_out,
            "durationMs": result.duration_ms,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "outputTruncated": result.output_truncated,
        }

    @staticmethod
    def _turn_snapshot(snapshot) -> dict[str, Any]:
        return {
            "turn": turn_view(snapshot.turn),
            "items": [item_view(item) for item in snapshot.items],
            "approvals": [approval_view(approval) for approval in snapshot.approvals],
        }

    @staticmethod
    def _workflow_snapshot(snapshot) -> dict[str, Any]:
        return {
            "workflow": workflow_view(snapshot.run),
            "turn": turn_view(snapshot.turn),
            "items": [item_view(item) for item in snapshot.items],
            "artifacts": [artifact_view(artifact) for artifact in snapshot.artifacts],
        }
