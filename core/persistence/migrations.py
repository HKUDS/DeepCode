"""Small, explicit SQLite migration runner.

Migrations are append-only source code. Each step runs in its own transaction;
the runner can also move backwards in tests and controlled recovery tooling.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Migration:
    version: int
    name: str
    up: str
    down: str


_INITIAL_SCHEMA = r"""
CREATE TABLE projects (
    id TEXT PRIMARY KEY,
    canonical_path TEXT NOT NULL UNIQUE,
    display_name TEXT NOT NULL,
    trust_state TEXT NOT NULL CHECK (trust_state IN ('untrusted', 'trusted')),
    settings_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    last_opened_at TEXT NOT NULL
);

CREATE TABLE threads (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    parent_thread_id TEXT REFERENCES threads(id) ON DELETE SET NULL,
    title TEXT NOT NULL,
    mode TEXT NOT NULL CHECK (mode IN ('code', 'paper', 'brief', 'review', 'goal')),
    status TEXT NOT NULL CHECK (status IN ('idle', 'running', 'waiting', 'failed', 'archived')),
    model TEXT,
    workspace_path TEXT NOT NULL,
    worktree_path TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    archived_at TEXT,
    CHECK (
        (status = 'archived' AND archived_at IS NOT NULL) OR
        (status <> 'archived' AND archived_at IS NULL)
    )
);
CREATE INDEX idx_threads_project_updated ON threads(project_id, updated_at DESC);

CREATE TABLE turns (
    id TEXT PRIMARY KEY,
    thread_id TEXT NOT NULL REFERENCES threads(id) ON DELETE CASCADE,
    ordinal INTEGER NOT NULL CHECK (ordinal > 0),
    prompt TEXT NOT NULL,
    status TEXT NOT NULL CHECK (
        status IN ('queued', 'running', 'waiting_approval', 'completed', 'failed', 'interrupted')
    ),
    stop_reason TEXT,
    error_code TEXT,
    error_message TEXT,
    started_at TEXT,
    completed_at TEXT,
    UNIQUE(thread_id, ordinal),
    UNIQUE(id, thread_id),
    CHECK (
        (status IN ('completed', 'failed', 'interrupted') AND completed_at IS NOT NULL) OR
        (status NOT IN ('completed', 'failed', 'interrupted') AND completed_at IS NULL)
    ),
    CHECK (
        (status = 'failed' AND error_code IS NOT NULL) OR
        (status <> 'failed' AND error_code IS NULL AND error_message IS NULL)
    )
);
CREATE INDEX idx_turns_thread ON turns(thread_id, ordinal);

CREATE TABLE items (
    id TEXT PRIMARY KEY,
    thread_id TEXT NOT NULL REFERENCES threads(id) ON DELETE CASCADE,
    turn_id TEXT NOT NULL,
    ordinal INTEGER NOT NULL CHECK (ordinal > 0),
    kind TEXT NOT NULL CHECK (kind IN (
        'user_message', 'assistant_message', 'reasoning_summary', 'plan',
        'tool_call', 'command_execution', 'file_change', 'diff', 'test_result',
        'approval_request', 'workflow_stage', 'artifact', 'error', 'completion'
    )),
    status TEXT NOT NULL CHECK (status IN ('pending', 'in_progress', 'completed', 'failed', 'declined')),
    summary TEXT NOT NULL,
    payload_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(turn_id, ordinal),
    UNIQUE(id, thread_id, turn_id),
    FOREIGN KEY(turn_id, thread_id) REFERENCES turns(id, thread_id) ON DELETE CASCADE
);
CREATE INDEX idx_items_thread ON items(thread_id, created_at, ordinal);

CREATE TABLE approvals (
    id TEXT PRIMARY KEY,
    thread_id TEXT NOT NULL REFERENCES threads(id) ON DELETE CASCADE,
    turn_id TEXT NOT NULL,
    item_id TEXT NOT NULL,
    category TEXT NOT NULL CHECK (category IN ('command', 'file_write', 'network', 'external_tool', 'destructive')),
    status TEXT NOT NULL CHECK (status IN ('pending', 'approved_once', 'approved_session', 'denied', 'cancelled', 'expired')),
    request_json TEXT NOT NULL,
    decision_json TEXT,
    requested_at TEXT NOT NULL,
    resolved_at TEXT,
    FOREIGN KEY(turn_id, thread_id) REFERENCES turns(id, thread_id) ON DELETE CASCADE,
    FOREIGN KEY(item_id, thread_id, turn_id)
        REFERENCES items(id, thread_id, turn_id) ON DELETE CASCADE,
    CHECK (
        (status = 'pending' AND decision_json IS NULL AND resolved_at IS NULL) OR
        (status <> 'pending' AND decision_json IS NOT NULL AND resolved_at IS NOT NULL)
    )
);
CREATE INDEX idx_approvals_pending ON approvals(thread_id, status);

CREATE TABLE workflow_runs (
    id TEXT PRIMARY KEY,
    thread_id TEXT NOT NULL REFERENCES threads(id) ON DELETE CASCADE,
    turn_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('queued', 'running', 'waiting', 'completed', 'failed', 'cancelled')),
    current_stage TEXT,
    progress_current INTEGER NOT NULL DEFAULT 0 CHECK (progress_current >= 0),
    progress_total INTEGER,
    checkpoint_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    completed_at TEXT,
    UNIQUE(id, thread_id),
    FOREIGN KEY(turn_id, thread_id) REFERENCES turns(id, thread_id) ON DELETE CASCADE,
    CHECK (progress_total IS NULL OR (progress_total >= 0 AND progress_current <= progress_total)),
    CHECK (
        (status IN ('completed', 'failed', 'cancelled') AND completed_at IS NOT NULL) OR
        (status NOT IN ('completed', 'failed', 'cancelled') AND completed_at IS NULL)
    )
);
CREATE INDEX idx_workflows_thread ON workflow_runs(thread_id, updated_at DESC);

CREATE TABLE artifacts (
    id TEXT PRIMARY KEY,
    thread_id TEXT NOT NULL REFERENCES threads(id) ON DELETE CASCADE,
    turn_id TEXT REFERENCES turns(id) ON DELETE SET NULL,
    workflow_run_id TEXT REFERENCES workflow_runs(id) ON DELETE SET NULL,
    kind TEXT NOT NULL,
    name TEXT NOT NULL,
    media_type TEXT NOT NULL,
    storage_path TEXT NOT NULL,
    byte_size INTEGER CHECK (byte_size IS NULL OR byte_size >= 0),
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);
CREATE INDEX idx_artifacts_thread ON artifacts(thread_id, created_at DESC);

