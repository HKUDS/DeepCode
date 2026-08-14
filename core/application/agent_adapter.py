"""Frontend-neutral adapter from application turns to the shared agent kernel."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

from core.agent_presets import AgentPresetSnapshot
from core.agent_runtime.goal_runtime import GoalRuntimeRouter
from core.agent_runtime.injections import InjectionCallback, TurnInputSink
from core.agent_setup import build_agent_session
from core.compat import DeepCodeRuntime, get_runtime
from core.config import home_config_path, load_config_for_workspace, project_config_path
from core.domain.execution_permission import ExecutionPermissionMode
from core.domain.execution_profile import ExecutionProfile
from core.domain.execution_security import ExecutionSecurityProfile
from core.events import Event, Op
from core.harness.permissions import PermissionMode
from core.providers.credentials import default_credentials_path

if TYPE_CHECKING:
    from core.mcp.models import ResolvedMcpServer
    from core.skills.runtime import SkillRuntime

ApprovalCallback = Callable[[str, dict[str, Any], str | None], Any]


class AgentSessionPort(Protocol):
    @property
    def history(self) -> list[dict[str, Any]]: ...

    def load_history(self, messages: list[dict[str, Any]]) -> None: ...

    def run_stream(self, op: Op) -> AsyncIterator[Event]: ...

    async def aclose(self) -> None: ...


class AgentSessionFactory(Protocol):
    def runtime_key(
        self,
        *,
        workspace: str,
        model: str | None,
        execution_profile: ExecutionProfile | None = None,
        execution_security_profile: ExecutionSecurityProfile | None = None,
        permission_mode_override: ExecutionPermissionMode | None = None,
    ) -> object: ...

    def create(
        self,
        *,
        workspace: str,
        model: str | None,
        execution_profile: ExecutionProfile | None = None,
        execution_security_profile: ExecutionSecurityProfile | None = None,
        permission_mode_override: ExecutionPermissionMode | None = None,
        approval_callback: ApprovalCallback,
        injection_callback: InjectionCallback | None = None,
        context_note_sink: Callable[[str, str], None] | None = None,
        subagent_transcript_sink: Callable[..., None] | None = None,
        active_turn_id_provider: Callable[[], str | None] | None = None,
        runtime_input_sink: TurnInputSink | None = None,
        goal_runtime: GoalRuntimeRouter | None = None,
        skill_runtime: SkillRuntime | None = None,
    ) -> AgentSessionPort: ...


class ConfiguredAgentSessionFactory:
    """Create sessions with a client-specific default, not a forked policy."""

    def __init__(
        self,
        *,
        default_permission_mode: PermissionMode,
        streaming: bool,
        max_iterations: int | None = None,
    ) -> None:
        self.default_permission_mode = default_permission_mode
        self.streaming = streaming
        self.max_iterations = max_iterations
        self._plugin_mcp_provider: (
            Callable[[Path], tuple[ResolvedMcpServer, ...]] | None
        ) = None
        self._plugin_revision_provider: Callable[[], str] | None = None
        self._mcp_status_observer: Callable[[str, tuple[Any, ...]], None] | None = None

    def configure_plugin_mcp(
        self,
        provider: Callable[[Path], tuple[ResolvedMcpServer, ...]],
        *,
        revision_provider: Callable[[], str],
    ) -> None:
        """Attach the application-owned Plugin host without coupling kernels."""

        self._plugin_mcp_provider = provider
        self._plugin_revision_provider = revision_provider

    def configure_mcp_status_observer(
        self,
        observer: Callable[[str, tuple[Any, ...]], None],
    ) -> None:
        self._mcp_status_observer = observer

    def create(
        self,
        *,
        workspace: str,
        model: str | None,
        execution_profile: ExecutionProfile | None = None,
        execution_security_profile: ExecutionSecurityProfile | None = None,
        permission_mode_override: ExecutionPermissionMode | None = None,
        approval_callback: ApprovalCallback,
        injection_callback: InjectionCallback | None = None,
        context_note_sink: Callable[[str, str], None] | None = None,
        subagent_transcript_sink: Callable[..., None] | None = None,
        active_turn_id_provider: Callable[[], str | None] | None = None,
        runtime_input_sink: TurnInputSink | None = None,
        goal_runtime: GoalRuntimeRouter | None = None,
        skill_runtime: SkillRuntime | None = None,
        agent_preset: AgentPresetSnapshot | None = None,
    ) -> AgentSessionPort:
        options: dict[str, Any] = {}
        if self.max_iterations is not None:
            options["max_iterations"] = self.max_iterations
        if agent_preset is not None:
            # A preset owns exactly the model-facing composition (persona,
            # tool narrowing, delegation) — never the model route or the
            # permission stack, which stay independent knobs (dsh's split).
            from core.agent_setup import SYSTEM_PROMPT

            options["system_prompt"] = agent_preset.compose_system_prompt(SYSTEM_PROMPT)
            preset_filter = agent_preset.tool_filter()
            if preset_filter is not None:
                options["tool_filter"] = preset_filter
            if agent_preset.allow_spawn is not None:
                options["allow_spawn"] = agent_preset.allow_spawn
        runtime = DeepCodeRuntime(load_config_for_workspace(workspace))
        plugin_mcp_servers = (
            self._plugin_mcp_provider(Path(workspace))
            if self._plugin_mcp_provider is not None
            else ()
        )
        session, _resolved_model, _engine = build_agent_session(
            workspace=workspace,
            model=model,
            execution_profile=execution_profile,
            execution_security_profile=execution_security_profile,
            approval_callback=approval_callback,
            injection_callback=injection_callback,
            context_note_sink=context_note_sink,
            subagent_transcript_sink=subagent_transcript_sink,
            active_turn_id_provider=active_turn_id_provider,
            runtime_input_sink=runtime_input_sink,
            goal_runtime=goal_runtime,
            skill_runtime=skill_runtime,
            streaming=self.streaming,
            default_permission_mode=self.default_permission_mode,
            permission_mode_override=(
                PermissionMode(permission_mode_override.value)
                if permission_mode_override is not None
                else None
            ),
            runtime=runtime,
            project_trusted=True,
            plugin_mcp_servers=plugin_mcp_servers,
            mcp_status_observer=self._mcp_status_observer,
            **options,
        )
        return session

    def runtime_key(
        self,
        *,
        workspace: str,
        model: str | None,
        execution_profile: ExecutionProfile | None = None,
        execution_security_profile: ExecutionSecurityProfile | None = None,
        permission_mode_override: ExecutionPermissionMode | None = None,
        agent_preset: AgentPresetSnapshot | None = None,
    ) -> object:
        """Invalidate idle sessions after an in-process config reload."""

        return (
            str(Path(workspace).expanduser().resolve(strict=False)),
            model,
            agent_preset.fingerprint() if agent_preset is not None else None,
            (
                execution_profile.connection_id,
                execution_profile.config_revision,
                execution_profile.context_window,
                execution_profile.max_output_tokens,
                execution_profile.max_tokens,
                execution_profile.temperature,
                execution_profile.reasoning_effort,
            )
            if execution_profile is not None
            else None,
            (
                execution_security_profile.to_dict()
                if execution_security_profile is not None
                else None
            ),
            (
                permission_mode_override.value
                if permission_mode_override is not None
                else None
            ),
            _config_signature(home_config_path()),
            _config_signature(project_config_path(workspace)),
            _config_signature(default_credentials_path()),
            _mcp_oauth_signature(),
            (
                self._plugin_revision_provider()
                if self._plugin_revision_provider is not None
                else None
            ),
            id(get_runtime()),
        )


def _config_signature(path: Path) -> tuple[str, int, int] | tuple[str, None, None]:
    try:
        stat = path.stat()
    except OSError:
        return (str(path), None, None)
    return (str(path), stat.st_mtime_ns, stat.st_size)


def _mcp_oauth_signature() -> tuple[str, int, int] | tuple[str, None, None]:
    from core.mcp.oauth import default_mcp_oauth_path

    return _config_signature(default_mcp_oauth_path())


class DefaultAgentSessionFactory(ConfiguredAgentSessionFactory):
    """Desktop/App Server factory with approval-first behavior when unconfigured."""

    def __init__(self) -> None:
        super().__init__(
            default_permission_mode=PermissionMode.DEFAULT,
            streaming=True,
        )
