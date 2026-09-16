"""TUI attachment using the shared service, with no local execution owner."""

from __future__ import annotations

import asyncio
from pathlib import Path

from app_server.blocking_client import BlockingServiceClient
from app_server.service_state import ServiceFiles
from cli.rpc_models import from_view
from cli.service_catalogs import (
    ServiceGoals,
    ServiceLLM,
    ServiceMCP,
    ServicePlugins,
    ServiceSkills,
)
from cli.service_events import ServiceEventRenderer
from cli.service_turns import ServiceTurns
from cli.thread_client import ThreadListing, TurnDelivery, turn_access_summary
from core.application.errors import ProjectNotTrustedError
from core.application.interactive_turn_router import InteractiveTurnRouter
from core.domain.approval import Approval, ApprovalStatus
from core.domain.common import new_id
from core.domain.event import DomainEvent
from core.domain.execution_profile import ExecutionProfile
from core.domain.execution_security import ExecutionSecurityProfile
from core.domain.message_provenance import ClientSurface
from core.domain.project import Project, TrustState
from core.domain.thread import Thread
from core.events import ErrorEvent, Event
from core.persistence.database import default_database_path
from core.sessions import SessionStore


class ServiceThreadClient:
    runtime_mode = "service"

    def __init__(
        self,
        *,
        workspace,
        model,
        connection_id,
        reasoning_effort,
        max_iterations,
        streaming,
        trust_workspace=False,
        resume_id=None,
        store=None,
        event_sink=None,
        database=None,
        surface="cli",
    ):
        if max_iterations is not None:
            raise ValueError("--max-iterations is not supported by the shared service")
        self.workspace = str(Path(workspace or Path.cwd()).expanduser().resolve())
        self._event_sink = event_sink
        self._domain_task = None
        self._renderer = ServiceEventRenderer()
        self._domain_sink = None
        self._selection_generation = 0
        self._sequence = 0
        self.rpc = BlockingServiceClient(
            ServiceFiles(database or default_database_path()), surface=surface
        )
        try:
            self.llm = ServiceLLM(self.rpc)
            self.skills = ServiceSkills(self.rpc)
            self.plugins = ServicePlugins(self.rpc)
            self.mcp = ServiceMCP(self.rpc)
            self.goals = ServiceGoals(self.rpc)
            self.turns = ServiceTurns(self.rpc)
            self.router = InteractiveTurnRouter(
                self.turns, client_surface=ClientSurface.CLI
            )
            diagnostics = self.rpc.call("diagnostics/read", {})["diagnostics"]
            self.store = store or SessionStore(Path(diagnostics["sessionStorePath"]))
            if resume_id is None:
                project = self.rpc.call(
                    "project/add",
                    {
                        "path": self.workspace,
                        "trustState": "trusted" if trust_workspace else "untrusted",
                    },
                )["project"]
                self.project = from_view(Project, project)
                if trust_workspace and not self.project_trusted:
                    self.project = from_view(
                        Project,
                        self.rpc.call(
                            "project/update",
                            {"projectId": self.project.id, "trustState": "trusted"},
                        )["project"],
                    )
                self._require_trust()
                value = self.rpc.call(
                    "thread/start",
                    {
                        "projectId": self.project.id,
                        "title": "New task",
                        "connectionId": connection_id,
                        "model": model,
                        "reasoningEffort": reasoning_effort,
                    },
                )
                self._replace_thread(from_view(Thread, value["thread"]))
            else:
                existing = from_view(
                    Thread,
                    self.rpc.call("thread/read", {"threadId": resume_id})["thread"],
                )
                if workspace is None:
                    self.workspace = existing.workspace_path
                project = (
                    self.rpc.call("project/add", {"path": self.workspace})["project"]
                    if workspace is not None
                    else self.rpc.call(
                        "project/read", {"projectId": existing.project_id}
                    )["project"]
                )
                self.project = from_view(Project, project)
                if trust_workspace:
                    self.project = from_view(
                        Project,
                        self.rpc.call(
                            "project/update",
                            {"projectId": self.project.id, "trustState": "trusted"},
                        )["project"],
                    )
                self._require_trust()
                value = self.rpc.call(
                    "thread/resume",
                    {"sessionId": resume_id, "workspacePath": self.workspace},
                )
                self._replace_thread(from_view(Thread, value["thread"]))
                if surface != "headless" and (
                    model is not None
                    or connection_id is not None
                    or reasoning_effort is not None
                ):
                    self.switch_execution(
                        connection_id=connection_id or self.thread.connection_id,
                        model=model or self.thread.model,
                        reasoning_effort=reasoning_effort
                        if reasoning_effort is not None
                        else self.thread.reasoning_effort,
                        context_window=self.thread.context_window,
                    )
        except BaseException:
            self.rpc.close()
            raise

    @property
    def session_id(self):
        return self.thread.id

    @property
    def model(self):
        return self.execution_profile.model_id

    @property
    def project_trusted(self):
        return self.project.trust_state is TrustState.TRUSTED

    @property
    def access_preset_override(self):
        return self.thread.access_preset_override

    def _require_trust(self):
        if not self.project_trusted:
            raise ProjectNotTrustedError(
                "Project is untrusted; inspect it and use --trust explicitly"
            )

    def _replace_thread(self, thread):
        project = from_view(
            Project,
            self.rpc.call("project/read", {"projectId": thread.project_id})["project"],
        )
        if project.trust_state is not TrustState.TRUSTED:
            raise ProjectNotTrustedError(
                "Project is untrusted; inspect it and use --trust explicitly"
            )
        profile = self.rpc.call("thread/execution/read", {"threadId": thread.id})
        execution = ExecutionProfile.from_dict(profile["executionProfile"])
        security = ExecutionSecurityProfile.from_dict(profile["securityProfile"])
        if execution is None or security is None:
            raise ValueError("Service returned an invalid execution profile")
        sequence = self.rpc.call("event/replay", {"threadId": thread.id, "limit": 1})[
            "headSequence"
        ]
        renderer = ServiceEventRenderer()
        active = self.turns.executing_for_thread(thread.id)
        if active:
            renderer.seed(self.turns.read(active.id).items)
        self.thread, self.project = thread, project
        self.execution_profile, self._security = execution, security
        self._sequence, self._renderer = sequence, renderer
        self._selection_generation += 1

    def access_summary(self):
        override = self.thread.access_preset_override
        if override is not None:
            return override.value.replace("_", " ")
        profile = self._security
        if profile.access_preset is not None:
            return f"default ({profile.access_preset.value.replace('_', ' ')})"
        return f"legacy ({profile.permission_mode.value})"

    def frozen_access_summaries(self):
        turns = self.turns.list_for_thread(self.thread.id)
        current = next(
            (
                turn
                for turn in turns
                if turn.status.value in {"running", "waiting_approval"}
            ),
            None,
        )
        return turn_access_summary(current) if current else None, tuple(
            turn_access_summary(turn) for turn in turns if turn.status.value == "queued"
        )

    def send(self, prompt, *, skill_ids=()):
        active = self.turns.executing_for_thread(self.thread.id)
        result = self.router.send(
            self.thread.id,
            prompt=prompt,
            message_id=new_id("tinp"),
            cached_active_turn_id=active.id if active else None,
            skill_ids=skill_ids,
        )
        if result.delivery.value in {"started", "queued"}:
            self._title_from_first_prompt(prompt)
        return TurnDelivery(result.delivery.value, result.turn)

    def queue(self, prompt, *, skill_ids=()):
        snapshot = self.turns.enqueue(
            self.thread.id,
            prompt=prompt,
            message_id=new_id("tinp"),
            skill_ids=skill_ids,
        )
        self._title_from_first_prompt(prompt)
        return TurnDelivery("queued", snapshot.turn)

    def has_active_turn(self):
        return self.turns.active_for_thread(self.thread.id) is not None

    def rename_thread(self, title):
        self.thread = from_view(
            Thread,
            self.rpc.call(
                "thread/rename", {"threadId": self.thread.id, "title": title}
            )["thread"],
        )
        return self.thread

    def delete_session(self, session_id):
        if session_id == self.thread.id:
            raise RuntimeError("cannot delete the current Session; /new first")
        self.rpc.call("thread/delete", {"threadId": session_id})

    def last_terminal_turn(self):
        return next(
            (
                turn
                for turn in reversed(self.turns.list_for_thread(self.thread.id))
                if turn.status.is_terminal
            ),
            None,
        )

    def retry_turn(self, turn_id):
        return self.turns.retry(turn_id, use_current_selection=True).turn

    def interrupt(self):
        active = self.turns.active_for_thread(self.thread.id)
        return self.turns.interrupt(self.thread.id, active.id) if active else None

    def pending_approval(self):
        active = self.turns.executing_for_thread(self.thread.id)
        if active is None:
            return None
        return next(
            (
                approval
                for approval in self.turns.read(active.id).approvals
                if approval.status is ApprovalStatus.PENDING
            ),
            None,
        )

    def respond_to_approval(self, approval_id, decision):
        return from_view(
            Approval,
            self.rpc.call(
                "approval/respond",
                {"approvalId": approval_id, "decision": decision.value},
            )["approval"],
        )

    async def wait_until_idle(self):
        while await asyncio.to_thread(self.has_active_turn):
            await asyncio.sleep(0.05)
        await self.drain_events_async()
        await asyncio.sleep(0)

    def _require_idle(self):
        if self.has_active_turn():
            raise RuntimeError(
                "the current Turn is still active; stop it before changing Session"
            )

    def new_thread(self, *, title=""):
        self._require_idle()
        value = self.rpc.call(
            "thread/start",
            {
                "projectId": self.project.id,
                "title": title.strip() or "New task",
                "connectionId": self.execution_profile.connection_id,
                "model": self.execution_profile.model_id,
                "reasoningEffort": self.thread.reasoning_effort,
                "contextWindow": self.thread.context_window,
            },
        )
        self._replace_thread(from_view(Thread, value["thread"]))
        return self.thread

    def resume(self, session_id):
        self._require_idle()
        existing = from_view(
            Thread, self.rpc.call("thread/read", {"threadId": session_id})["thread"]
        )
        project = from_view(
            Project,
            self.rpc.call("project/read", {"projectId": existing.project_id})[
                "project"
            ],
        )
        if project.trust_state is not TrustState.TRUSTED:
            raise ProjectNotTrustedError("Project is untrusted")
        value = self.rpc.call(
            "thread/resume", {"sessionId": session_id, "workspacePath": self.workspace}
        )
        self._replace_thread(from_view(Thread, value["thread"]))
        self._require_trust()
        return self.thread

    def list_recent(self, *, limit, include_all):
        result = self.rpc.call(
            "thread/list",
            {"cwd": None if include_all else self.workspace, "limit": limit},
        )
        listings = []
        for value in result["threads"]:
            thread = from_view(Thread, value)
            session = self.store.get_session(thread.id)
            if session and session.messages:
                listings.append(
                    ThreadListing(
                        thread.id,
                        thread.title,
                        len(session.messages),
                        thread.updated_at,
                        thread.workspace_path,
                        thread.id == self.thread.id,
                    )
                )
        return listings

    def switch_execution(
        self, *, connection_id, model, reasoning_effort, context_window
    ):
        value = self.rpc.call(
            "thread/execution/update",
            {
                "threadId": self.thread.id,
                "connectionId": connection_id,
                "model": model,
                "reasoningEffort": reasoning_effort,
                "contextWindow": context_window,
            },
        )
        self.thread = from_view(Thread, value["thread"])
        profile = self.rpc.call("thread/execution/read", {"threadId": self.thread.id})
        self.execution_profile = ExecutionProfile.from_dict(profile["executionProfile"])
        return self.execution_profile

    def set_access_preset(self, access_preset):
        value = self.rpc.call(
            "thread/permission/update",
            {
                "threadId": self.thread.id,
                "accessPreset": access_preset.value if access_preset else None,
                "riskAcknowledged": access_preset is not None
                and access_preset.value == "full_access",
            },
        )
        self.thread = from_view(Thread, value["thread"])
        return self.thread

    def set_agent_preset(self, preset_id):
        self.rpc.call(
            "preset/select", {"threadId": self.thread.id, "agentPreset": preset_id}
        )
        return self.refresh_thread()

    def current_agent_preset_id(self):
        return self.rpc.call("preset/current", {"threadId": self.thread.id})[
            "agentPreset"
        ]

    def refresh_thread(self):
        thread_id = self.thread.id
        thread = from_view(
            Thread, self.rpc.call("thread/read", {"threadId": thread_id})["thread"]
        )
        profile = self.rpc.call("thread/execution/read", {"threadId": thread_id})
        execution = ExecutionProfile.from_dict(profile["executionProfile"])
        security = ExecutionSecurityProfile.from_dict(profile["securityProfile"])
        if execution is None or security is None:
            raise ValueError("Service returned an invalid execution profile")
        if self.thread.id == thread_id:
            self.thread, self.execution_profile, self._security = (
                thread,
                execution,
                security,
            )
        return self.thread

    def clear_context(self):
        self.rpc.call("thread/context/clear", {"threadId": self.thread.id})

    async def compact_context(self):
        return await asyncio.to_thread(
            self.rpc.call, "thread/context/compact", {"threadId": self.thread.id}
        )

    def set_event_loop(self, loop):
        # Events are consumed on the TUI loop by the bounded pump below.
        pass

    def _consume_page(self, page):
        for value in page["events"]:
            event = from_view(DomainEvent, value)
            if event.sequence <= self._sequence:
                continue
            if event.sequence != self._sequence + 1:
                raise RuntimeError("Thread event replay is not contiguous")
            if self._domain_sink:
                self._domain_sink(event)
            for rendered in self._renderer.convert(event):
                if self._event_sink:
                    self._event_sink(rendered)
            self._sequence = event.sequence

    def drain_events(self):
        through = None
        while True:
            params = {"threadId": self.thread.id, "after": self._sequence, "limit": 100}
            if through is not None:
                params["through"] = through
            page = self.rpc.call("event/replay", params)
            through = page["headSequence"]
            self._consume_page(page)
            if not page["hasMore"]:
                return

    async def drain_events_async(self):
        through = None
        generation = self._selection_generation
        while True:
            params = {"threadId": self.thread.id, "after": self._sequence, "limit": 100}
            if through is not None:
                if through <= self._sequence:
                    return
                params["through"] = through
            page = await asyncio.to_thread(self.rpc.call, "event/replay", params)
            if generation != self._selection_generation:
                return
            through = page["headSequence"]
            self._consume_page(page)
            if not page["hasMore"]:
                return

    async def start_domain_events(self, sink):
        if self._domain_task is not None and not self._domain_task.done():
            return
        self._domain_sink = sink

        async def pump():
            through = None
            failures = 0
            while True:
                thread_id, generation = self.thread.id, self._selection_generation
                params = {"threadId": thread_id, "after": self._sequence, "limit": 100}
                if through is not None and through > self._sequence:
                    params["through"] = through
                try:
                    page = await asyncio.to_thread(
                        self.rpc.call, "event/replay", params
                    )
                    failures = 0
                except (RuntimeError, OSError) as exc:
                    failures += 1
                    if failures == 1 and self._event_sink:
                        self._event_sink(
                            Event(
                                "connection",
                                ErrorEvent(
                                    f"Service disconnected: {exc}. Admitted tasks may still be running."
                                ),
                            )
                        )
                    if failures >= 6:
                        if self._event_sink:
                            self._event_sink(
                                Event(
                                    "connection",
                                    ErrorEvent(
                                        "Use /reconnect after the service is available."
                                    ),
                                )
                            )
                        return
                    await asyncio.sleep(min(4, 0.25 * 2**failures))
                    continue
                if generation != self._selection_generation:
                    through = None
                    continue
                self._consume_page(page)
                through = page["headSequence"] if page["hasMore"] else None
                await asyncio.sleep(0.05)

        self._domain_task = asyncio.create_task(pump())

    async def reconnect(self):
        await self.stop_domain_events()
        await asyncio.to_thread(self.rpc.reconnect)
        await self.start_domain_events(self._domain_sink)

    async def stop_domain_events(self):
        if self._domain_task is not None:
            self._domain_task.cancel()
            await asyncio.gather(self._domain_task, return_exceptions=True)
            self._domain_task = None

    async def close(self):
        await self.stop_domain_events()
        await asyncio.to_thread(self.rpc.close)

    def _title_from_first_prompt(self, prompt):
        if self.thread.title == "New task" and prompt.strip():
            try:
                self.rename_thread(prompt.strip().splitlines()[0][:60])
            except (RuntimeError, OSError):
                # Admission already succeeded. A presentation write cannot
                # make the composer report that the submitted input failed.
                pass
