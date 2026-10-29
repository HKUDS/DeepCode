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
import concurrent.futures
import logging
import os
import signal
import sys
import threading
import time
from collections.abc import Callable

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
from cli.thread_client import ThreadClient
from cli.tui import approval_view, commands, theme
from cli.tui.goal_controller import TuiGoalController
from cli.tui.input import InputInterrupted, InputReader, expand_file_refs
from cli.tui.renderer import EventRenderer
from cli.tui.session_bridge import SessionBridge
from core.application.errors import ApplicationError
from core.config import ConfigError
from core.domain.approval import ApprovalStatus
from core.domain.event import DomainEvent
from core.domain.execution_security import ExecutionAccessPreset
from core.domain.message_provenance import GoalPrompt, goal_prompt
from core.domain.project import Project, TrustState
from core.file_lock import FileLease
from core.providers.reasoning import normalize_reasoning_effort
from core.sessions.transcript import is_compaction_checkpoint, is_context_note

logger = logging.getLogger(__name__)

_MODEL_CATALOG_PREVIEW = 4  # models shown per connection in /model
# Sentinel: switch_model keeps the session's effort unless told otherwise.
_KEEP_EFFORT: object = object()
# Completion runs on every keystroke; service-backed candidate lists (Session
# ids, Skills, effort levels) are reused this long instead of refetched, and a
# fetch is waited on only this long before the menu shows what it already has.
_COMPLETION_CACHE_SECONDS = 5.0
_COMPLETION_WAIT_SECONDS = 0.15
_RESUME_TAIL_MESSAGES = 4  # messages replayed on /resume
_RESUME_TAIL_LINES = 3  # lines shown per replayed message
_QUEUE_PROBE_SECONDS = 2.0  # how often a queued Turn's reason is re-read


