"""Typed RPC adapters for the TUI's existing catalog and Goal commands."""

from __future__ import annotations

from cli.rpc_models import from_view
from core.application.errors import InvalidArgumentError
from core.application.goal_extension import GoalContinueResult
from core.application.mcp_service import McpInventory, McpPresetInventory
from core.application.plugin_service import PluginDiscovery
from core.application.skill_service import SkillDiscovery, SkillInfo
from core.domain.thread_goal import GoalOutcome, ThreadGoal
from core.mcp.oauth import McpOAuthFlowInfo
from core.mcp.probe import McpProbeResult
from core.providers.reasoning import ModelReasoningCapabilities


class ServiceLLM:
    def __init__(self, rpc):
        self.rpc = rpc

    def list_connections(self, project_id=None):
        return self.rpc.call("provider/list", {"projectId": project_id})

    def list_models(self, connection_id, *, project_id=None):
        return self.rpc.call(
            "model/list", {"projectId": project_id, "connectionId": connection_id}
        )

    def model_reasoning(self, connection_id, model, *, project_id=None):
        value = self.rpc.call(
            "model/reasoning",
            {"projectId": project_id, "connectionId": connection_id, "model": model},
        )["reasoning"]
        return ModelReasoningCapabilities.from_dict(value)


class ServiceSkills:
    def __init__(self, rpc):
        self.rpc = rpc

    def list(self, project_id):
        return from_view(
            SkillDiscovery, self.rpc.call("skills/list", {"projectId": project_id})
        )

    def select(self, project_id, identifier):
        value = self.rpc.call(
            "skill/read", {"projectId": project_id, "name": identifier}
        )["skill"]
        skill = from_view(SkillInfo, value)
        if not skill.selectable:
            raise InvalidArgumentError(f"Skill {skill.name} is not selectable")
        return skill


class ServicePlugins:
    def __init__(self, rpc):
        self.rpc = rpc

    def list(self):
        return from_view(PluginDiscovery, self.rpc.call("plugins/list", {}))


class ServiceMCP:
    def __init__(self, rpc):
        self.rpc = rpc

    def list(self, project_id=None):
        return from_view(
            McpInventory, self.rpc.call("mcp/list", {"projectId": project_id})
        )

    def list_presets(self, project_id=None):
        return from_view(
            McpPresetInventory, self.rpc.call("mcp/presets", {"projectId": project_id})
        )

    def add_preset(self, preset_id, *, project_id=None):
        return self.rpc.call(
            "mcp/preset/add", {"projectId": project_id, "presetId": preset_id}
        )

    def probe(self, name, *, project_id=None):
        return from_view(
            McpProbeResult,
            self.rpc.call("mcp/probe", {"projectId": project_id, "name": name}),
        )

    def oauth_start(self, name, *, project_id=None, open_browser=True):
        return from_view(
            McpOAuthFlowInfo,
            self.rpc.call(
                "mcp/oauth/start",
                {"projectId": project_id, "name": name, "openBrowser": open_browser},
            ),
        )

    def oauth_logout(self, name, *, project_id=None):
        return self.rpc.call(
            "mcp/oauth/logout", {"projectId": project_id, "name": name}
        )["removed"]

    def oauth_cancel(self, name, *, project_id=None):
        return self.rpc.call(
            "mcp/oauth/cancel", {"projectId": project_id, "name": name}
        )["cancelled"]

    def set_enabled(self, name, *, enabled, project_id=None):
        return self.rpc.call(
            "mcp/set-enabled",
            {"projectId": project_id, "name": name, "enabled": enabled},
        )

    def remove(self, *, name, scope, project_id=None):
        return self.rpc.call(
            "mcp/remove", {"projectId": project_id, "name": name, "scope": scope}
        )


class ServiceGoals:
    def __init__(self, rpc):
        self.rpc = rpc

    def read(self, thread_id):
        value = self.rpc.call("thread/goal/get", {"threadId": thread_id})["goal"]
        return from_view(ThreadGoal, value) if value else None

    def read_outcome(self, thread_id):
        value = self.rpc.call("thread/goal/get", {"threadId": thread_id})["outcome"]
        return from_view(GoalOutcome, value) if value else None

    def pause(self, thread_id, *, expected_goal_id):
        value = self.rpc.call(
            "thread/goal/pause",
            {"threadId": thread_id, "expectedGoalId": expected_goal_id},
        )
        return from_view(ThreadGoal, value["goal"])

    def resume(
        self,
        thread_id,
        *,
        expected_goal_id,
        client_surface=None,
        connection_id=None,
        model=None,
        reasoning_effort=None,
    ):
        value = self.rpc.call(
            "thread/goal/resume",
            {
                "threadId": thread_id,
                "expectedGoalId": expected_goal_id,
                **(
                    {"connectionId": connection_id} if connection_id is not None else {}
                ),
                **({"model": model} if model is not None else {}),
                **(
                    {"reasoningEffort": reasoning_effort}
                    if reasoning_effort is not None
                    else {}
                ),
            },
        )
        return from_view(ThreadGoal, value["goal"])

    def continue_goal(
        self,
        thread_id,
        *,
        expected_goal_id,
        client_surface=None,
        connection_id=None,
        model=None,
        reasoning_effort=None,
    ):
        return from_view(
            GoalContinueResult,
            self.rpc.call(
                "thread/goal/continue",
                {
                    "threadId": thread_id,
                    "expectedGoalId": expected_goal_id,
                    **(
                        {"connectionId": connection_id}
                        if connection_id is not None
                        else {}
                    ),
                    **({"model": model} if model is not None else {}),
                    **(
                        {"reasoningEffort": reasoning_effort}
                        if reasoning_effort is not None
                        else {}
                    ),
                },
            ),
        )

    def clear(self, thread_id, *, expected_goal_id):
        self.rpc.call(
            "thread/goal/clear",
            {"threadId": thread_id, "expectedGoalId": expected_goal_id},
        )

    def create(
        self,
        thread_id,
        *,
        objective,
        skill_ids=(),
        start=True,
        client_surface=None,
        token_budget=None,
    ):
        value = self.rpc.call(
            "thread/goal/set",
            {
                "threadId": thread_id,
                "objective": objective,
                "tokenBudget": token_budget,
                "skills": list(skill_ids),
                "start": start,
            },
        )
        return from_view(ThreadGoal, value["goal"])

    def edit(
        self,
        thread_id,
        *,
        expected_goal_id,
        objective,
        token_budget,
        skill_ids,
        continue_work,
        client_surface=None,
    ):
        value = self.rpc.call(
            "thread/goal/set",
            {
                "threadId": thread_id,
                "expectedGoalId": expected_goal_id,
                "objective": objective,
                "tokenBudget": token_budget,
                "skills": list(skill_ids),
                "start": continue_work,
            },
        )
        return from_view(ThreadGoal, value["goal"])