CREATE TABLE event_log (
    id TEXT PRIMARY KEY,
    thread_id TEXT NOT NULL REFERENCES threads(id) ON DELETE CASCADE,
    sequence INTEGER NOT NULL CHECK (sequence > 0),
    type TEXT NOT NULL,
    turn_id TEXT REFERENCES turns(id) ON DELETE SET NULL,
    item_id TEXT REFERENCES items(id) ON DELETE SET NULL,
    timestamp TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    UNIQUE(thread_id, sequence)
);
CREATE INDEX idx_events_replay ON event_log(thread_id, sequence);

CREATE TRIGGER enforce_artifact_scope_insert
BEFORE INSERT ON artifacts
WHEN
    (NEW.turn_id IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM turns WHERE id = NEW.turn_id AND thread_id = NEW.thread_id
    )) OR
    (NEW.workflow_run_id IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM workflow_runs WHERE id = NEW.workflow_run_id AND thread_id = NEW.thread_id
    ))
BEGIN
    SELECT RAISE(ABORT, 'artifact references a different thread');
END;

CREATE TRIGGER enforce_event_scope_insert
BEFORE INSERT ON event_log
WHEN
    (NEW.turn_id IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM turns WHERE id = NEW.turn_id AND thread_id = NEW.thread_id
    )) OR
    (NEW.item_id IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM items WHERE id = NEW.item_id AND thread_id = NEW.thread_id
    ))
BEGIN
    SELECT RAISE(ABORT, 'event references a different thread');
END;

CREATE TABLE legacy_imports (
    source_key TEXT PRIMARY KEY,
    source_session_id TEXT NOT NULL,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    thread_id TEXT NOT NULL REFERENCES threads(id) ON DELETE CASCADE,
    imported_at TEXT NOT NULL
);
"""

_DROP_INITIAL_SCHEMA = r"""
DROP TABLE IF EXISTS legacy_imports;
DROP TABLE IF EXISTS event_log;
DROP TABLE IF EXISTS artifacts;
DROP TABLE IF EXISTS workflow_runs;
DROP TABLE IF EXISTS approvals;
DROP TABLE IF EXISTS items;
DROP TABLE IF EXISTS turns;
DROP TABLE IF EXISTS threads;
DROP TABLE IF EXISTS projects;
"""

_WORKFLOW_LIFECYCLE_V2 = r"""
ALTER TABLE workflow_runs ADD COLUMN input_json TEXT NOT NULL DEFAULT '{}';
ALTER TABLE workflow_runs ADD COLUMN result_json TEXT NOT NULL DEFAULT '{}';
ALTER TABLE workflow_runs ADD COLUMN attempt INTEGER NOT NULL DEFAULT 1;
ALTER TABLE workflow_runs ADD COLUMN retry_of TEXT REFERENCES workflow_runs(id) ON DELETE SET NULL;
ALTER TABLE workflow_runs ADD COLUMN started_at TEXT;
ALTER TABLE workflow_runs ADD COLUMN error_code TEXT;
ALTER TABLE workflow_runs ADD COLUMN error_message TEXT;
CREATE INDEX idx_workflows_status ON workflow_runs(status, updated_at DESC);
CREATE INDEX idx_workflows_retry ON workflow_runs(retry_of, attempt);
"""

_DROP_WORKFLOW_LIFECYCLE_V2 = r"""
DROP INDEX IF EXISTS idx_workflows_retry;
DROP INDEX IF EXISTS idx_workflows_status;
ALTER TABLE workflow_runs DROP COLUMN error_message;
ALTER TABLE workflow_runs DROP COLUMN error_code;
ALTER TABLE workflow_runs DROP COLUMN started_at;
ALTER TABLE workflow_runs DROP COLUMN retry_of;
ALTER TABLE workflow_runs DROP COLUMN attempt;
ALTER TABLE workflow_runs DROP COLUMN result_json;
ALTER TABLE workflow_runs DROP COLUMN input_json;
"""

_AUTOMATIONS_V3 = r"""
CREATE TABLE automations (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    thread_id TEXT NOT NULL UNIQUE REFERENCES threads(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    prompt TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('enabled', 'paused')),
    schedule_kind TEXT NOT NULL CHECK (schedule_kind IN ('manual', 'interval')),
    interval_seconds INTEGER,
    next_run_at TEXT,
    last_run_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    CHECK (
        (
            schedule_kind = 'manual' AND
            interval_seconds IS NULL AND
            next_run_at IS NULL
        ) OR (
            schedule_kind = 'interval' AND
            interval_seconds >= 60 AND
            (status = 'paused' OR next_run_at IS NOT NULL)
        )
    )
);
CREATE INDEX idx_automations_project ON automations(project_id, updated_at DESC);
CREATE INDEX idx_automations_due
    ON automations(status, schedule_kind, next_run_at);

CREATE TABLE automation_runs (
    id TEXT PRIMARY KEY,
    automation_id TEXT NOT NULL REFERENCES automations(id) ON DELETE CASCADE,
    thread_id TEXT NOT NULL REFERENCES threads(id) ON DELETE CASCADE,
    turn_id TEXT REFERENCES turns(id) ON DELETE SET NULL,
    trigger TEXT NOT NULL CHECK (trigger IN ('manual', 'scheduled')),
    status TEXT NOT NULL CHECK (
        status IN (
            'queued', 'running', 'waiting', 'completed',
            'failed', 'interrupted', 'skipped'
        )
    ),
    scheduled_for TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    started_at TEXT,
    completed_at TEXT,
    CHECK (
        (
            status IN ('completed', 'failed', 'interrupted', 'skipped') AND
            completed_at IS NOT NULL
        ) OR (
            status NOT IN ('completed', 'failed', 'interrupted', 'skipped') AND
            completed_at IS NULL
        )
    )
);
CREATE INDEX idx_automation_runs_job
    ON automation_runs(automation_id, created_at DESC);
CREATE INDEX idx_automation_runs_turn ON automation_runs(turn_id);
CREATE INDEX idx_automation_runs_active
    ON automation_runs(status, updated_at DESC);

CREATE TRIGGER enforce_automation_thread_scope_insert
BEFORE INSERT ON automations
WHEN NOT EXISTS (
    SELECT 1 FROM threads
    WHERE id = NEW.thread_id AND project_id = NEW.project_id
)
BEGIN
    SELECT RAISE(ABORT, 'automation references a different project');
END;

CREATE TRIGGER enforce_automation_run_scope_insert
BEFORE INSERT ON automation_runs
WHEN
    NOT EXISTS (
        SELECT 1 FROM automations
        WHERE id = NEW.automation_id AND thread_id = NEW.thread_id
    ) OR (
        NEW.turn_id IS NOT NULL AND NOT EXISTS (
            SELECT 1 FROM turns
            WHERE id = NEW.turn_id AND thread_id = NEW.thread_id
        )
    )
BEGIN
    SELECT RAISE(ABORT, 'automation run references a different thread');
END;

