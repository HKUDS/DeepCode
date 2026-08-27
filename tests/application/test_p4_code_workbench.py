from __future__ import annotations

import shutil
import subprocess
import threading
from datetime import UTC, datetime
from pathlib import Path

import pytest

from core.application import DeepCodeApplication
from core.application.errors import (
    ConflictError,
    FileChangedError,
    TerminalNotFoundError,
    WorkspaceOutOfScopeError,
)
from core.application.file_service import MAX_READ_LIMIT
from core.domain import TrustState
from core.domain.item import ItemKind, ItemStatus
from core.domain.turn import Turn, TurnStatus
from core.persistence.execution_repository import TurnRepository


def _git(workspace: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", *arguments],
        cwd=workspace,
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout


def _repository(path: Path) -> None:
    path.mkdir()
    _git(path, "init", "-q")
    _git(path, "config", "user.name", "DeepCode Test")
    _git(path, "config", "user.email", "deepcode@example.test")
    (path / "tracked.txt").write_text("one\ntwo\n", encoding="utf-8")
    (path / "secondary.txt").write_text("secondary\n", encoding="utf-8")
    _git(path, "add", "tracked.txt", "secondary.txt")
    _git(path, "commit", "-q", "-m", "base")


def _application(tmp_path: Path, workspace: Path) -> tuple[DeepCodeApplication, str]:
    application = DeepCodeApplication.open(tmp_path / "state.sqlite3")
    project = application.projects.add(str(workspace), trust_state=TrustState.TRUSTED)
    thread = application.threads.start(project.id, title="P4 workbench")
    return application, thread.id


def test_file_tree_read_and_optimistic_edit_stay_inside_workspace(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    outside = tmp_path / "outside"
    workspace.mkdir()
    outside.mkdir()
    (workspace / "src").mkdir()
    source = workspace / "src" / "main.py"
    source.write_text("answer = 41\n", encoding="utf-8")
    (outside / "secret.txt").write_text("secret", encoding="utf-8")
    (workspace / "escape").symlink_to(outside, target_is_directory=True)
    application, thread_id = _application(tmp_path, workspace)
    try:
        entries, truncated = application.files.list(thread_id, depth=3)
        assert truncated is False
        assert {entry.path for entry in entries} >= {"src", "src/main.py", "escape"}
        assert (
            next(entry for entry in entries if entry.path == "escape").kind == "symlink"
        )

        content = application.files.read(thread_id, "src/main.py")
        assert content.content == "answer = 41\n"
        updated = application.files.write(
            thread_id,
            "src/main.py",
            "answer = 42\n",
            expected_sha256=content.sha256,
        )
        assert updated.content == "answer = 42\n"
        with pytest.raises(FileChangedError):
            application.files.write(
                thread_id,
                "src/main.py",
                "answer = 43\n",
                expected_sha256=content.sha256,
            )
        with pytest.raises(WorkspaceOutOfScopeError):
            application.files.read(thread_id, "escape/secret.txt")
    finally:
        application.close()


def test_file_read_truncates_on_a_utf8_boundary_without_hashing_partial_content(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    source = workspace / "unicode.txt"
    source.write_bytes(b"1234567" + "中".encode() + b"tail")
    application, thread_id = _application(tmp_path, workspace)
    try:
        content = application.files.read(thread_id, "unicode.txt", max_bytes=8)
        assert content.content == "1234567"
        assert content.truncated is True
        assert content.sha256 is None

        large = workspace / "large.txt"
        large.write_bytes(b"x" * (MAX_READ_LIMIT + 1))
        large_content = application.files.read(thread_id, "large.txt")
        assert large_content.byte_size == MAX_READ_LIMIT + 1
        assert large_content.truncated is True
        assert large_content.sha256 is None
    finally:
        application.close()


def test_git_status_and_diff_include_tracked_and_untracked_files(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "repository"
    _repository(workspace)
    application, thread_id = _application(tmp_path, workspace)
    try:
        (workspace / "tracked.txt").write_text(
            "one\nchanged\nthree\n", encoding="utf-8"
        )
        (workspace / "new.txt").write_text("new line\n", encoding="utf-8")

        status = application.git.status(thread_id)
        by_path = {entry.path: entry for entry in status.entries}
        assert status.repository_root == str(workspace)
        assert by_path["tracked.txt"].kind == "modified"
        assert by_path["new.txt"].kind == "untracked"

        diffs = {
            file_diff.path: file_diff for file_diff in application.git.diff(thread_id)
        }
        assert diffs["tracked.txt"].additions == 2
        assert diffs["tracked.txt"].deletions == 1
        assert diffs["tracked.txt"].hunks[0].lines
        assert diffs["new.txt"].status == "untracked"
        assert diffs["new.txt"].additions == 1
        assert len(diffs["tracked.txt"].revision) == 64

        stale_revision = diffs["tracked.txt"].revision
        (workspace / "tracked.txt").write_text("changed again\n", encoding="utf-8")
        with pytest.raises(FileChangedError, match="changed since it was reviewed"):
            application.git.discard(thread_id, "tracked.txt", stale_revision)
        current = application.git.diff(thread_id, path="tracked.txt")[0]
        application.git.discard(thread_id, "tracked.txt", current.revision)
        assert (workspace / "tracked.txt").read_text(encoding="utf-8") == "one\ntwo\n"
        application.git.discard(thread_id, "new.txt", diffs["new.txt"].revision)
        assert not (workspace / "new.txt").exists()
    finally:
        application.close()


def test_git_diff_handles_unborn_repositories_renames_and_deletions(
    tmp_path: Path,
) -> None:
    unborn = tmp_path / "unborn"
    unborn.mkdir()
    _git(unborn, "init", "-q")
    (unborn / "first.txt").write_text("first\n", encoding="utf-8")
    _git(unborn, "add", "first.txt")
    application, thread_id = _application(tmp_path, unborn)
    try:
        staged = application.git.diff(thread_id, scope="all")
        assert staged[0].path == "first.txt"
        assert staged[0].status == "added"
        assert staged[0].additions == 1
        application.git.discard(thread_id, "first.txt", staged[0].revision)
        assert not (unborn / "first.txt").exists()
        assert application.git.status(thread_id).entries == ()
    finally:
        application.close()

    repository = tmp_path / "repository"
    _repository(repository)
    _git(repository, "mv", "tracked.txt", "renamed.txt")
    (repository / "secondary.txt").unlink()
    application, thread_id = _application(tmp_path, repository)
    try:
        status = {
            entry.path: entry for entry in application.git.status(thread_id).entries
        }
        assert status["renamed.txt"].original_path == "tracked.txt"
        assert status["secondary.txt"].kind == "deleted"
        diffs = {entry.path: entry for entry in application.git.diff(thread_id)}
        assert diffs["renamed.txt"].original_path == "tracked.txt"
        assert diffs["renamed.txt"].status == "renamed"
        assert diffs["secondary.txt"].deletions == 1
        application.git.discard(thread_id, "renamed.txt", diffs["renamed.txt"].revision)
        assert (repository / "tracked.txt").is_file()
        assert not (repository / "renamed.txt").exists()
        restored_deletion = application.git.diff(thread_id, path="secondary.txt")[0]
        application.git.discard(thread_id, "secondary.txt", restored_deletion.revision)
        assert (repository / "secondary.txt").is_file()
    finally:
        application.close()


def test_worktree_lifecycle_requires_explicit_dirty_cleanup(tmp_path: Path) -> None:
    workspace = tmp_path / "repository"
    _repository(workspace)
    application, thread_id = _application(tmp_path, workspace)
    try:
        created = application.worktrees.create(thread_id)
        worktree_path = Path(created.path)
        assert created.disposition == "created"
        assert worktree_path.is_dir()
        assert application.threads.read(thread_id).worktree_path == str(worktree_path)

        reclaimed = application.worktrees.create(thread_id)
        assert reclaimed.disposition == "reclaimed"
        (worktree_path / "tracked.txt").write_text("thread change\n", encoding="utf-8")
        kept = application.worktrees.remove(thread_id, disposition="keep")
        assert kept.disposition == "kept"
        assert kept.dirty is True
        with pytest.raises(ConflictError, match="uncommitted changes"):
            application.worktrees.remove(thread_id, disposition="clean")

        cleaned = application.worktrees.remove(
            thread_id, disposition="clean", force=True
        )
        assert cleaned.disposition == "cleaned"
        assert not worktree_path.exists()
        restored = application.threads.read(thread_id)
        assert restored.worktree_path is None
        assert restored.workspace_path == str(workspace)
        _git(workspace, "show-ref", "--verify", f"refs/heads/{created.branch}")
    finally:
        application.close()


def test_worktree_recreates_a_missing_registered_path_only_with_its_manifest(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "repository"
    _repository(workspace)
    application, thread_id = _application(tmp_path, workspace)
    try:
        created = application.worktrees.create(thread_id)
        path = Path(created.path)
        manifest = path.parent / ".deepcode-manifests" / f"{thread_id}.json"
        assert manifest.is_file()

        shutil.rmtree(path)
        recreated = application.worktrees.create(thread_id)
        assert recreated.disposition == "created"
        assert path.is_dir()

        manifest.write_text("{}", encoding="utf-8")
        with pytest.raises(ConflictError, match="manifest does not match"):
            application.worktrees.remove(thread_id, disposition="clean")
        assert path.is_dir()
    finally:
        # Repair only the test-owned manifest so application cleanup can run safely.
        if "created" in locals() and Path(created.path).exists():
            manifest.unlink(missing_ok=True)
            _git(workspace, "worktree", "remove", "--force", created.path)
        application.close()


@pytest.mark.skipif(not Path("/bin/sh").exists(), reason="requires a Unix PTY")
def test_terminal_is_thread_owned_and_emits_output_and_exit(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    application, thread_id = _application(tmp_path, workspace)
    other = application.threads.start(
        application.threads.read(thread_id).project_id,
        title="Other",
    )
    messages: list[tuple[str, dict]] = []
    exited = threading.Event()

    def listener(method: str, payload: dict) -> None:
        messages.append((method, payload))
        if method == "terminal.exit":
            exited.set()

    token = application.terminals.subscribe(listener)
    try:
        terminal = application.terminals.create(thread_id, columns=80, rows=24)
        with pytest.raises(TerminalNotFoundError):
            application.terminals.write(other.id, terminal.id, "echo forbidden\n")
        application.terminals.write(
            thread_id, terminal.id, "printf 'P4_TERMINAL_OK\\n'; exit\n"
        )
        assert exited.wait(timeout=5)
        output = "".join(
            payload["data"]
            for method, payload in messages
            if method == "terminal.output"
        )
        assert "P4_TERMINAL_OK" in output
        assert application.terminals.active_for_thread(thread_id) is False
    finally:
        application.terminals.unsubscribe(token)
        application.close()


def test_allowlisted_test_run_creates_durable_test_result_item(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    tests_dir = workspace / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_ok.py").write_text(
        "def test_ok():\n    assert 6 * 7 == 42\n", encoding="utf-8"
    )
    application, thread_id = _application(tmp_path, workspace)
    now = datetime.now(UTC)
    with application.database.transaction() as connection:
        turns = TurnRepository(connection)
        turn = Turn(
            thread_id=thread_id,
            ordinal=turns.next_ordinal(thread_id),
            prompt="verify",
            status=TurnStatus.COMPLETED,
            stop_reason="completed",
            started_at=now,
            completed_at=now,
        )
        turns.add(turn)
    try:
        commands = application.tests.discover(thread_id)
        assert [command.id for command in commands] == ["pytest"]
        result = application.tests.run(thread_id, turn.id, "pytest", timeout_seconds=30)
        assert result.exit_code == 0
        assert result.item.kind is ItemKind.TEST_RESULT
        assert result.item.status is ItemStatus.COMPLETED
        snapshot = application.turns.read(turn.id)
        assert snapshot.items[-1].id == result.item.id
        assert application.events.replay(thread_id)[-1].item_id == result.item.id
    finally:
        application.close()


def test_test_discovery_requires_a_real_npm_script_and_bounds_failure_output(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "package.json").write_text('{"scripts": {}}', encoding="utf-8")
    tests_dir = workspace / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_noisy.py").write_text(
        "def test_noisy():\n    print('x' * 100000)\n    assert False\n",
        encoding="utf-8",
    )
    application, thread_id = _application(tmp_path, workspace)
    now = datetime.now(UTC)
    with application.database.transaction() as connection:
        turns = TurnRepository(connection)
        turn = Turn(
            thread_id=thread_id,
            ordinal=turns.next_ordinal(thread_id),
            prompt="verify noisy output",
            status=TurnStatus.COMPLETED,
            stop_reason="completed",
            started_at=now,
            completed_at=now,
        )
        turns.add(turn)
    try:
        assert [command.id for command in application.tests.discover(thread_id)] == [
            "pytest"
        ]
        result = application.tests.run(thread_id, turn.id, "pytest", timeout_seconds=30)
        assert result.item.status is ItemStatus.FAILED
        assert result.output_truncated is True
        assert len(result.stdout.encode("utf-8")) <= 64 * 1024

        (workspace / "package.json").write_text(
            '{"scripts": {"test": "node --test"}}', encoding="utf-8"
        )
        assert [command.id for command in application.tests.discover(thread_id)] == [
            "pytest",
            "npm-test",
        ]
    finally:
        application.close()
