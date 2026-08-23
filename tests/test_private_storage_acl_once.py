"""Cross-platform tests for the per-open ACL optimization in private_storage.

The Windows ACL restriction is applied at file *creation*; opening an
existing private file must not re-run icacls (maintainer feedback on the
earlier ACL PR: "open_private_file() currently calls it on each call; once at
creation is enough"). These tests mock `_restrict_windows_acl` to count calls,
so they run on any platform.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.private_storage import open_private_file


def _file_calls(calls, target: Path) -> int:
    """Count restrictions applied to the target file itself (excludes the
    parent-directory restriction that ensure_private_directory performs)."""
    return sum(1 for p in calls if Path(p) == target)


def test_open_existing_file_does_not_rerun_acl(monkeypatch, tmp_path: Path) -> None:
    import core.private_storage as ps

    calls = []
    monkeypatch.setattr(ps, "_restrict_windows_acl", lambda p: calls.append(p))

    target = tmp_path / "existing.jsonl"
    # First open: file does not exist → new → restrict once.
    fd = open_private_file(target, os.O_CREAT | os.O_RDWR)
    os.close(fd)
    assert _file_calls(calls, target) == 1, "new file must be restricted exactly once"

    # Second open: file exists → must NOT re-run the ACL restriction.
    fd = open_private_file(target, os.O_RDWR)
    os.close(fd)
    assert _file_calls(calls, target) == 1, (
        "existing file must not re-run the ACL restriction"
    )


def test_open_created_file_restricts_once(monkeypatch, tmp_path: Path) -> None:
    import core.private_storage as ps

    calls = []
    monkeypatch.setattr(ps, "_restrict_windows_acl", lambda p: calls.append(p))

    target = tmp_path / "fresh.jsonl"
    for _ in range(3):
        fd = open_private_file(target, os.O_CREAT | os.O_RDWR)
        os.close(fd)
    assert _file_calls(calls, target) == 1, "created once, restricted once, never again"


def test_open_without_creat_never_restricts(monkeypatch, tmp_path: Path) -> None:
    import core.private_storage as ps

    calls = []
    monkeypatch.setattr(ps, "_restrict_windows_acl", lambda p: calls.append(p))

    target = tmp_path / "pre.jsonl"
    target.write_text("x", encoding="utf-8")
    # O_RDONLY (no O_CREAT) on an existing file → no new file → no restriction.
    fd = open_private_file(target, os.O_RDONLY)
    os.close(fd)
    assert _file_calls(calls, target) == 0, (
        "read-only open of an existing file must not restrict"
    )


def test_concurrent_creator_is_not_mistaken_for_our_new_file(
    monkeypatch, tmp_path: Path
) -> None:
    import core.private_storage as ps

    target = tmp_path / "raced.jsonl"
    original_open = os.open
    restrictions = []
    raced = False

    def racing_open(path, flags, mode=0o777):
        nonlocal raced
        if Path(path) == target and flags & os.O_EXCL and not raced:
            raced = True
            other = original_open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, mode)
            os.close(other)
            raise FileExistsError(os.fspath(target))
        return original_open(path, flags, mode)

    monkeypatch.setattr(ps.os, "open", racing_open)
    monkeypatch.setattr(
        ps, "_restrict_windows_acl", lambda path: restrictions.append(Path(path))
    )

    descriptor = ps.open_private_file(target, os.O_CREAT | os.O_RDWR)
    os.close(descriptor)

    assert raced is True
    assert target not in restrictions


def test_acl_restriction_orders_grant_strip_and_broad_principal_removal(
    monkeypatch, tmp_path: Path
) -> None:
    import core.private_storage as ps

    calls = []
    monkeypatch.setattr(ps, "_windows_identity", lambda: "DOMAIN\\user")
    monkeypatch.setattr(ps, "_windows_icacls", lambda: "trusted-icacls.exe")

    def record(executable, path, *arguments):
        calls.append((executable, Path(path), arguments))
        return True

    monkeypatch.setattr(ps, "_run_icacls", record)

    directory = tmp_path / "private"
    directory.mkdir()
    ps._restrict_windows_acl(directory)

    assert calls[0][2] == ("/grant:r", "DOMAIN\\user:(OI)(CI)F")
    assert calls[1][2] == ("/inheritance:r",)
    assert calls[2][2] == ("/remove", *ps._WINDOWS_BROAD_ACCESS_SIDS)


def test_acl_restriction_stops_before_strip_when_grant_fails(
    monkeypatch, tmp_path: Path
) -> None:
    import core.private_storage as ps

    calls = []
    monkeypatch.setattr(ps, "_windows_identity", lambda: "DOMAIN\\user")
    monkeypatch.setattr(ps, "_windows_icacls", lambda: "trusted-icacls.exe")

    def fail_grant(executable, path, *arguments):
        calls.append(arguments)
        return False

    monkeypatch.setattr(ps, "_run_icacls", fail_grant)

    ps._restrict_windows_acl(tmp_path / "private.json")

    assert calls == [("/grant:r", "DOMAIN\\user:F")]


def test_acl_restriction_does_not_remove_entries_when_strip_fails(
    monkeypatch, tmp_path: Path
) -> None:
    import core.private_storage as ps

    calls = []
    monkeypatch.setattr(ps, "_windows_identity", lambda: "DOMAIN\\user")
    monkeypatch.setattr(ps, "_windows_icacls", lambda: "trusted-icacls.exe")

    def fail_strip(executable, path, *arguments):
        calls.append(arguments)
        return arguments != ("/inheritance:r",)

    monkeypatch.setattr(ps, "_run_icacls", fail_strip)

    ps._restrict_windows_acl(tmp_path / "private.json")

    assert calls == [
        ("/grant:r", "DOMAIN\\user:F"),
        ("/inheritance:r",),
    ]
