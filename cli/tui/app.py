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
from rich.panel import Panel

from cli.agent_setup import build_agent_session
from cli.tui import commands, theme
from cli.tui.input import InputReader, expand_file_refs
from cli.tui.renderer import EventRenderer
from cli.tui.session_bridge import SessionBridge
from core.events import Interrupt, UserInput


class TuiApp:
    """State + REPL loop; slash commands drive it via the public methods."""

    def __init__(
        self,
        *,
        workspace: str,
        model: str | None,
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
        self._rebuild_agent(resume_id=resume_id)

    # -- agent lifecycle ----------------------------------------------------

    def _rebuild_agent(
        self,
        *,
        resume_id: str | None = None,
        carry_history: list | None = None,
        title: str = "",
    ) -> None:
        """(Re)assemble the AgentSession + persistence bridge."""
        agent, resolved_model, engine = build_agent_session(
            workspace=self.workspace,
            model=self._requested_model,
            max_iterations=self.max_iterations,
            approval_callback=self._approve,
            # Streaming deltas only make sense on a live terminal; piped
            # runs (tests, scripts) consume final messages.
            streaming=self.reader.interactive,
        )
        self.agent = agent
        self.model = resolved_model
        self.engine = engine
        if resume_id is not None:
            self.bridge = SessionBridge(session_id=resume_id)
            self.bridge.load_into(agent)
        else:
            self.bridge = SessionBridge(title=title)
            if carry_history:
                agent.load_history(carry_history)

    # -- public surface used by slash commands -------------------------------

    def new_conversation(self, title: str = "") -> None:
        self._rebuild_agent(title=title)

    def resume_conversation(self, session_id: str) -> int:
        self._rebuild_agent(resume_id=session_id)
        return len(self.agent.history)

    def switch_model(self, model: str) -> None:
        self._requested_model = model
        history = self.agent.history
        current_session = self.bridge.session_id
        self._rebuild_agent(carry_history=history)
        # Keep recording into the same stored session.
        self.bridge = SessionBridge(session_id=current_session)

    def clear_conversation(self) -> None:
        self.agent.load_history([])
        if self.reader.interactive:
            self.console.clear()

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
        loop = asyncio.get_running_loop()

        def _interrupt() -> None:
            asyncio.ensure_future(self.agent.submit(Interrupt()))

        try:
            loop.add_signal_handler(signal.SIGINT, _interrupt)
        except (NotImplementedError, RuntimeError):  # non-main loop / windows
            pass
        try:
            async for event in self.agent.run_stream(UserInput(text=text)):
                self.renderer.on_event(event)
                if event.msg.type == "task_complete":
                    final_text = event.msg.final_text
        finally:
            try:
                loop.remove_signal_handler(signal.SIGINT)
            except (NotImplementedError, RuntimeError, ValueError):
                pass
        self.bridge.record_turn(text, final_text)

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
                    self.console.print(f"[{theme.META_STYLE}]{status}[/]")
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
    parser.add_argument("--resume", "-r", default=None, help="Session id to resume.")
    parser.add_argument("--max-iterations", type=int, default=40)
    args = parser.parse_args(argv)

    app = TuiApp(
        workspace=args.workspace,
        model=args.model,
        max_iterations=args.max_iterations,
        resume_id=args.resume,
    )
    return asyncio.run(app.repl())


if __name__ == "__main__":
    raise SystemExit(main())
