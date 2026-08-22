"""Input layer — prompt_toolkit when interactive, plain stdin when piped.

Interactive mode gives persistent history (~/.deepcode/tui_history),
``@path`` file completion against the workspace, and slash-command
completion. When stdin is not a TTY (scripted runs, tests, CI) the same
``read()`` coroutine falls back to plain line reads, so the whole TUI stays
drivable by a pipe — which is exactly how the real-model review harness
exercises it.
"""

from __future__ import annotations

import asyncio
import os
import sys
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Protocol

from prompt_toolkit import PromptSession
from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.formatted_text import StyleAndTextTuples
from prompt_toolkit.history import FileHistory
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.patch_stdout import patch_stdout
from prompt_toolkit.styles import Style

from cli.tui import theme
from cli.tui.commands import REGISTRY
from core.config import deepcode_home
from core.private_storage import (
    ensure_private_directory,
    ensure_private_file,
    open_private_file,
)

_HISTORY_PATH: Path | None = None
_SKIP_DIRS = {".git", "__pycache__", "node_modules", ".venv", "venv"}
_MAX_COMPLETIONS = 30
# The status line is animated (spinner ~11fps, dsh's 2.6s glare sweep), so
# while work runs the prompt has to repaint faster than the eye notices.
# It is driven by an activity probe rather than prompt_toolkit's
# ``refresh_interval``, which is a fixed metronome for the whole prompt: at
# 10Hz an *idle* TUI burned ~10% of a core doing layout passes for a line
# that never changes. Motion costs only while there is motion.
_ANIMATION_INTERVAL_SECONDS = 0.1
_IDLE_PROBE_SECONDS = 0.25


class InputInterrupted(Exception):
    """The user requested that the active Turn stop, not that the CLI exit."""


class SkillCompletionItem(Protocol):
    name: str
    description: str
    status: str


class TuiCompleter(Completer):
    """Complete ``/commands`` at line start and ``@paths`` anywhere."""

    def __init__(
        self,
        workspace: str,
        skill_provider: Callable[[], Iterable[SkillCompletionItem]] | None = None,
    ) -> None:
        self.workspace = Path(workspace)
        self._skill_provider = skill_provider
        # (command name, typed prefix) -> candidate first arguments; wired by
        # the app so command knowledge stays in the registry, not here.
        self._argument_provider: Callable[[str, str], Iterable[str]] | None = None

    def set_skill_provider(
        self,
        provider: Callable[[], Iterable[SkillCompletionItem]],
    ) -> None:
        self._skill_provider = provider

    def set_argument_provider(
        self,
        provider: Callable[[str, str], Iterable[str]],
    ) -> None:
        self._argument_provider = provider

    def get_completions(self, document, complete_event):
        text = document.text_before_cursor
        if text.startswith("/") and " " not in text:
            prefix = text[1:]
            for name in REGISTRY:
                if name.startswith(prefix):
                    yield Completion(name, start_position=-len(prefix))
            return
        if text.startswith("/"):
            name, _, argument = text[1:].partition(" ")
            provider = self._argument_provider
            if provider is not None and " " not in argument:
                emitted = False
                for value in provider(name.lower(), argument):
                    emitted = True
                    yield Completion(value, start_position=-len(argument))
                if emitted:
                    return
            # Commands without argument candidates keep @path / $skill
            # completion for free-form arguments below.
        dollar = text.rfind("$")
        if dollar >= 0 and (dollar == 0 or text[dollar - 1].isspace()):
            fragment = text[dollar + 1 :]
            if fragment and any(character.isspace() for character in fragment):
                return
            try:
                records = (
                    tuple(self._skill_provider())
                    if self._skill_provider is not None
                    else ()
                )
            except Exception:  # noqa: BLE001 - completion must remain best effort
                records = ()
            for record in records:
                if record.status != "active" or not record.name.casefold().startswith(
                    fragment.casefold()
                ):
                    continue
                yield Completion(
                    record.name,
                    start_position=-len(fragment),
                    display=f"${record.name} — {record.description}",
                )
            return
        at = text.rfind("@")
        if at == -1:
            return
        fragment = text[at + 1 :]
        if any(ch.isspace() for ch in fragment):
            return
        yield from self._path_completions(fragment)

    def _path_completions(self, fragment: str):
        base = self.workspace / fragment if fragment else self.workspace
        directory = base if fragment.endswith("/") else base.parent
        stem = "" if fragment.endswith("/") else base.name
        if not directory.is_dir():
            return
        count = 0
        for entry in sorted(directory.iterdir()):
            if entry.name in _SKIP_DIRS or entry.name.startswith("."):
                continue
            if stem and not entry.name.startswith(stem):
                continue
            rel = entry.relative_to(self.workspace)
            suffix = "/" if entry.is_dir() else ""
            yield Completion(f"{rel}{suffix}", start_position=-len(fragment))
            count += 1
            if count >= _MAX_COMPLETIONS:
                return


