"""Fail-closed verification for Python distributions before publication."""

from __future__ import annotations

import argparse
import importlib.util
import os
import re
import subprocess
import tarfile
import tempfile
import venv
import zipfile
from dataclasses import dataclass
from email.parser import BytesParser
from email.policy import default
from pathlib import Path

from packaging.requirements import InvalidRequirement, Requirement
from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.version import InvalidVersion, Version

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_NAME = "deepcode-hku"
MINIMUM_MCP_VERSION = Version("1.29")
UNSUPPORTED_MCP_VERSION = Version("2")
SUPPORTED_PYTHON_VERSIONS = (Version("3.12"), Version("3.13"), Version("3.14"))


class DistributionVerificationError(RuntimeError):
    """The built distribution does not satisfy DeepCode's release contract."""


@dataclass(frozen=True, slots=True)
class DistributionMetadata:
    name: str
    version: str
    requires_python: str
    requires_dist: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class VerifiedArtifacts:
    wheel: Path
    sdist: Path
    version: str


def _canonical_version() -> str:
    version_file = REPOSITORY_ROOT / "core" / "version.py"
    spec = importlib.util.spec_from_file_location(
        "deepcode_release_version",
        version_file,
    )
    if spec is None or spec.loader is None:  # pragma: no cover - static path
        raise DistributionVerificationError(
            f"could not load canonical version from {version_file}"
        )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    value = getattr(module, "__version__", None)
    if not isinstance(value, str) or not value.strip():
        raise DistributionVerificationError(
            f"canonical version is missing from {version_file}"
        )
    return value.strip()


def _normalized_name(value: str) -> str:
    return re.sub(r"[-_.]+", "-", value).lower()


def _parse_metadata(payload: bytes, *, source: Path) -> DistributionMetadata:
    message = BytesParser(policy=default).parsebytes(payload)
    name = message.get("Name", "").strip()
    version = message.get("Version", "").strip()
    requires_python = message.get("Requires-Python", "").strip()
    if not name or not version or not requires_python:
        raise DistributionVerificationError(f"{source} has incomplete package metadata")
    return DistributionMetadata(
        name=name,
        version=version,
        requires_python=requires_python,
        requires_dist=tuple(message.get_all("Requires-Dist", [])),
    )


def _wheel_metadata(path: Path) -> DistributionMetadata:
    try:
        with zipfile.ZipFile(path) as archive:
            metadata_files = [
                name
                for name in archive.namelist()
                if name.endswith(".dist-info/METADATA")
            ]
            if len(metadata_files) != 1:
                raise DistributionVerificationError(
                    f"{path} must contain exactly one .dist-info/METADATA file"
                )
            payload = archive.read(metadata_files[0])
    except (OSError, zipfile.BadZipFile, KeyError) as exc:
        raise DistributionVerificationError(
            f"could not inspect wheel {path}: {exc}"
        ) from exc
    return _parse_metadata(payload, source=path)


def _sdist_metadata(path: Path) -> DistributionMetadata:
    try:
        with tarfile.open(path, mode="r:gz") as archive:
            metadata_files = [
                member
                for member in archive.getmembers()
                if member.isfile()
                and member.name.count("/") == 1
                and member.name.endswith("/PKG-INFO")
            ]
            if len(metadata_files) != 1:
                raise DistributionVerificationError(
                    f"{path} must contain exactly one top-level PKG-INFO file"
                )
            extracted = archive.extractfile(metadata_files[0])
            if extracted is None:  # pragma: no cover - guarded by member.isfile
                raise DistributionVerificationError(
                    f"could not read PKG-INFO from {path}"
                )
            payload = extracted.read()
    except (OSError, tarfile.TarError) as exc:
        raise DistributionVerificationError(
            f"could not inspect sdist {path}: {exc}"
        ) from exc
    return _parse_metadata(payload, source=path)


def _parse_mcp_requirement(
    requirements: tuple[str, ...],
    *,
    source: Path,
) -> Requirement:
    parsed: list[Requirement] = []
    for value in requirements:
        try:
            requirement = Requirement(value)
        except InvalidRequirement as exc:
            raise DistributionVerificationError(
                f"{source} has an invalid Requires-Dist entry: {value!r}"
            ) from exc
        if _normalized_name(requirement.name) == "mcp":
            parsed.append(requirement)
    if len(parsed) != 1:
        raise DistributionVerificationError(
            f"{source} must declare exactly one unconditional mcp dependency"
        )
    requirement = parsed[0]
    if requirement.marker is not None:
        raise DistributionVerificationError(
            f"{source} must not condition the mcp dependency on an environment marker"
        )
    return requirement


def validate_metadata(
    metadata: DistributionMetadata,
    *,
    expected_version: str,
    source: Path,
) -> None:
    """Validate one wheel/sdist metadata record against the release contract."""

    if _normalized_name(metadata.name) != PACKAGE_NAME:
        raise DistributionVerificationError(
            f"{source} names package {metadata.name!r}, expected {PACKAGE_NAME!r}"
        )
    if metadata.version != expected_version:
        raise DistributionVerificationError(
            f"{source} has version {metadata.version!r}, expected {expected_version!r}"
        )

    try:
        python_specifier = SpecifierSet(metadata.requires_python)
    except InvalidSpecifier as exc:
        raise DistributionVerificationError(
            f"{source} has invalid Requires-Python: {metadata.requires_python!r}"
        ) from exc
    if Version("3.11") in python_specifier or any(
        version not in python_specifier for version in SUPPORTED_PYTHON_VERSIONS
    ):
        raise DistributionVerificationError(
            f"{source} must reject Python 3.11 and accept Python 3.12-3.14; "
            f"found {metadata.requires_python!r}"
        )

    mcp_requirement = _parse_mcp_requirement(
        metadata.requires_dist,
        source=source,
    )
    mcp_specifier = mcp_requirement.specifier
    has_floor = any(
        item.operator == ">=" and Version(item.version) == MINIMUM_MCP_VERSION
        for item in mcp_specifier
    )
    has_upper_bound = any(
        item.operator == "<" and Version(item.version) == UNSUPPORTED_MCP_VERSION
        for item in mcp_specifier
    )
    if not has_floor or not has_upper_bound:
        raise DistributionVerificationError(
            f"{source} must declare mcp>=1.29,<2; found {mcp_requirement}"
        )


