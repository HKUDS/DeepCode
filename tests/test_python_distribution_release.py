"""Release-contract tests for Python wheel and sdist verification."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPOSITORY_ROOT / "scripts" / "verify_python_distribution.py"


def _load_script():
    spec = importlib.util.spec_from_file_location(
        "deepcode_test_verify_python_distribution",
        SCRIPT,
    )
    if spec is None or spec.loader is None:  # pragma: no cover - static path
        raise RuntimeError(f"could not load {SCRIPT}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


release = _load_script()


def _metadata(
    *,
    version: str = "2.0.0",
    requires_python: str = ">=3.12",
    mcp: str = "mcp>=1.29,<2",
):
    return release.DistributionMetadata(
        name="deepcode-hku",
        version=version,
        requires_python=requires_python,
        requires_dist=(mcp, "httpx>=0.27"),
    )


def test_distribution_metadata_accepts_the_supported_runtime_contract(tmp_path):
    release.validate_metadata(
        _metadata(),
        expected_version="2.0.0",
        source=tmp_path / "artifact.whl",
    )


@pytest.mark.parametrize(
    ("mcp", "message"),
    (
        ("mcp>=1.0", "mcp>=1.29,<2"),
        ("mcp>=1.29", "mcp>=1.29,<2"),
        ("mcp<2", "mcp>=1.29,<2"),
        ("mcp>=1.29,<2; python_version < '3.13'", "must not condition"),
    ),
)
def test_distribution_metadata_rejects_unsafe_mcp_ranges(tmp_path, mcp, message):
    with pytest.raises(release.DistributionVerificationError, match=message):
        release.validate_metadata(
            _metadata(mcp=mcp),
            expected_version="2.0.0",
            source=tmp_path / "artifact.whl",
        )


@pytest.mark.parametrize("requires_python", (">=3.9", ">=3.13", "==3.12.*"))
def test_distribution_metadata_enforces_the_python_support_window(
    tmp_path,
    requires_python,
):
    with pytest.raises(
        release.DistributionVerificationError,
        match="reject Python 3.11 and accept Python 3.12-3.14",
    ):
        release.validate_metadata(
            _metadata(requires_python=requires_python),
            expected_version="2.0.0",
            source=tmp_path / "artifact.whl",
        )


def test_distribution_metadata_rejects_a_version_mismatch(tmp_path):
    with pytest.raises(release.DistributionVerificationError, match="expected '2.0.1'"):
        release.validate_metadata(
            _metadata(),
            expected_version="2.0.1",
            source=tmp_path / "artifact.whl",
        )


def test_release_tag_must_match_the_canonical_version(tmp_path):
    with pytest.raises(release.DistributionVerificationError, match="does not match"):
        release.verify_artifacts(tmp_path, release_tag="v1.3.0")
