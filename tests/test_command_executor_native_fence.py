"""Issue #128: the native fast path in ``tools/command_executor`` runs
in-process, before the sandbox wrapper, so ``rm``/``cp``/``mv`` on operands
outside the working directory executed as plain Python file operations with
no sandbox at all. The fix declines the fast path (``None``) for those, so
the command takes the sandboxed shell route every other command uses.
"""

from __future__ import annotations

import os
import platform
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.command_executor import (
    _try_native_execute,
    execute_command_batch,
    execute_single_command,
)

_seatbelt = platform.system() == "Darwin" and os.path.exists("/usr/bin/sandbox-exec")


@pytest.fixture()
def workspace(tmp_path):
    ws = tmp_path / "file_tree"
    ws.mkdir()
    return ws


def test_native_ops_outside_workspace_declined_inside_still_fast(workspace, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep.txt").write_text("precious")
    (workspace / "a.txt").write_text("x")

    # absolute and ..-escape operands must decline the fast path ...
    assert _try_native_execute(f"rm -rf {outside}", workspace) is None
    assert (outside / "keep.txt").exists()
    assert _try_native_execute("rm -rf ../sibling", workspace) is None
    assert _try_native_execute("cp a.txt ../elsewhere/b.txt", workspace) is None
    assert _try_native_execute("mkdir -p ../newly", workspace) is None
    assert _try_native_execute("touch ../escaped.txt", workspace) is None
    assert not (workspace.parent / "escaped.txt").exists()
    assert not (workspace.parent / "newly").exists()

    # ... while in-workspace operands keep it (over-broad gate would break
    # both assertions' contract: declined vs handled)
    rc, _, err = _try_native_execute("mkdir -p src/nested", workspace)
    assert rc == 0, err
    rc, _, err = _try_native_execute("cp a.txt src/nested/b.txt", workspace)
    assert rc == 0, err
    rc, _, err = _try_native_execute("rm -rf src", workspace)
    assert rc == 0, err
    assert not (workspace / "src").exists()


@pytest.mark.skipif(not _seatbelt, reason="seatbelt sandbox not available")
@pytest.mark.asyncio
async def test_public_executors_outside_workspace_rm_sandboxed(workspace):
    """Through both public tools, a recursive remove targeting the user's
    home (which the sandbox write-fence denies) must survive. Pre-fix the
    native handler deleted it in-process with no sandbox involved."""
    victims = []
    for name in (".deepcode_fence_v1", ".deepcode_fence_v2"):
        victim = Path.home() / name
        victim.mkdir(exist_ok=True)
        (victim / "keep.txt").write_text("precious")
        victims.append(victim)
    try:
        await execute_single_command(f"rm -rf {victims[0]}", str(workspace))
        await execute_command_batch(f"rm -rf {victims[1]}", str(workspace))
        for victim in victims:
            assert (victim / "keep.txt").exists()
    finally:
        for victim in victims:
            if victim.exists():
                for child in victim.iterdir():
                    child.unlink()
                victim.rmdir()
