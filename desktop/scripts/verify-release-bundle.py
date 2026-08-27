"""Verify platform bundles and the packaged App Server before publication."""

from __future__ import annotations

import argparse
import json
import os
import plistlib
import subprocess
import tempfile
import time
from pathlib import Path

DESKTOP_ROOT = Path(__file__).resolve().parents[1]
TARGET_ROOT = DESKTOP_ROOT / "src-tauri" / "target" / "release"
BUNDLE_ROOT = TARGET_ROOT / "bundle"
SIDECAR_ROOT = DESKTOP_ROOT / "build" / "sidecar" / "dist" / "deepcode-app-server"
NOTICE_NAMES = ("THIRD_PARTY_NOTICES.md", "PRIVACY_AND_DIAGNOSTICS.md")


def _sidecar_binary(root: Path) -> Path:
    candidate = root / (
        "deepcode-app-server.exe" if os.name == "nt" else "deepcode-app-server"
    )
    if not candidate.is_file():
        raise RuntimeError(f"App Server executable is missing: {candidate}")
    return candidate


def _verify_runtime(binary: Path) -> None:
    completed = subprocess.run(
        [str(binary), "--verify-runtime"],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    result = json.loads(completed.stdout)
    if (
        result.get("ok") is not True
        or result.get("skillCreator") is not True
        or not result.get("bundledMcpPresets")
        or "tools.document_conversion" not in result.get("modules", [])
    ):
        raise RuntimeError("packaged App Server runtime probe is incomplete")


def _verify_resource_tree(root: Path) -> Path:
    binary_name = (
        "deepcode-app-server.exe" if os.name == "nt" else "deepcode-app-server"
    )
    sidecars = [
        path
        for path in root.rglob(binary_name)
        if path.parent.name.lower() == "app-server"
    ]
    if len(sidecars) != 1:
        raise RuntimeError(
            f"expected one packaged App Server under {root}, found {len(sidecars)}"
        )
    for notice in NOTICE_NAMES:
        if not any(path.is_file() for path in root.rglob(notice)):
            raise RuntimeError(f"bundle notice is missing: {notice}")
    _verify_runtime(sidecars[0])
    return sidecars[0]


def _single(pattern: str) -> Path:
    candidates = sorted(BUNDLE_ROOT.glob(pattern))
    if len(candidates) != 1:
        raise RuntimeError(
            f"expected one bundle matching {pattern}, found {len(candidates)}"
        )
    candidate = candidates[0]
    if candidate.is_file() and candidate.stat().st_size < 1024 * 1024:
        raise RuntimeError(f"bundle is unexpectedly small: {candidate}")
    return candidate


def _verify_macos_app(app: Path, *, signed: bool, notarized: bool) -> None:
    resources = app / "Contents" / "Resources"
    _verify_resource_tree(resources)
    subprocess.run(
        ["codesign", "--verify", "--deep", "--strict", str(app)],
        check=True,
    )
    if signed:
        details = subprocess.run(
            ["codesign", "-dvv", str(app)],
            check=True,
            capture_output=True,
            text=True,
        )
        if "Signature=adhoc" in details.stderr or "Authority=" not in details.stderr:
            raise RuntimeError("production macOS bundle is only ad-hoc signed")
    if notarized:
        subprocess.run(
            ["spctl", "--assess", "--type", "execute", "--verbose=2", str(app)],
            check=True,
        )


def _verify_dmg(dmg: Path, *, signed: bool, notarized: bool) -> None:
    if notarized:
        subprocess.run(["xcrun", "stapler", "validate", str(dmg)], check=True)
        subprocess.run(
            [
                "spctl",
                "--assess",
                "--type",
                "open",
                "--context",
                "context:primary-signature",
                "--verbose=2",
                str(dmg),
            ],
            check=True,
        )
    attached = subprocess.run(
        ["hdiutil", "attach", "-nobrowse", "-readonly", "-plist", str(dmg)],
        check=True,
        capture_output=True,
    )
    details = plistlib.loads(attached.stdout)
    entities = details.get("system-entities", [])
    mounted = [
        (entity.get("dev-entry"), entity.get("mount-point"))
        for entity in entities
        if entity.get("mount-point")
    ]
    if len(mounted) != 1:
        raise RuntimeError(f"expected one mounted DMG volume, found {len(mounted)}")
    device, mount_point = mounted[0]
    try:
        apps = sorted(Path(mount_point).rglob("*.app"))
        if len(apps) != 1:
            raise RuntimeError(
                f"expected one application in the DMG, found {len(apps)}"
            )
        _verify_macos_app(apps[0], signed=signed, notarized=notarized)
    finally:
        subprocess.run(
            ["hdiutil", "detach", str(device or mount_point)],
            check=True,
        )


def _verify_macos(bundle_type: str, *, signed: bool, notarized: bool) -> list[Path]:
    if bundle_type == "app":
        app = _single("macos/*.app")
        _verify_macos_app(app, signed=signed, notarized=notarized)
        return [app]
    dmg = _single("dmg/*.dmg")
    _verify_dmg(dmg, signed=signed, notarized=notarized)
    return [dmg]


def _archive_listing(command: list[str]) -> str:
    completed = subprocess.run(
        command,
        check=True,
        capture_output=True,
        text=True,
        errors="replace",
    )
    return completed.stdout.replace("\\", "/").lower()


def _verify_deb() -> list[Path]:
    package = _single("deb/*.deb")
    listing = _archive_listing(["dpkg-deb", "--contents", str(package)])
    _require_archive_contents(listing)
    with tempfile.TemporaryDirectory(prefix="deepcode-deb-") as temporary:
        root = Path(temporary)
        subprocess.run(
            ["dpkg-deb", "--extract", str(package), str(root)],
            check=True,
        )
        _verify_resource_tree(root)
    return [package]


def _verify_appimage(*, signed: bool) -> list[Path]:
    appimage = _single("appimage/*.AppImage")
    if signed:
        completed = subprocess.run(
            [str(appimage), "--appimage-signature"],
            check=True,
            capture_output=True,
            text=True,
            errors="replace",
        )
        if not completed.stdout.strip():
            raise RuntimeError("signed AppImage did not expose an embedded signature")
    with tempfile.TemporaryDirectory(prefix="deepcode-appimage-") as temporary:
        root = Path(temporary)
        subprocess.run(
            [str(appimage.resolve()), "--appimage-extract"],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=120,
        )
        extracted = root / "squashfs-root"
        if not extracted.is_dir():
            raise RuntimeError("AppImage extraction did not create squashfs-root")
        _verify_resource_tree(extracted)
    return [appimage]


def _verify_authenticode(path: Path) -> None:
    escaped_path = str(path).replace("'", "''")
    command = (
        "$signature = Get-AuthenticodeSignature -LiteralPath "
        f"'{escaped_path}'; "
        "if ($signature.Status -ne 'Valid') { "
        'throw "invalid Authenticode signature: $($signature.Status)" }'
    )
    subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", command],
        check=True,
    )


