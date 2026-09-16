"""Pin a bundled service outside an updater-owned Desktop installation."""

from __future__ import annotations

import hashlib
import os
import shutil
import sys
import tempfile
from pathlib import Path

from core.config import deepcode_home
from core.file_lock import exclusive_file_lock
from core.private_storage import ensure_private_directory
from core.version import __version__


def pinned_service_executable() -> Path:
    """Publish a complete immutable onedir copy, without modifying older versions."""
    executable = Path(sys.executable).absolute()
    source = executable.parent
    if not (source / "_internal").is_dir():
        raise RuntimeError("The complete onedir App Server bundle is required")
    # The executable includes the Python code; the manifest identifies separate
    # frontend resources. Versioned copies survive Desktop updater replacement.
    digest = hashlib.sha256(executable.read_bytes())
    manifest = source / "_internal" / "app_server" / "web_assets" / "web-build.json"
    digest.update(manifest.read_bytes())
    root = ensure_private_directory(deepcode_home() / "runtimes")
    destination = root / f"{__version__}-{digest.hexdigest()[:24]}"
    target = destination / executable.name
    if source.resolve() == destination.resolve():
        return executable
    with exclusive_file_lock(root / "install.lock"):
        if target.is_file() and (destination / ".complete").is_file():
            return target
        if destination.exists():
            raise RuntimeError(
                "Incomplete pinned service installation; inspect the runtime directory"
            )
        staging = Path(tempfile.mkdtemp(prefix=".install-", dir=root))
        try:
            shutil.copytree(source, staging, dirs_exist_ok=True, symlinks=True)
            (staging / ".complete").write_text(digest.hexdigest(), encoding="ascii")
            os.rename(staging, destination)
        finally:
            if staging.exists():
                shutil.rmtree(staging)
    return target
