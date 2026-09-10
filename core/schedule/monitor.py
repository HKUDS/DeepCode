"""Monitor gate — hash-suppressed change detection (P4).

Core idea: before each scheduled tick, run a script or fetch a URL;
SHA-256 the output and compare against the last known hash. When
unchanged, skip the LLM call entirely (silent tick). When changed,
inject a context block (unified diff + new output) for the next LLM
turn.

Design principles (derived from Hermes cron/monitor.py):
1. Hash = SHA-256 of exact UTF-8 bytes, no normalisation (JSON key
   order / whitespace changes ARE changes).
2. Source failure (non-zero exit / URL timeout) is ERROR, never
   treated as a change; stored hash is preserved.
3. No change = silent — do not emit ``changed=False`` output
   (avoids noise).
4. First run (no stored hash) = treated as a change, injects a
   "Monitor Baseline" block.
5. Diff is capped at ``MAX_DIFF_CHARS`` (4K), new output at 8K
   (avoid feeding the delta to the LLM).

Usage::

    from core.schedule.monitor import MonitorStore, monitor_gate

    store = MonitorStore(Path("state.db"))
    outcome = monitor_gate("my-job", store=store,
                           monitor_script="git diff --stat")
    if outcome.changed:
        agent_prompt += outcome.context_block  # inject change info
    elif outcome.error:
        log.warning(outcome.error)             # source failure
    else:
        pass                                   # silent tick, skip LLM
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import subprocess
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from difflib import unified_diff
from pathlib import Path
from urllib.request import Request, urlopen

# ── Constants ────────────────────────────────────────────────────────────────

MAX_DIFF_CHARS = 4000
"""Max characters in the unified-diff injected on change."""
MAX_OUTPUT_CHARS = 8000
"""Max characters of current output injected on change."""
MAX_URL_BYTES = 256 * 1024
"""Max bytes read from a monitored URL (256 KiB)."""
URL_TIMEOUT_SECONDS = 30
"""HTTP timeout for URL monitoring."""
SCRIPT_TIMEOUT_SECONDS = 30
"""Timeout for script monitoring."""
SNAPSHOT_MAX_BYTES = 512 * 1024
"""Max bytes stored as the previous-output snapshot (512 KiB)."""

# ── Outcome dataclass ────────────────────────────────────────────────────────


@dataclass
class MonitorOutcome:
    """Result of one monitor check."""

    changed: bool
    """True when the source output differs from the stored baseline."""
    context_block: str = ""
    """Markdown block to inject (baseline on first run, diff+output on change)."""
    error: str = ""
    """Non-empty when the source could not be read (not a change, stored hash unchanged)."""

    @property
    def ok(self) -> bool:
        """True when no error occurred (even ``changed`` may be False)."""
        return not self.error


# ── Pure functions ───────────────────────────────────────────────────────────


def hash_monitor_output(output: str) -> str:
    """SHA-256 of the exact UTF-8 bytes (no normalisation)."""
    return hashlib.sha256(output.encode("utf-8")).hexdigest()


def build_monitor_diff(old: str, new: str) -> str:
    """Unified diff between two snapshots, truncated at ``MAX_DIFF_CHARS``."""
    diff = "".join(
        unified_diff(
            old.splitlines(keepends=True),
            new.splitlines(keepends=True),
            fromfile="previous",
            tofile="current",
        )
    )
    if len(diff) > MAX_DIFF_CHARS:
        diff = (
            diff[:MAX_DIFF_CHARS] + f"\n... (diff truncated at {MAX_DIFF_CHARS} chars)"
        )
    return diff


# ── Source readers ───────────────────────────────────────────────────────────


def _fetch_monitor_url(url: str) -> tuple[str, str]:
    """Bounded GET fetch; returns ``(body, '')`` on success.

    On failure returns ``('', error_description)``. The body is truncated
    to ``MAX_URL_BYTES``; exceeding the limit is reported as an error.
    """
    try:
        req = Request(url, method="GET")
        with urlopen(req, timeout=URL_TIMEOUT_SECONDS) as resp:
            data = resp.read(MAX_URL_BYTES + 1)
            if len(data) > MAX_URL_BYTES:
                return "", f"Response exceeds {MAX_URL_BYTES} bytes, truncated"
            text = data.decode("utf-8", errors="replace")
            return text, ""
    except Exception as exc:  # noqa: BLE001 — network/IO errors are expected here
        return "", f"URL fetch failed: {exc}"


def _run_monitor_script(script: str) -> tuple[str, str]:
    """Execute a shell script; returns ``(stdout, '')`` on success.

    Non-zero exit and timeouts are reported as errors (not changes).
    stdout is capped at ``SNAPSHOT_MAX_BYTES``.
    """
    try:
        proc = subprocess.run(
            script,
            shell=True,
            capture_output=True,
            text=True,
            timeout=SCRIPT_TIMEOUT_SECONDS,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        if proc.returncode != 0:
            tail = (proc.stderr or "")[:500]
            return "", f"Script exit code {proc.returncode}: {tail}"
        stdout = (proc.stdout or "")[:SNAPSHOT_MAX_BYTES]
        return stdout, ""
    except subprocess.TimeoutExpired:
        return "", f"Script timeout ({SCRIPT_TIMEOUT_SECONDS}s)"
    except Exception as exc:  # noqa: BLE001 — subprocess/OS errors are expected here
        return "", f"Script execution failed: {exc}"


def _read_source(*, monitor_script: str = "", monitor_url: str = "") -> tuple[str, str]:
    """Read the monitored source; ``(content, '')`` on success.

    ``monitor_script`` takes priority when both are given.
    """
    if monitor_script:
        return _run_monitor_script(monitor_script)
    if monitor_url:
        return _fetch_monitor_url(monitor_url)
    return "", "Neither monitor_script nor monitor_url provided"


# ── Core comparison ──────────────────────────────────────────────────────────


def _check_block(
    *,
    changed: bool,
    first_run: bool,
    monitor_url: str = "",
    monitor_script: str = "",
    output: str = "",
    diff: str = "",
) -> str:
    """Build the markdown context block describing a monitor event."""
    source = monitor_url or monitor_script[:200]
    if first_run:
        return (
            "## MONITOR BASELINE (first run)\n\n"
            f"**Monitor**: {source}\n\n"
            f"```\n{output[:MAX_OUTPUT_CHARS]}\n```\n"
        )
    if changed:
        return (
            "## MONITOR CHANGE DETECTED\n\n"
            f"**Monitor**: {source}\n\n"
            "**Diff (previous → current):**\n"
            f"```diff\n{diff}\n```\n\n"
            "**Current output:**\n"
            f"```\n{output[:MAX_OUTPUT_CHARS]}\n```\n"
        )
    return ""


def _evaluate(
    *,
    monitor_script: str = "",
    monitor_url: str = "",
    stored_hash: str = "",
    stored_snapshot: str = "",
) -> tuple[MonitorOutcome, str]:
    """Compare the monitored source against the stored state.

    Returns ``(outcome, current_output)``. This single-fetch design lets
    ``monitor_gate`` persist without calling ``_read_source`` a second time.
    """
    output, error = _read_source(monitor_script=monitor_script, monitor_url=monitor_url)
    if error:
        return MonitorOutcome(changed=False, error=error), ""

    current_hash = hash_monitor_output(output)
    first_run = not stored_hash
    changed = first_run or current_hash != stored_hash

    if changed:
        diff = "" if first_run else build_monitor_diff(stored_snapshot, output)
        block = _check_block(
            changed=changed,
            first_run=first_run,
            monitor_url=monitor_url,
            monitor_script=monitor_script,
            output=output,
            diff=diff,
        )
    else:
        block = ""

    return MonitorOutcome(changed=changed, context_block=block, error=""), output


def check_monitor(
    *,
    monitor_script: str = "",
    monitor_url: str = "",
    stored_hash: str = "",
    stored_snapshot: str = "",
) -> MonitorOutcome:
    """Check whether the monitored source has changed since the stored state.

    This is a **pure** function — no side effects, no persistence. Callers are
    responsible for persisting the new hash/snapshot.

    Returns
    -------
    MonitorOutcome
        - ``changed=True``: source has changed (or first run).
        - ``changed=False, error=''``: source unchanged (silent).
        - ``changed=False, error=...``: source read failed; stored hash should
          **not** be updated.
    """
    return _evaluate(
        monitor_script=monitor_script,
        monitor_url=monitor_url,
        stored_hash=stored_hash,
        stored_snapshot=stored_snapshot,
    )[0]


# ── Persistence ──────────────────────────────────────────────────────────────


class MonitorStore:
    """SQLite-backed persistence for monitor job state.

    The table ``monitor_jobs`` stores per-job: last seen hash, previous-output
    snapshot, and ISO-8601 timestamp.
    """

    def __init__(self, db_path: str | Path) -> None:
        self._db_path = Path(db_path)
        self._init_db()

    # ----- helpers -----------------------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._db_path), timeout=10.0)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout = 5000")
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = self._connect()
        try:
            conn.execute(
                """CREATE TABLE IF NOT EXISTS monitor_jobs (
                    job_id       TEXT PRIMARY KEY,
                    monitor_hash TEXT NOT NULL DEFAULT '',
                    snapshot     TEXT NOT NULL DEFAULT '',
                    updated_at   TEXT NOT NULL
                )"""
            )
            conn.commit()
        finally:
            conn.close()

    # ----- public API --------------------------------------------------------

    def get_job(self, job_id: str) -> dict | None:
        """Retrieve the stored state for *job_id*, or ``None`` if unknown."""
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT * FROM monitor_jobs WHERE job_id=?", (job_id,)
            ).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def save_job(
        self,
        job_id: str,
        monitor_hash: str,
        snapshot: str,
        updated_at: str | None = None,
    ) -> None:
        """Persist (or upsert) the state for *job_id*.

        Parameters
        ----------
        monitor_hash :
            The SHA-256 hash of the output.
        snapshot :
            The full output (trimmed to ``SNAPSHOT_MAX_BYTES``).
        updated_at :
            ISO-8601 timestamp; defaults to ``datetime.now(timezone.utc)``.
        """
        if updated_at is None:
            updated_at = datetime.now(UTC).isoformat(timespec="milliseconds")
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                """INSERT INTO monitor_jobs(job_id, monitor_hash, snapshot, updated_at)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(job_id) DO UPDATE SET
                       monitor_hash=excluded.monitor_hash,
                       snapshot=excluded.snapshot,
                       updated_at=excluded.updated_at""",
                (job_id, monitor_hash, snapshot[:SNAPSHOT_MAX_BYTES], updated_at),
            )
            conn.commit()
        finally:
            conn.close()


# ── Combined entry point ─────────────────────────────────────────────────────


def monitor_gate(
    job_id: str,
    *,
    monitor_script: str = "",
    monitor_url: str = "",
    store: MonitorStore,
) -> MonitorOutcome:
    """High-level check: load persisted state, check, persist new state.

    This is the primary entry point for production use. It:

    1. Loads the previous hash/snapshot from *store*.
    2. Fetches the current source output exactly once.
    3. Compares hashes.
    4. Persists the new hash/snapshot.
    5. Returns the outcome.

    Parameters
    ----------
    job_id :
        Unique identifier for the monitored job (used as the DB key).
    monitor_script :
        Shell command to run; takes priority when both *monitor_script* and
        *monitor_url* are given.
    monitor_url :
        URL to fetch.
    store :
        Persistence backend (required, no default).
    """
    prev = store.get_job(job_id) or {}
    outcome, output = _evaluate(
        monitor_script=monitor_script,
        monitor_url=monitor_url,
        stored_hash=prev.get("monitor_hash", ""),
        stored_snapshot=prev.get("snapshot", ""),
    )

    if outcome.error:
        return outcome  # source read failed — do not touch stored state

    store.save_job(job_id, hash_monitor_output(output), output)
    return outcome


# ── CLI ──────────────────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    """CLI entry point::

    python -m core.schedule.monitor --job-id my-job --script "ls -la"
    python -m core.schedule.monitor --job-id price --url "https://api.example.com/price"
    """
    parser = argparse.ArgumentParser(description="Monitor gate — change detection")
    parser.add_argument("--job-id", required=True)
    parser.add_argument("--script", default="", help="Shell command to monitor")
    parser.add_argument("--url", default="", help="URL to monitor")
    parser.add_argument("--db", default="", help="Path to monitor_state.db")
    parser.add_argument("--json", action="store_true", help="JSON output")

    args = parser.parse_args(argv)

    store = (
        MonitorStore(args.db)
        if args.db
        else MonitorStore(Path.cwd() / "monitor_state.db")
    )
    outcome = monitor_gate(
        job_id=args.job_id,
        monitor_script=args.script,
        monitor_url=args.url,
        store=store,
    )

    if args.json:
        print(
            json.dumps(
                {
                    "changed": outcome.changed,
                    "context_block": outcome.context_block,
                    "error": outcome.error,
                },
                ensure_ascii=False,
            )
        )
    else:
        if outcome.error:
            print(f"[monitor] ERROR: {outcome.error}", file=sys.stderr)
            return 1
        if not outcome.changed:
            print(f"[monitor] no_change: {args.job_id}")
        else:
            print(f"[monitor] changed: {args.job_id}")
            if outcome.context_block:
                print(outcome.context_block)
    return 0


if __name__ == "__main__":
    sys.exit(main())
