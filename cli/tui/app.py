"""DeepCode interactive TUI — free-form multi-turn coding conversations.

The Claude Code / Codex CLI analogue: launch straight into a conversation
(no menus), type tasks in natural language, watch the agent stream text and
tool progress live, steer with slash commands. Every pixel comes from the
SQ/EQ event stream — this layer never touches the kernel (§3 event-sourcing
first).

    python -m cli.tui                       # converse in the current dir
    python -m cli.tui -w ./proj -m gpt-5.4  # explicit workspace/model
    python -m cli.tui --resume <id>         # pick up a stored session

Composition (one concern per module, no god objects):
``app`` REPL/state · ``renderer`` events→terminal · ``commands`` slash
registry · ``input`` prompt/completion/piped-stdin · ``session_bridge``
persistence/resume · ``theme`` visual vocabulary.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys

sys.path.insert(
    0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)

from pathlib import Path

from rich.console import Console
from rich.markup import escape

from cli.config_errors import format_config_error
from cli.execution_options import (
    add_access_preset_argument,
    add_reasoning_effort_argument,
    add_workspace_trust_argument,
    parse_access_preset,
)
from cli.project_trust import (
    open_workspace_project,
    require_project_trusted,
    set_project_trusted,
)
from cli.tui import commands, theme
from cli.tui.goal_controller import TuiGoalController
from cli.tui.input import InputInterrupted, InputReader, expand_file_refs
from cli.tui.renderer import EventRenderer
from cli.tui.session_bridge import SessionBridge
from cli.tui.thread_client import TuiThreadClient
from core.application.application import DeepCodeApplication
from core.application.errors import ApplicationError
from core.config import ConfigError
from core.domain.approval import ApprovalStatus
from core.domain.event import DomainEvent
from core.domain.execution_security import ExecutionAccessPreset
from core.domain.project import TrustState
from core.file_lock import FileLease
from core.providers.reasoning import normalize_reasoning_effort


_MODEL_CATALOG_PREVIEW = 4  # models shown per connection in /model
# Sentinel: switch_model keeps the session's effort unless told otherwise.
_KEEP_EFFORT: object = object()
_RESUME_TAIL_MESSAGES = 4  # messages replayed on /resume
_RESUME_TAIL_LINES = 3  # lines shown per replayed message


class TuiApp:
    """State + REPL loop; slash commands drive it via the public methods."""

    def __init__(
        self,
        *,
        workspace: str,
        model: str | None,
        connection_id: str | None = None,
        reasoning_effort: str | None = None,
        max_iterations: int | None,
        trust_workspace: bool = False,
        access_preset: ExecutionAccessPreset | None = None,
        resume_id: str | None = None,
    ) -> None:
        self.workspace = os.path.abspath(workspace)
        self.max_iterations = max_iterations
        self.console = Console()
        self.renderer = EventRenderer(self.console, workspace=self.workspace)
        self.reader = InputReader(
            self.workspace,
            status_provider=self.renderer.status_fragments,
            toggle_transcript=self.renderer.cycle_transcript_mode,
            activity_probe=self.renderer.is_working,
        )
        self._exit_requested = False
        self._last_send_delivered = True
        self._requested_model = model
        self._requested_connection = connection_id
        self._requested_reasoning_effort = (
            normalize_reasoning_effort(reasoning_effort)
            if reasoning_effort is not None
            else None
        )
        self._requested_context_window: int | None = None
        self.selected_skill_ids: list[str] = []
        self._session_activity: FileLease | None = None
        self._leased_session_id: str | None = None
        self.thread_client = TuiThreadClient(
            workspace=self.workspace,
            model=self._requested_model,
            connection_id=self._requested_connection,
            reasoning_effort=self._requested_reasoning_effort,
            max_iterations=self.max_iterations,
            streaming=self.reader.interactive,
            trust_workspace=trust_workspace,
            resume_id=resume_id,
            event_sink=self.renderer.on_event,
        )
        if access_preset is not None:
            self.thread_client.set_access_preset(access_preset)
        self._sync_thread_state()
        self.reader.set_skill_provider(self._completion_skills)
        self.reader.set_argument_provider(self._complete_command_argument)
        self.goal_controller = TuiGoalController(self)

    # -- shared Thread lifecycle -------------------------------------------

    def _sync_thread_state(self) -> None:
        profile = self.thread_client.execution_profile
        thread = self.thread_client.thread
        self.model = profile.model_id
        self._requested_connection = profile.connection_id
        self._requested_model = profile.model_id
        self._requested_reasoning_effort = thread.reasoning_effort
        self._requested_context_window = thread.context_window
        self.bridge = SessionBridge(
            store=self.thread_client.store,
            session_id=thread.id,
            workspace=self.workspace,
        )
        self._lease_session(self.bridge.session_id)

    def _lease_session(self, session_id: str) -> None:
        if self._leased_session_id == session_id and self._session_activity is not None:
            return
        lease = self.bridge.store.acquire_activity_lease(session_id)
        if lease is None:
            raise ValueError(f"no such session: {session_id}")
        previous = self._session_activity
        self._session_activity = lease
        self._leased_session_id = session_id
        if previous is not None:
            previous.close()

    # -- public surface used by slash commands -------------------------------

    def new_conversation(self, title: str = "") -> None:
        # Switch the thread FIRST: if it fails (busy turn, untrusted
        # project) the app must keep its previous session intact instead of
        # having already dropped skills and goal state.
        self.thread_client.new_thread(title=title)
        self.goal_controller.close()
        self.selected_skill_ids.clear()
        self._sync_thread_state()

    def resume_conversation(self, session_id: str) -> int:
        self.thread_client.resume(session_id)
        self.goal_controller.close()
        self.selected_skill_ids.clear()
        self._sync_thread_state()
        stored = self.bridge.store.get_session(self.thread_client.session_id)
        return len(stored.messages) if stored is not None else 0

    def render_resume_tail(self) -> None:
        """Print the resumed conversation's tail, dimmed, for context.

        `/resume` used to report "N messages restored" to a blank screen:
        the model's context was rehydrated, but the user had no way to see
        what those messages were.
        """
        stored = self.bridge.store.get_session(self.thread_client.session_id)
        if stored is None:
            return
        visible = [
            message
            for message in stored.messages
            if message.role in ("user", "assistant") and message.content
        ]
        if not visible:
            return
        tail = visible[-_RESUME_TAIL_MESSAGES:]
        hidden = len(visible) - len(tail)
        self.console.print()
        if hidden > 0:
            self.console.print(
                f"[{theme.META_STYLE}]… {hidden} earlier messages[/]",
                highlight=False,
            )
        for message in tail:
            lines = [
                line for line in message.content.strip().splitlines() if line.strip()
            ]
            if not lines:
                continue
            shown = lines[:_RESUME_TAIL_LINES]
            if message.role == "user":
                shown[0] = f"{theme.PROMPT}{shown[0]}"
            for line in shown:
                self.console.print(
                    f"[{theme.META_STYLE}]{escape(line)}[/]",
                    highlight=False,
                )
            if len(lines) > len(shown):
                self.console.print(f"[{theme.META_STYLE}]…[/]", highlight=False)

    async def switch_model(
        self,
        model: str,
        *,
        connection_id: str | None = None,
        reasoning_effort: object = _KEEP_EFFORT,
    ) -> None:
        # A bare model name resolves its connection (configured default,
        # else catalog match) — the Desktop semantics. Pinning the current
        # connection here made `/model <other-vendor-model>` resolve against
        # the wrong provider. The model picker commits model and effort as
        # one pair (dsh's Shift+Tab pairing); the argument path keeps the
        # session's effort by omitting ``reasoning_effort``.
        effort = (
            self._requested_reasoning_effort
            if reasoning_effort is _KEEP_EFFORT
            else normalize_reasoning_effort(
                reasoning_effort if reasoning_effort is None else str(reasoning_effort)
            )
        )
        profile = self.thread_client.switch_execution(
            connection_id=connection_id,
            model=model,
            reasoning_effort=effort,
            context_window=self._requested_context_window,
        )
        self.model = profile.model_id
        self._requested_connection = profile.connection_id
        self._requested_model = profile.model_id
        self._requested_reasoning_effort = effort

    def reasoning_options(
        self,
        model_id: str,
        *,
        connection_id: str | None = None,
    ) -> tuple[str, ...]:
        """Published effort levels for one route, offline.

        Snapshot-first through the LLM service — the same source an actual
        Turn resolves its capabilities from, so the picker never advertises
        a ladder the switch would reject.
        """
        capabilities = self.thread_client.application.llm.model_reasoning(
            connection_id or self.thread_client.execution_profile.connection_id,
            model_id,
            project_id=self.thread_client.project.id,
        )
        if capabilities is None:
            return ()
        return tuple(capabilities.supported_efforts)

    async def switch_reasoning_effort(self, effort: str) -> None:
        """Change future turns while preserving this Session's history."""

        requested = normalize_reasoning_effort(effort)
        profile = self.thread_client.switch_execution(
            connection_id=self.thread_client.execution_profile.connection_id,
            model=self.thread_client.execution_profile.model_id,
            reasoning_effort=requested,
            context_window=self._requested_context_window,
        )
        self.model = profile.model_id
        self._requested_reasoning_effort = requested

    async def switch_context_window(self, context_window: int | None) -> None:
        """Change the context cap for future Turns in this Session."""

        profile = self.thread_client.switch_execution(
            connection_id=self.thread_client.execution_profile.connection_id,
            model=self.thread_client.execution_profile.model_id,
            reasoning_effort=self._requested_reasoning_effort,
            context_window=context_window,
        )
        self.model = profile.model_id
        self._requested_context_window = context_window

    def connection_views(self) -> list[dict]:
        data = self.thread_client.application.llm.list_connections(
            self.thread_client.project.id
        )
        return list(data.get("connections", []))

    def model_overview(self) -> str:
        """Current execution triple plus the configured connection catalog.

        The same directory the Desktop's provider page reads — without it,
        `/model` gave the user nothing to discover switch targets from.
        """
        profile = self.thread_client.execution_profile
        current = (
            f"connection: {profile.connection_id} · model: {self.model} · "
            f"effort: {self.requested_reasoning_effort} · "
            f"context: {self.requested_context_window}"
        )
        views = [
            view
            for view in self.connection_views()
            if view.get("configured") and view.get("enabled")
        ]
        if not views:
            return current
        lines = [
            current,
            "configured connections (switch with /model [connection] <model>):",
        ]
        width = max(len(str(view.get("id", ""))) for view in views)
        for view in views:
            models = [str(model) for model in view.get("manualModels") or []]
            if models:
                shown = ", ".join(models[:_MODEL_CATALOG_PREVIEW])
                extra = len(models) - _MODEL_CATALOG_PREVIEW
                catalog = shown + (f", +{extra} more" if extra > 0 else "")
            else:
                catalog = "no catalog configured — any model id accepted"
            marker = " · current" if view.get("id") == profile.connection_id else ""
            lines.append(f"  {str(view.get('id', '')):<{width}}  {catalog}{marker}")
        return "\n".join(lines)

    def connection_model_catalog(self) -> list[tuple[str, list[dict]]]:
        """Every configured connection's model directory, cache-first.

        Auto catalogs serve the remote snapshot (fetching once when the
        cache is cold), manual catalogs serve the declarations — the same
        ``model/list`` data the Desktop picker reads, so both surfaces
        offer one directory. A connection whose catalog cannot be read
        contributes nothing instead of failing the whole picker.
        """
        llm = self.thread_client.application.llm
        catalog: list[tuple[str, list[dict]]] = []
        for view in self.connection_views():
            if not (view.get("configured") and view.get("enabled")):
                continue
            connection_id = str(view.get("id", ""))
            try:
                listing = llm.list_models(
                    connection_id,
                    project_id=self.thread_client.project.id,
                )
                models = [
                    model
                    for model in listing.get("models", [])
                    if isinstance(model, dict)
                ]
            except (OSError, RuntimeError, ValueError):
                models = []
            catalog.append((connection_id, models))
        return catalog

    def model_catalog_note(self) -> str | None:
        """Advisory catalog check after a switch — never gates routing."""
        profile = self.thread_client.execution_profile
        try:
            views = self.connection_views()
        except (OSError, RuntimeError, ValueError):
            return None
        for view in views:
            if view.get("id") != profile.connection_id:
                continue
            models = [str(model) for model in view.get("manualModels") or []]
            if models and profile.model_id not in models:
                return (
                    f"note: {profile.model_id} is not in "
                    f"{profile.connection_id}'s configured catalog — if the "
                    "provider rejects it, run /model to see the known options"
                )
            return None
        return None

    def access_status(self) -> str:
        override = self.thread_client.access_preset_override
        if override is None:
            future = (
                f"New Turns: inherit · effective: {self.thread_client.access_summary()}"
            )
        else:
            future = f"New Turns: {override.value.replace('_', ' ')}"
        current, queued = self.thread_client.frozen_access_summaries()
        lines = [future]
        if current is not None:
            lines.append(f"Current Turn (frozen): {current}")
        if queued:
            distinct = tuple(dict.fromkeys(queued))
            summary = distinct[0] if len(distinct) == 1 else ", ".join(distinct)
            lines.append(f"Queued Turns (frozen): {len(queued)} · {summary}")
        return "\n".join(lines)

    async def set_access_preset(self, raw_preset: str | None) -> str:
        preset = ExecutionAccessPreset(raw_preset) if raw_preset is not None else None
        if self.thread_client.access_preset_override is preset:
            return self.access_status()
        if preset is ExecutionAccessPreset.FULL_ACCESS:
            self.console.print(
                f"[bold {theme.APPROVAL_STYLE}]Enable Full access for this Session?[/]\n"
                "Tools may run without approval and outside the workspace sandbox. "
                "They can read, modify, and execute files anywhere your account can "
                f"access. [{theme.META_STYLE}]Type yes to continue.[/]"
            )
            answer = await self.reader.read()
            if answer is None or answer.strip().casefold() != "yes":
                return "Full access was not enabled."
        self.thread_client.set_access_preset(preset)
        return (
            f"{self.access_status()}\n"
            "New submissions use this access. Active and queued Turns keep "
            "their frozen access."
        )

    def set_transcript_mode(self, mode: str) -> str:
        selected = self.renderer.set_transcript_mode(mode)
        return f"transcript mode: {selected.value}"

    @property
    def requested_reasoning_effort(self) -> str:
        """User-facing selection; ``auto`` means provider/model default."""

        return self._requested_reasoning_effort or "auto"

    @property
    def requested_context_window(self) -> str:
        value = self._requested_context_window
        return f"{value} tokens" if value is not None else "auto"

    def clear_conversation(self) -> None:
        self.goal_controller.close()
        self.thread_client.clear_context()
        if self.reader.interactive:
            self.console.clear()

    async def compact_conversation(self) -> str:
        report = await self.thread_client.compact_context()
        saved = report["chars_before"] - report["chars_after"]
        return (
            f"Compacted: {report['messages_before']} → "
            f"{report['messages_after']} messages "
            f"(~{saved:,} chars of older turns replaced by a summary)."
        )

    def agent_preset_overview(self) -> str:
        from core.agent_presets import list_agent_presets

        current = self.thread_client.current_agent_preset_id()
        lines = [f"agent preset: {current or 'default composition (none)'}"]
        for preset in list_agent_presets(self.thread_client.workspace):
            if preset.broken is not None:
                lines.append(f"  ✗ {preset.id} — broken: {preset.broken}")
                continue
            marker = "●" if preset.id == current else "○"
            face = ", ".join(preset.tools) if preset.tools is not None else "all tools"
            lines.append(
                f"  {marker} {preset.id} [{preset.trust}] — "
                f"{preset.description or preset.display_name} ({face})"
            )
        lines.append("usage: /preset <id> on a blank Session · /preset clear")
        return "\n".join(lines)

    def set_agent_preset(self, preset_id: str | None) -> str:
        self.thread_client.set_agent_preset(preset_id)
        chosen = self.thread_client.current_agent_preset_id()
        return (
            f"agent preset set to {chosen}" if chosen else "agent preset cleared"
        ) + " — takes effect from the first message of this Session."

    def list_skills(self) -> str:
        try:
            skills = self.thread_client.application.skills.list(
                self.thread_client.project.id
            ).skills
        except (ApplicationError, OSError, ValueError) as exc:
            return f"Skill error: {exc}"
        if not skills:
            return "no Skills discovered"
        selected = set(self.selected_skill_ids)
        lines = ["", "Skills (select with /skill <id|name>):"]
        for skill in skills:
            marker = "*" if skill.id in selected else " "
            lines.append(f" {marker} {skill.status:<9} {skill.name:<24} {skill.id}")
        return "\n".join(lines)

    def select_skill(self, identifier: str) -> str:
        try:
            skill = self.thread_client.application.skills.select(
                self.thread_client.project.id,
                identifier,
            )
        except (ApplicationError, OSError, ValueError) as exc:
            return f"Skill error: {exc}"
        if not skill.selectable:
            return f"Skill {skill.name} is {skill.status} and cannot be selected"
        if skill.id not in self.selected_skill_ids:
            if len(self.selected_skill_ids) >= 8:
                return "a turn may select at most 8 Skills"
            self.selected_skill_ids.append(skill.id)
        return f"selected {skill.name} for the next turn"

    def remove_skill(self, identifier: str) -> str:
        try:
            skills = self.thread_client.application.skills.list(
                self.thread_client.project.id
            ).skills
        except (ApplicationError, OSError, ValueError) as exc:
            return f"Skill error: {exc}"
        folded = identifier.casefold()
        matches = tuple(
            skill
            for skill in skills
            if skill.id in self.selected_skill_ids
            and (skill.id == identifier or skill.name.casefold() == folded)
        )
        if not matches:
            return f"{identifier} is not selected"
        if len(matches) > 1:
            return f"Skill name is ambiguous: {identifier}; remove it by ID"
        skill = matches[0]
        self.selected_skill_ids.remove(skill.id)
        return f"removed {skill.name} from the next turn"

    def _completion_skills(self):
        return self.thread_client.application.skills.list(
            self.thread_client.project.id
        ).skills

    def _complete_command_argument(self, name: str, prefix: str) -> tuple[str, ...]:
        command = commands.REGISTRY.get(name)
        if command is None or command.arguments is None:
            return ()
        try:
            return tuple(command.arguments(self, prefix))
        except Exception:  # noqa: BLE001 - completion must remain best effort
            return ()

    def clear_skills(self) -> str:
        self.selected_skill_ids.clear()
        return "cleared next-turn Skills"

    def list_plugins(self) -> str:
        try:
            discovery = self.thread_client.application.plugins.list()
        except (ApplicationError, OSError, ValueError) as exc:
            return f"Plugin error: {exc}"
        if not discovery.plugins:
            return "no Plugins installed"
        lines = ["", "Plugins (manage with `deepcode plugin ...`):"]
        for plugin in discovery.plugins:
            components = (
                ", ".join(
                    f"{component.kind}:{component.status}"
                    for component in plugin.components
                )
                or "no components"
            )
            lines.append(f"  {plugin.status:<9} {plugin.name:<24} {components}")
        return "\n".join(lines)

    def list_mcp_servers(self) -> str:
        try:
            inventory = self.thread_client.application.mcp.list(
                self.thread_client.project.id
            )
        except (ApplicationError, OSError, ValueError) as exc:
            return f"MCP error: {exc}"
        if not inventory.servers:
            return "no MCP servers configured"
        lines = ["", "MCP servers (/mcp help for actions):"]
        for server in inventory.servers:
            lines.append(
                f"  {server.configuration_state:<20} {server.auth_state:<16} "
                f"{server.runtime_state:<12} {server.name}"
            )
        return "\n".join(lines)

    async def manage_mcp(self, args: str) -> str:
        action, _, target = args.strip().partition(" ")
        action = action.casefold()
        target = target.strip()
        project_id = self.thread_client.project.id
        usage = (
            "usage: /mcp [presets | add <preset> | test <name> | "
            "login <name> | logout <name> | cancel <name> | "
            "enable <name> | disable <name> | remove <name>]"
        )
        if not action or action == "list":
            return self.list_mcp_servers()
        if action == "help":
            return usage
        try:
            if action == "presets":
                presets = self.thread_client.application.mcp.list_presets(project_id)
                lines = ["", "Bundled MCP presets (add with /mcp add <id>):"]
                for preset in presets.presets:
                    status = "configured" if preset.configured else "available"
                    missing = (
                        f" · missing {', '.join(preset.missing_environment)}"
                        if preset.missing_environment
                        else ""
                    )
                    lines.append(
                        f"  {status:<10} {preset.category:<13} {preset.id}{missing}"
                    )
                return "\n".join(lines)
            if not target:
                return usage
            if action == "add":
                await asyncio.to_thread(
                    self.thread_client.application.mcp.add_preset,
                    target,
                    project_id=project_id,
                )
                return (
                    f"added MCP preset {target} in disabled state; "
                    f"run /mcp test {target}, then /mcp enable {target}"
                )
            if action == "test":
                result = await asyncio.to_thread(
                    self.thread_client.application.mcp.probe,
                    target,
                    project_id=project_id,
                )
                if not result.ok:
                    return f"MCP test failed for {target}: {result.error}"
                return (
                    f"MCP test passed for {target}: {result.tool_count} tools, "
                    f"{result.resource_count} resources, {result.prompt_count} prompts"
                )
            if action == "login":
                flow = await asyncio.to_thread(
                    self.thread_client.application.mcp.oauth_start,
                    target,
                    project_id=project_id,
                    open_browser=True,
                )
                if flow.status == "failed":
                    return f"MCP authorization failed: {flow.error}"
                suffix = (
                    f"\nIf the browser did not open: {flow.authorization_url}"
                    if flow.authorization_url
                    else ""
                )
                return f"Browser authorization started for {target}.{suffix}"
            if action == "logout":
                removed = await asyncio.to_thread(
                    self.thread_client.application.mcp.oauth_logout,
                    target,
                    project_id=project_id,
                )
                return (
                    f"removed OAuth credentials for {target}"
                    if removed
                    else f"no OAuth credentials were stored for {target}"
                )
            if action == "cancel":
                await asyncio.to_thread(
                    self.thread_client.application.mcp.oauth_cancel,
                    target,
                    project_id=project_id,
                )
                return f"cancelled OAuth authorization for {target}"
            if action in {"enable", "disable", "remove"}:
                if action != "remove":
                    enabled = action == "enable"
                    await asyncio.to_thread(
                        self.thread_client.application.mcp.set_enabled,
                        target,
                        enabled=enabled,
                        project_id=project_id,
                    )
                    return f"{'enabled' if enabled else 'disabled'} MCP server {target}"
                inventory = self.thread_client.application.mcp.list(project_id)
                matches = [
                    server
                    for server in inventory.servers
                    if server.id == target or server.name == target
                ]
                if len(matches) != 1:
                    return f"unknown or ambiguous MCP server: {target}"
                server = matches[0]
                if server.source == "plugin":
                    return "Plugin MCP servers are managed through /plugins"
                scope = "project" if server.source == "project" else "user"
                await asyncio.to_thread(
                    self.thread_client.application.mcp.remove,
                    name=server.name,
                    scope=scope,
                    project_id=project_id,
                )
                return f"removed MCP server {server.name}"
            return usage
        except (ApplicationError, OSError, ValueError) as exc:
            return f"MCP error: {exc}"

    async def run_goal_command(self, args: str) -> str:
        result = await self.goal_controller.execute(args)
        if result.refresh_session:
            await self._reload_current_session()
        return result.message

    async def _reload_current_session(self) -> None:
        self.thread_client.refresh_thread()
        self._sync_thread_state()

    def request_exit(self) -> None:
        self._exit_requested = True

    # -- turns ----------------------------------------------------------------

    async def run_turn(self, text: str) -> None:
        self.thread_client.set_event_loop(asyncio.get_running_loop())
        self.send_turn(text)
        await self.thread_client.wait_until_idle()

    def send_turn(self, text: str) -> str:
        """Deliver one user message; the returned status is what the REPL
        shows. The normal case — the turn started — says nothing: the turn's
        own output follows immediately, so an ack line is pure noise. Only
        the surprising outcomes (queued behind a running turn, steered into
        one) deserve a line, and they name the situation, not a turn id."""
        try:
            delivery = self.thread_client.send(
                text,
                skill_ids=tuple(self.selected_skill_ids),
            )
        except ApplicationError as exc:
            # The cross-process run lease refusing, most commonly: another
            # process is executing a turn on this Session right now. A
            # sentence, not a traceback; the message is kept and nothing is
            # consumed, so the user can simply try again in a moment.
            self._last_send_delivered = False
            return exc.user_message
        self._last_send_delivered = True
        if delivery.kind == "started":
            self.selected_skill_ids.clear()
            return ""
        if delivery.kind == "queued":
            self.selected_skill_ids.clear()
            return "Queued — runs after the active turn."
        suffix = (
            " Selected next-Turn Skills remain selected."
            if self.selected_skill_ids
            else ""
        )
        return f"Steered the active turn.{suffix}"

    def queue_turn(self, text: str) -> str:
        # Read busyness BEFORE enqueueing: an idle Session starts the
        # queued Turn immediately, and claiming it will "run after the
        # active turn" would be a lie.
        busy = self.thread_client.has_active_turn()
        self.thread_client.queue(
            text,
            skill_ids=tuple(self.selected_skill_ids),
        )
        self.selected_skill_ids.clear()
        return (
            "Queued — runs after the active turn." if busy else "Queued — starting now."
        )

    def stop_turn(self) -> str:
        result = self.thread_client.interrupt()
        if result is None:
            self.renderer.settle_turn()
            return "no active Turn"
        accepted, turn = result
        # A cancelled Turn reaches its terminal state in the durable record
        # without emitting a terminal event, so the renderer would keep
        # reporting a turn that is already over. This call is the only place
        # that learns the truth.
        if turn.status.is_terminal:
            self.renderer.settle_turn()
        return "Interrupted the turn." if accepted else "The turn had already stopped."

    def _respond_to_pending_approval(self, text: str) -> str | None:
        approval = self.thread_client.pending_approval()
        if approval is None:
            return None
        decision = {
            "y": ApprovalStatus.APPROVED_ONCE,
            "yes": ApprovalStatus.APPROVED_ONCE,
            "a": ApprovalStatus.APPROVED_SESSION,
            "always": ApprovalStatus.APPROVED_SESSION,
            "n": ApprovalStatus.DENIED,
            "no": ApprovalStatus.DENIED,
        }.get(text.strip().casefold())
        if decision is None:
            return None
        resolved = self.thread_client.respond_to_approval(approval.id, decision)
        outcome = {
            ApprovalStatus.APPROVED_ONCE: "approved once",
            ApprovalStatus.APPROVED_SESSION: "approved for this Session",
            ApprovalStatus.DENIED: "denied",
        }.get(resolved.status, resolved.status.value.replace("_", " "))
        return f"Approval {outcome}."

    def _on_domain_event(self, event: DomainEvent) -> None:
        if event.type != "approval.requested":
            return
        approval = event.payload.get("approval")
        if not isinstance(approval, dict):
            return
        request = approval.get("request")
        request = request if isinstance(request, dict) else {}
        tool = str(request.get("toolName") or "tool")
        reason = str(request.get("reason") or "sensitive operation")
        self.console.print(
            f"\n[bold {theme.APPROVAL_STYLE}]◆ approval needed[/] "
            f"[bold]{escape(tool)}[/]\n"
            f"  {escape(reason)}\n"
            f"[{theme.META_STYLE}]{theme.TOOL_RESULT_ELBOW.strip()} "
            f"reply {theme.APPROVAL_PROMPT}[/]"
        )

    # -- REPL -----------------------------------------------------------------

    def _banner(self) -> None:
        """Borderless startup banner (the dsh rule: no box, one leading
        space per line, brand first, everything else recessed).

        The brand is the product logo drawn as a terminal mark — the thick
        bracket whose open side dissolves into circuit traces. Rows print
        independently, so nothing here can misalign into a broken frame;
        a terminal too narrow for the art falls back to the one-line
        wordmark, which is the same brand at a smaller size.
        """
        workspace = str(self.workspace)
        home = str(Path.home())
        if workspace.startswith(home):
            workspace = "~" + workspace[len(home) :]
        self.console.print()
        if self.console.width >= theme.BRAND_ART_WIDTH:
            for row in theme.brand_art_markup():
                self.console.print(f" {row}", highlight=False)
            self.console.print(
                f" {theme.wordmark_markup()} "
                f"[{theme.META_STYLE}]· {theme.BRAND_TAGLINE}[/]",
                highlight=False,
            )
        else:
            self.console.print(f" {theme.brand_markup()}")
        self.console.print(
            f" [{theme.META_STYLE}]{escape(self.model)} · {escape(workspace)}[/]",
            soft_wrap=True,
            highlight=False,
        )
        self.console.print(
            f" [{theme.META_STYLE}]session {escape(self.bridge.session_id)} · "
            f"access {escape(self.thread_client.access_summary())} · "
            f"effort {escape(self.requested_reasoning_effort)}[/]",
            soft_wrap=True,
            highlight=False,
        )
        self.console.print(
            f" [{theme.META_STYLE}]/help for commands · {theme.INTERRUPT_HINT}[/]",
            highlight=False,
        )
        self.console.print()

    async def repl(self) -> int:
        loop = asyncio.get_running_loop()
        self.thread_client.set_event_loop(loop)
        await self.thread_client.start_domain_events(self._on_domain_event)
        if self.reader.interactive:
            self._banner()
        try:
            while not self._exit_requested:
                try:
                    line = await self.reader.read()
                except InputInterrupted:
                    status = self.stop_turn()
                    self.console.print(
                        f"[{theme.META_STYLE}]{escape(status)}[/]",
                        soft_wrap=True,
                        highlight=False,
                    )
                    continue
                if line is None:
                    break
                text = line.strip()
                if not text:
                    continue
                if text.startswith("/"):
                    status = await commands.dispatch(self, text)
                    if status:
                        # escape(): statuses carry user data (paths, titles) that
                        # must never be parsed as rich markup. soft_wrap: long
                        # paths must not be hard-wrapped mid-line.
                        self.console.print(
                            f"[{theme.META_STYLE}]{escape(status)}[/]",
                            soft_wrap=True,
                            highlight=False,
                        )
                    continue
                approval_status = self._respond_to_pending_approval(text)
                if approval_status is not None:
                    self.console.print(
                        f"[{theme.META_STYLE}]{escape(approval_status)}[/]"
                    )
                    continue
                expanded = expand_file_refs(text, self.workspace)
                status = self.send_turn(expanded)
                if status:
                    self.console.print(
                        f"[{theme.META_STYLE}]{escape(status)}[/]",
                        soft_wrap=True,
                        highlight=False,
                    )
                if not self.reader.interactive and self._last_send_delivered:
                    # "Idle" is a property of the shared Thread, which another
                    # process's turn keeps busy. A refused submission started
                    # nothing here, so there is nothing of ours to wait for —
                    # waiting would make a piped run stand guard over the
                    # other process's work.
                    await self.thread_client.wait_until_idle()
            if self.reader.interactive:
                self.console.print(f"[{theme.META_STYLE}]bye[/]")
            return 0
        finally:
            self.goal_controller.close()
            await self.thread_client.close()
            if self._session_activity is not None:
                self._session_activity.close()
                self._session_activity = None
                self._leased_session_id = None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="deepcode",
        description="Interactive DeepCode coding agent (multi-turn TUI).",
    )
    parser.add_argument("--workspace", "-w", default=os.getcwd())
    parser.add_argument("--model", "-m", default=None)
    parser.add_argument("--connection", "-c", default=None)
    add_reasoning_effort_argument(parser)
    add_access_preset_argument(parser)
    add_workspace_trust_argument(parser)
    parser.add_argument("--resume", "-r", default=None, help="Session id to resume.")
    parser.add_argument(
        "--max-iterations",
        type=int,
        default=None,
        help="Optional model-sampling limit for diagnostics (unlimited by default).",
    )
    args = parser.parse_args(argv)
    _bootstrap_quiet_logging()

    if not _prepare_workspace_trust(args.workspace, grant=args.trust):
        return 1

    try:
        app = TuiApp(
            workspace=args.workspace,
            model=args.model,
            connection_id=args.connection,
            reasoning_effort=args.reasoning_effort,
            max_iterations=args.max_iterations,
            trust_workspace=args.trust,
            access_preset=parse_access_preset(args.access),
            resume_id=args.resume,
        )
    except ConfigError as exc:
        print(format_config_error(exc), file=sys.stderr)
        return 1
    except ApplicationError as exc:
        print(exc.user_message, file=sys.stderr)
        return 1
    if app.reader.interactive:
        _silence_console_logging()
    return asyncio.run(app.repl())


