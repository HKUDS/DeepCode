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

from rich.console import Console
from rich.markup import escape
from rich.panel import Panel

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
from core.skills.management import LocalSkillManager


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
        self.renderer = EventRenderer(self.console)
        self.reader = InputReader(
            self.workspace,
            status_provider=self.renderer.status_line,
            toggle_transcript=self.renderer.cycle_transcript_mode,
        )
        self._exit_requested = False
        self._requested_model = model
        self._requested_connection = connection_id
        self._requested_reasoning_effort = (
            normalize_reasoning_effort(reasoning_effort)
            if reasoning_effort is not None
            else None
        )
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
        self.skill_manager = LocalSkillManager(self.workspace)
        self.goal_controller = TuiGoalController(self)

    # -- shared Thread lifecycle -------------------------------------------

    def _sync_thread_state(self) -> None:
        profile = self.thread_client.execution_profile
        thread = self.thread_client.thread
        self.model = profile.model_id
        self._requested_connection = profile.connection_id
        self._requested_model = profile.model_id
        self._requested_reasoning_effort = thread.reasoning_effort
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
        self.goal_controller.close()
        self.selected_skill_ids.clear()
        self.thread_client.new_thread(title=title)
        self._sync_thread_state()

    def resume_conversation(self, session_id: str) -> int:
        self.goal_controller.close()
        self.selected_skill_ids.clear()
        self.thread_client.resume(session_id)
        self._sync_thread_state()
        stored = self.bridge.store.get_session(session_id)
        return len(stored.messages) if stored is not None else 0

    async def switch_model(
        self,
        model: str,
        *,
        connection_id: str | None = None,
    ) -> None:
        profile = self.thread_client.switch_execution(
            connection_id=(
                connection_id or self.thread_client.execution_profile.connection_id
            ),
            model=model,
            reasoning_effort=self._requested_reasoning_effort,
        )
        self.model = profile.model_id
        self._requested_connection = profile.connection_id
        self._requested_model = profile.model_id

    async def switch_reasoning_effort(self, effort: str) -> None:
        """Change future turns while preserving this Session's history."""

        requested = normalize_reasoning_effort(effort)
        profile = self.thread_client.switch_execution(
            connection_id=self.thread_client.execution_profile.connection_id,
            model=self.thread_client.execution_profile.model_id,
            reasoning_effort=requested,
        )
        self.model = profile.model_id
        self._requested_reasoning_effort = requested

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

    def clear_conversation(self) -> None:
        self.goal_controller.close()
        self.thread_client.clear_context()
        if self.reader.interactive:
            self.console.clear()

    def list_skills(self) -> str:
        try:
            catalog = self.skill_manager.catalog()
        except (OSError, ValueError) as exc:
            return f"Skill error: {exc}"
        if not catalog.records:
            return "no Skills discovered"
        selected = set(self.selected_skill_ids)
        lines = ["", "Skills (select with /skill <id|name>):"]
        for record in catalog.records:
            marker = "*" if record.id in selected else " "
            lines.append(
                f" {marker} {record.status.value:<9} {record.name:<24} {record.id}"
            )
        return "\n".join(lines)

    def select_skill(self, identifier: str) -> str:
        try:
            record = self.skill_manager.select(identifier)
        except (OSError, ValueError) as exc:
            return f"Skill error: {exc}"
        if not record.selectable:
            return (
                f"Skill {record.name} is {record.status.value} and cannot be selected"
            )
        if record.id not in self.selected_skill_ids:
            if len(self.selected_skill_ids) >= 8:
                return "a turn may select at most 8 Skills"
            self.selected_skill_ids.append(record.id)
        return f"selected {record.name} for the next turn"

    def remove_skill(self, identifier: str) -> str:
        try:
            record = self.skill_manager.find(identifier)
        except (OSError, ValueError) as exc:
            return f"Skill error: {exc}"
        if record.id not in self.selected_skill_ids:
            return f"{record.name} is not selected"
        self.selected_skill_ids.remove(record.id)
        return f"removed {record.name} from the next turn"

    def clear_skills(self) -> str:
        self.selected_skill_ids.clear()
        return "cleared next-turn Skills"

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
        delivery = self.thread_client.send(
            text,
            skill_ids=tuple(self.selected_skill_ids),
        )
        if delivery.kind == "started":
            self.selected_skill_ids.clear()
            return f"Started Turn {delivery.turn.id}."
        if delivery.kind == "queued":
            self.selected_skill_ids.clear()
            return f"Queued Turn {delivery.turn.id}."
        suffix = (
            " Selected next-Turn Skills remain selected."
            if self.selected_skill_ids
            else ""
        )
        return f"Steered Turn {delivery.turn.id}.{suffix}"

    def queue_turn(self, text: str) -> str:
        delivery = self.thread_client.queue(
            text,
            skill_ids=tuple(self.selected_skill_ids),
        )
        self.selected_skill_ids.clear()
        return f"Queued Turn {delivery.turn.id}."

    def stop_turn(self) -> str:
        result = self.thread_client.interrupt()
        if result is None:
            return "no active Turn"
        accepted, turn = result
        return (
            f"Stopped Turn {turn.id}."
            if accepted
            else f"Turn {turn.id} had already stopped."
        )

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
        return f"Approval {resolved.status.value}."

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
            f"[{theme.APPROVAL_STYLE}]approval needed[/] {escape(tool)}: "
            f"{escape(reason)}\n"
            f"[{theme.META_STYLE}]reply y = once · a = Session · n = deny[/]"
        )

    # -- REPL -----------------------------------------------------------------

    def _banner(self) -> None:
        self.console.print(
            Panel.fit(
                f"[bold {theme.ACCENT}]{theme.BRAND}[/]\n"
                f"[{theme.META_STYLE}]model[/] {self.model}"
                f"  [{theme.META_STYLE}]workspace[/] {self.workspace}\n"
                f"[{theme.META_STYLE}]access[/] "
                f"{self.thread_client.access_summary()}"
                f"  [{theme.META_STYLE}]effort[/] {self.requested_reasoning_effort}"
                f"  [{theme.META_STYLE}]session[/] {self.bridge.session_id}"
                f"  [{theme.META_STYLE}]transcript[/] "
                f"{self.renderer.transcript_mode.value}"
                f"   [{theme.META_STYLE}]/help for commands[/]",
                border_style=theme.DIM,
            )
        )

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
                self.console.print(
                    f"[{theme.META_STYLE}]{escape(status)}[/]",
                    soft_wrap=True,
                    highlight=False,
                )
                if not self.reader.interactive:
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
    return asyncio.run(app.repl())


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