CREATE TRIGGER enforce_automation_run_scope_update
BEFORE UPDATE OF automation_id, thread_id, turn_id ON automation_runs
WHEN
    NOT EXISTS (
        SELECT 1 FROM automations
        WHERE id = NEW.automation_id AND thread_id = NEW.thread_id
    ) OR (
        NEW.turn_id IS NOT NULL AND NOT EXISTS (
            SELECT 1 FROM turns
            WHERE id = NEW.turn_id AND thread_id = NEW.thread_id
        )
    )
BEGIN
    SELECT RAISE(ABORT, 'automation run references a different thread');
END;
"""

_DROP_AUTOMATIONS_V3 = r"""
DROP TRIGGER IF EXISTS enforce_automation_run_scope_update;
DROP TRIGGER IF EXISTS enforce_automation_run_scope_insert;
DROP TRIGGER IF EXISTS enforce_automation_thread_scope_insert;
DROP TABLE IF EXISTS automation_runs;
DROP TABLE IF EXISTS automations;
"""

_TURN_SKILLS_V4 = r"""
ALTER TABLE turns
    ADD COLUMN skill_ids_json TEXT NOT NULL DEFAULT '[]';
"""

_DROP_TURN_SKILLS_V4 = r"""
ALTER TABLE turns DROP COLUMN skill_ids_json;
"""

_LLM_EXECUTION_PROFILES_V5 = r"""
ALTER TABLE threads ADD COLUMN connection_id TEXT;
ALTER TABLE turns ADD COLUMN execution_profile_json TEXT;
"""

_DROP_LLM_EXECUTION_PROFILES_V5 = r"""
ALTER TABLE turns DROP COLUMN execution_profile_json;
ALTER TABLE threads DROP COLUMN connection_id;
"""

_GOAL_TURN_LINKS_V6 = r"""
ALTER TABLE turns ADD COLUMN goal_id TEXT;
ALTER TABLE turns ADD COLUMN goal_definition_revision INTEGER;
ALTER TABLE turns ADD COLUMN goal_attempt_id TEXT;
CREATE UNIQUE INDEX idx_turns_goal_attempt
    ON turns(goal_attempt_id)
    WHERE goal_attempt_id IS NOT NULL;
"""

_DROP_GOAL_TURN_LINKS_V6 = r"""
DROP INDEX IF EXISTS idx_turns_goal_attempt;
ALTER TABLE turns DROP COLUMN goal_attempt_id;
ALTER TABLE turns DROP COLUMN goal_definition_revision;
ALTER TABLE turns DROP COLUMN goal_id;
"""

_THREAD_REASONING_EFFORT_V7 = r"""
ALTER TABLE threads ADD COLUMN reasoning_effort TEXT;
"""

_DROP_THREAD_REASONING_EFFORT_V7 = r"""
ALTER TABLE threads DROP COLUMN reasoning_effort;
"""

_AUTOMATION_EXECUTION_FACTS_V8 = r"""
DROP TRIGGER IF EXISTS enforce_automation_run_scope_update;
DROP TRIGGER IF EXISTS enforce_automation_run_scope_insert;
DROP TRIGGER IF EXISTS enforce_automation_thread_scope_insert;
DROP INDEX IF EXISTS idx_automation_runs_active;
DROP INDEX IF EXISTS idx_automation_runs_turn;
DROP INDEX IF EXISTS idx_automation_runs_job;
DROP INDEX IF EXISTS idx_automations_due;
DROP INDEX IF EXISTS idx_automations_project;

ALTER TABLE automation_runs RENAME TO automation_runs_v7;
ALTER TABLE automations RENAME TO automations_v7;

