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
