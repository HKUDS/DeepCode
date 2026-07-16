"""Sanitized local health snapshot for Desktop troubleshooting."""

from __future__ import annotations

import os
import platform
import shutil
import sqlite3
import sys
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

from core.application.project_service import ProjectService
from core.config import (
    home_config_path,
    load_config_for_workspace,
    project_config_path,
)
from core.persistence.database import Database
from core.persistence.migrations import current_version
from core.sessions import SessionStore


class DiagnosticsService:
    def __init__(
        self,
        database: Database,
        projects: ProjectService,
        session_store: SessionStore,
    ) -> None:
        self.database = database
        self.projects = projects
        self.session_store = session_store

    def read(self, project_id: str | None = None) -> dict[str, Any]:
        project = self.projects.read(project_id) if project_id is not None else None
        workspace = (
            Path(project.canonical_path).resolve(strict=False)
            if project is not None
            else None
        )
        checks: list[dict[str, str]] = []
        config_error: str | None = None
        try:
            if workspace is not None:
                load_config_for_workspace(workspace)
            else:
                from core.config import load_config

                load_config(config_path=home_config_path())
            checks.append(
                _check("config", "Configuration", "ok", "Configuration is valid")
            )
        except Exception as exc:  # noqa: BLE001 - diagnostics report, never crash
            config_error = f"{type(exc).__name__}: {exc}"
            checks.append(_check("config", "Configuration", "error", config_error))

        database_ok = self._database_health()
        checks.append(
            _check(
                "database",
                "Desktop database",
                "ok" if database_ok else "error",
                "SQLite integrity check passed"
                if database_ok
                else "SQLite integrity check failed",
            )
        )
        session_writable = _writable_location(self.session_store.root)
        checks.append(
            _check(
                "sessions",
                "Canonical Session store",
                "ok" if session_writable else "warning",
                "JSONL Session directory is writable"
                if session_writable
                else "JSONL Session directory is not writable",
            )
        )
        git = shutil.which("git")
        checks.append(
            _check(
                "git",
                "Git",
                "ok" if git else "warning",
                git or "Git is not available on PATH",
            )
        )
        with self.database.read() as connection:
            project_count = int(
                connection.execute("SELECT COUNT(*) FROM projects").fetchone()[0]
            )
            thread_count = int(
                connection.execute("SELECT COUNT(*) FROM threads").fetchone()[0]
            )
            workflow_count = int(
                connection.execute("SELECT COUNT(*) FROM workflow_runs").fetchone()[0]
            )
            automation_count = int(
                connection.execute("SELECT COUNT(*) FROM automations").fetchone()[0]
            )
            schema_version = current_version(connection)

        return {
            "appVersion": _package_version(),
            "pythonVersion": platform.python_version(),
            "pythonExecutable": sys.executable,
            "platform": platform.platform(),
            "architecture": platform.machine(),
            "processId": os.getpid(),
            "databasePath": str(self.database.path),
            "databaseSchemaVersion": schema_version,
            "databaseBytes": _file_size(self.database.path),
            "sessionStorePath": str(self.session_store.root),
            "sessionCount": len(self.session_store.list_sessions(limit=100_000)),
            "projectCount": project_count,
            "threadCount": thread_count,
            "workflowCount": workflow_count,
            "automationCount": automation_count,
            "userConfigPath": str(home_config_path()),
            "projectConfigPath": (
                str(project_config_path(workspace)) if workspace is not None else None
            ),
            "projectPath": str(workspace) if workspace is not None else None,
            "projectTrust": project.trust_state.value if project is not None else None,
            "configError": config_error,
            "checks": checks,
        }

    def _database_health(self) -> bool:
        try:
            with sqlite3.connect(self.database.path) as connection:
                row = connection.execute("PRAGMA quick_check").fetchone()
            return bool(row and row[0] == "ok")
        except sqlite3.Error:
            return False


def _check(identifier: str, label: str, status: str, detail: str) -> dict[str, str]:
    return {"id": identifier, "label": label, "status": status, "detail": detail}


def _writable_location(path: Path) -> bool:
    candidate = path if path.exists() else path.parent
    return candidate.exists() and os.access(candidate, os.W_OK)


def _file_size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def _package_version() -> str:
    try:
        return version("deepcode-hku")
    except PackageNotFoundError:
        return "1.2.0"
