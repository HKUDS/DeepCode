"""Allowlisted project test discovery with durable TestResult Items."""

from __future__ import annotations

from dataclasses import dataclass

from core.application.errors import (
    ConflictError,
    InvalidArgumentError,
    TurnNotFoundError,
)
from core.application.event_service import EventBroker
from core.application.views import item_view
from core.application.workspace_service import WorkspaceService
from core.domain.common import utc_now
from core.domain.item import Item, ItemKind, ItemStatus
from core.persistence.database import Database
from core.persistence.event_repository import EventRepository
from core.persistence.execution_repository import ItemRepository, TurnRepository
from core.verification import discover_verification_commands, run_verification


@dataclass(frozen=True, slots=True)
class TestCommand:
    id: str
    label: str
    argv: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class TestRunResult:
    item: Item
    command: TestCommand
    exit_code: int | None
    timed_out: bool
    duration_ms: int
    stdout: str
    stderr: str
    output_truncated: bool


class TestService:
    def __init__(
        self,
        database: Database,
        broker: EventBroker,
        workspaces: WorkspaceService,
    ) -> None:
        self.database = database
        self.broker = broker
        self.workspaces = workspaces

    def discover(self, thread_id: str) -> tuple[TestCommand, ...]:
        context = self.workspaces.resolve(thread_id)
        return tuple(
            TestCommand(command.id, command.label, command.argv)
            for command in discover_verification_commands(context.root)
        )

    def run(
        self,
        thread_id: str,
        turn_id: str,
        command_id: str,
        *,
        timeout_seconds: int = 300,
    ) -> TestRunResult:
        if not 1 <= timeout_seconds <= 1800:
            raise InvalidArgumentError(
                "test timeout must be between 1 and 1800 seconds"
            )
        context = self.workspaces.resolve(thread_id, require_trusted=True)
        commands = {command.id: command for command in self.discover(thread_id)}
        command = commands.get(command_id)
        if command is None:
            raise InvalidArgumentError(
                "test command is not available for this workspace"
            )
        with self.database.read() as connection:
            turn = TurnRepository(connection).get(turn_id)
            active = TurnRepository(connection).active_for_thread(thread_id)
        if turn is None or turn.thread_id != thread_id:
            raise TurnNotFoundError(f"turn not found in Thread: {turn_id}")
        if active is not None:
            raise ConflictError("cannot run verification while a Turn is active")

        try:
            discovered = next(
                candidate
                for candidate in discover_verification_commands(context.root)
                if candidate.id == command.id
            )
            process_result = run_verification(
                context.root, discovered, timeout_seconds=timeout_seconds
            )
        except (OSError, StopIteration) as exc:
            raise InvalidArgumentError(
                f"test executable is unavailable: {command.argv[0]}"
            ) from exc
        duration_ms = process_result.duration_ms
        stdout = process_result.stdout
        stderr = process_result.stderr
        timed_out = process_result.timed_out
        output_truncated = process_result.output_truncated
        succeeded = process_result.passed
        summary = (
            f"{command.label} passed"
            if succeeded
            else f"{command.label} timed out"
            if timed_out
            else f"{command.label} failed with exit code {process_result.exit_code}"
        )
        now = utc_now()
        with self.database.transaction() as connection:
            items = ItemRepository(connection)
            item = Item(
                thread_id=thread_id,
                turn_id=turn_id,
                ordinal=items.next_ordinal(turn_id),
                kind=ItemKind.TEST_RESULT,
                status=ItemStatus.COMPLETED if succeeded else ItemStatus.FAILED,
                summary=summary,
                payload={
                    "commandId": command.id,
                    "command": list(command.argv),
                    "exitCode": process_result.exit_code,
                    "timedOut": timed_out,
                    "durationMs": duration_ms,
                    "stdout": stdout,
                    "stderr": stderr,
                    "outputTruncated": output_truncated,
                },
                created_at=now,
                updated_at=now,
            )
            items.add(item)
            event = EventRepository(connection).append(
                thread_id=thread_id,
                turn_id=turn_id,
                item_id=item.id,
                type="item.created",
                payload={"item": item_view(item)},
            )
        self.broker.publish(event)
        return TestRunResult(
            item=item,
            command=command,
            exit_code=process_result.exit_code,
            timed_out=timed_out,
            duration_ms=duration_ms,
            stdout=stdout,
            stderr=stderr,
            output_truncated=output_truncated,
        )
