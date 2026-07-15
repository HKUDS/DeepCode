"""``deepcode init`` — one-time setup so ``deepcode`` runs in any directory.

DeepCode resolves its config like Codex / Claude Code: a user-level base at
``deepcode_home()`` (``$DEEPCODE_HOME`` or ``~/.deepcode``) that is independent
of the current directory, deep-merged with an optional project-level file. The
base is where a provider key lives, so once it exists the ``deepcode`` command
works from anywhere.

This command creates that base for the user instead of asking them to copy
files by hand. It is a single Python implementation that adapts to Windows,
macOS and Linux (``pathlib.Path.home()`` resolves ``%USERPROFILE%`` / ``$HOME``
uniformly; the printed shell hints are tailored per OS). It never overwrites an
existing config unless ``--force`` is given, so re-running it is safe.
"""

from __future__ import annotations

import argparse
import os
import platform
import shutil
from pathlib import Path

from core.config import (
    DEEPCODE_HOME_ENV,
    default_config_path,
    deepcode_home,
    home_config_path,
    load_config,
)

_REPO_ROOT = Path(__file__).resolve().parents[1]
_TEMPLATE = _REPO_ROOT / "deepcode_config.json.example"


def _pick_source(from_path: str | None) -> tuple[Path, str]:
    """Where to seed the home config from, and a human label for it.

    Preference: an explicit ``--from`` path; else the project config walked up
    from the cwd (so running ``deepcode init`` inside a configured checkout
    lifts that working config — keys and all — to the home base); else the
    packaged template (the user then fills in a key).
    """
    if from_path:
        src = Path(from_path).expanduser().resolve()
        if not src.is_file():
            raise FileNotFoundError(f"--from path does not exist: {src}")
        return src, str(src)

    project = default_config_path()
    if project.is_file() and project != home_config_path():
        return project, f"{project} (current project config)"

    if _TEMPLATE.is_file():
        return _TEMPLATE, f"{_TEMPLATE.name} (template — add a provider key next)"

    raise FileNotFoundError(
        "No source config found: pass --from <path>, run from a directory that "
        f"has a deepcode_config.json, or restore {_TEMPLATE.name}."
    )


def _has_provider_key() -> bool:
    """True if the home config on disk resolves to at least one usable provider."""
    try:
        cfg = load_config(config_path=home_config_path())
    except Exception:
        return False
    return cfg.get_provider() is not None


def _launch_hint() -> str:
    """OS-tailored one-liner on how the home base is located going forward."""
    system = platform.system()
    home = deepcode_home()
    custom = os.environ.get(DEEPCODE_HOME_ENV)
    if custom:
        return f"Config base is {home} (from {DEEPCODE_HOME_ENV})."
    if system == "Windows":
        return (
            f"Config base is {home}. To relocate it, set the {DEEPCODE_HOME_ENV} "
            f"environment variable, e.g.  setx {DEEPCODE_HOME_ENV} D:\\deepcode"
        )
    return (
        f"Config base is {home}. To relocate it, export {DEEPCODE_HOME_ENV}, "
        f'e.g.  export {DEEPCODE_HOME_ENV}="$HOME/.config/deepcode"'
    )


def run(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="deepcode init",
        description="Create the user-level config so deepcode runs in any directory.",
    )
    parser.add_argument(
        "--from", dest="from_path", metavar="PATH", default=None,
        help="Seed the home config from this file instead of auto-detecting.",
    )
    parser.add_argument(
        "--force", action="store_true",
        help="Overwrite an existing home config (a .bak backup is kept).",
    )
    args = parser.parse_args(argv)

    home = deepcode_home()
    dest = home_config_path()
    home.mkdir(parents=True, exist_ok=True)

    if dest.is_file() and not args.force:
        keyed = _has_provider_key()
        print(f"Already configured: {dest}")
        if keyed:
            print("A provider key is present — deepcode is ready in any directory.")
        else:
            print("No provider key resolved yet — edit the file and add one under "
                  '"providers", then you are set.')
        print(_launch_hint())
        print("(Re-run with --force to reseed from another source.)")
        return 0

    try:
        source, label = _pick_source(args.from_path)
    except FileNotFoundError as exc:
        print(f"Error: {exc}")
        return 1

    if dest.is_file() and args.force:
        backup = dest.with_suffix(dest.suffix + ".bak")
        shutil.copyfile(dest, backup)
        print(f"Backed up existing config to {backup}")

    shutil.copyfile(source, dest)
    if os.name == "posix":  # config holds secrets — make it user-only on unix
        os.chmod(dest, 0o600)

    print(f"Wrote {dest}")
    print(f"  seeded from: {label}")
    if _has_provider_key():
        print("A provider key is present — you can now run `deepcode` from ANY directory.")
    else:
        print('No provider key yet: open the file and fill in one under "providers" '
              "(inline value or a ${ENV_VAR} reference).")
    print(_launch_hint())
    return 0
