"""Regression tests: sensitive-artifact provenance footer on read (2610.13227 §5.2 proxy).

The Read tool appends a read-time snapshot footer (mtime/size/sha256 head) when
the target matches SENSITIVE_PATH_PATTERNS (credential stores on upstream main),
so a later session sees mutation facts before reusing the artifact. Proxy, not
write-time ledger: writer identity is not recorded. Default on; kill switch
DEEPCODE_PROVENANCE_CONTEXT=0; footer never breaks a read.
"""

from __future__ import annotations

import asyncio

import pytest

from core.harness.tools.files import ReadTool, _provenance_footer


@pytest.fixture()
def workspace(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    return str(ws)


def _read(workspace: str, file_path: str) -> str:
    return asyncio.run(
        ReadTool(workspace).execute(file_path=file_path, offset=1, limit=100)
    )


def test_sensitive_read_gets_footer(workspace):
    p = f"{workspace}/.env"
    with open(p, "w", encoding="utf-8") as f:
        f.write("SECRET=1\n")
    out = _read(workspace, ".env")
    assert "[provenance]" in out
    assert "sha256_64k=" in out
    assert "mtime=" in out
    assert "size=" in out
    assert "2610.13227" in out


def test_normal_read_has_no_footer(workspace):
    p = f"{workspace}/notes.txt"
    with open(p, "w", encoding="utf-8") as f:
        f.write("hello")
    out = _read(workspace, "notes.txt")
    assert "[provenance]" not in out
    assert "hello" in out


def test_kill_switch_env(workspace, monkeypatch):
    monkeypatch.setenv("DEEPCODE_PROVENANCE_CONTEXT", "0")
    p = f"{workspace}/.env"
    with open(p, "w", encoding="utf-8") as f:
        f.write("SECRET=1\n")
    out = _read(workspace, ".env")
    assert "[provenance]" not in out


def test_footer_is_helper_pure_on_missing_file(tmp_path):
    # A vanished path must not raise: helper degrades to "" (footer is
    # best-effort, never breaks the read contract).
    out = _provenance_footer(tmp_path / "no-such.mcp.json", str(tmp_path))
    assert out == ""


def test_footer_empty_for_non_sensitive(workspace):
    p = f"{workspace}/plain.md"
    with open(p, "w", encoding="utf-8") as f:
        f.write("x")
    from pathlib import Path

    assert _provenance_footer(Path(p), workspace) == ""
