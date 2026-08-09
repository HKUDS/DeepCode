from __future__ import annotations

import pytest

from scripts.desktop_ci_scope import affects_desktop


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
        "scripts/desktop_ci_scope.py",
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
