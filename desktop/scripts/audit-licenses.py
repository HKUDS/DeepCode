"""Audit licenses across the packaged Python, Node, and Rust dependency graphs."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

DESKTOP_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = DESKTOP_ROOT.parent
SIDECAR_ENV = DESKTOP_ROOT / "build" / "sidecar" / ".venv"
BUILD_ONLY_PYTHON = {
    "pip",
    "pyinstaller",
    "pyinstaller-hooks-contrib",
    "setuptools",
}
FORBIDDEN_LICENSE_MARKERS = (
    "AGPL",
    "BUSL",
    "COMMONS CLAUSE",
    "SSPL",
)


def _sidecar_python() -> Path:
    relative = "Scripts/python.exe" if os.name == "nt" else "bin/python"
    candidate = SIDECAR_ENV / relative
    if not candidate.is_file():
        raise RuntimeError(
            "the sidecar environment is missing; run npm run setup:sidecar first"
        )
    return candidate


def _python_packages() -> list[dict[str, str]]:
    probe = r"""
import json
from importlib.metadata import distributions

rows = []
for dist in distributions():
    name = dist.metadata.get("Name") or "unknown"
    license_value = (
        dist.metadata.get("License-Expression")
        or dist.metadata.get("License")
        or ""
    ).strip()
    if not license_value:
        classifiers = [
            item.removeprefix("License :: ").strip()
            for item in dist.metadata.get_all("Classifier", [])
            if item.startswith("License :: ")
        ]
        license_value = "; ".join(classifiers)
    rows.append({
        "name": name,
        "version": dist.version,
        "license": license_value,
    })
print(json.dumps(rows))
"""
    completed = subprocess.run(
        [str(_sidecar_python()), "-c", probe],
        check=True,
        capture_output=True,
        text=True,
    )
    rows = json.loads(completed.stdout)
    for row in rows:
        row["scope"] = (
            "build" if row["name"].lower() in BUILD_ONLY_PYTHON else "runtime"
        )
    return sorted(rows, key=lambda row: (row["name"].lower(), row["version"]))


def _node_packages() -> list[dict[str, str]]:
    lock = json.loads((DESKTOP_ROOT / "package-lock.json").read_text(encoding="utf-8"))
    packages: dict[tuple[str, str], dict[str, str]] = {}
    for path, metadata in lock.get("packages", {}).items():
        if not path.startswith("node_modules/"):
            continue
        name = metadata.get("name") or path.rsplit("node_modules/", 1)[-1]
        version = str(metadata.get("version") or "")
        key = (str(name), version)
        packages[key] = {
            "name": str(name),
            "version": version,
            "license": str(metadata.get("license") or ""),
            "scope": "development" if metadata.get("dev") is True else "runtime",
        }
    return sorted(packages.values(), key=lambda row: (row["name"], row["version"]))


def _rust_packages() -> list[dict[str, str]]:
    completed = subprocess.run(
        ["cargo", "metadata", "--format-version", "1", "--locked"],
        cwd=DESKTOP_ROOT / "src-tauri",
        check=True,
        capture_output=True,
        text=True,
    )
    metadata = json.loads(completed.stdout)
    packages = []
    for package in metadata["packages"]:
        if package["name"] == "deepcode-desktop":
            continue
        packages.append(
            {
                "name": package["name"],
                "version": package["version"],
                "license": package.get("license") or "",
                "scope": "runtime",
            }
        )
    return sorted(packages, key=lambda row: (row["name"], row["version"]))


def _violations(ecosystem: str, packages: list[dict[str, str]]) -> list[str]:
    violations: list[str] = []
    for package in packages:
        name = package["name"]
        license_value = package["license"].strip()
        if not license_value:
            violations.append(f"{ecosystem}:{name} has no declared license")
            continue
        upper = license_value.upper()
        forbidden = next(
            (marker for marker in FORBIDDEN_LICENSE_MARKERS if marker in upper),
            None,
        )
        if forbidden:
            violations.append(
                f"{ecosystem}:{name} uses forbidden license marker {forbidden}"
            )
    return violations


def _write_report(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    ecosystems = {
        "python": _python_packages(),
        "node": _node_packages(),
        "rust": _rust_packages(),
    }
    violations = [
        violation
        for ecosystem, packages in ecosystems.items()
        for violation in _violations(ecosystem, packages)
    ]
    report = {
        "formatVersion": 1,
        "policy": {
            "forbiddenMarkers": list(FORBIDDEN_LICENSE_MARKERS),
            "pyinstallerExceptionReviewed": True,
        },
        "ecosystems": ecosystems,
        "violations": violations,
    }
    if args.output:
        _write_report(args.output.resolve(), report)
    counts = ", ".join(
        f"{ecosystem}={len(packages)}" for ecosystem, packages in ecosystems.items()
    )
    print(f"License audit: {counts}")
    if violations:
        for violation in violations:
            print(violation, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
