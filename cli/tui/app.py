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
import signal
import sys

sys.path.insert(
    0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)

from rich.console import Console
from rich.markup import escape
from rich.panel import Panel

from core.agent_setup import DEFAULT_MAX_ITERATIONS, build_agent_session
from cli.config_errors import format_config_error
from core.config import ConfigError
from cli.tui import commands, theme
from cli.tui.input import InputReader, expand_file_refs
from cli.tui.goal_controller import TuiGoalController
from cli.tui.renderer import EventRenderer
from cli.tui.session_bridge import SessionBridge
from core.events import Interrupt, SkillLoaded, TurnStarted, UserInput
from core.skills.management import LocalSkillManager
from core.skills.models import SkillSelection


class TuiApp:
    """State + REPL loop; slash commands drive it via the public methods."""

    def __init__(
        self,
        *,
        workspace: str,
        model: str | None,
        connection_id: str | None = None,
        max_iterations: int,
        resume_id: str | None = None,
    ) -> None:
        self.workspace = os.path.abspath(workspace)
        self.max_iterations = max_iterations
        self.console = Console()
        self.renderer = EventRenderer(self.console)
        self.reader = InputReader(self.workspace)
        self._exit_requested = False
        self._requested_model = model
        self._requested_connection = connection_id
        self.selected_skill_ids: list[str] = []
        self._rebuild_agent(resume_id=resume_id)
        self.skill_manager = LocalSkillManager(self.workspace)
        self.goal_controller = TuiGoalController(self)

    # -- agent lifecycle ----------------------------------------------------

    def _rebuild_agent(
        self,
        *,
        resume_id: str | None = None,
        carry_history: list | None = None,
        title: str = "",
    ) -> None:
        """(Re)assemble the AgentSession + persistence bridge."""
        if resume_id is not None:
            stored_bridge = SessionBridge(
                session_id=resume_id,
                workspace=self.workspace,
            )
            stored_connection, stored_model = stored_bridge.execution_selection()
            if self._requested_connection is None:
                self._requested_connection = stored_connection
            if self._requested_model is None:
                self._requested_model = stored_model
        agent, resolved_model, engine = build_agent_session(
            workspace=self.workspace,
            model=self._requested_model,
            connection_id=self._requested_connection,
            max_iterations=self.max_iterations,
            approval_callback=self._approve,
            # Streaming deltas only make sense on a live terminal; piped
            # runs (tests, scripts) consume final messages.
            streaming=self.reader.interactive,
        )
        self.agent = agent
        self.model = resolved_model
        self.engine = engine
        # workspace is always the scoping context for the resume picker;
        # it is stamped into metadata only when creating a new session.
        if resume_id is not None:
            self.bridge = SessionBridge(session_id=resume_id, workspace=self.workspace)
            self.bridge.load_into(agent)
        else:
            profile = self.agent.execution_profile
            self.bridge = SessionBridge(
                title=title,
                workspace=self.workspace,
                connection_id=profile.connection_id,
                model=profile.model_id,
            )
            if carry_history:
                agent.load_history(carry_history)

    # -- public surface used by slash commands -------------------------------

    def new_conversation(self, title: str = "") -> None:
        self.selected_skill_ids.clear()
        self._rebuild_agent(title=title)

    def resume_conversation(self, session_id: str) -> int:
        self.selected_skill_ids.clear()
        self._rebuild_agent(resume_id=session_id)
        return len(self.agent.history)

    async def switch_model(
        self,
        model: str,
        *,
        connection_id: str | None = None,
    ) -> None:
        previous_model = self._requested_model
        previous_connection = self._requested_connection
        self._requested_model = model
        if connection_id is not None:
            self._requested_connection = connection_id
        history = self.agent.history
        current_session = self.bridge.session_id
        old_agent = self.agent
        try:
            self._rebuild_agent(carry_history=history)
        except Exception:
            self._requested_model = previous_model
            self._requested_connection = previous_connection
            raise
        await old_agent.aclose()
        # Keep recording into the same stored session (scoping unchanged).
        self.bridge = SessionBridge(
            session_id=current_session, workspace=self.workspace
        )
        profile = self.agent.execution_profile
        self.bridge.update_execution_selection(
            connection_id=profile.connection_id,
            model=profile.model_id,
        )

    def clear_conversation(self) -> None:
        self.agent.load_history([])
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
        session_id = self.bridge.session_id
        old_agent = self.agent
        await old_agent.aclose()
        self._rebuild_agent(resume_id=session_id)

    def request_exit(self) -> None:
        self._exit_requested = True

    # -- approvals ------------------------------------------------------------

    async def _approve(self, tool_name: str, arguments, reason: str) -> bool:
        self.console.print(
            f"[{theme.APPROVAL_STYLE}]approval needed[/] {tool_name}: {reason}"
        )
        answer = await self.reader.read()
        return bool(answer and answer.strip().lower() in ("y", "yes"))

    # -- turns ----------------------------------------------------------------

    async def run_turn(self, text: str) -> None:
        if not self.agent.history:
            self.bridge.set_title_from(text)
        final_text: str | None = None
        turn_started = False
        invocations = {}
        selected = tuple(
            SkillSelection(skill_id=skill_id) for skill_id in self.selected_skill_ids
        )
        loop = asyncio.get_running_loop()

        def _interrupt() -> None:
            asyncio.ensure_future(self.agent.submit(Interrupt()))

        try:
            loop.add_signal_handler(signal.SIGINT, _interrupt)
        except (NotImplementedError, RuntimeError):  # non-main loop / windows
            pass
        try:
            async for event in self.agent.run_stream(
                UserInput(text=text, skills=selected)
            ):
                self.renderer.on_event(event)
                if isinstance(event.msg, TurnStarted):
                    turn_started = True
                    for invocation in event.msg.skill_invocations:
                        invocations[invocation.skill_id] = invocation
                elif isinstance(event.msg, SkillLoaded):
                    invocation = event.msg.invocation
                    invocations[invocation.skill_id] = invocation
                elif event.msg.type == "task_complete":
                    final_text = event.msg.final_text
        finally:
            try:
                loop.remove_signal_handler(signal.SIGINT)
            except (NotImplementedError, RuntimeError, ValueError):
                pass
            self.selected_skill_ids.clear()
        if turn_started:
            self.bridge.record_turn(
                text,
                final_text,
                skill_invocations=tuple(invocations.values()),
                execution_profile=self.agent.execution_profile,
            )

    # -- REPL -----------------------------------------------------------------

    def _banner(self) -> None:
        self.console.print(
            Panel.fit(
                f"[bold {theme.ACCENT}]{theme.BRAND}[/]\n"
                f"[{theme.META_STYLE}]model[/] {self.model}"
                f"  [{theme.META_STYLE}]workspace[/] {self.workspace}\n"
                f"[{theme.META_STYLE}]permission[/] {self.engine.mode.value}"
                f"  [{theme.META_STYLE}]session[/] {self.bridge.session_id}"
                f"   [{theme.META_STYLE}]/help for commands[/]",
                border_style=theme.DIM,
            )
        )

    async def repl(self) -> int:
        if self.reader.interactive:
            self._banner()
        while not self._exit_requested:
            line = await self.reader.read()
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
            await self.run_turn(expand_file_refs(text, self.workspace))
        if self.reader.interactive:
            self.console.print(f"[{theme.META_STYLE}]bye[/]")
        return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="deepcode",
        description="Interactive DeepCode coding agent (multi-turn TUI).",
    )
    parser.add_argument("--workspace", "-w", default=os.getcwd())
    parser.add_argument("--model", "-m", default=None)
    parser.add_argument("--connection", "-c", default=None)
    parser.add_argument("--resume", "-r", default=None, help="Session id to resume.")
    parser.add_argument("--max-iterations", type=int, default=DEFAULT_MAX_ITERATIONS)
    args = parser.parse_args(argv)

    try:
        app = TuiApp(
            workspace=args.workspace,
            model=args.model,
            connection_id=args.connection,
            max_iterations=args.max_iterations,
            resume_id=args.resume,
        )
    except ConfigError as exc:
        print(format_config_error(exc), file=sys.stderr)
        return 1
    return asyncio.run(app.repl())


if __name__ == "__main__":
    raise SystemExit(main())