def _bootstrap_quiet_logging() -> None:
    """Install a quiet console sink before anything can log (#167's rule).

    ``deepcode`` does this for every subcommand it dispatches, but
    ``python -m cli.tui`` — the invocation this module's own docstring
    documents — reaches ``main`` without passing through it, so loguru
    keeps its built-in DEBUG sink and the config loader's "layer absent"
    lines land on the terminal *above the banner*, before the TUI owns the
    screen. ``setup_logging`` is idempotent without ``force``, so this is a
    no-op when the entrypoint already installed the user's real sinks.
    """
    try:
        from core.config import LoggerConfig
        from core.observability import setup_logging

        setup_logging(
            LoggerConfig(
                level=os.environ.get("DEEPCODE_LOG_LEVEL") or "INFO",
                transports=["console"],
            )
        )
    except Exception:  # noqa: BLE001 - logging must never block the TUI
        pass


def _silence_console_logging() -> None:
    """Route runtime logs to files while the TUI owns the terminal.

    The interactive transcript is a rendered surface (the dsh rule: the TUI
    owns the terminal); a loguru INFO line landing mid-stream shreds the
    conversation and the prompt redraw. Keep the user's configured file
    sinks (adding the global file as a fallback so diagnostics survive) and
    drop only the console transport. Best effort: logging trouble must not
    keep the TUI from starting.
    """
    try:
        from core.config import load_config
        from core.observability import setup_logging

        config = load_config().logger.model_copy(deep=True)
        config.transports = [t for t in config.transports if t != "console"]
        if not config.transports:
            config.transports = ["global_file"]
        setup_logging(config, force=True)
    except Exception:  # noqa: BLE001 - logging must never block the TUI
        pass


def _prepare_workspace_trust(workspace: str, *, grant: bool) -> bool:
    """Persist trust before creating a Session, avoiding empty denied threads."""

    application = DeepCodeApplication.open(
        host_surface="cli-trust",
        run_automation_scheduler=False,
    )
    try:
        project = open_workspace_project(
            application,
            workspace,
            grant_trust=grant,
        )
        if project.trust_state is TrustState.TRUSTED:
            return True
        if not sys.stdin.isatty():
            try:
                require_project_trusted(project)
            except ApplicationError as exc:
                print(exc.user_message, file=sys.stderr)
            return False
        print(
            "DeepCode can read files and run tools in this workspace:\n"
            f"  {project.canonical_path}\n"
            "Trust this folder? Type yes to continue: ",
            end="",
            file=sys.stderr,
            flush=True,
        )
        answer = sys.stdin.readline()
        if answer.strip().casefold() != "yes":
            print("Workspace was not trusted; no Session was created.", file=sys.stderr)
            return False
        set_project_trusted(application, project)
        return True
    finally:
        application.close()


if __name__ == "__main__":
    raise SystemExit(main())
