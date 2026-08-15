"""JSON-RPC method dispatch; transport validation ends at this boundary."""

from __future__ import annotations

from collections.abc import Callable
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
from core.agent_presets import METADATA_KEY as PRESET_METADATA_KEY
from core.agent_presets import list_agent_presets
from core.application.application import DeepCodeApplication
from core.application.errors import (
    NoActiveTurnError,
    ThreadNotFoundError,
    TurnNotSteerableError,
)
from core.application.views import (
    agent_preset_view,
    approval_view,
    artifact_view,
    automation_run_view,
    automation_view,
    event_view,
    file_content_view,
    file_diff_view,
    file_entry_view,
    git_status_view,
    goal_outcome_view,
    hook_info_view,
    item_view,
    mcp_inventory_view,
    mcp_oauth_flow_view,
    mcp_preset_inventory_view,
    mcp_probe_view,
    plugin_diagnostic_view,
    plugin_info_view,
    project_view,
    skill_detail_view,
    skill_info_view,
    terminal_info_view,
    test_command_view,
    thread_goal_view,
    thread_view,
    turn_view,
    workflow_view,
    worktree_result_view,
)
from core.domain.approval import ApprovalStatus
from core.domain.automation import (
    AutomationActivationStatus,
    AutomationScheduleKind,
)
from core.domain.execution_profile import ExecutionSelection
from core.domain.execution_security import ExecutionAccessPreset
from core.domain.message_provenance import ClientSurface
from core.domain.project import TrustState
from core.domain.thread import ThreadMode
from core.skills.models import MAX_SELECTED_SKILLS, SkillScope
from core.version import __version__

PROTOCOL_VERSION = "1.0"
SERVER_VERSION = __version__


