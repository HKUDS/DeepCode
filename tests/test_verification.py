from __future__ import annotations

from pathlib import Path

from core.verification import discover_verification_commands, run_verification


def test_discovers_real_test_scripts_and_rejects_npm_placeholder(
    tmp_path: Path,
) -> None:
    (tmp_path / "package.json").write_text(
        '{"scripts":{"test":"echo Error: no test specified && exit 1"}}',
        encoding="utf-8",
    )
    assert discover_verification_commands(tmp_path) == ()

    (tmp_path / "tests").mkdir()
    (tmp_path / "package.json").write_text(
        '{"scripts":{"test":"vitest run"}}', encoding="utf-8"
    )
    commands = discover_verification_commands(tmp_path)
    assert [command.id for command in commands] == ["pytest", "npm-test"]


def test_verification_reports_pass_and_keeps_output_bounded(tmp_path: Path) -> None:
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_ok.py").write_text(
        "def test_ok():\n    assert True\n", encoding="utf-8"
    )
    command = discover_verification_commands(tmp_path)[0]
    result = run_verification(tmp_path, command, timeout_seconds=30)

    assert result.passed is True
    assert result.exit_code == 0
    assert result.timed_out is False
    assert len(result.stdout.encode()) <= 64 * 1024
    assert len(result.stderr.encode()) <= 64 * 1024