def _verify_nsis(*, signed: bool) -> list[Path]:
    installer = _single("nsis/*-setup.exe")
    if signed:
        _verify_authenticode(installer)
    with tempfile.TemporaryDirectory(prefix="deepcode-nsis-") as temporary:
        install_root = Path(temporary) / "DeepCode"
        subprocess.run(
            [str(installer.resolve()), "/S", f"/D={install_root}"],
            check=True,
            timeout=180,
        )
        try:
            _verify_resource_tree(install_root)
        finally:
            uninstallers = sorted(install_root.rglob("uninstall.exe"))
            if uninstallers:
                subprocess.run(
                    [str(uninstallers[0]), "/S"],
                    check=True,
                    timeout=120,
                )
                deadline = time.monotonic() + 30
                while uninstallers[0].exists() and time.monotonic() < deadline:
                    time.sleep(0.1)
                if uninstallers[0].exists():
                    raise RuntimeError("NSIS uninstaller did not finish cleanup")
    return [installer]


def _require_archive_contents(listing: str) -> None:
    required = (
        "app-server/deepcode-app-server",
        "third_party_notices.md",
        "privacy_and_diagnostics.md",
    )
    missing = [value for value in required if value not in listing]
    if missing:
        raise RuntimeError(
            "bundle archive is missing required resources: " + ", ".join(missing)
        )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--bundle",
        choices=("app", "dmg", "deb", "appimage", "nsis"),
        required=True,
    )
    parser.add_argument("--signed", action="store_true")
    parser.add_argument("--notarized", action="store_true")
    args = parser.parse_args()

    source_sidecar = _sidecar_binary(SIDECAR_ROOT)
    _verify_runtime(source_sidecar)
    if args.bundle in {"app", "dmg"}:
        artifacts = _verify_macos(
            args.bundle,
            signed=args.signed,
            notarized=args.notarized,
        )
    elif args.bundle == "deb":
        artifacts = _verify_deb()
    elif args.bundle == "appimage":
        artifacts = _verify_appimage(signed=args.signed)
    else:
        artifacts = _verify_nsis(signed=args.signed)
    print("\n".join(str(path) for path in artifacts))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