def _single_artifact(dist_dir: Path, pattern: str, label: str) -> Path:
    candidates = sorted(path for path in dist_dir.glob(pattern) if path.is_file())
    if len(candidates) != 1:
        raise DistributionVerificationError(
            f"expected exactly one {label} in {dist_dir}, found {len(candidates)}"
        )
    return candidates[0].resolve()


def verify_artifacts(
    dist_dir: Path,
    *,
    release_tag: str | None = None,
) -> VerifiedArtifacts:
    """Validate the only wheel and sdist in ``dist_dir`` and return their paths."""

    expected_version = _canonical_version()
    if release_tag is not None and release_tag != f"v{expected_version}":
        raise DistributionVerificationError(
            f"release tag {release_tag!r} does not match canonical version "
            f"v{expected_version}"
        )
    wheel = _single_artifact(dist_dir, "*.whl", "wheel")
    sdist = _single_artifact(dist_dir, "*.tar.gz", "sdist")
    validate_metadata(
        _wheel_metadata(wheel),
        expected_version=expected_version,
        source=wheel,
    )
    validate_metadata(
        _sdist_metadata(sdist),
        expected_version=expected_version,
        source=sdist,
    )
    return VerifiedArtifacts(wheel=wheel, sdist=sdist, version=expected_version)


def _venv_python(root: Path) -> Path:
    return root / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def _run(
    command: list[str],
    *,
    cwd: Path,
    stdin=None,
    timeout: int = 180,
) -> None:
    environment = {
        **os.environ,
        "PIP_DISABLE_PIP_VERSION_CHECK": "1",
        "PYTHONNOUSERSITE": "1",
    }
    try:
        subprocess.run(
            command,
            cwd=cwd,
            env=environment,
            stdin=stdin,
            check=True,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.CalledProcessError as exc:
        details = "\n".join(
            part.strip() for part in (exc.stdout, exc.stderr) if part and part.strip()
        )
        suffix = f"\n{details}" if details else ""
        raise DistributionVerificationError(
            f"distribution smoke command failed: {' '.join(command)}{suffix}"
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise DistributionVerificationError(
            f"distribution smoke command timed out: {' '.join(command)}"
        ) from exc


def smoke_installed_wheel(wheel: Path) -> None:
    """Install the wheel with unconstrained dependencies and exercise public CLIs."""

    with tempfile.TemporaryDirectory(prefix="deepcode-wheel-smoke-") as temporary:
        root = Path(temporary)
        environment = root / "venv"
        # POSIX virtual environments should symlink the selected interpreter.
        # Copying a uv-managed macOS binary invalidates its code signature and
        # makes ensurepip abort before the wheel can be tested.
        venv.EnvBuilder(
            with_pip=True,
            clear=True,
            symlinks=os.name != "nt",
        ).create(environment)
        python = _venv_python(environment)
        workspace = root / "workspace"
        workspace.mkdir()
        _run(
            [str(python), "-m", "pip", "install", str(wheel.resolve())],
            cwd=workspace,
        )
        _run(
            [
                str(python),
                "-c",
                (
                    "from importlib.metadata import version; "
                    "assert version('mcp').startswith('1.'), version('mcp'); "
                    "from importlib.resources import files; "
                    "assert files('core.application.goal_prompts')"
                    ".joinpath('continuation.md').read_text(encoding='utf-8').strip(); "
                    "from core.skills.runtime import SkillRuntime; "
                    "assert {record.name for record in "
                    "SkillRuntime('.', include_user=False).catalog().active()} "
                    "== {'frontend-design', 'mcp-builder', "
                    "'review-agent', 'security-best-practices', "
                    "'security-ownership-map', 'security-threat-model', "
                    "'skill-creator', 'webapp-testing'}; "
                    "from cli.mcp_server import build_server; build_server()"
                ),
            ],
            cwd=workspace,
        )
        _run([str(python), "-m", "deepcode", "--help"], cwd=workspace)
        _run(
            [str(python), "-m", "app_server", "--verify-runtime"],
            cwd=workspace,
        )
        _run(
            [str(python), "-m", "deepcode", "mcp"],
            cwd=workspace,
            stdin=subprocess.DEVNULL,
            timeout=30,
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Verify DeepCode Python artifacts and their installed runtime."
    )
    parser.add_argument("--dist-dir", type=Path, default=Path("dist"))
    parser.add_argument(
        "--release-tag",
        help="Require an exact vX.Y.Z match with core/version.py.",
    )
    parser.add_argument(
        "--metadata-only",
        action="store_true",
        help="Skip the clean-environment installation smoke test.",
    )
    args = parser.parse_args(argv)
    artifacts = verify_artifacts(
        args.dist_dir.expanduser().resolve(),
        release_tag=args.release_tag,
    )
    if not args.metadata_only:
        smoke_installed_wheel(artifacts.wheel)
    print(
        f"Verified {PACKAGE_NAME} {artifacts.version}: "
        f"{artifacts.wheel.name}, {artifacts.sdist.name}"
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (DistributionVerificationError, InvalidVersion) as exc:
        raise SystemExit(f"error: {exc}") from exc