# How a replayed Goal instruction reads, in place of its text.
_GOAL_PROMPT_LABELS = {
    GoalPrompt.AUTOMATIC: "Goal continued automatically",
    GoalPrompt.REQUESTED: "Working toward the Goal",
}


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
        shared_service: bool = True,
    ) -> None:
        self.workspace = os.path.abspath(workspace)
        self.max_iterations = max_iterations
        # emoji=False: rich would turn ":x:" in "root:x:0" into an emoji.
        # Everything this console prints is content, never emoji shortcodes.
        self.console = Console(emoji=False)
        self.renderer = EventRenderer(self.console, workspace=self.workspace)
        self.reader = InputReader(
            self.workspace,
            status_provider=self.renderer.status_fragments,
            toggle_transcript=self.renderer.cycle_transcript_mode,
            activity_probe=self.renderer.is_working,
            prompt_provider=lambda: theme.prompt_fragments(
                approval=self.renderer.approval_hint is not None
            ),
        )
        self._exit_requested = False
        self._external_refresh_task: asyncio.Task | None = None
        self._external_refresh_dirty = False
        self._last_send_delivered = True
        # Approval prompts shown in this terminal, and those it answered, so a
        # resolution made elsewhere can be reported instead of misread.
        self._prompted_approval_ids: set[str] = set()
        self._answered_approval_ids: set[str] = set()
        # The approvals shown here, by id, for the answer's wording.
        self._shown_approvals: dict[str, dict] = {}
        self._queue_probe_task: asyncio.Task | None = None
        self._completion_cache: dict[str, tuple[float, object]] = {}
        self._completion_loads: dict[str, concurrent.futures.Future] = {}
        self._completion_cache_lock = threading.Lock()
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
        if shared_service:
            from cli.service_thread_client import ServiceThreadClient

            client_type = ServiceThreadClient
        else:
            from cli.tui.thread_client import TuiThreadClient

            client_type = TuiThreadClient
        self.thread_client: ThreadClient = client_type(
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
        bridge = getattr(self, "bridge", None)
        # Every thread event (each Turn start and end) lands here. Building a
        # bridge re-reads the whole Session file, so keep it while the Session
        # is the same one.
        if bridge is None or bridge.session_id != thread.id:
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
        # Notes and compaction checkpoints are user-role records nobody said.
        visible = [
            message
            for message in stored.messages
            if message.role in ("user", "assistant")
            and message.content
            and not is_context_note(message)
            and not is_compaction_checkpoint(message)
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
            kind = (
                goal_prompt(message.metadata or {}) if message.role == "user" else None
            )
            if kind is not None:
                # The Goal runtime's instruction to the model, not the user's words.
                self.console.print(
                    f"[{theme.META_STYLE}]{theme.GOAL_PROMPT_MARK} "
                    f"{_GOAL_PROMPT_LABELS[kind]}[/]",
                    highlight=False,
                )
                continue
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
        capabilities = self.thread_client.llm.model_reasoning(
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
        data = self.thread_client.llm.list_connections(self.thread_client.project.id)
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
                catalog = f"browse models with deepcode provider models {view['id']}"
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
        llm = self.thread_client.llm
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
            try:
                answer = await self.reader.read()
            except InputInterrupted:
                answer = None
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

    def set_raw_output(self, enabled: bool) -> str:
        self.renderer.raw_output = enabled
        if enabled:
            return "raw output on: answers print exactly as written; /raw off restores Markdown"
        return "raw output off: answers render as Markdown"

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
            if preset.disallowed_tools:
                face = f"{face} except {', '.join(preset.disallowed_tools)}"
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
            discovery = self.thread_client.skills.list(self.thread_client.project.id)
        except (ApplicationError, OSError, ValueError) as exc:
            return f"Skill error: {exc}"
        skills = discovery.skills
        # Invalid Skills and ignored settings explain what the list leaves out.
        warnings = [f" warning: {warning}" for warning in discovery.warnings]
        if not skills:
            return "\n".join(["no Skills discovered", *warnings])
        selected = set(self.selected_skill_ids)
        lines = ["", "Skills (select with /skill <id|name>):"]
        for skill in skills:
            marker = "*" if skill.id in selected else " "
            lines.append(f" {marker} {skill.status:<9} {skill.name:<24} {skill.id}")
        return "\n".join([*lines, *warnings])

    def select_skill(self, identifier: str) -> str:
        try:
            skill = self.thread_client.skills.select(
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
            skills = self.thread_client.skills.list(
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

    def completion_candidates(self, key: str, load: Callable[[], object]):
        """A service-backed completion source that never holds the prompt.

        Results are reused for a few seconds, since completion runs on every
        keystroke. A fetch is waited on only briefly: prompt_toolkit waits for
        an in-flight completion before it submits the line, so a slow service
        would otherwise delay Enter. A fetch still running keeps going in the
        background and serves the next keystroke. Commands never read this.
        """
        with self._completion_cache_lock:
            cached = self._completion_cache.get(key)
            if (
                cached is not None
                and time.monotonic() - cached[0] < _COMPLETION_CACHE_SECONDS
            ):
                return cached[1]
            pending = self._completion_loads.get(key)
            if pending is None:
                pending = concurrent.futures.Future()
                self._completion_loads[key] = pending
                threading.Thread(
                    target=self._load_completion_candidates,
                    args=(key, load, pending),
                    name="deepcode-completion",
                    daemon=True,
                ).start()
        try:
            return pending.result(timeout=_COMPLETION_WAIT_SECONDS)
        except concurrent.futures.TimeoutError:
            return cached[1] if cached is not None else ()

    def _load_completion_candidates(
        self,
        key: str,
        load: Callable[[], object],
        pending: concurrent.futures.Future,
    ) -> None:
        try:
            value = load()
        except BaseException as exc:  # noqa: BLE001 - delivered to the waiter
            pending.set_exception(exc)
        else:
            with self._completion_cache_lock:
                self._completion_cache[key] = (time.monotonic(), value)
            pending.set_result(value)
        finally:
            with self._completion_cache_lock:
                self._completion_loads.pop(key, None)

    def _completion_skills(self):
        project_id = self.thread_client.project.id
        return self.completion_candidates(
            f"skills:{project_id}",
            lambda: tuple(self.thread_client.skills.list(project_id).skills),
        )

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
            discovery = self.thread_client.plugins.list()
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
            inventory = self.thread_client.mcp.list(self.thread_client.project.id)
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
                presets = self.thread_client.mcp.list_presets(project_id)
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
                    self.thread_client.mcp.add_preset,
                    target,
                    project_id=project_id,
                )
                return (
                    f"added MCP preset {target} in disabled state; "
                    f"run /mcp test {target}, then /mcp enable {target}"
                )
            if action == "test":
                result = await asyncio.to_thread(
                    self.thread_client.mcp.probe,
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
                    self.thread_client.mcp.oauth_start,
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
                    self.thread_client.mcp.oauth_logout,
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
                    self.thread_client.mcp.oauth_cancel,
                    target,
                    project_id=project_id,
                )
                return f"cancelled OAuth authorization for {target}"
            if action in {"enable", "disable", "remove"}:
                if action != "remove":
                    enabled = action == "enable"
                    await asyncio.to_thread(
                        self.thread_client.mcp.set_enabled,
                        target,
                        enabled=enabled,
                        project_id=project_id,
                    )
                    return f"{'enabled' if enabled else 'disabled'} MCP server {target}"
                inventory = self.thread_client.mcp.list(project_id)
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
                    self.thread_client.mcp.remove,
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
        await asyncio.to_thread(self.thread_client.refresh_thread)
        self._sync_thread_state()

    async def refresh_execution_state(self) -> str | None:
        """Re-read this Session's model and access before showing them.

        A change to this Session made elsewhere arrives as a thread event,
        but a changed default (Settings, or the config file) emits nothing,
        so a Session that follows the defaults kept showing the old model
        and access. Returns a message when the service could not be read.
        """
        try:
            await self._reload_current_session()
        except (RuntimeError, OSError, ValueError) as exc:  # ApplicationError included
            return f"Could not read the current settings: {_error_text(exc)}"
        return None

    async def reconnect_service(self) -> str:
        if self.thread_client.runtime_mode != "service":
            return "This connection does not support reconnection."
        try:
            await self.thread_client.reconnect()
            return "Reconnected to the shared service."
        except (RuntimeError, OSError) as exc:
            return f"Reconnect failed: {exc}"

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
        try:
            result = self.thread_client.interrupt()
        except (RuntimeError, OSError) as exc:  # ApplicationError included
            return f"Could not stop the turn: {_error_text(exc)}"
        return self._settle_interrupt(result)

    async def stop_turn_async(self) -> str:
        """Interrupt without blocking rendering while the service waits."""

        if self.thread_client.runtime_mode != "service":
            return self.stop_turn()
        try:
            result = await asyncio.to_thread(self.thread_client.interrupt)
        except (RuntimeError, OSError) as exc:  # ApplicationError included
            return f"Could not stop the turn: {_error_text(exc)}"
        return self._settle_interrupt(result)

    def _settle_interrupt(self, result) -> str:
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
        decision = _APPROVAL_ANSWERS.get(text.strip().casefold())
        if decision is None:
            return self._hold_for_pending_approval(text)
        approval = self.thread_client.pending_approval()
        if approval is None:
            if self._prompted_approval_ids:
                # The prompt this reply was meant for is gone (answered in
                # another client, or timed out). Sending "y" to the model as
                # a message would be wrong, so say what happened instead.
                self._prompted_approval_ids.clear()
                return (
                    "No approval is waiting any more; it was answered elsewhere "
                    "or timed out."
                )
            return None
        self._answered_approval_ids.add(approval.id)
        self._prompted_approval_ids.discard(approval.id)
        try:
            resolved = self.thread_client.respond_to_approval(approval.id, decision)
        except ApplicationError as exc:
            return f"Could not answer the approval: {exc.user_message}"
        shown = self._shown_approvals.pop(approval.id, None) or {
            "request": getattr(approval, "request", {})
        }
        self.renderer.approval_resolved(approval.id)
        return approval_view.outcome_text(resolved.status.value, shown)

    def _hold_for_pending_approval(self, text: str) -> str | None:
        """Keep a message typed while an approval waits out of the Turn.

        Sent, it would reach the model as a steer while the Turn cannot read
        it, and the approval would still be waiting. It goes back in the
        prompt instead, to send once the approval is answered.
        """
        if not self._prompted_approval_ids:
            return None
        if self.thread_client.pending_approval() is None:
            # The approval was settled without an event reaching here.
            self._prompted_approval_ids.clear()
            self._shown_approvals.clear()
            return None
        self.reader.keep_draft(text)
        return (
            "Answer the approval first (y, a or n). "
            "Your message was not sent; it is back in the prompt."
        )

    def _on_approval_resolved(self, approval) -> None:
        if not isinstance(approval, dict) or not isinstance(approval.get("id"), str):
            return
        approval_id = approval["id"]
        self.renderer.approval_resolved(approval_id)
        self._shown_approvals.pop(approval_id, None)
        if approval_id in self._answered_approval_ids:
            self._answered_approval_ids.discard(approval_id)
            return
        if approval_id not in self._prompted_approval_ids:
            return
        self._prompted_approval_ids.discard(approval_id)
        status = str(approval.get("status") or "resolved").replace("_", " ")
        self.console.print(
            f"[{theme.META_STYLE}]Approval {escape(status)} elsewhere.[/]",
            soft_wrap=True,
            highlight=False,
        )

    def _print_status(self, status: str) -> None:
        # escape(): statuses carry user data (paths, titles) that must never be
        # parsed as rich markup. soft_wrap: long paths must not hard-wrap.
        self.console.print(
            f"[{theme.META_STYLE}]{escape(status)}[/]",
            soft_wrap=True,
            highlight=False,
        )

    def _on_domain_event(self, event: DomainEvent) -> None:
        if self.thread_client.runtime_mode == "service" and event.type.startswith(
            "thread."
        ):
            thread = event.payload.get("thread")
            if (
                isinstance(thread, dict)
                and thread.get("id") == self.thread_client.session_id
            ):
                self._external_refresh_dirty = True
                if (
                    self._external_refresh_task is None
                    or self._external_refresh_task.done()
                ):

                    async def refresh():
                        try:
                            while self._external_refresh_dirty:
                                self._external_refresh_dirty = False
                                await self._reload_current_session()
                        except (ApplicationError, OSError, ValueError) as exc:
                            self.console.print(
                                f"Service state refresh failed: {escape(str(exc))}"
                            )

                    self._external_refresh_task = asyncio.create_task(refresh())
        if event.turn_id is not None and event.thread_id == getattr(
            self.thread_client, "session_id", None
        ):
            self._on_turn_event(event)
        if event.type == "approval.resolved":
            self._on_approval_resolved(event.payload.get("approval"))
            return
        if event.type != "approval.requested":
            return
        approval = event.payload.get("approval")
        if not isinstance(approval, dict):
            return
        approval_id = approval.get("id")
        if isinstance(approval_id, str):
            self._prompted_approval_ids.add(approval_id)
            self._shown_approvals[approval_id] = approval
            self.renderer.approval_requested(
                approval_id, approval_view.answer_hint(approval)
            )
        self.console.print(
            approval_view.approval_card(
                approval,
                workspace=self.workspace,
                verbose=self.renderer.transcript_mode.value == "verbose",
            ),
            highlight=False,
        )

    def _on_turn_event(self, event: DomainEvent) -> None:
        """Tell the status line which of this Session's Turns wait or run."""
        turn = event.payload.get("turn")
        status = turn.get("status") if isinstance(turn, dict) else None
        if status is None:
            return
        if status == "queued":
            if event.type in {"turn.started", "turn.queued"}:
                self.renderer.turn_queued(event.turn_id)
                self._explain_queue()
        elif status in {"running", "waiting_approval"}:
            self.renderer.turn_running(event.turn_id)
        else:
            self.renderer.turn_finished(event.turn_id)
            self._explain_queue()

    def _explain_queue(self) -> None:
        """Find out what a queued Turn waits for, while it waits."""
        if self.thread_client.runtime_mode != "service":
            return
        task = getattr(self, "_queue_probe_task", None)
        if task is not None and not task.done():
            return

        async def probe() -> None:
            while self.renderer.has_queued:
                if self.renderer.waiting_in_queue:
                    try:
                        reason = await asyncio.to_thread(self._queue_reason)
                    except Exception:  # noqa: BLE001 - the reason is a courtesy
                        reason = None
                    self.renderer.set_queue_reason(reason)
                await asyncio.sleep(_QUEUE_PROBE_SECONDS)

        try:
            self._queue_probe_task = asyncio.get_running_loop().create_task(probe())
        except RuntimeError:  # no running loop (tests driving the app directly)
            self._queue_probe_task = None

    def _queue_reason(self) -> str | None:
        """What holds this Session's queued Turn, from the service's state.

        A Turn runs once it holds three things: its Session, the Project's
        workspace (shared by the Project's Sessions, unless one works in its
        own worktree) and one of the service's execution slots.
        """
        client = self.thread_client
        if client.turns.executing_for_thread(client.session_id) is not None:
            return "waiting for the current Turn in this session to finish"
        if not getattr(client.thread, "worktree_path", None):
            threads = client.rpc.call(
                "thread/list", {"projectId": client.project.id, "limit": 100}
            )["threads"]
            for thread in threads:
                if (
                    thread.get("id") != client.session_id
                    and not thread.get("worktreePath")
                    and thread.get("status") in {"running", "waiting"}
                    and client.turns.executing_for_thread(thread["id"]) is not None
                ):
                    return "waiting for another session in this workspace to finish"
        from app_server.service_client import ServiceClient

        # The service's own status (not a Session RPC) counts running Turns.
        running = (
            ServiceClient(client.rpc.files).call("status", timeout=2).get("activeTurns")
        )
        if running:
            return f"waiting for a free slot ({running} running)"
        return None

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
        model = self.model
        if self._uses_builtin_default_model():
            model += " (built-in default; no model configured)"
        self.console.print(
            f" [{theme.META_STYLE}]{escape(model)} · {escape(workspace)}[/]",
            soft_wrap=True,
            highlight=False,
        )
        self.console.print(
            f" [{theme.META_STYLE}]session {escape(self.bridge.session_id)} · "
            f"runtime {self.thread_client.runtime_mode} · "
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

    def _uses_builtin_default_model(self) -> bool:
        """Whether nothing chose the model: DeepCode fell back to its own."""
        try:
            from core.config import AgentDefaults, load_config_for_workspace

            config = load_config_for_workspace(self.workspace)
        except Exception:  # noqa: BLE001 - a banner note must never block startup
            return False
        builtin = AgentDefaults.model_fields["model"].default
        phase = config.agents.implementation
        return (
            self.model == builtin
            and "model" not in config.agents.defaults.model_fields_set
            and not (phase is not None and phase.model)
        )

    def _setup_problem(self) -> list[str]:
        """What keeps the first message from working, and the fix, if anything.

        A missing key used to surface only as the first Turn's failure. The
        connection the Session will use is checked at startup instead.
        """
        try:
            connection_id = self.thread_client.execution_profile.connection_id
            views = self.connection_views()
        except Exception:  # noqa: BLE001 - a startup check must not block the TUI
            return []
        view = next((v for v in views if v.get("id") == connection_id), None)
        if view is None or (view.get("enabled", True) and view.get("configured")):
            return []
        name = str(view.get("label") or connection_id)
        if not view.get("enabled", True):
            lines = [
                f"The {name} connection is turned off, so messages will fail.",
                f"Turn it on:  deepcode provider set {connection_id} --enabled",
            ]
        elif view.get("auth") == "oauth":
            lines = [
                f"{name} is not signed in, so messages will fail.",
                f"Sign in:  deepcode provider login {connection_id}",
            ]
        elif view.get("credentialSource") not in {None, "missing"}:
            # The credential is there; the endpoint is what is missing.
            lines = [
                f"{name} has no API address, so messages will fail.",
                f"Set it:  deepcode provider set {connection_id} --api-base <url>",
            ]
        else:
            lines = [
                f"{name} has no API key, so messages will fail.",
                f"Add one (it asks for the key and does not show it):  "
                f"deepcode provider set {connection_id} --api-key",
            ]
            env = view.get("apiKeyEnv")
            if env:
                lines.append(f"or set {env} before starting DeepCode.")
        # Connections that are set up. A built-in local one (Ollama, vLLM)
        # needs no key but may not be running, so it counts once configured.
        ready = [
            str(v.get("id"))
            for v in views
            if v.get("enabled")
            and v.get("configured")
            and v.get("id") != connection_id
            and (v.get("explicit") or v.get("credentialSource") != "not_required")
        ]
        if ready:
            lines.append(
                f"Or switch to a ready connection with /model ({', '.join(ready)})."
            )
        if self._uses_builtin_default_model():
            lines.append(
                f"{self.model} is DeepCode's built-in default: no model is configured "
                "yet (deepcode init creates a config file)."
            )
        return lines

    def _print_setup_problem(self) -> None:
        lines = self._setup_problem()
        if not lines:
            return
        self.console.print(
            f"[bold {theme.WARNING}]! {escape(lines[0])}[/]",
            soft_wrap=True,
            highlight=False,
        )
        for line in lines[1:]:
            self.console.print(f"  {escape(line)}", soft_wrap=True, highlight=False)
        self.console.print()

    def _print_exit_note(self) -> None:
        """Say what happens to a Turn that is still running on exit.

        The service owns the Turn, so it keeps running after the TUI is
        gone; leaving without saying so read as if the work stopped.
        """
        if self.thread_client.runtime_mode != "service":
            return
        try:
            active = self.thread_client.has_active_turn()
            waiting = active and self.thread_client.pending_approval() is not None
        except Exception:  # noqa: BLE001 - leaving must not fail on a status read
            return
        if not active:
            return
        state = "is waiting for an approval" if waiting else "keeps running"
        self.console.print(
            f"[{theme.WARNING}]The current task {state} in the background service.[/]",
            highlight=False,
        )
        self.console.print(
            f"[{theme.META_STYLE}]Reattach:  deepcode --resume "
            f"{escape(self.thread_client.session_id)}[/]",
            soft_wrap=True,
            highlight=False,
        )

    async def _run_line(self, text: str) -> None:
        """Run one line as a task that Ctrl-C cancels without ending the TUI.

        At the prompt, Ctrl-C is a key binding; while a command runs it is a
        SIGINT, which asyncio's runner turns into KeyboardInterrupt for the
        whole program. Route it to the running line instead.
        """

        task = asyncio.create_task(self._handle_line(text))
        restore = self._route_sigint_to(task)
        try:
            await task
        except asyncio.CancelledError:
            current = asyncio.current_task()
            if not task.cancelled() or (current is not None and current.cancelling()):
                raise
            self._print_status(
                "Stopped. Work the service already accepted may still finish."
            )
        finally:
            restore()

    def _route_sigint_to(self, task: asyncio.Task) -> Callable[[], None]:
        if not self.reader.interactive:
            return lambda: None
        loop = asyncio.get_running_loop()
        previous = signal.getsignal(signal.SIGINT)
        try:
            loop.add_signal_handler(signal.SIGINT, task.cancel)
        except (NotImplementedError, RuntimeError, ValueError):
            # Windows event loops have no signal handlers; keep the default.
            return lambda: None

        def restore() -> None:
            try:
                loop.remove_signal_handler(signal.SIGINT)
            except (NotImplementedError, RuntimeError, ValueError):
                pass
            if previous is not None:
                try:
                    signal.signal(signal.SIGINT, previous)
                except (TypeError, ValueError):
                    pass

        return restore

    async def _handle_line(self, text: str) -> None:
        if text.startswith("/"):
            status = await commands.dispatch(self, text)
            if status:
                self._print_status(status)
            return
        approval_status = self._respond_to_pending_approval(text)
        if approval_status is not None:
            self._print_status(approval_status)
            return
        expanded = expand_file_refs(text, self.workspace)
        if self.thread_client.runtime_mode == "service":
            # Admission is a few service round trips that can queue behind
            # the database while another Turn streams; off the loop, typing
            # and the status line keep moving meanwhile.
            status = await asyncio.to_thread(self.send_turn, expanded)
        else:
            status = self.send_turn(expanded)
        if status:
            self._print_status(status)
        if not self.reader.interactive and self._last_send_delivered:
            # "Idle" is a property of the shared Thread, which another
            # process's turn keeps busy. A refused submission started
            # nothing here, so there is nothing of ours to wait for —
            # waiting would make a piped run stand guard over the
            # other process's work.
            await self.thread_client.wait_until_idle()

    async def repl(self) -> int:
        loop = asyncio.get_running_loop()
        self.thread_client.set_event_loop(loop)
        await self.thread_client.start_domain_events(self._on_domain_event)
        if self.reader.interactive:
            self._banner()
            self._print_setup_problem()
        try:
            self.render_resume_tail()
            while not self._exit_requested:
                try:
                    line = await self.reader.read()
                except InputInterrupted:
                    self._print_status(await self.stop_turn_async())
                    continue
                if line is None:
                    break
                text = line.strip()
                if not text:
                    continue
                try:
                    await self._run_line(text)
                except InputInterrupted:
                    # Esc/Ctrl-C at a nested prompt (e.g. a confirmation)
                    # cancels that command, never the session.
                    self._print_status("Cancelled.")
                except (RuntimeError, OSError) as exc:  # ApplicationError included
                    # A service that is down, draining or that refuses one
                    # request must cost this line, not the whole session.
                    if not isinstance(exc, ApplicationError):
                        logger.exception("TUI request failed")
                    self._print_status(f"Request failed: {_error_text(exc)}")
            if self.reader.interactive:
                self._print_exit_note()
                self.console.print(f"[{theme.META_STYLE}]bye[/]")
            return 0
        finally:
            if getattr(self, "_queue_probe_task", None) is not None:
                self._queue_probe_task.cancel()
            if self._external_refresh_task is not None:
                self._external_refresh_task.cancel()
                await asyncio.gather(
                    self._external_refresh_task, return_exceptions=True
                )
            self.goal_controller.close()
            await self.thread_client.close()
            if self._session_activity is not None:
                self._session_activity.close()
                self._session_activity = None
                self._leased_session_id = None


def main(argv: list[str] | None = None, *, shared_service: bool = True) -> int:
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
    parser.set_defaults(max_iterations=None)
    if not shared_service:
        parser.add_argument(
            "--max-iterations", type=int, help="Optional model-sampling limit."
        )
    args = parser.parse_args(argv)
    _bootstrap_quiet_logging()
    if sys.stdin.isatty():
        # Before connecting: opening the service client logs every HTTP
        # request at INFO, and those lines used to land above the banner.
        _silence_console_logging()

    if not shared_service and not _prepare_workspace_trust(
        args.workspace, grant=args.trust
    ):
        return 1

    try:
        if shared_service and not _prepare_service_workspace_trust(
            args.workspace, grant=args.trust
        ):
            return 1
        app = TuiApp(
            workspace=args.workspace,
            model=args.model,
            connection_id=args.connection,
            reasoning_effort=args.reasoning_effort,
            max_iterations=args.max_iterations,
            trust_workspace=args.trust,
            access_preset=parse_access_preset(args.access),
            resume_id=args.resume,
            shared_service=shared_service,
        )
    except ConfigError as exc:
        print(format_config_error(exc), file=sys.stderr)
        return 1
    except ApplicationError as exc:
        print(exc.user_message, file=sys.stderr)
        return 1
    except (RuntimeError, OSError) as exc:
        print(f"DeepCode could not start: {exc}", file=sys.stderr)
        return 1
    return asyncio.run(app.repl())


_APPROVAL_ANSWERS = {
    "y": ApprovalStatus.APPROVED_ONCE,
    "yes": ApprovalStatus.APPROVED_ONCE,
    "a": ApprovalStatus.APPROVED_SESSION,
    "always": ApprovalStatus.APPROVED_SESSION,
    "n": ApprovalStatus.DENIED,
    "no": ApprovalStatus.DENIED,
}


def _error_text(exc: BaseException) -> str:
    if isinstance(exc, ApplicationError):
        return exc.user_message
    return str(exc) or type(exc).__name__


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

    from core.application.application import DeepCodeApplication

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
        if not _confirm_trust(project):
            return False
        set_project_trusted(application, project)
        return True
    finally:
        application.close()


def _prepare_service_workspace_trust(workspace: str, *, grant: bool) -> bool:
    """Ask what ``_prepare_workspace_trust`` asks, through the shared service
    that owns the Project."""

    if grant:
        return True  # ServiceThreadClient persists an explicit --trust itself
    from app_server.blocking_client import BlockingServiceClient
    from app_server.service_state import ServiceFiles
    from cli.rpc_models import from_view
    from core.persistence.database import default_database_path

    # The service resolves a relative path against its own directory.
    path = str(Path(workspace).expanduser().resolve())
    rpc = BlockingServiceClient(ServiceFiles(default_database_path()))
    try:
        project = from_view(Project, rpc.call("project/add", {"path": path})["project"])
        if project.trust_state is TrustState.TRUSTED:
            return True
        if not _confirm_trust(project):
            return False
        rpc.call("project/update", {"projectId": project.id, "trustState": "trusted"})
        return True
    finally:
        rpc.close()


def _confirm_trust(project: Project) -> bool:
    """Ask in a terminal whether to trust ``project``; elsewhere say how."""

    if not sys.stdin.isatty():
        try:
            require_project_trusted(project)
        except ApplicationError as exc:
            print(exc.user_message, file=sys.stderr)
        return False
    print(
        "DeepCode can read files and run tools in this workspace:\n"
        f"  {project.canonical_path}",
        file=sys.stderr,
    )
    question = "Trust this folder? [y/n]: "
    for _attempt in range(3):
        print(question, end="", file=sys.stderr, flush=True)
        line = sys.stdin.readline()
        if not line:  # end of input
            break
        answer = line.strip().casefold()
        if answer in {"y", "yes"}:
            return True
        if answer in {"n", "no"}:
            break
        question = "Please answer y (trust it) or n (leave): "
    print(
        "Not trusted, so DeepCode will not open this folder. To use it, run "
        "deepcode again here and answer y, or start with --trust.",
        file=sys.stderr,
    )
    return False


if __name__ == "__main__":
    raise SystemExit(main())
