"""Explicit domain-to-wire projections.

Keeping projections here prevents dataclass internals and enum objects from
leaking into JSON-RPC or CLI consumers.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from core.domain.approval import Approval
from core.domain.artifact import Artifact
from core.domain.automation import Automation, AutomationRun
from core.domain.event import DomainEvent
from core.domain.item import Item
from core.domain.project import Project
from core.domain.thread import Thread
from core.domain.thread_goal import GoalOutcome, ThreadGoal
from core.domain.turn import Turn
from core.domain.workflow import WorkflowRun


def file_entry_view(entry) -> dict[str, Any]:
    return {
        "path": entry.path,
        "name": entry.name,
        "kind": entry.kind,
        "size": entry.size,
        "modifiedAt": entry.modified_at,
        "hidden": entry.hidden,
    }


def file_content_view(content) -> dict[str, Any]:
    return {
        "path": content.path,
        "content": content.content,
        "byteSize": content.byte_size,
        "sha256": content.sha256,
        "lineCount": content.line_count,
        "truncated": content.truncated,
    }


def git_status_view(status) -> dict[str, Any]:
    return {
        "repositoryRoot": status.repository_root,
        "branch": status.branch,
        "upstream": status.upstream,
        "ahead": status.ahead,
        "behind": status.behind,
        "detached": status.detached,
        "entries": [
            {
                "path": entry.path,
                "originalPath": entry.original_path,
                "indexStatus": entry.index_status,
                "worktreeStatus": entry.worktree_status,
                "kind": entry.kind,
            }
            for entry in status.entries
        ],
    }


def file_diff_view(file_diff) -> dict[str, Any]:
    return {
        "path": file_diff.path,
        "originalPath": file_diff.original_path,
        "status": file_diff.status,
        "binary": file_diff.binary,
        "additions": file_diff.additions,
        "deletions": file_diff.deletions,
        "revision": file_diff.revision,
        "hunks": [
            {
                "oldStart": hunk.old_start,
                "oldLines": hunk.old_lines,
                "newStart": hunk.new_start,
                "newLines": hunk.new_lines,
                "heading": hunk.heading,
                "lines": [
                    {
                        "kind": line.kind,
                        "text": line.text,
                        "oldLine": line.old_line,
                        "newLine": line.new_line,
                    }
                    for line in hunk.lines
                ],
            }
            for hunk in file_diff.hunks
        ],
    }


def worktree_result_view(result) -> dict[str, Any]:
    return {
        "thread": thread_view(result.thread),
        "path": result.path,
        "branch": result.branch,
        "disposition": result.disposition,
        "dirty": result.dirty,
    }


def terminal_info_view(info) -> dict[str, Any]:
    return {
        "terminalId": info.id,
        "threadId": info.thread_id,
        "pid": info.pid,
        "columns": info.columns,
        "rows": info.rows,
        "workspacePath": info.workspace_path,
    }


def test_command_view(command) -> dict[str, Any]:
    return {"id": command.id, "label": command.label, "argv": list(command.argv)}


def skill_info_view(skill) -> dict[str, Any]:
    return {
        "id": skill.id,
        "name": skill.name,
        "description": skill.description,
        "allowedTools": list(skill.allowed_tools),
        "scope": skill.scope,
        "sourceRoot": skill.source_root,
        "source": skill.source,
        "location": skill.location,
        "status": skill.status,
        "enabled": skill.enabled,
        "selectable": skill.selectable,
        "revision": skill.revision,
        "byteSize": skill.byte_size,
        "shadowedBy": skill.shadowed_by,
        "error": skill.error,
    }


def skill_detail_view(skill) -> dict[str, Any]:
    return {
        **skill_info_view(skill.info),
        "instructions": skill.instructions,
        "truncated": skill.truncated,
    }


def hook_info_view(hook) -> dict[str, Any]:
    return {
        "eventName": hook.event_name,
        "matcher": hook.matcher,
        "command": hook.command,
        "timeoutSeconds": hook.timeout_seconds,
        "source": hook.source,
        "sourcePath": hook.source_path,
        "displayOrder": hook.display_order,
        "statusMessage": hook.status_message,
    }


def mcp_server_view(server) -> dict[str, Any]:
    return {
        "name": server.name,
        "transport": server.transport,
        "command": server.command,
        "args": list(server.args),
        "url": server.url,
        "enabledTools": list(server.enabled_tools),
        "toolTimeout": server.tool_timeout,
        "description": server.description,
        "envKeys": list(server.env_keys),
        "headerKeys": list(server.header_keys),
        "source": server.source,
        "configurationState": server.configuration_state,
        "configurationMessage": server.configuration_message,
    }


def mcp_inventory_view(inventory) -> dict[str, Any]:
    return {
        "servers": [mcp_server_view(server) for server in inventory.servers],
        "userConfigPath": inventory.user_config_path,
        "projectConfigPath": inventory.project_config_path,
    }


def automation_view(automation: Automation) -> dict[str, Any]:
    return {
        "id": automation.id,
        "projectId": automation.project_id,
        "threadId": automation.thread_id,
        "name": automation.name,
        "currentRevisionId": automation.current_revision_id,
        "prompt": automation.prompt,
        "status": automation.status.value,
        "scheduleKind": automation.schedule_kind.value,
        "intervalSeconds": automation.interval_seconds,
        "nextRunAt": timestamp(automation.next_run_at),
        "lastRunAt": timestamp(automation.last_run_at),
        "createdAt": timestamp(automation.created_at),
        "updatedAt": timestamp(automation.updated_at),
    }


def automation_run_view(run: AutomationRun) -> dict[str, Any]:
    return {
        "id": run.id,
        "automationId": run.automation_id,
        "revisionId": run.revision_id,
        "occurrenceId": run.occurrence_id,
        "threadId": run.thread_id,
        "goalId": run.goal_id,
        "turnId": run.turn_id,
        "trigger": run.trigger.value,
        "status": run.status.value,
        "scheduledFor": timestamp(run.scheduled_for),
        "detail": run.detail,
        "createdAt": timestamp(run.created_at),
        "updatedAt": timestamp(run.updated_at),
        "startedAt": timestamp(run.started_at),
        "completedAt": timestamp(run.completed_at),
    }


def thread_goal_view(goal: ThreadGoal) -> dict[str, Any]:
    """Project the minimal Goal without legacy Attempt aggregates."""

    return {
        "id": goal.id,
        "threadId": goal.thread_id,
        "objective": goal.objective,
        "status": goal.status.value,
        "tokenBudget": goal.token_budget,
        "tokensUsed": goal.tokens_used,
        "timeUsedSeconds": goal.time_used_seconds,
        "skillIds": list(goal.skill_ids),
        "createdAt": timestamp(goal.created_at),
        "updatedAt": timestamp(goal.updated_at),
    }


def goal_outcome_view(outcome: GoalOutcome) -> dict[str, Any]:
    return {
        "status": outcome.status.value,
        "reason": outcome.reason,
        "source": outcome.source.value,
        "decidedByTurnId": outcome.decided_by_turn_id,
        "decidedAt": timestamp(outcome.decided_at),
        "evidenceRefs": [
            {
                "itemId": evidence.item_id,
                "turnId": evidence.turn_id,
                "kind": evidence.kind,
                "status": evidence.status,
                "summary": evidence.summary,
            }
            for evidence in outcome.evidence_refs
        ],
    }


def timestamp(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def project_view(project: Project) -> dict[str, Any]:
    return {
        "id": project.id,
        "canonicalPath": project.canonical_path,
        "displayName": project.display_name,
        "trustState": project.trust_state.value,
        "settings": project.settings,
        "createdAt": timestamp(project.created_at),
        "updatedAt": timestamp(project.updated_at),
        "lastOpenedAt": timestamp(project.last_opened_at),
    }


def thread_view(thread: Thread) -> dict[str, Any]:
    return {
        "id": thread.id,
        "projectId": thread.project_id,
        "parentThreadId": thread.parent_thread_id,
        "title": thread.title,
        "mode": thread.mode.value,
        "status": thread.status.value,
        "model": thread.model,
        "connectionId": thread.connection_id,
        "reasoningEffort": thread.reasoning_effort,
        "accessPresetOverride": (
            thread.access_preset_override.value
            if thread.access_preset_override is not None
            else None
        ),
        "workspacePath": thread.workspace_path,
        "worktreePath": thread.worktree_path,
        "createdAt": timestamp(thread.created_at),
        "updatedAt": timestamp(thread.updated_at),
        "archivedAt": timestamp(thread.archived_at),
    }


def turn_view(turn: Turn) -> dict[str, Any]:
    return {
        "id": turn.id,
        "threadId": turn.thread_id,
        "ordinal": turn.ordinal,
        "prompt": turn.prompt,
        "skillIds": list(turn.skill_ids),
        "executionProfile": (
            turn.execution_profile.to_dict()
            if turn.execution_profile is not None
            else None
        ),
        "executionPermissionMode": (
            turn.execution_permission_mode.value
            if turn.execution_permission_mode is not None
            else None
        ),
        "executionSecurityProfile": (
            turn.execution_security_profile.to_dict()
            if turn.execution_security_profile is not None
            else None
        ),
        "goalId": turn.goal_id,
        "status": turn.status.value,
        "stopReason": turn.stop_reason,
        "errorCode": turn.error_code,
        "errorMessage": turn.error_message,
        "startedAt": timestamp(turn.started_at),
        "completedAt": timestamp(turn.completed_at),
    }


def item_view(item: Item) -> dict[str, Any]:
    return {
        "id": item.id,
        "threadId": item.thread_id,
        "turnId": item.turn_id,
        "ordinal": item.ordinal,
        "kind": item.kind.value,
        "status": item.status.value,
        "summary": item.summary,
        "payload": item.payload,
        "createdAt": timestamp(item.created_at),
        "updatedAt": timestamp(item.updated_at),
    }


def approval_view(approval: Approval) -> dict[str, Any]:
    return {
        "id": approval.id,
        "threadId": approval.thread_id,
        "turnId": approval.turn_id,
        "itemId": approval.item_id,
        "category": approval.category.value,
        "status": approval.status.value,
        "request": approval.request,
        "decision": approval.decision,
        "requestedAt": timestamp(approval.requested_at),
        "resolvedAt": timestamp(approval.resolved_at),
    }


def workflow_view(run: WorkflowRun) -> dict[str, Any]:
    return {
        "id": run.id,
        "threadId": run.thread_id,
        "turnId": run.turn_id,
        "kind": run.kind,
        "status": run.status.value,
        "input": run.input,
        "result": run.result,
        "attempt": run.attempt,
        "retryOf": run.retry_of,
        "currentStage": run.current_stage,
        "progressCurrent": run.progress_current,
        "progressTotal": run.progress_total,
        "checkpoint": run.checkpoint,
        "createdAt": timestamp(run.created_at),
        "updatedAt": timestamp(run.updated_at),
        "startedAt": timestamp(run.started_at),
        "completedAt": timestamp(run.completed_at),
        "errorCode": run.error_code,
        "errorMessage": run.error_message,
    }


def artifact_view(artifact: Artifact) -> dict[str, Any]:
    return {
        "id": artifact.id,
        "threadId": artifact.thread_id,
        "turnId": artifact.turn_id,
        "workflowRunId": artifact.workflow_run_id,
        "kind": artifact.kind,
        "name": artifact.name,
        "mediaType": artifact.media_type,
        "storagePath": artifact.storage_path,
        "byteSize": artifact.byte_size,
        "metadata": artifact.metadata,
        "createdAt": timestamp(artifact.created_at),
    }


def event_view(event: DomainEvent) -> dict[str, Any]:
    return {
        "eventId": event.id,
        "sequence": event.sequence,
        "type": event.type,
        "threadId": event.thread_id,
        "turnId": event.turn_id,
        "itemId": event.item_id,
        "timestamp": timestamp(event.timestamp),
        "payload": event.payload,
    }