class InputReader:
    """One awaitable ``read()`` for both interactive and piped stdin."""

    def __init__(
        self,
        workspace: str,
        *,
        # Formatted text (prompt_toolkit ``(style, text)`` fragments) or a
        # plain string: the status line paints its own colour and motion, so
        # the reader only has to call it on every repaint.
        status_provider: Callable[[], StyleAndTextTuples | str] | None = None,
        toggle_transcript: Callable[[], str] | None = None,
        # "Is there anything moving right now?" — the only question the
        # animation loop asks, several times a second.
        activity_probe: Callable[[], bool] | None = None,
    ) -> None:
        self.interactive = sys.stdin.isatty()
        self._prompt_session: PromptSession | None = None
        self._completer = TuiCompleter(workspace)
        self._activity_probe = activity_probe
        if self.interactive:
            history_path = _HISTORY_PATH or deepcode_home() / "tui_history"
            ensure_private_directory(history_path.parent)
            if not history_path.exists():
                descriptor = open_private_file(
                    history_path,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                )
                os.close(descriptor)
            ensure_private_file(history_path)
            bindings = KeyBindings()

            @bindings.add("escape")
            def _interrupt(event) -> None:
                event.app.exit(exception=InputInterrupted())

            @bindings.add("c-o")
            def _toggle_transcript(event) -> None:
                if toggle_transcript is not None:
                    toggle_transcript()
                event.app.invalidate()

            self._prompt_session = PromptSession(
                history=FileHistory(str(history_path)),
                completer=self._completer,
                complete_while_typing=True,
                key_bindings=bindings,
                bottom_toolbar=status_provider,
                style=Style.from_dict(theme.STATUS_STYLE_RULES),
            )

    def set_skill_provider(
        self,
        provider: Callable[[], Iterable[SkillCompletionItem]],
    ) -> None:
        self._completer.set_skill_provider(provider)

    def set_argument_provider(
        self,
        provider: Callable[[str, str], Iterable[str]],
    ) -> None:
        self._completer.set_argument_provider(provider)

    async def _animate(self) -> None:
        """Repaint the prompt while the status line has something moving.

        The spinner and the sweep are pure functions of elapsed time, so
        motion is entirely a matter of *when* the prompt redraws. This
        polls the probe cheaply, redraws at animation speed only while work
        runs, and spends one final redraw on the way back to idle so the
        line settles instead of freezing on its last frame.
        """
        session = self._prompt_session
        if session is None or self._activity_probe is None:
            return
        moving = False
        while True:
            try:
                active = bool(self._activity_probe())
            except Exception:  # noqa: BLE001 - a probe must never kill the prompt
                active = False
            if active or moving:
                session.app.invalidate()
            moving = active
            await asyncio.sleep(
                _ANIMATION_INTERVAL_SECONDS if active else _IDLE_PROBE_SECONDS
            )

    async def read(self) -> str | None:
        """Next input line, or ``None`` on EOF (ctrl-d / pipe end)."""
        if self._prompt_session is not None:
            animator = asyncio.create_task(self._animate())
            try:
                # raw=True: without it prompt_toolkit REPLACES every ESC
                # byte written while the prompt is active with "?", so all
                # rich color output printed mid-turn rendered as literal
                # "?[36m" garbage on screen.
                with patch_stdout(raw=True):
                    return await self._prompt_session.prompt_async(
                        theme.prompt_fragments()
                    )
            except KeyboardInterrupt as exc:
                raise InputInterrupted() from exc
            except EOFError:
                return None
            finally:
                animator.cancel()
        loop = asyncio.get_running_loop()
        line = await loop.run_in_executor(None, sys.stdin.readline)
        if line == "":
            return None
        return line.rstrip("\n")


def expand_file_refs(text: str, workspace: str) -> str:
    """Inline ``@path`` references as fenced file content.

    Each ``@token`` that resolves to a readable file inside the workspace is
    appended as a fenced block, so the model sees the content without a read
    round-trip. Unresolvable tokens are left untouched (they may be emails,
    decorators, etc. — never guess).
    """
    attachments: list[str] = []
    for token in text.split():
        if not token.startswith("@") or len(token) < 2:
            continue
        candidate = token[1:].rstrip(",;:")
        path = Path(os.path.expanduser(candidate))
        if not path.is_absolute():
            path = Path(workspace) / candidate
        try:
            resolved = path.resolve()
            resolved.relative_to(Path(workspace).resolve())
            if resolved.is_file() and resolved.stat().st_size <= 128 * 1024:
                content = resolved.read_text(encoding="utf-8", errors="replace")
                attachments.append(
                    f"\n\n[attached file: {candidate}]\n```\n{content}\n```"
                )
        except (OSError, ValueError):
            continue
    return text + "".join(attachments)
