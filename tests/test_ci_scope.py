from __future__ import annotations

import pytest

from scripts.ci_scope import affects_desktop, affects_runtime


@pytest.mark.parametrize(
    "path",
    [
        "desktop/src/App.tsx",
        "desktop/src-tauri/src/main.rs",
        "app_server/dispatcher.py",
        "core/skills/service.py",
        "protocol/app-server.schema.json",
        "requirements.txt",
        ".github/workflows/desktop-ci.yml",
        "scripts/ci_scope.py",
    ],
)
def test_desktop_ci_runs_for_desktop_and_sidecar_inputs(path: str) -> None:
    assert affects_desktop([path])


@pytest.mark.parametrize(
    "path",
    [
        "README.md",
        "docs/HEADLESS_AND_AUTOMATION.md",
        "tests/test_python_distribution_release.py",
        "scripts/verify_python_distribution.py",
        ".github/workflows/python-ci.yml",
        ".github/workflows/pypi-publish.yml",
        "desktop-notes/architecture.md",
    ],
)
def test_desktop_ci_skips_release_only_and_documentation_changes(path: str) -> None:
    assert not affects_desktop([path])


def test_desktop_ci_runs_when_any_changed_path_has_desktop_impact() -> None:
    assert affects_desktop(["README.md", "core/version.py"])


@pytest.mark.parametrize(
    "path",
    [
        "README.md",
        "README_ZH.md",
        "CONTRIBUTORS.md",
        "CHANGELOG.md",
        "LICENSE",
        "docs/CI.md",
        "assets/readme/demo.png",
        "assets/logo.svg",
        "website/package-lock.json",
        ".github/ISSUE_TEMPLATE/bug_report.yml",
    ],
)
def test_documentation_does_not_require_runtime_tests(path):
    assert not affects_runtime([path])


@pytest.mark.parametrize(
    "path",
    [
        "tests/test_ci_scope.py",
        "scripts/ci/requirements.lock",
        "core/skills/builtin/example/SKILL.md",
        "prompts/agent.md",
        "desktop/README.md",
        "docs.py",
        ".github/actions/ci-scope/action.yml",
        ".github/workflows/python-ci.yml",
        "setup.py",
        "unknown-new-input",
    ],
)
def test_code_and_unknown_changes_always_require_runtime_tests(path):
    assert affects_runtime(["README.md", path])


def test_scope_cli_handles_spaces_and_empty_input():
    from pathlib import Path
    import subprocess
    import sys

    script = Path(__file__).resolve().parents[1] / "scripts/ci_scope.py"
    for paths, expected in (
        (b"docs/a file.md\0README.md\0", "false"),
        (b"docs/a file.md\0core/new.py\0", "true"),
        (b"", "true"),
    ):
        result = subprocess.run(
            [sys.executable, str(script)], input=paths, capture_output=True, check=True
        )
        assert result.stdout.decode().splitlines() == [
            f"runtime_changed={expected}",
            f"desktop_changed={expected}",
        ]


@pytest.mark.parametrize(
    "scenario",
    [
        "docs",
        "base_advanced",
        "code_then_docs",
        "delete_then_docs",
        "missing",
        "manual",
    ],
)
def test_scope_action_uses_the_full_diff_and_fails_safe(tmp_path, scenario):
    import os
    from pathlib import Path
    import shutil
    import subprocess

    import yaml

    root = Path(__file__).resolve().parents[1]
    action = yaml.safe_load((root / ".github/actions/ci-scope/action.yml").read_text())
    script = action["runs"]["steps"][0]["run"]
    (tmp_path / "scripts").mkdir()
    shutil.copyfile(root / "scripts/ci_scope.py", tmp_path / "scripts/ci_scope.py")
    (tmp_path / "core").mkdir()
    (tmp_path / "core/example.py").write_text("value = 1\n")
    (tmp_path / "README.md").write_text("Initial documentation\n")

    def git(*args):
        return subprocess.run(
            [
                "git",
                "-c",
                "user.name=CI test",
                "-c",
                "user.email=ci@example.test",
                *args,
            ],
            cwd=tmp_path,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

    git("init")
    git("add", ".")
    git("commit", "-m", "base")
    base = git("rev-parse", "HEAD")
    if scenario == "base_advanced":
        (tmp_path / "core/example.py").write_text("value = 3\n")
        git("add", ".")
        git("commit", "-m", "upstream runtime change")
        upstream = git("rev-parse", "HEAD")
        git("checkout", "-b", "docs", base)
        base = upstream
    if scenario == "code_then_docs":
        (tmp_path / "core/example.py").write_text("value = 2\n")
    if scenario == "delete_then_docs":
        (tmp_path / "core/example.py").unlink()
    if scenario in {"code_then_docs", "delete_then_docs"}:
        git("add", "-A")
        git("commit", "-m", "runtime change")
    (tmp_path / "README.md").write_text("Updated documentation\n")
    git("add", "README.md")
    git("commit", "-m", "documentation update")
    output = tmp_path / "action-output"
    subprocess.run(
        ["bash", "-e", "-o", "pipefail", "-c", script],
        cwd=tmp_path,
        env={
            **os.environ,
            "EVENT_NAME": "workflow_dispatch"
            if scenario == "manual"
            else "pull_request",
            "BASE_SHA": "f" * 40 if scenario == "missing" else base,
            "HEAD_SHA": git("rev-parse", "HEAD"),
            "GITHUB_OUTPUT": str(output),
        },
        check=True,
        capture_output=True,
    )
    expected = "false" if scenario in {"docs", "base_advanced"} else "true"
    assert output.read_text().splitlines() == [
        f"runtime_changed={expected}",
        f"desktop_changed={expected}",
    ]