CREATE TABLE automations (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    thread_id TEXT NOT NULL UNIQUE REFERENCES threads(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    current_revision_id TEXT NOT NULL,
    status TEXT NOT NULL CHECK (
        status IN ('enabled', 'paused', 'retired')
    ),
    schedule_kind TEXT NOT NULL CHECK (
        schedule_kind IN ('manual', 'interval')
    ),
    interval_seconds INTEGER,
    next_run_at TEXT,
    last_run_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(current_revision_id, id)
        REFERENCES automation_revisions(id, automation_id)
        ON DELETE NO ACTION DEFERRABLE INITIALLY DEFERRED,
    CHECK (
        (
            schedule_kind = 'manual' AND
            interval_seconds IS NULL AND
            next_run_at IS NULL
        ) OR (
            schedule_kind = 'interval' AND
            interval_seconds >= 60 AND
            (status <> 'enabled' OR next_run_at IS NOT NULL)
        )
    ),
    CHECK (status <> 'retired' OR next_run_at IS NULL)
);

CREATE TABLE automation_revisions (
    id TEXT PRIMARY KEY,
    automation_id TEXT NOT NULL,
    ordinal INTEGER NOT NULL CHECK (ordinal > 0),
    instruction TEXT NOT NULL CHECK (length(trim(instruction)) > 0),
    created_at TEXT NOT NULL,
    UNIQUE(automation_id, ordinal),
    UNIQUE(id, automation_id),
    FOREIGN KEY(automation_id) REFERENCES automations(id)
        ON DELETE CASCADE DEFERRABLE INITIALLY DEFERRED
);

CREATE TABLE automation_occurrences (
    id TEXT PRIMARY KEY,
    automation_id TEXT NOT NULL REFERENCES automations(id) ON DELETE CASCADE,
    kind TEXT NOT NULL CHECK (kind IN ('manual', 'scheduled')),
    occurrence_key TEXT NOT NULL CHECK (length(trim(occurrence_key)) > 0),
    nominal_at TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    UNIQUE(automation_id, kind, occurrence_key),
    UNIQUE(id, automation_id)
);

CREATE TABLE automation_runs (
    id TEXT PRIMARY KEY,
    automation_id TEXT NOT NULL REFERENCES automations(id) ON DELETE CASCADE,
    revision_id TEXT NOT NULL,
    occurrence_id TEXT NOT NULL UNIQUE,
    goal_id TEXT,
    thread_id TEXT NOT NULL REFERENCES threads(id) ON DELETE NO ACTION,
    turn_id TEXT REFERENCES turns(id) ON DELETE SET NULL,
    trigger TEXT NOT NULL CHECK (trigger IN ('manual', 'scheduled')),
    status TEXT NOT NULL CHECK (
        status IN (
            'queued', 'running', 'waiting', 'blocked', 'completed',
            'failed', 'interrupted', 'skipped'
        )
    ),
    scheduled_for TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    started_at TEXT,
    completed_at TEXT,
    FOREIGN KEY(revision_id, automation_id)
        REFERENCES automation_revisions(id, automation_id)
        ON DELETE NO ACTION,
    FOREIGN KEY(occurrence_id, automation_id)
        REFERENCES automation_occurrences(id, automation_id)
        ON DELETE NO ACTION,
    CHECK (
        (
            status IN ('completed', 'failed', 'interrupted', 'skipped') AND
            completed_at IS NOT NULL
        ) OR (
            status NOT IN ('completed', 'failed', 'interrupted', 'skipped') AND
            completed_at IS NULL
        )
    )
);

INSERT INTO automations (
    id, project_id, thread_id, name, current_revision_id, status,
    schedule_kind, interval_seconds, next_run_at, last_run_at,
    created_at, updated_at
)
SELECT
    id, project_id, thread_id, name, 'arev_legacy_' || id, status,
    schedule_kind, interval_seconds, next_run_at, last_run_at,
    created_at, updated_at
FROM automations_v7;

INSERT INTO automation_revisions (
    id, automation_id, ordinal, instruction, created_at
)
SELECT
    'arev_legacy_' || id, id, 1, prompt, created_at
FROM automations_v7;

INSERT INTO automation_occurrences (
    id, automation_id, kind, occurrence_key, nominal_at, observed_at
)
SELECT
    'aocc_legacy_' || id,
    automation_id,
    trigger,
    'legacy:' || id,
    scheduled_for,
    created_at
FROM automation_runs_v7;

INSERT INTO automation_runs (
    id, automation_id, revision_id, occurrence_id, goal_id, thread_id,
    turn_id, trigger, status, scheduled_for, detail, created_at,
    updated_at, started_at, completed_at
)
SELECT
    id,
    automation_id,
    'arev_legacy_' || automation_id,
    'aocc_legacy_' || id,
    (
        SELECT goal_id
        FROM turns
        WHERE turns.id = automation_runs_v7.turn_id
    ),
    thread_id,
    turn_id,
    trigger,
    status,
    scheduled_for,
    detail,
    created_at,
    updated_at,
    started_at,
    completed_at
FROM automation_runs_v7;

UPDATE automation_runs
SET
    status = 'interrupted',
    detail = CASE
        WHEN detail = '' THEN
            'Interrupted during v8 migration: superseded duplicate open run'
        ELSE
            detail || '; interrupted during v8 migration: superseded duplicate open run'
    END,
    updated_at = CASE
        WHEN updated_at < created_at THEN created_at
        ELSE updated_at
    END,
    completed_at = COALESCE(completed_at, updated_at, created_at)
WHERE id IN (
    SELECT id
    FROM (
        SELECT
            id,
            ROW_NUMBER() OVER (
                PARTITION BY automation_id
                ORDER BY updated_at DESC, created_at DESC, id DESC
            ) AS open_rank
        FROM automation_runs
        WHERE status IN ('queued', 'running', 'waiting', 'blocked')
    )
    WHERE open_rank > 1
);

UPDATE automation_runs
SET
    goal_id = NULL,
    turn_id = NULL,
    status = 'interrupted',
    detail = CASE
        WHEN detail = '' THEN
            'Interrupted during v8 migration: duplicate Goal ownership'
        ELSE
            detail || '; interrupted during v8 migration: duplicate Goal ownership'
    END,
    updated_at = CASE
        WHEN updated_at < created_at THEN created_at
        ELSE updated_at
    END,
    completed_at = COALESCE(completed_at, updated_at, created_at)
WHERE id IN (
    SELECT id
    FROM (
        SELECT
            id,
            ROW_NUMBER() OVER (
                PARTITION BY goal_id
                ORDER BY
                    CASE WHEN turn_id IS NOT NULL THEN 0 ELSE 1 END,
                    CASE
                        WHEN status IN ('queued', 'running', 'waiting', 'blocked')
                        THEN 0
                        ELSE 1
                    END,
                    updated_at DESC,
                    created_at DESC,
                    id DESC
            ) AS goal_rank
        FROM automation_runs
        WHERE goal_id IS NOT NULL
    )
    WHERE goal_rank > 1
);

DROP TABLE automation_runs_v7;
DROP TABLE automations_v7;

CREATE INDEX idx_automations_project
    ON automations(project_id, updated_at DESC);
CREATE INDEX idx_automations_due
    ON automations(status, schedule_kind, next_run_at);
CREATE INDEX idx_automation_revisions_definition
    ON automation_revisions(automation_id, ordinal DESC);
CREATE INDEX idx_automation_occurrences_nominal
    ON automation_occurrences(automation_id, nominal_at DESC);
CREATE INDEX idx_automation_runs_job
    ON automation_runs(automation_id, created_at DESC);
CREATE INDEX idx_automation_runs_turn ON automation_runs(turn_id);
CREATE UNIQUE INDEX idx_automation_runs_goal
    ON automation_runs(goal_id)
    WHERE goal_id IS NOT NULL;
CREATE INDEX idx_automation_runs_active
    ON automation_runs(status, updated_at DESC);
CREATE UNIQUE INDEX idx_automation_runs_one_open
    ON automation_runs(automation_id)
    WHERE status IN ('queued', 'running', 'waiting', 'blocked');

CREATE TRIGGER enforce_automation_thread_scope_insert
BEFORE INSERT ON automations
WHEN NOT EXISTS (
    SELECT 1 FROM threads
    WHERE id = NEW.thread_id AND project_id = NEW.project_id
)
BEGIN
    SELECT RAISE(ABORT, 'automation references a different project');
END;

CREATE TRIGGER enforce_automation_thread_scope_update
BEFORE UPDATE OF project_id, thread_id ON automations
WHEN NOT EXISTS (
    SELECT 1 FROM threads
    WHERE id = NEW.thread_id AND project_id = NEW.project_id
)
BEGIN
    SELECT RAISE(ABORT, 'automation references a different project');
END;

CREATE TRIGGER prevent_automation_revision_update
BEFORE UPDATE ON automation_revisions
BEGIN
    SELECT RAISE(ABORT, 'automation revisions are immutable');
END;

CREATE TRIGGER prevent_automation_occurrence_update
BEFORE UPDATE ON automation_occurrences
BEGIN
    SELECT RAISE(ABORT, 'automation occurrences are immutable');
END;

CREATE TRIGGER enforce_automation_run_scope_insert
BEFORE INSERT ON automation_runs
WHEN
    NOT EXISTS (
        SELECT 1 FROM automations
        WHERE id = NEW.automation_id AND thread_id = NEW.thread_id
    ) OR
    NOT EXISTS (
        SELECT 1 FROM automation_occurrences
        WHERE
            id = NEW.occurrence_id AND
            automation_id = NEW.automation_id AND
            kind = NEW.trigger AND
            nominal_at = NEW.scheduled_for
    ) OR (
        NEW.turn_id IS NOT NULL AND NOT EXISTS (
            SELECT 1 FROM turns
            WHERE
                id = NEW.turn_id AND
                thread_id = NEW.thread_id AND
                (NEW.goal_id IS NULL OR goal_id = NEW.goal_id)
        )
    )
BEGIN
    SELECT RAISE(ABORT, 'automation run references inconsistent execution scope');
END;

CREATE TRIGGER enforce_automation_run_scope_update
BEFORE UPDATE OF
    automation_id, revision_id, occurrence_id, goal_id,
    thread_id, turn_id, trigger, scheduled_for
ON automation_runs
WHEN
    NOT EXISTS (
        SELECT 1 FROM automations
        WHERE id = NEW.automation_id AND thread_id = NEW.thread_id
    ) OR
    NOT EXISTS (
        SELECT 1 FROM automation_occurrences
        WHERE
            id = NEW.occurrence_id AND
            automation_id = NEW.automation_id AND
            kind = NEW.trigger AND
            nominal_at = NEW.scheduled_for
    ) OR (
        NEW.turn_id IS NOT NULL AND NOT EXISTS (
            SELECT 1 FROM turns
            WHERE
                id = NEW.turn_id AND
                thread_id = NEW.thread_id AND
                (NEW.goal_id IS NULL OR goal_id = NEW.goal_id)
        )
    )
BEGIN
    SELECT RAISE(ABORT, 'automation run references inconsistent execution scope');
END;
"""

_DROP_AUTOMATION_EXECUTION_FACTS_V8 = r"""
DROP TRIGGER IF EXISTS enforce_automation_run_scope_update;
DROP TRIGGER IF EXISTS enforce_automation_run_scope_insert;
DROP TRIGGER IF EXISTS prevent_automation_occurrence_update;
DROP TRIGGER IF EXISTS prevent_automation_revision_update;
DROP TRIGGER IF EXISTS enforce_automation_thread_scope_update;
DROP TRIGGER IF EXISTS enforce_automation_thread_scope_insert;
DROP INDEX IF EXISTS idx_automation_runs_one_open;
DROP INDEX IF EXISTS idx_automation_runs_active;
DROP INDEX IF EXISTS idx_automation_runs_goal;
DROP INDEX IF EXISTS idx_automation_runs_turn;
DROP INDEX IF EXISTS idx_automation_runs_job;
DROP INDEX IF EXISTS idx_automation_occurrences_nominal;
DROP INDEX IF EXISTS idx_automation_revisions_definition;
DROP INDEX IF EXISTS idx_automations_due;
DROP INDEX IF EXISTS idx_automations_project;

ALTER TABLE automation_runs RENAME TO automation_runs_v8;
ALTER TABLE automation_occurrences RENAME TO automation_occurrences_v8;
ALTER TABLE automation_revisions RENAME TO automation_revisions_v8;
ALTER TABLE automations RENAME TO automations_v8;

CREATE TABLE automations (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    thread_id TEXT NOT NULL UNIQUE REFERENCES threads(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    prompt TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('enabled', 'paused')),
    schedule_kind TEXT NOT NULL CHECK (schedule_kind IN ('manual', 'interval')),
    interval_seconds INTEGER,
    next_run_at TEXT,
    last_run_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    CHECK (
        (
            schedule_kind = 'manual' AND
            interval_seconds IS NULL AND
            next_run_at IS NULL
        ) OR (
            schedule_kind = 'interval' AND
            interval_seconds >= 60 AND
            (status = 'paused' OR next_run_at IS NOT NULL)
        )
    )
);

CREATE TABLE automation_runs (
    id TEXT PRIMARY KEY,
    automation_id TEXT NOT NULL REFERENCES automations(id) ON DELETE CASCADE,
    thread_id TEXT NOT NULL REFERENCES threads(id) ON DELETE CASCADE,
    turn_id TEXT REFERENCES turns(id) ON DELETE SET NULL,
    trigger TEXT NOT NULL CHECK (trigger IN ('manual', 'scheduled')),
    status TEXT NOT NULL CHECK (
        status IN (
            'queued', 'running', 'waiting', 'completed',
            'failed', 'interrupted', 'skipped'
        )
    ),
    scheduled_for TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    started_at TEXT,
    completed_at TEXT,
    CHECK (
        (
            status IN ('completed', 'failed', 'interrupted', 'skipped') AND
            completed_at IS NOT NULL
        ) OR (
            status NOT IN ('completed', 'failed', 'interrupted', 'skipped') AND
            completed_at IS NULL
        )
    )
);

INSERT INTO automations (
    id, project_id, thread_id, name, prompt, status, schedule_kind,
    interval_seconds, next_run_at, last_run_at, created_at, updated_at
)
SELECT
    automation.id,
    automation.project_id,
    automation.thread_id,
    automation.name,
    revision.instruction,
    CASE
        WHEN automation.status = 'retired' THEN 'paused'
        ELSE automation.status
    END,
    automation.schedule_kind,
    automation.interval_seconds,
    automation.next_run_at,
    automation.last_run_at,
    automation.created_at,
    automation.updated_at
FROM automations_v8 AS automation
JOIN automation_revisions_v8 AS revision
    ON revision.id = automation.current_revision_id
    AND revision.automation_id = automation.id;

INSERT INTO automation_runs (
    id, automation_id, thread_id, turn_id, trigger, status,
    scheduled_for, detail, created_at, updated_at, started_at, completed_at
)
SELECT
    id,
    automation_id,
    thread_id,
    turn_id,
    trigger,
    CASE
        WHEN status = 'blocked' THEN 'waiting'
        ELSE status
    END,
    scheduled_for,
    detail,
    created_at,
    updated_at,
    started_at,
    completed_at
FROM automation_runs_v8;

DROP TABLE automation_runs_v8;
DROP TABLE automation_occurrences_v8;
DELETE FROM automation_revisions_v8;
DELETE FROM automations_v8;
DROP TABLE automation_revisions_v8;
DROP TABLE automations_v8;

CREATE INDEX idx_automations_project ON automations(project_id, updated_at DESC);
CREATE INDEX idx_automations_due
    ON automations(status, schedule_kind, next_run_at);
CREATE INDEX idx_automation_runs_job
    ON automation_runs(automation_id, created_at DESC);
CREATE INDEX idx_automation_runs_turn ON automation_runs(turn_id);
CREATE INDEX idx_automation_runs_active
    ON automation_runs(status, updated_at DESC);

CREATE TRIGGER enforce_automation_thread_scope_insert
BEFORE INSERT ON automations
WHEN NOT EXISTS (
    SELECT 1 FROM threads
    WHERE id = NEW.thread_id AND project_id = NEW.project_id
)
BEGIN
    SELECT RAISE(ABORT, 'automation references a different project');
END;

CREATE TRIGGER enforce_automation_run_scope_insert
BEFORE INSERT ON automation_runs
WHEN
    NOT EXISTS (
        SELECT 1 FROM automations
        WHERE id = NEW.automation_id AND thread_id = NEW.thread_id
    ) OR (
        NEW.turn_id IS NOT NULL AND NOT EXISTS (
            SELECT 1 FROM turns
            WHERE id = NEW.turn_id AND thread_id = NEW.thread_id
        )
    )
BEGIN
    SELECT RAISE(ABORT, 'automation run references a different thread');
END;

CREATE TRIGGER enforce_automation_run_scope_update
BEFORE UPDATE OF automation_id, thread_id, turn_id ON automation_runs
WHEN
    NOT EXISTS (
        SELECT 1 FROM automations
        WHERE id = NEW.automation_id AND thread_id = NEW.thread_id
    ) OR (
        NEW.turn_id IS NOT NULL AND NOT EXISTS (
            SELECT 1 FROM turns
            WHERE id = NEW.turn_id AND thread_id = NEW.thread_id
        )
    )
BEGIN
    SELECT RAISE(ABORT, 'automation run references a different thread');
END;
"""

_AUTOMATION_RUN_INVARIANTS_V9 = r"""
ALTER TABLE automation_runs
    ADD COLUMN version INTEGER NOT NULL DEFAULT 1 CHECK (version > 0);

CREATE TRIGGER prevent_automation_run_identity_update
BEFORE UPDATE ON automation_runs
WHEN
    OLD.automation_id IS NOT NEW.automation_id OR
    OLD.revision_id IS NOT NEW.revision_id OR
    OLD.occurrence_id IS NOT NEW.occurrence_id OR
    OLD.goal_id IS NOT NEW.goal_id OR
    OLD.thread_id IS NOT NEW.thread_id OR
    OLD.trigger IS NOT NEW.trigger OR
    OLD.scheduled_for IS NOT NEW.scheduled_for OR
    OLD.created_at IS NOT NEW.created_at
BEGIN
    SELECT RAISE(ABORT, 'automation run identity is immutable');
END;

CREATE TRIGGER prevent_automation_run_turn_reassignment
BEFORE UPDATE OF turn_id ON automation_runs
WHEN
    OLD.turn_id IS NOT NEW.turn_id AND (
        OLD.turn_id IS NOT NULL OR
        NEW.turn_id IS NULL OR
        OLD.status IN ('completed', 'failed', 'interrupted', 'skipped')
    )
BEGIN
    SELECT RAISE(ABORT, 'automation run Turn ownership is immutable');
END;

CREATE TRIGGER prevent_automation_run_terminal_update
BEFORE UPDATE ON automation_runs
WHEN OLD.status IN ('completed', 'failed', 'interrupted', 'skipped')
BEGIN
    SELECT RAISE(ABORT, 'terminal automation runs are immutable');
END;

CREATE TRIGGER enforce_automation_run_version_update
BEFORE UPDATE ON automation_runs
WHEN NEW.version <> OLD.version + 1
BEGIN
    SELECT RAISE(ABORT, 'automation run version must advance exactly once');
END;
"""

_DROP_AUTOMATION_RUN_INVARIANTS_V9 = r"""
DROP TRIGGER IF EXISTS enforce_automation_run_version_update;
DROP TRIGGER IF EXISTS prevent_automation_run_terminal_update;
DROP TRIGGER IF EXISTS prevent_automation_run_turn_reassignment;
DROP TRIGGER IF EXISTS prevent_automation_run_identity_update;
ALTER TABLE automation_runs DROP COLUMN version;
"""

_RUNTIME_COORDINATION_V10 = r"""
CREATE TABLE runtime_workers (
    id TEXT NOT NULL PRIMARY KEY,
    pid INTEGER NOT NULL CHECK (pid > 0),
    surface TEXT NOT NULL CHECK (length(trim(surface)) > 0),
    started_at TEXT NOT NULL,
    heartbeat_at TEXT NOT NULL,
    stopped_at TEXT,
    CHECK (heartbeat_at >= started_at),
    CHECK (stopped_at IS NULL OR stopped_at >= heartbeat_at)
);
CREATE INDEX idx_runtime_workers_liveness
    ON runtime_workers(stopped_at, heartbeat_at);

ALTER TABLE turns ADD COLUMN enqueued_at TEXT NOT NULL
    DEFAULT '1970-01-01T00:00:00Z';
ALTER TABLE turns ADD COLUMN execution_class TEXT NOT NULL DEFAULT 'interactive'
    CHECK (execution_class IN (
        'interactive',
        'manual_automation',
        'goal_continuation',
        'event_automation',
        'scheduled_automation',
        'backfill'
    ));
ALTER TABLE turns ADD COLUMN home_worker_id TEXT
    REFERENCES runtime_workers(id) ON DELETE RESTRICT;
ALTER TABLE turns ADD COLUMN execution_owner_id TEXT
    REFERENCES runtime_workers(id) ON DELETE RESTRICT;
ALTER TABLE turns ADD COLUMN execution_epoch INTEGER NOT NULL DEFAULT 0
    CHECK (execution_epoch >= 0);
ALTER TABLE turns ADD COLUMN cancel_requested_at TEXT;

UPDATE turns
SET enqueued_at = COALESCE(
    (
        SELECT MIN(event_log.timestamp)
        FROM event_log
        WHERE event_log.turn_id = turns.id
    ),
    started_at,
    completed_at,
    '1970-01-01T00:00:00Z'
);

CREATE TRIGGER initialize_turn_enqueued_at
AFTER INSERT ON turns
WHEN NEW.enqueued_at = '1970-01-01T00:00:00Z'
BEGIN
    UPDATE turns
    SET enqueued_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
    WHERE id = NEW.id;
END;

CREATE TRIGGER enforce_turn_execution_owner_insert
BEFORE INSERT ON turns
WHEN
    (NEW.execution_owner_id IS NOT NULL AND NEW.execution_epoch < 1) OR
    (
        NEW.execution_owner_id IS NOT NULL AND
        NEW.status IN ('completed', 'failed', 'interrupted')
    )
BEGIN
    SELECT RAISE(ABORT, 'invalid Turn execution ownership');
END;

CREATE TRIGGER enforce_turn_execution_owner_update
BEFORE UPDATE OF status, execution_owner_id, execution_epoch ON turns
WHEN
    (NEW.execution_owner_id IS NOT NULL AND NEW.execution_epoch < 1) OR
    (
        NEW.execution_owner_id IS NOT NULL AND
        NEW.status IN ('completed', 'failed', 'interrupted')
    )
BEGIN
    SELECT RAISE(ABORT, 'invalid Turn execution ownership');
END;

CREATE INDEX idx_turns_coordination_queue
    ON turns(
        status,
        home_worker_id,
        execution_owner_id,
        enqueued_at,
        thread_id,
        ordinal,
        id
    );
CREATE INDEX idx_turns_execution_owner
    ON turns(execution_owner_id, status)
    WHERE execution_owner_id IS NOT NULL;
CREATE UNIQUE INDEX idx_turns_execution_fence
    ON turns(id, execution_epoch);

CREATE TABLE resource_leases (
    resource_key TEXT NOT NULL PRIMARY KEY
        CHECK (
            length(trim(resource_key)) > 0 AND
            length(resource_key) <= 512
        ),
    epoch INTEGER NOT NULL CHECK (epoch > 0),
    holder_worker_id TEXT
        REFERENCES runtime_workers(id) ON DELETE RESTRICT,
    holder_turn_id TEXT,
    holder_turn_epoch INTEGER,
    acquired_at TEXT NOT NULL,
    heartbeat_at TEXT NOT NULL,
    released_at TEXT,
    release_reason TEXT,
    FOREIGN KEY(holder_turn_id, holder_turn_epoch)
        REFERENCES turns(id, execution_epoch) ON DELETE RESTRICT,
    CHECK (heartbeat_at >= acquired_at),
    CHECK (
        (holder_turn_id IS NULL AND holder_turn_epoch IS NULL) OR
        (
            holder_worker_id IS NOT NULL AND
            holder_turn_id IS NOT NULL AND
            holder_turn_epoch > 0
        )
    ),
    CHECK (
        (
            holder_worker_id IS NOT NULL AND
            released_at IS NULL AND
            release_reason IS NULL
        ) OR (
            holder_worker_id IS NULL AND
            holder_turn_id IS NULL AND
            holder_turn_epoch IS NULL AND
            released_at IS NOT NULL AND
            release_reason IS NOT NULL AND
            length(trim(release_reason)) > 0 AND
            released_at >= heartbeat_at
        )
    )
);
CREATE INDEX idx_resource_leases_holder
    ON resource_leases(holder_worker_id, holder_turn_id)
    WHERE holder_worker_id IS NOT NULL;
"""

_DROP_RUNTIME_COORDINATION_V10 = r"""
DROP INDEX IF EXISTS idx_resource_leases_holder;
DROP TABLE IF EXISTS resource_leases;

DROP INDEX IF EXISTS idx_turns_execution_fence;
DROP INDEX IF EXISTS idx_turns_execution_owner;
DROP INDEX IF EXISTS idx_turns_coordination_queue;
DROP TRIGGER IF EXISTS enforce_turn_execution_owner_update;
DROP TRIGGER IF EXISTS enforce_turn_execution_owner_insert;
DROP TRIGGER IF EXISTS initialize_turn_enqueued_at;

ALTER TABLE turns DROP COLUMN cancel_requested_at;
ALTER TABLE turns DROP COLUMN execution_epoch;
ALTER TABLE turns DROP COLUMN execution_owner_id;
ALTER TABLE turns DROP COLUMN home_worker_id;
ALTER TABLE turns DROP COLUMN execution_class;
ALTER TABLE turns DROP COLUMN enqueued_at;

DROP INDEX IF EXISTS idx_runtime_workers_liveness;
DROP TABLE IF EXISTS runtime_workers;
"""

_TURN_EXECUTORS_V11 = r"""
ALTER TABLE turns
    ADD COLUMN executor TEXT NOT NULL DEFAULT 'agent'
    CHECK (executor IN ('agent', 'workflow'));

UPDATE turns
SET executor = 'workflow'
WHERE EXISTS (
    SELECT 1
    FROM workflow_runs
    WHERE workflow_runs.turn_id = turns.id
);

CREATE INDEX idx_turns_executor_queue
    ON turns(
        executor,
        status,
        home_worker_id,
        execution_owner_id,
        enqueued_at,
        thread_id,
        ordinal,
        id
    );
"""

_DROP_TURN_EXECUTORS_V11 = r"""
DROP INDEX IF EXISTS idx_turns_executor_queue;
ALTER TABLE turns DROP COLUMN executor;
"""

_APPROVAL_GRANTS_V12 = r"""
CREATE UNIQUE INDEX idx_approvals_id_thread
    ON approvals(id, thread_id);

CREATE TABLE approval_grants (
    thread_id TEXT NOT NULL
        REFERENCES threads(id) ON DELETE CASCADE,
    tool_name TEXT NOT NULL
        CHECK (length(trim(tool_name)) > 0),
    source_approval_id TEXT NOT NULL UNIQUE,
    granted_at TEXT NOT NULL,
    PRIMARY KEY(thread_id, tool_name),
    FOREIGN KEY(source_approval_id, thread_id)
        REFERENCES approvals(id, thread_id) ON DELETE CASCADE
);
"""

_DROP_APPROVAL_GRANTS_V12 = r"""
DROP TABLE IF EXISTS approval_grants;
DROP INDEX IF EXISTS idx_approvals_id_thread;
"""

_TURN_EXECUTION_PERMISSION_V13 = r"""
ALTER TABLE turns
    ADD COLUMN execution_permission_mode TEXT
    CHECK (
        execution_permission_mode IS NULL OR
        execution_permission_mode IN ('default', 'plan', 'full_auto')
    );

UPDATE turns
SET execution_permission_mode = 'default'
WHERE goal_id IN (
    SELECT goal_id
    FROM automation_runs
    WHERE goal_id IS NOT NULL
);
"""

_DROP_TURN_EXECUTION_PERMISSION_V13 = r"""
ALTER TABLE turns DROP COLUMN execution_permission_mode;
"""

_SESSION_EXECUTION_SECURITY_V14 = r"""
ALTER TABLE threads
    ADD COLUMN access_preset_override TEXT
    CHECK (
        access_preset_override IS NULL OR
        access_preset_override IN ('ask', 'read_only', 'full_access')
    );

ALTER TABLE turns
    ADD COLUMN execution_security_profile_json TEXT
    CHECK (
        execution_security_profile_json IS NULL OR
        length(trim(execution_security_profile_json)) > 0
    );

-- A worker may resume non-terminal Turns immediately after the schema upgrade.
-- Freeze a least-privilege profile instead of letting the resumed worker
-- reinterpret mutable config.  v13 did not persist sandbox or custom rules,
-- so its permission mode is not enough to reconstruct the original grant.
-- A final wildcard deny preserves the v13 mode for reversible data decoding
-- while preventing any tool from running under guessed rules. Users can retry
-- under an explicit current Session preset when appropriate.
UPDATE turns
SET execution_security_profile_json = CASE execution_permission_mode
    WHEN 'plan' THEN
        '{"accessPreset":null,"permissionMode":"plan","commandSandbox":true,"filesystemScope":"workspace","approvalPolicy":"on_request","permissionRules":[{"permission":"*","pattern":"*","action":"deny"}]}'
    WHEN 'full_auto' THEN
        '{"accessPreset":null,"permissionMode":"full_auto","commandSandbox":true,"filesystemScope":"workspace","approvalPolicy":"on_request","permissionRules":[{"permission":"*","pattern":"*","action":"deny"}]}'
    WHEN 'default' THEN
        '{"accessPreset":null,"permissionMode":"default","commandSandbox":true,"filesystemScope":"workspace","approvalPolicy":"on_request","permissionRules":[{"permission":"*","pattern":"*","action":"deny"}]}'
    ELSE
        '{"accessPreset":"read_only","permissionMode":"plan","commandSandbox":true,"filesystemScope":"workspace","approvalPolicy":"never","permissionRules":[]}'
END
WHERE status IN ('queued', 'running', 'waiting_approval')
  AND execution_security_profile_json IS NULL;

-- v10 introduced execution_class with an interactive default but could not
-- identify Automation owner Turns that already existed. Backfill the
-- authoritative automation_runs.turn_id now so Goal continuations inherit
-- the owner policy instead of a mutable Session selection.
UPDATE turns
SET execution_class = CASE
    WHEN (
        SELECT automation_runs.trigger
        FROM automation_runs
        WHERE automation_runs.turn_id = turns.id
        LIMIT 1
    ) = 'scheduled'
    THEN 'scheduled_automation'
    ELSE 'manual_automation'
END
WHERE execution_class = 'interactive'
  AND EXISTS (
      SELECT 1
      FROM automation_runs
      WHERE automation_runs.turn_id = turns.id
  );
"""

_DROP_SESSION_EXECUTION_SECURITY_V14 = r"""
ALTER TABLE turns DROP COLUMN execution_security_profile_json;
ALTER TABLE threads DROP COLUMN access_preset_override;
"""

MIGRATIONS = (
    Migration(1, "initial_domain", _INITIAL_SCHEMA, _DROP_INITIAL_SCHEMA),
    Migration(
        2,
        "workflow_lifecycle",
        _WORKFLOW_LIFECYCLE_V2,
        _DROP_WORKFLOW_LIFECYCLE_V2,
    ),
    Migration(
        3,
        "desktop_automations",
        _AUTOMATIONS_V3,
        _DROP_AUTOMATIONS_V3,
    ),
    Migration(
        4,
        "turn_skill_selections",
        _TURN_SKILLS_V4,
        _DROP_TURN_SKILLS_V4,
    ),
    Migration(
        5,
        "llm_execution_profiles",
        _LLM_EXECUTION_PROFILES_V5,
        _DROP_LLM_EXECUTION_PROFILES_V5,
    ),
    Migration(
        6,
        "goal_turn_links",
        _GOAL_TURN_LINKS_V6,
        _DROP_GOAL_TURN_LINKS_V6,
    ),
    Migration(
        7,
        "thread_reasoning_effort",
        _THREAD_REASONING_EFFORT_V7,
        _DROP_THREAD_REASONING_EFFORT_V7,
    ),
    Migration(
        8,
        "automation_execution_facts",
        _AUTOMATION_EXECUTION_FACTS_V8,
        _DROP_AUTOMATION_EXECUTION_FACTS_V8,
    ),
    Migration(
        9,
        "automation_run_invariants",
        _AUTOMATION_RUN_INVARIANTS_V9,
        _DROP_AUTOMATION_RUN_INVARIANTS_V9,
    ),
    Migration(
        10,
        "runtime_coordination",
        _RUNTIME_COORDINATION_V10,
        _DROP_RUNTIME_COORDINATION_V10,
    ),
    Migration(
        11,
        "turn_executors",
        _TURN_EXECUTORS_V11,
        _DROP_TURN_EXECUTORS_V11,
    ),
    Migration(
        12,
        "approval_grants",
        _APPROVAL_GRANTS_V12,
        _DROP_APPROVAL_GRANTS_V12,
    ),
    Migration(
        13,
        "turn_execution_permission",
        _TURN_EXECUTION_PERMISSION_V13,
        _DROP_TURN_EXECUTION_PERMISSION_V13,
    ),
    Migration(
        14,
        "session_execution_security",
        _SESSION_EXECUTION_SECURITY_V14,
        _DROP_SESSION_EXECUTION_SECURITY_V14,
    ),
)
LATEST_SCHEMA_VERSION = MIGRATIONS[-1].version


class MigrationError(RuntimeError):
    pass


def migrate(
    connection: sqlite3.Connection, target: int = LATEST_SCHEMA_VERSION
) -> None:
    """Move ``connection`` to ``target`` using ordered atomic migrations."""
    if target < 0 or target > LATEST_SCHEMA_VERSION:
        raise MigrationError(f"unsupported schema version: {target}")
    connection.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations ("
        "version INTEGER PRIMARY KEY, name TEXT NOT NULL, applied_at TEXT NOT NULL)"
    )
    by_version = {migration.version: migration for migration in MIGRATIONS}
    current = 0
    try:
        # Lock before reading the current version. Otherwise two fresh
        # processes can both plan migration 1 and the loser executes a stale
        # CREATE script after the winner commits.
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            "SELECT COALESCE(MAX(version), 0) FROM schema_migrations"
        ).fetchone()
        current = int(row[0])
        if current > LATEST_SCHEMA_VERSION:
            raise MigrationError(
                f"database schema {current} is newer than supported "
                f"{LATEST_SCHEMA_VERSION}"
            )
        if current < target:
            for version in range(current + 1, target + 1):
                migration = by_version[version]
                _execute_script(connection, migration.up)
                connection.execute(
                    "INSERT INTO schema_migrations(version, name, applied_at) "
                    "VALUES (?, ?, datetime('now'))",
                    (migration.version, migration.name),
                )
        elif current > target:
            for version in range(current, target, -1):
                migration = by_version[version]
                _execute_script(connection, migration.down)
                connection.execute(
                    "DELETE FROM schema_migrations WHERE version = ?", (version,)
                )
        connection.commit()
    except MigrationError:
        if connection.in_transaction:
            connection.rollback()
        raise
    except sqlite3.Error as exc:
        if connection.in_transaction:
            connection.rollback()
        raise MigrationError(
            f"failed to migrate schema from {current} to {target}"
        ) from exc


def _execute_script(connection: sqlite3.Connection, script: str) -> None:
    """Execute a SQLite script without `executescript`'s implicit COMMIT."""

    pending = ""
    for line in script.splitlines(keepends=True):
        pending += line
        if not sqlite3.complete_statement(pending):
            continue
        statement = pending.strip()
        pending = ""
        if statement:
            connection.execute(statement)
    if pending.strip():
        raise sqlite3.OperationalError("migration contains an incomplete statement")


def current_version(connection: sqlite3.Connection) -> int:
    row = connection.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'schema_migrations'"
    ).fetchone()
    if row is None:
        return 0
    result = connection.execute(
        "SELECT COALESCE(MAX(version), 0) FROM schema_migrations"
    ).fetchone()
    return int(result[0])