class Params:
    def __init__(self, values: dict[str, Any]) -> None:
        self.values = values

    def only(self, *allowed: str) -> Params:
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
        maximum: int | None = 1000,
    ) -> int:
        value = self.values.get(name, default)
        if isinstance(value, bool) or not isinstance(value, int):
            raise InvalidParams(f"{name} must be an integer")
        if value < minimum or (maximum is not None and value > maximum):
            if maximum is None:
                raise InvalidParams(f"{name} must be at least {minimum}")
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

    def required_boolean(self, name: str) -> bool:
        if name not in self.values:
            raise InvalidParams(f"{name} is required")
        return self.boolean(name)

    def object(self, name: str, *, required: bool = True) -> dict[str, Any] | None:
        value = self.values.get(name)
        if value is None and not required:
            return None
        if not isinstance(value, dict):
            raise InvalidParams(f"{name} must be an object")
        return value

    def string_array(
        self,
        name: str,
        *,
        default: tuple[str, ...] = (),
        maximum: int = 100,
    ) -> tuple[str, ...]:
        value = self.values.get(name, list(default))
        if not isinstance(value, list) or any(
            not isinstance(item, str) or not item.strip() for item in value
        ):
            raise InvalidParams(f"{name} must be an array of non-empty strings")
        if len(value) > maximum:
            raise InvalidParams(f"{name} may contain at most {maximum} entries")
        return tuple(value)

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
            rpc_methods.PROVIDER_LIST: self._provider_list,
            rpc_methods.PROVIDER_UPSERT: self._provider_upsert,
            rpc_methods.PROVIDER_REMOVE: self._provider_remove,
            rpc_methods.PROVIDER_TEST: self._provider_test,
            rpc_methods.PROVIDER_DISCOVER: self._provider_discover,
            rpc_methods.MODEL_LIST: self._model_list,
            rpc_methods.PRESET_LIST: self._preset_list,
            rpc_methods.PRESET_CURRENT: self._preset_current,
            rpc_methods.PRESET_SELECT: self._preset_select,
            rpc_methods.SKILLS_LIST: self._skills_list,
            rpc_methods.SKILL_READ: self._skill_read,
            rpc_methods.SKILLS_IMPORT: self._skills_import,
            rpc_methods.SKILLS_SET_ENABLED: self._skills_set_enabled,
            rpc_methods.SKILLS_DELETE: self._skills_delete,
            rpc_methods.SKILLS_RELOAD: self._skills_reload,
            rpc_methods.PLUGINS_LIST: self._plugins_list,
            rpc_methods.PLUGINS_ADD: self._plugins_add,
            rpc_methods.PLUGINS_SET_ENABLED: self._plugins_set_enabled,
            rpc_methods.PLUGINS_REMOVE: self._plugins_remove,
            rpc_methods.HOOKS_LIST: self._hooks_list,
            rpc_methods.MCP_LIST: self._mcp_list,
            rpc_methods.MCP_UPSERT: self._mcp_upsert,
            rpc_methods.MCP_REMOVE: self._mcp_remove,
            rpc_methods.MCP_PRESETS: self._mcp_presets,
            rpc_methods.MCP_PRESET_ADD: self._mcp_preset_add,
            rpc_methods.MCP_SET_ENABLED: self._mcp_set_enabled,
            rpc_methods.MCP_PROBE: self._mcp_probe,
            rpc_methods.MCP_OAUTH_START: self._mcp_oauth_start,
            rpc_methods.MCP_OAUTH_CANCEL: self._mcp_oauth_cancel,
            rpc_methods.MCP_OAUTH_LOGOUT: self._mcp_oauth_logout,
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
            rpc_methods.THREAD_EXECUTION_UPDATE: self._thread_execution_update,
            rpc_methods.THREAD_PERMISSION_UPDATE: self._thread_permission_update,
            rpc_methods.THREAD_ARCHIVE: self._thread_archive,
            rpc_methods.THREAD_DELETE: self._thread_delete,
            rpc_methods.THREAD_FORK: self._thread_fork,
            rpc_methods.THREAD_GOAL_GET: self._thread_goal_get,
            rpc_methods.THREAD_GOAL_SET: self._thread_goal_set,
            rpc_methods.THREAD_GOAL_PAUSE: self._thread_goal_pause,
            rpc_methods.THREAD_GOAL_RESUME: self._thread_goal_resume,
            rpc_methods.THREAD_GOAL_CONTINUE: self._thread_goal_continue,
            rpc_methods.THREAD_GOAL_CLEAR: self._thread_goal_clear,
            rpc_methods.TURN_START: self._turn_start,
            rpc_methods.TURN_ENQUEUE: self._turn_enqueue,
            rpc_methods.TURN_STEER: self._turn_steer,
            rpc_methods.TURN_READ: self._turn_read,
            rpc_methods.TURN_INTERRUPT: self._turn_interrupt,
            rpc_methods.TURN_RETRY: self._turn_retry,
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

    @property
    def client_surface(self) -> ClientSurface:
        value = getattr(self.connection, "client_surface", ClientSurface.APP_SERVER)
        return value if isinstance(value, ClientSurface) else ClientSurface(str(value))

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
        client_info = Params(params.object("clientInfo") or {}).only(
            "name",
            "version",
            "surface",
        )
        client_name = client_info.string("name")
        client_version = client_info.string("version")
        surface_value = client_info.string("surface", required=False)
        try:
            client_surface = (
                ClientSurface(str(surface_value))
                if surface_value is not None
                else ClientSurface.APP_SERVER
            )
        except ValueError as exc:
            raise InvalidParams(f"unsupported client surface: {surface_value}") from exc
        self.connection.initialize(str(client_name), client_surface)
        return {
            "protocolVersion": PROTOCOL_VERSION,
            "serverInfo": {"name": "deepcode-app-server", "version": SERVER_VERSION},
            "clientInfo": {
                "name": client_name,
                "version": client_version,
                "surface": client_surface.value,
            },
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
        params.only(
            "patch", "scope", "projectId", "riskAcknowledged", "expectedRevision"
        )
        patch = dict(params.object("patch") or {})
        security = patch.get("security")
        if (
            isinstance(security, dict)
            and security.get("accessPreset") == "full_access"
            and not params.boolean("riskAcknowledged")
        ):
            raise InvalidParams(
                "riskAcknowledged must be true when selecting full_access"
            )
        settings = self.application.settings.update(
            patch,
            scope=params.string("scope", required=False) or "user",
            project_id=params.string("projectId", required=False),
            expected_revision=params.string("expectedRevision", required=False),
        )
        return {"settings": settings}

    def _provider_list(self, params: Params) -> dict[str, Any]:
        params.only("projectId")
        return self.application.llm.list_connections(
            params.string("projectId", required=False)
        )

    def _provider_upsert(self, params: Params) -> dict[str, Any]:
        params.only("connection", "expectedRevision")
        return self.application.llm.upsert(
            dict(params.object("connection") or {}),
            expected_revision=params.string("expectedRevision", required=False),
        )

    def _provider_remove(self, params: Params) -> dict[str, Any]:
        params.only("connectionId", "expectedRevision")
        return self.application.llm.remove(
            str(params.string("connectionId")),
            expected_revision=params.string("expectedRevision", required=False),
        )

    def _provider_test(self, params: Params) -> dict[str, Any]:
        params.only("connectionId", "projectId", "model")
        return self.application.llm.test(
            str(params.string("connectionId")),
            project_id=params.string("projectId", required=False),
            model_id=params.string("model", required=False),
        )

    def _provider_discover(self, params: Params) -> dict[str, Any]:
        params.only("connectionId", "template", "apiBase", "apiKey", "projectId")
        return self.application.llm.discover_models(
            connection_id=params.string("connectionId", required=False),
            template=params.string("template", required=False),
            api_base=params.string("apiBase", required=False),
            api_key=params.string("apiKey", required=False),
            project_id=params.string("projectId", required=False),
        )

    def _model_list(self, params: Params) -> dict[str, Any]:
        params.only("connectionId", "projectId", "refresh")
        return self.application.llm.list_models(
            str(params.string("connectionId")),
            project_id=params.string("projectId", required=False),
            refresh=params.boolean("refresh"),
        )

    def _preset_list(self, params: Params) -> dict[str, Any]:
        """The trust-ranked agent-preset roster for one project's workspace.

        Broken entries ride along with their reason (the dsh roster rule) —
        a picker excludes them, a management surface shows why they failed.
        """
        params.only("projectId")
        project = self.application.projects.read(str(params.string("projectId")))
        return {
            "presets": [
                agent_preset_view(preset)
                for preset in list_agent_presets(project.canonical_path)
            ]
        }

    def _preset_current(self, params: Params) -> dict[str, Any]:
        params.only("threadId")
        thread_id = str(params.string("threadId"))
        session = self.application.session_store.get_session(thread_id)
        if session is None:
            raise ThreadNotFoundError(f"thread not found: {thread_id}")
        stored = session.metadata.get(PRESET_METADATA_KEY)
        return {"agentPreset": stored.get("id") if isinstance(stored, dict) else None}

    def _preset_select(self, params: Params) -> dict[str, Any]:
        params.only("threadId", "agentPreset")
        thread = self.application.threads.set_agent_preset(
            str(params.string("threadId")),
            params.nullable_string("agentPreset"),
        )
        session = self.application.session_store.get_session(thread.id)
        stored = (
            session.metadata.get(PRESET_METADATA_KEY) if session is not None else None
        )
        return {"agentPreset": stored.get("id") if isinstance(stored, dict) else None}

    def _skills_list(self, params: Params) -> dict[str, Any]:
        params.only("projectId", "refresh")
        discovery = self.application.skills.list(
            str(params.string("projectId")),
            force=params.boolean("refresh", default=False),
        )
        return {
            "skills": [skill_info_view(skill) for skill in discovery.skills],
            "warnings": list(discovery.warnings),
            "catalogRevision": discovery.catalog_revision,
            "authoringSkillId": discovery.authoring_skill_id,
        }

    def _skill_read(self, params: Params) -> dict[str, Any]:
        params.only("projectId", "skillId", "name")
        identifier = params.string("skillId", required=False) or params.string(
            "name",
            required=False,
        )
        if identifier is None:
            raise InvalidParams("skillId is required")
        detail = self.application.skills.read(
            str(params.string("projectId")),
            identifier,
        )
        return {"skill": skill_detail_view(detail)}

    def _skills_import(self, params: Params) -> dict[str, Any]:
        params.only("projectId", "path", "scope")
        detail = self.application.skills.import_directory(
            str(params.string("projectId")),
            str(params.string("path")),
            scope=self._skill_scope(params),
        )
        return {"skill": skill_detail_view(detail)}

    def _skills_set_enabled(self, params: Params) -> dict[str, Any]:
        params.only("projectId", "skillId", "enabled", "scope")
        discovery = self.application.skills.set_enabled(
            str(params.string("projectId")),
            str(params.string("skillId")),
            enabled=params.required_boolean("enabled"),
            scope=self._skill_scope(params),
        )
        return {
            "skills": [skill_info_view(skill) for skill in discovery.skills],
            "warnings": list(discovery.warnings),
            "catalogRevision": discovery.catalog_revision,
            "authoringSkillId": discovery.authoring_skill_id,
        }

    def _skills_delete(self, params: Params) -> dict[str, Any]:
        params.only("projectId", "skillId")
        return {
            "removed": self.application.skills.delete(
                str(params.string("projectId")),
                str(params.string("skillId")),
            )
        }

    def _skills_reload(self, params: Params) -> dict[str, Any]:
        params.only("projectId")
        discovery = self.application.skills.list(
            str(params.string("projectId")),
            force=True,
        )
        return {
            "skills": [skill_info_view(skill) for skill in discovery.skills],
            "warnings": list(discovery.warnings),
            "catalogRevision": discovery.catalog_revision,
            "authoringSkillId": discovery.authoring_skill_id,
        }

    def _plugins_list(self, params: Params) -> dict[str, Any]:
        params.only()
        return self._plugin_discovery_view(self.application.plugins.list())

    def _plugins_add(self, params: Params) -> dict[str, Any]:
        params.only("path")
        discovery = self.application.plugins.add(str(params.string("path")))
        return self._plugin_discovery_view(discovery)

    def _plugins_set_enabled(self, params: Params) -> dict[str, Any]:
        params.only("pluginId", "enabled")
        discovery = self.application.plugins.set_enabled(
            str(params.string("pluginId")),
            enabled=params.required_boolean("enabled"),
        )
        return self._plugin_discovery_view(discovery)

    def _plugins_remove(self, params: Params) -> dict[str, Any]:
        params.only("pluginId")
        removed = self.application.plugins.remove(str(params.string("pluginId")))
        return {"removed": True, "plugin": plugin_info_view(removed)}

    @staticmethod
    def _plugin_discovery_view(discovery) -> dict[str, Any]:
        return {
            "plugins": [plugin_info_view(plugin) for plugin in discovery.plugins],
            "diagnostics": [
                plugin_diagnostic_view(item) for item in discovery.diagnostics
            ],
            "revision": discovery.revision,
        }

    @staticmethod
    def _skill_scope(params: Params) -> SkillScope:
        try:
            return SkillScope(str(params.string("scope")))
        except ValueError as exc:
            raise InvalidParams("scope must be user or project") from exc

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

    def _mcp_presets(self, params: Params) -> dict[str, Any]:
        params.only("projectId")
        return mcp_preset_inventory_view(
            self.application.mcp.list_presets(
                params.string("projectId", required=False)
            )
        )

    def _mcp_preset_add(self, params: Params) -> dict[str, Any]:
        params.only("projectId", "presetId", "enabled")
        inventory = self.application.mcp.add_preset(
            str(params.string("presetId")),
            project_id=params.string("projectId", required=False),
            enabled=params.boolean("enabled", default=False),
        )
        return mcp_inventory_view(inventory)

    def _mcp_set_enabled(self, params: Params) -> dict[str, Any]:
        params.only("projectId", "name", "enabled")
        inventory = self.application.mcp.set_enabled(
            str(params.string("name")),
            project_id=params.string("projectId", required=False),
            enabled=params.required_boolean("enabled"),
        )
        return mcp_inventory_view(inventory)

    def _mcp_probe(self, params: Params) -> dict[str, Any]:
        params.only("projectId", "name")
        return mcp_probe_view(
            self.application.mcp.probe(
                str(params.string("name")),
                project_id=params.string("projectId", required=False),
            )
        )

    def _mcp_oauth_start(self, params: Params) -> dict[str, Any]:
        params.only("projectId", "name", "openBrowser", "resetCredentials")
        return mcp_oauth_flow_view(
            self.application.mcp.oauth_start(
                str(params.string("name")),
                project_id=params.string("projectId", required=False),
                open_browser=params.boolean("openBrowser", default=True),
                reset_credentials=params.boolean("resetCredentials", default=False),
            )
        )

    def _mcp_oauth_cancel(self, params: Params) -> dict[str, Any]:
        params.only("projectId", "name")
        return {
            "cancelled": self.application.mcp.oauth_cancel(
                str(params.string("name")),
                project_id=params.string("projectId", required=False),
            )
        }

    def _mcp_oauth_logout(self, params: Params) -> dict[str, Any]:
        params.only("projectId", "name")
        return {
            "removed": self.application.mcp.oauth_logout(
                str(params.string("name")),
                project_id=params.string("projectId", required=False),
            )
        }

    def _diagnostics_read(self, params: Params) -> dict[str, Any]:
        params.only("projectId")
        return {
            "diagnostics": self.application.diagnostics.read(
                params.string("projectId", required=False)
            )
        }

    def _automation_list(self, params: Params) -> dict[str, Any]:
        params.only("projectId", "limit", "offset")
        inventory = self.application.automations.list(
            params.string("projectId", required=False),
            limit=params.integer("limit", default=100, minimum=1, maximum=500),
            offset=params.integer("offset", default=0, minimum=0, maximum=None),
        )
        return {
            "automations": [
                automation_view(automation) for automation in inventory.automations
            ],
            "latestRuns": [automation_run_view(run) for run in inventory.latest_runs],
            "schedulerActive": inventory.scheduler_active,
            "executionMode": "requires_live_runtime",
            "hasMore": inventory.has_more,
            "nextOffset": inventory.next_offset,
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
        if not any(
            params.values.get(field) is not None
            for field in (
                "name",
                "prompt",
                "status",
                "scheduleKind",
                "intervalSeconds",
            )
        ):
            raise InvalidParams("automation/update requires at least one changed field")
        raw_status = params.string("status", required=False)
        raw_schedule = params.string("scheduleKind", required=False)
        try:
            status = (
                AutomationActivationStatus(raw_status)
                if raw_status is not None
                else None
            )
        except ValueError as exc:
            raise InvalidParams("status must be enabled or paused") from exc
        try:
            schedule_kind = (
                AutomationScheduleKind(raw_schedule)
                if raw_schedule is not None
                else None
            )
        except ValueError as exc:
            raise InvalidParams("scheduleKind must be manual or interval") from exc
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
        params.only("automationId", "requestId")
        request_id = params.string("requestId", required=False)
        if request_id is not None and len(request_id) > 255:
            raise InvalidParams("requestId must be at most 255 characters")
        execution = self.application.automations.run_now(
            str(params.string("automationId")),
            request_id=request_id,
        )
        return {
            "run": automation_run_view(execution.run),
            "turn": (turn_view(execution.turn) if execution.turn is not None else None),
        }

    def _automation_runs(self, params: Params) -> dict[str, Any]:
        params.only("automationId", "limit", "offset")
        page = self.application.automations.list_runs(
            str(params.string("automationId")),
            limit=params.integer("limit", default=100, minimum=1, maximum=500),
            offset=params.integer("offset", default=0, minimum=0, maximum=None),
        )
        return {
            "runs": [automation_run_view(run) for run in page.runs],
            "hasMore": page.has_more,
            "nextOffset": page.next_offset,
        }

    def _thread_start(self, params: Params) -> dict[str, Any]:
        params.only(
            "projectId",
            "title",
            "mode",
            "connectionId",
            "model",
            "reasoningEffort",
            "workspacePath",
            "parentThreadId",
            "agentPreset",
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
            connection_id=params.string("connectionId", required=False),
            model=params.string("model", required=False),
            reasoning_effort=params.string("reasoningEffort", required=False),
            workspace_path=params.string("workspacePath", required=False),
            parent_thread_id=params.string("parentThreadId", required=False),
            agent_preset=params.string("agentPreset", required=False),
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
        params.only("threadId", "connectionId", "model")
        thread_id = str(params.string("threadId"))
        current = self.application.threads.read(thread_id)
        if "connectionId" in params.values:
            connection_id = params.nullable_string("connectionId")
            model = params.nullable_string("model")
            self.application.llm.resolve(
                current.workspace_path,
                ExecutionSelection(
                    connection_id=connection_id,
                    model_id=model,
                    reasoning_effort=current.reasoning_effort,
                ),
            )
            thread = self.application.threads.set_execution_selection(
                thread_id,
                connection_id=connection_id,
                model=model,
                reasoning_effort=current.reasoning_effort,
            )
        else:
            model = params.nullable_string("model")
            self.application.llm.resolve(
                current.workspace_path,
                ExecutionSelection(
                    connection_id=current.connection_id,
                    model_id=model,
                    reasoning_effort=current.reasoning_effort,
                ),
            )
            thread = self.application.threads.set_model(
                thread_id,
                model,
            )
        return {"thread": thread_view(thread)}

    def _thread_execution_update(self, params: Params) -> dict[str, Any]:
        """Atomically validate and update the future-Turn execution choice."""

        params.only("threadId", "connectionId", "model", "reasoningEffort")
        thread_id = str(params.string("threadId"))
        current = self.application.threads.read(thread_id)
        connection_id = params.nullable_string("connectionId")
        model = params.nullable_string("model")
        reasoning_effort = params.nullable_string("reasoningEffort")
        self.application.llm.resolve(
            current.workspace_path,
            ExecutionSelection(
                connection_id=connection_id,
                model_id=model,
                reasoning_effort=reasoning_effort,
            ),
        )
        thread = self.application.threads.set_execution_selection(
            thread_id,
            connection_id=connection_id,
            model=model,
            reasoning_effort=reasoning_effort,
        )
        return {"thread": thread_view(thread)}

    def _thread_permission_update(self, params: Params) -> dict[str, Any]:
        """Set or clear the current Session's product-level access override."""

        params.only("threadId", "accessPreset", "riskAcknowledged")
        raw_preset = params.nullable_string("accessPreset")
        try:
            access_preset = (
                ExecutionAccessPreset(raw_preset) if raw_preset is not None else None
            )
        except ValueError as exc:
            raise InvalidParams(
                "accessPreset must be ask, read_only, full_access, or null"
            ) from exc
        risk_acknowledged = params.boolean("riskAcknowledged")
        if access_preset is ExecutionAccessPreset.FULL_ACCESS and not risk_acknowledged:
            raise InvalidParams(
                "riskAcknowledged must be true when selecting full_access"
            )
        thread = self.application.threads.set_access_preset(
            str(params.string("threadId")),
            access_preset,
        )
        return {"thread": thread_view(thread)}

    def _thread_archive(self, params: Params) -> dict[str, Any]:
        params.only("threadId")
        thread = self.application.threads.archive(str(params.string("threadId")))
        return {"thread": thread_view(thread)}

    def _thread_delete(self, params: Params) -> dict[str, Any]:
        params.only("threadId")
        result = self.application.deletions.delete(str(params.string("threadId")))
        return {
            "threadId": result.thread_id,
            "cleanupPending": result.cleanup_pending,
        }

    def _thread_fork(self, params: Params) -> dict[str, Any]:
        params.only("threadId", "title")
        thread = self.application.threads.fork(
            str(params.string("threadId")),
            title=params.string("title", required=False),
        )
        return {"thread": thread_view(thread)}

    def _thread_goal_get(self, params: Params) -> dict[str, Any]:
        params.only("threadId")
        thread_id = str(params.string("threadId"))
        goal = self.application.goals.read(thread_id)
        return self._goal_result(thread_id, goal)

    def _thread_goal_set(self, params: Params) -> dict[str, Any]:
        params.only(
            "threadId",
            "objective",
            "tokenBudget",
            "skills",
            "expectedGoalId",
            "start",
        )
        thread_id = str(params.string("threadId"))
        existing = self.application.goals.read(thread_id)
        expected_goal_id = (
            params.nullable_string("expectedGoalId")
            if "expectedGoalId" in params.values
            else None
        )
        skills = (
            params.string_array("skills", maximum=MAX_SELECTED_SKILLS)
            if "skills" in params.values
            else (existing.skill_ids if existing is not None else ())
        )
        objective = (
            str(params.string("objective"))
            if "objective" in params.values
            else existing.objective
            if existing is not None
            else None
        )
        if objective is None:
            raise InvalidParams("objective is required when creating a Goal")
        token_budget = (
            params.optional_integer(
                "tokenBudget",
                minimum=1,
                maximum=2_147_483_647,
            )
            if "tokenBudget" in params.values
            else existing.token_budget
            if existing is not None
            else None
        )
        start = params.boolean("start", default=True)
        if existing is None:
            if expected_goal_id is not None:
                raise InvalidParams(
                    "expectedGoalId must be null or omitted when creating a Goal"
                )
            goal = self.application.goals.create(
                thread_id,
                objective=objective,
                token_budget=token_budget,
                skill_ids=skills,
                start=start,
                client_surface=self.client_surface,
            )
        else:
            if expected_goal_id is None:
                raise InvalidParams(
                    "expectedGoalId is required when editing an existing Goal"
                )
            goal = self.application.goals.edit(
                thread_id,
                expected_goal_id=expected_goal_id,
                objective=objective,
                token_budget=token_budget,
                skill_ids=skills,
                continue_work=start,
                client_surface=self.client_surface,
            )
        return self._goal_result(thread_id, goal)

    def _thread_goal_pause(self, params: Params) -> dict[str, Any]:
        params.only("threadId", "expectedGoalId")
        thread_id = str(params.string("threadId"))
        goal = self.application.goals.pause(
            thread_id,
            expected_goal_id=str(params.string("expectedGoalId")),
        )
        return self._goal_result(thread_id, goal)

    def _thread_goal_resume(self, params: Params) -> dict[str, Any]:
        params.only("threadId", "expectedGoalId")
        thread_id = str(params.string("threadId"))
        goal = self.application.goals.resume(
            thread_id,
            expected_goal_id=str(params.string("expectedGoalId")),
            client_surface=self.client_surface,
        )
        return self._goal_result(thread_id, goal)

    def _thread_goal_continue(self, params: Params) -> dict[str, Any]:
        params.only("threadId", "expectedGoalId")
        result = self.application.goals.continue_goal(
            str(params.string("threadId")),
            expected_goal_id=str(params.string("expectedGoalId")),
            client_surface=self.client_surface,
        )
        return {
            **self._goal_result(result.goal.thread_id, result.goal),
            "disposition": result.disposition.value,
            "turnId": result.turn_id,
        }

    def _thread_goal_clear(self, params: Params) -> dict[str, Any]:
        params.only("threadId", "expectedGoalId")
        thread_id = str(params.string("threadId"))
        self.application.goals.clear(
            thread_id,
            expected_goal_id=str(params.string("expectedGoalId")),
        )
        return {"goal": None, "outcome": None}

    def _goal_result(self, thread_id: str, goal) -> dict[str, Any]:
        outcome = (
            self.application.goals.read_outcome(thread_id) if goal is not None else None
        )
        return {
            "goal": thread_goal_view(goal) if goal is not None else None,
            "outcome": (goal_outcome_view(outcome) if outcome is not None else None),
        }

    def _turn_start(self, params: Params) -> dict[str, Any]:
        params.only(
            "threadId",
            "prompt",
            "skills",
            "connectionId",
            "model",
            "reasoningEffort",
            "messageId",
        )
        snapshot = self.application.turns.start(
            str(params.string("threadId")),
            prompt=str(params.string("prompt", allow_empty=True)),
            message_id=str(params.string("messageId", allow_empty=True)),
            skill_ids=params.string_array(
                "skills",
                maximum=MAX_SELECTED_SKILLS,
            ),
            connection_id=params.string("connectionId", required=False),
            model=params.string("model", required=False),
            reasoning_effort=params.string("reasoningEffort", required=False),
            client_surface=self.client_surface,
        )
        return self._turn_snapshot(snapshot)

    def _turn_enqueue(self, params: Params) -> dict[str, Any]:
        params.only(
            "threadId",
            "prompt",
            "skills",
            "connectionId",
            "model",
            "reasoningEffort",
            "messageId",
        )
        snapshot = self.application.turns.enqueue(
            str(params.string("threadId")),
            prompt=str(params.string("prompt", allow_empty=True)),
            message_id=str(params.string("messageId", allow_empty=True)),
            skill_ids=params.string_array(
                "skills",
                maximum=MAX_SELECTED_SKILLS,
            ),
            connection_id=params.string("connectionId", required=False),
            model=params.string("model", required=False),
            reasoning_effort=params.string("reasoningEffort", required=False),
            client_surface=self.client_surface,
        )
        return self._turn_snapshot(snapshot)

    def _turn_steer(self, params: Params) -> dict[str, Any]:
        params.only("threadId", "expectedTurnId", "prompt", "messageId")
        thread_id = str(params.string("threadId"))
        expected_turn_id = str(params.string("expectedTurnId"))
        try:
            receipt = self.application.turns.steer(
                thread_id,
                expected_turn_id=expected_turn_id,
                prompt=str(params.string("prompt", allow_empty=True)),
                message_id=str(params.string("messageId", allow_empty=True)),
                client_surface=self.client_surface,
            )
        except TurnNotSteerableError as exc:
            if (
                not exc.crossed_final_input_boundary
                or self.application.turns.wait_until_terminal(expected_turn_id) is None
            ):
                raise
            raise NoActiveTurnError(
                f"thread has no active Turn: {thread_id}",
                details={"threadId": thread_id, "actualTurnId": None},
            ) from exc
        return {
            "messageId": receipt.message_id,
            "delivery": receipt.delivery,
            "duplicate": receipt.duplicate,
            "turn": turn_view(receipt.turn),
        }

    def _turn_read(self, params: Params) -> dict[str, Any]:
        params.only("turnId")
        return self._turn_snapshot(
            self.application.turns.read(str(params.string("turnId")))
        )

    def _turn_interrupt(self, params: Params) -> dict[str, Any]:
        params.only("threadId", "turnId")
        accepted, turn = self.application.turns.interrupt(
            str(params.string("threadId")),
            str(params.string("turnId")),
        )
        return {"accepted": accepted, "turn": turn_view(turn)}

    def _turn_retry(self, params: Params) -> dict[str, Any]:
        params.only("turnId", "useCurrentSelection")
        return self._turn_snapshot(
            self.application.turns.retry(
                str(params.string("turnId")),
                use_current_selection=params.boolean("useCurrentSelection"),
            )
        )

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
