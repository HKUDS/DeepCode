"""Tests for the P1-1 append-only transparency log (hash chain).

What is being protected: a malicious model "router" between the harness and
the provider can rewrite the tool calls we get back and read every credential
we send. The client cannot prove the relay is honest, but it can make its own
record of what happened tamper-evident. These tests pin down the three
properties the forensic answer depends on:

* appended entries form a verifiable chain (and a fresh process resumes it);
* editing, deleting, or reordering a line is detected and *located*;
* the whole thing is inert when unused — an empty or missing file, or a
  corrupt tail, must never break logging.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.observability import bus
from core.observability.bus import log_llm_call
from core.observability.context import task_scope
from core.observability.records import (
    LLMLogRecord,
    canonical_json,
    compute_entry_hash,
    sha256_hex,
)

_VERIFIER_PATH = ROOT / "scripts" / "verify_transparency_log.py"


def _load_verifier():
    """Import the CLI script by path — ``scripts/`` is not a package.

    The module must be registered in ``sys.modules`` before execution: the
    ``@dataclass`` decorator looks its class's module up there (only when the
    script runs as ``__main__``, as it normally does, is that automatic).
    """
    spec = importlib.util.spec_from_file_location(
        "verify_transparency_log", _VERIFIER_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


VERIFIER = _load_verifier()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clean_chain_state():
    """Drop cached chain tails so each test starts from a cold process."""
    bus.reset_transparency_chain()
    yield
    bus.reset_transparency_chain()


def _llm_path(tmp_path: Path) -> Path:
    return tmp_path / "logs" / "llm.jsonl"


def _emit(tmp_path: Path, task_id: str, **kwargs: Any) -> None:
    """Write one LLM record through the real public path."""
    bus.set_task_dir(task_id, tmp_path)
    with task_scope(task_id, f"sess-{task_id}"):
        log_llm_call(
            provider="openai",
            model="gpt-x",
            phase="act",
            duration_ms=7,
            response=kwargs.pop("response", "hello"),
            **kwargs,
        )


def _entries(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _rewrite_lines(path: Path, lines: list[str]) -> None:
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _raw_lines(path: Path) -> list[str]:
    return [
        line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]


# ---------------------------------------------------------------------------
# (a) two appended records chain correctly and the verifier passes
# ---------------------------------------------------------------------------


def test_two_appended_records_chain_and_verify(tmp_path):
    _emit(tmp_path, "t1", response="first")
    _emit(tmp_path, "t1", response="second")
    path = _llm_path(tmp_path)
    entries = _entries(path)

    assert len(entries) == 2
    # Genesis: the first entry chains to the empty string, which is what makes
    # line 1 verifiable with no prior file.
    assert entries[0]["prev_hash"] == ""
    assert entries[1]["prev_hash"] == entries[0]["entry_hash"]
    assert entries[0]["entry_hash"] != entries[1]["entry_hash"]

    result = VERIFIER.verify_file(path)
    assert result.ok, result.problems
    assert result.checked == 2
    assert result.first_timestamp is not None
    assert result.last_timestamp is not None
    assert result.first_timestamp <= result.last_timestamp


def test_entry_hash_matches_manual_computation(tmp_path):
    _emit(tmp_path, "t1", response="payload")
    entry = _entries(_llm_path(tmp_path))[0]
    payload = {k: v for k, v in entry.items() if k != "entry_hash"}
    assert entry["entry_hash"] == compute_entry_hash(payload, "")
    # The digest must be over canonical bytes, not repr/insertion order.
    assert canonical_json({"b": 1, "a": 2}) == '{"a":2,"b":1}'


def test_response_and_tool_call_digests_cover_full_content(tmp_path):
    body = "x" * 5000  # longer than the 2000-char preview cap
    _emit(
        tmp_path,
        "t1",
        response=body,
        tool_calls=[{"id": "c1", "name": "bash", "arguments": {"cmd": "ls"}}],
    )
    entry = _entries(_llm_path(tmp_path))[0]

    assert "truncated" in entry["response_preview"]
    assert entry["response_sha256"] == sha256_hex(body)
    assert entry["response_sha256"] != sha256_hex(entry["response_preview"])
    assert entry["tool_calls_sha256"] == sha256_hex(
        [{"id": "c1", "name": "bash", "arguments": {"cmd": "ls"}}]
    )


def test_endpoint_fields_are_persisted(tmp_path):
    _emit(
        tmp_path,
        "t1",
        endpoint_host="relay.example:8443",
        endpoint_class="proxy",
        request_nonce="nonce-1",
    )
    entry = _entries(_llm_path(tmp_path))[0]
    assert entry["endpoint_host"] == "relay.example:8443"
    assert entry["endpoint_class"] == "proxy"
    assert entry["request_nonce"] == "nonce-1"


def test_sha256_hex_is_none_only_for_none():
    assert sha256_hex(None) is None
    # An empty response was still observed, so it has a digest.
    assert sha256_hex("") == sha256_hex("")
    # Key order must not change the tool-call digest.
    a = sha256_hex([{"name": "t", "arguments": {"a": 1, "b": 2}}])
    b = sha256_hex([{"arguments": {"b": 2, "a": 1}, "name": "t"}])
    assert a == b


# ---------------------------------------------------------------------------
# (b) tampering is detected and the offending line is named
# ---------------------------------------------------------------------------


def test_tampering_with_one_field_fails_and_names_the_line(tmp_path):
    _emit(tmp_path, "t1", response="one")
    _emit(tmp_path, "t1", response="two")
    _emit(tmp_path, "t1", response="three")
    path = _llm_path(tmp_path)

    lines = _raw_lines(path)
    tampered = json.loads(lines[1])
    tampered["model"] = "attacker-model"  # rewrite what the entry claims to be
    lines[1] = json.dumps(tampered, ensure_ascii=False)
    _rewrite_lines(path, lines)

    result = VERIFIER.verify_file(path)
    assert not result.ok
    assert [p.line_no for p in result.problems] == [2]
    assert "entry_hash mismatch" in result.problems[0].reason

    assert VERIFIER.main([str(path)]) == 1


def test_tampering_cli_reports_path_and_line(tmp_path, capsys):
    _emit(tmp_path, "t1", response="one")
    _emit(tmp_path, "t1", response="two")
    path = _llm_path(tmp_path)

    lines = _raw_lines(path)
    entry = json.loads(lines[1])
    entry["response_preview"] = "something else entirely"
    lines[1] = json.dumps(entry, ensure_ascii=False)
    _rewrite_lines(path, lines)

    assert VERIFIER.main([str(path)]) == 1
    captured = capsys.readouterr()
    assert f"{path}:2:" in captured.err
    assert "FAIL" in captured.err


def test_deleted_line_breaks_the_linkage(tmp_path):
    _emit(tmp_path, "t1", response="one")
    _emit(tmp_path, "t1", response="two")
    _emit(tmp_path, "t1", response="three")
    path = _llm_path(tmp_path)

    lines = _raw_lines(path)
    del lines[1]  # excise the middle entry
    _rewrite_lines(path, lines)

    result = VERIFIER.verify_file(path)
    assert not result.ok
    assert [p.line_no for p in result.problems] == [2]
    assert "prev_hash does not match" in result.problems[0].reason


def test_reordered_lines_are_detected(tmp_path):
    _emit(tmp_path, "t1", response="one")
    _emit(tmp_path, "t1", response="two")
    path = _llm_path(tmp_path)

    lines = _raw_lines(path)
    lines[0], lines[1] = lines[1], lines[0]
    _rewrite_lines(path, lines)

    result = VERIFIER.verify_file(path)
    assert not result.ok
    assert result.problems[0].line_no == 1


def test_unchained_or_corrupt_lines_are_reported(tmp_path):
    _emit(tmp_path, "t1", response="one")
    path = _llm_path(tmp_path)

    lines = _raw_lines(path)
    lines.append("{not json at all")
    lines.append(json.dumps({"timestamp": "2026-01-01T00:00:00+00:00"}))
    _rewrite_lines(path, lines)

    result = VERIFIER.verify_file(path)
    by_line: dict[int, list[str]] = {}
    for problem in result.problems:
        by_line.setdefault(problem.line_no, []).append(problem.reason)
    assert any("not valid JSON" in r for r in by_line[2])
    # A bare object has neither a digest nor a link, and says so per line.
    assert any("missing entry_hash" in r for r in by_line[3])
    assert any("missing prev_hash" in r for r in by_line[3])


# ---------------------------------------------------------------------------
# (c) empty / missing files are handled
# ---------------------------------------------------------------------------


def test_empty_file_verifies_clean(tmp_path, capsys):
    path = tmp_path / "empty.jsonl"
    path.write_text("", encoding="utf-8")

    result = VERIFIER.verify_file(path)
    assert result.ok
    assert result.checked == 0
    assert result.first_timestamp is None and result.last_timestamp is None

    assert VERIFIER.main([str(path)]) == 0
    assert "0 entries" in capsys.readouterr().out


def test_absent_file_is_reported_without_crashing(tmp_path, capsys):
    missing = tmp_path / "nope.jsonl"
    assert VERIFIER.main([str(missing)]) == 2
    captured = capsys.readouterr()
    assert "does not exist" in captured.err


def test_directory_argument_is_rejected(tmp_path, capsys):
    assert VERIFIER.main([str(tmp_path)]) == 2
    assert "is a directory" in capsys.readouterr().err


def test_cli_runs_as_a_script_from_the_repo_root(tmp_path):
    _emit(tmp_path, "t1", response="one")
    _emit(tmp_path, "t1", response="two")
    path = _llm_path(tmp_path)

    proc = subprocess.run(
        [sys.executable, str(_VERIFIER_PATH), str(path)],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert "verified 2 entries" in proc.stdout


# ---------------------------------------------------------------------------
# (d) the chain survives a process restart
# ---------------------------------------------------------------------------


def test_chain_recovers_tail_after_cache_reset(tmp_path):
    _emit(tmp_path, "t1", response="one")
    _emit(tmp_path, "t1", response="two")
    path = _llm_path(tmp_path)
    before = _entries(path)

    # A fresh process has no in-memory tail: it must recover it from the file.
    bus.reset_transparency_chain()
    _emit(tmp_path, "t1", response="three")

    entries = _entries(path)
    assert len(entries) == 3
    assert entries[2]["prev_hash"] == before[1]["entry_hash"]
    assert VERIFIER.verify_file(path).ok


def test_chain_resumes_across_a_real_subprocess(tmp_path):
    """Same as above but with a genuinely new interpreter, none of our state."""
    _emit(tmp_path, "t1", response="one")
    path = _llm_path(tmp_path)

    script = (
        "import sys; sys.path.insert(0, sys.argv[1]);"
        "from core.observability import bus;"
        "from core.observability.bus import log_llm_call;"
        "from core.observability.context import task_scope;"
        "bus.set_task_dir('t1', sys.argv[2]);"
        "ctx = task_scope('t1', 's1'); ctx.__enter__();"
        "log_llm_call(provider='openai', model='gpt-x', response='two')"
    )
    proc = subprocess.run(
        [sys.executable, "-c", script, str(ROOT), str(tmp_path)],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    assert proc.returncode == 0, proc.stderr

    entries = _entries(path)
    assert len(entries) == 2
    assert entries[1]["prev_hash"] == entries[0]["entry_hash"]
    assert VERIFIER.verify_file(path).ok


def test_concurrent_threads_do_not_fork_the_chain(tmp_path):
    """Recover + hash + append must be atomic, or two entries share a parent."""
    bus.set_task_dir("t-conc", tmp_path)

    def worker(index: int) -> None:
        with task_scope("t-conc", "s1"):
            log_llm_call(provider="openai", model="gpt-x", response=f"r{index}")

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(worker, range(40)))

    path = _llm_path(tmp_path)
    assert len(_entries(path)) == 40
    assert VERIFIER.verify_file(path).ok


def test_corrupt_tail_is_skipped_without_breaking_logging(tmp_path):
    _emit(tmp_path, "t1", response="one")
    _emit(tmp_path, "t1", response="two")
    path = _llm_path(tmp_path)
    second_hash = _entries(path)[1]["entry_hash"]

    # Crash-mid-write simulation: a truncated/partial line with no newline.
    with path.open("a", encoding="utf-8") as fh:
        fh.write('{"timestamp":"2026-01-01T00:00:00+00:00","entry_ha')

    bus.reset_transparency_chain()
    _emit(tmp_path, "t1", response="three")  # must not raise

    raw = _raw_lines(path)
    assert len(raw) == 4  # two entries, the partial line, the new entry
    resumed = json.loads(raw[3])
    assert resumed["prev_hash"] == second_hash
    # Logging survived, and the verifier still reports the gap instead of
    # pretending the file is fine.
    result = VERIFIER.verify_file(path)
    assert not result.ok
    assert any("not valid JSON" in p.reason for p in result.problems)


# ---------------------------------------------------------------------------
# Backward compatibility: nothing changes when the feature is unused
# ---------------------------------------------------------------------------


def test_make_keeps_legacy_signature_working():
    record = LLMLogRecord.make(
        task_id="t",
        session_id="s",
        provider="p",
        model="m",
        phase=None,
        duration_ms=1,
        status="ok",
    )
    for field_name in (
        "endpoint_host",
        "endpoint_class",
        "request_nonce",
        "response_sha256",
        "tool_calls_sha256",
        "prev_hash",
        "entry_hash",
    ):
        assert getattr(record, field_name) is None
    # Unchained records serialise exactly as before — no new keys appear.
    payload = json.loads(record.to_jsonl())
    assert "prev_hash" not in payload
    assert "entry_hash" not in payload


def test_mcp_records_are_not_chained(tmp_path):
    """Only LLM entries participate; MCP format must stay untouched."""
    bus.set_task_dir("t-mcp", tmp_path)
    with task_scope("t-mcp", "s1"):
        bus.log_mcp_call(server="srv", tool="tool", duration_ms=1, result="ok")
    entries = _entries(tmp_path / "logs" / "mcp.jsonl")
    assert len(entries) == 1
    assert "entry_hash" not in entries[0]
    assert "prev_hash" not in entries[0]
