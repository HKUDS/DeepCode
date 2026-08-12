"""Focused tests for Desktop release gates that do not require a platform bundle."""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

import pytest


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_ROOT = REPOSITORY_ROOT / "desktop" / "scripts"


def _load_script(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS_ROOT / filename)
    if spec is None or spec.loader is None:  # pragma: no cover - static paths
        raise RuntimeError(f"could not load {filename}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


release_config = _load_script(
    "deepcode_test_create_release_config",
    "create-release-config.py",
)
release_environment = _load_script(
    "deepcode_test_validate_release_environment",
    "validate-release-environment.py",
)
release_bundle = _load_script(
    "deepcode_test_verify_release_bundle",
    "verify-release-bundle.py",
)
license_audit = _load_script(
    "deepcode_test_audit_licenses",
    "audit-licenses.py",
)
sidecar_setup = _load_script(
    "deepcode_test_setup_sidecar",
    "setup-sidecar-env.py",
)
python_audit = _load_script(
    "deepcode_test_audit_python_environment",
    "audit-python-environment.py",
)
sidecar_build = _load_script(
    "deepcode_test_build_sidecar",
    "build-sidecar.py",
)


def test_release_endpoint_defaults_to_the_repository(monkeypatch):
    monkeypatch.delenv("TAURI_UPDATER_ENDPOINT", raising=False)
    monkeypatch.setenv("GITHUB_REPOSITORY", "example/deepcode")

    assert release_config._endpoint() == (
        "https://github.com/example/deepcode/releases/latest/download/latest.json"
    )


@pytest.mark.parametrize(
    "endpoint",
    (
        "http://updates.example.test/latest.json",
        "https://user@updates.example.test/latest.json",
        "https://updates.example.test/latest.json#candidate",
        "relative/latest.json",
    ),
)
def test_release_endpoint_rejects_unsafe_values(monkeypatch, endpoint):
    monkeypatch.setenv("TAURI_UPDATER_ENDPOINT", endpoint)

    with pytest.raises(RuntimeError, match="absolute HTTPS URL"):
        release_config._endpoint()


def test_release_environment_fails_closed_and_accepts_complete_placeholders(
    monkeypatch,
):
    required = (
        release_environment.COMMON + release_environment.PLATFORM_REQUIREMENTS["macos"]
    )
    for name in required:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(
        sys, "argv", ["validate-release-environment.py", "--platform", "macos"]
    )

    with pytest.raises(RuntimeError, match="credentials are incomplete"):
        release_environment.main()

    for name in required:
        monkeypatch.setenv(name, f"test-{name.lower()}")
    assert release_environment.main() == 0


def test_bundle_resource_tree_requires_one_runtime_and_notices(
    tmp_path,
    monkeypatch,
):
    binary_name = (
        "deepcode-app-server.exe"
        if release_bundle.os.name == "nt"
        else "deepcode-app-server"
    )
    binary = tmp_path / "resources" / "app-server" / binary_name
    binary.parent.mkdir(parents=True)
    binary.write_bytes(b"runtime")
    for notice in release_bundle.NOTICE_NAMES:
        (tmp_path / "resources" / notice).write_text("notice\n", encoding="utf-8")
    verified: list[Path] = []
    monkeypatch.setattr(release_bundle, "_verify_runtime", verified.append)

    assert release_bundle._verify_resource_tree(tmp_path) == binary
    assert verified == [binary]

    (tmp_path / "resources" / release_bundle.NOTICE_NAMES[0]).unlink()
    with pytest.raises(RuntimeError, match="bundle notice is missing"):
        release_bundle._verify_resource_tree(tmp_path)


def test_archive_listing_accepts_platform_sidecar_suffixes():
    listing = "\n".join(
        (
            "./usr/lib/deepcode/app-server/deepcode-app-server.exe",
            "./usr/lib/deepcode/THIRD_PARTY_NOTICES.md",
            "./usr/lib/deepcode/PRIVACY_AND_DIAGNOSTICS.md",
        )
    ).lower()

    release_bundle._require_archive_contents(listing)


def test_license_policy_rejects_missing_and_forbidden_declarations():
    packages = [
        {"name": "missing", "version": "1", "license": "", "scope": "runtime"},
        {
            "name": "forbidden",
            "version": "1",
            "license": "AGPL-3.0",
            "scope": "runtime",
        },
    ]

    violations = license_audit._violations("python", packages)

    assert violations == [
        "python:missing has no declared license",
        "python:forbidden uses forbidden license marker AGPL",
    ]


def test_sidecar_version_probe_is_fail_closed(monkeypatch, tmp_path):
    python = tmp_path / ("python.exe" if os.name == "nt" else "python")
    python.touch()
    monkeypatch.setattr(sidecar_setup, "_check_version", lambda _python: None)
    assert sidecar_setup._is_python312(python) is True

    def reject(_python):
        raise RuntimeError("wrong version")

    monkeypatch.setattr(sidecar_setup, "_check_version", reject)
    assert sidecar_setup._is_python312(python) is False


def test_sidecar_bundles_every_file_backed_runtime_resource():
    bundled = {
        (source.relative_to(REPOSITORY_ROOT).as_posix(), destination)
        for source, destination in sidecar_build.BUNDLED_DATA
    }

    assert ("core/mcp/presets.json", "core/mcp") in bundled
    assert all(source.exists() for source, _destination in sidecar_build.BUNDLED_DATA)


def test_python_audit_preserves_virtualenv_executable_symlink(
    monkeypatch,
    tmp_path,
):
    base_python = tmp_path / "base" / "python"
    base_python.parent.mkdir()
    base_python.touch()
    venv_python = tmp_path / "venv" / "bin" / "python"
    venv_python.parent.mkdir(parents=True)
    venv_python.symlink_to(base_python)
    site_packages = tmp_path / "venv" / "lib" / "site-packages"
    site_packages.mkdir(parents=True)
    output = tmp_path / "audit.json"
    captured: list[Path] = []

    def site_for(python):
        captured.append(python)
        return site_packages

    monkeypatch.setattr(python_audit, "_site_packages", site_for)
    monkeypatch.setattr(python_audit.subprocess, "run", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "audit-python-environment.py",
            "--python",
            str(venv_python),
            "--output",
            str(output),
        ],
    )

    assert python_audit.main() == 0
    assert captured == [venv_python.absolute()]
    assert captured[0] != base_python.resolve()
