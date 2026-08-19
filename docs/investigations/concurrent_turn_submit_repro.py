"""Reproduction for the concurrent turn-submit crash (see the note beside it).

Not part of the test suite: it drives real subprocesses and fails roughly half
the time by design. Run it directly:

    python docs/investigations/concurrent_turn_submit_repro.py

The probe it installs prints, at the moment of failure, which foreign key was
violated and what the database actually contained.
"""

from __future__ import annotations
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path("/Users/lizongwei/Desktop/Coding_Project/DeepCode_workbase/DeepCode")


def _driver(script: Path, ws: Path, sessions: Path, home: Path) -> None:
    script.write_text(
        """
import io, os, sys, json, sqlite3
sys.path.insert(0, "%s")
from core.persistence import execution_repository as _er
_orig_add = _er.TurnRepository.add
def _traced(self, turn):
    try:
        return _orig_add(self, turn)
    except sqlite3.IntegrityError as exc:
        c = self.connection
        print("PROBE failure:", exc, file=sys.stderr)
        print("PROBE thread_id=", turn.thread_id, "ordinal=", turn.ordinal, file=sys.stderr)
        print("PROBE home_worker=", turn.home_worker_id, "owner=", turn.execution_owner_id, file=sys.stderr)
        for label, sql, params in (
            ("thread_rows", "select count(*) from threads where id=?", (turn.thread_id,)),
            ("worker_rows", "select count(*) from runtime_workers where id=?", (turn.home_worker_id,)),
            ("same_ordinal", "select count(*) from turns where thread_id=? and ordinal=?", (turn.thread_id, turn.ordinal)),
            ("project_rows", "select count(*) from projects", ()),
            ("all_workers", "select count(*) from runtime_workers", ()),
        ):
            try:
                print("PROBE", label, "=", c.execute(sql, params).fetchone()[0], file=sys.stderr)
            except Exception as e:
                print("PROBE", label, "query failed:", e, file=sys.stderr)
        # 换一条全新连接再查一次：区分"行不存在"和"本事务看不见"
        try:
            import os as _os
            fresh = sqlite3.connect(_os.environ["PROBE_DB"])
            n = fresh.execute("select count(*) from runtime_workers where id=?",
                              (turn.home_worker_id,)).fetchone()[0]
            total = fresh.execute("select count(*) from runtime_workers").fetchone()[0]
            print("PROBE fresh_connection_sees_worker =", n, "of", total, file=sys.stderr)
            for row in fresh.execute("select id, pid, surface, stopped_at from runtime_workers"):
                print("PROBE existing_worker", row, file=sys.stderr)
            print("PROBE my_pid", _os.getpid(), file=sys.stderr)
            fresh.close()
        except Exception as e:
            print("PROBE fresh query failed:", e, file=sys.stderr)
        raise
_er.TurnRepository.add = _traced
sys.argv = ["tui"]
from core.providers.base import LLMResponse, ToolCallRequest
from core import agent_setup
import cli.tui.app as tui_app

class _P: model = "fake-model"

class Prov:
    def __init__(self): self.calls = 0
    def get_default_model(self): return "fake-model"
    async def chat_with_retry(self, **kw):
        self.calls += 1
        tag = os.environ["TAG"]
        if self.calls %% 2 == 1:
            return LLMResponse(content="", finish_reason="tool_calls",
                tool_calls=[ToolCallRequest(id=f"{tag}-c{self.calls}", name="bash",
                                            arguments={"command": f"echo {tag}"})])
        return LLMResponse(content=f"{tag} answer {self.calls}", finish_reason="stop")

prov = Prov()
agent_setup.get_workflow_provider = lambda **k: (prov, _P())
agent_setup.get_runtime = lambda: type("R", (), {"config": type("C", (), {"security": None})()})()
sys.stdin = io.StringIO(os.environ["SCRIPT"])
raise SystemExit(tui_app.main(json.loads(os.environ["ARGV"])))
"""
        % ROOT
    )


def main() -> int:
    import tempfile

    base = Path(tempfile.mkdtemp())
    ws = base / "ws"
    ws.mkdir()
    sessions = base / "sessions"
    home = base / "home"
    script = base / "drive.py"
    _driver(script, ws, sessions, home)

    env = dict(os.environ)
    env["DEEPCODE_HOME"] = str(home)
    env["DEEPCODE_SESSIONS_DIR"] = str(sessions)
    env["PROBE_DB"] = str(home / "state" / "deepcode.sqlite3")
    base_argv = ["--workspace", str(ws), "--trust", "--access", "full-access"]

    # 进程 1 建会话
    e = dict(env)
    e["TAG"] = "A"
    e["SCRIPT"] = "a-one\n/exit\n"
    e["ARGV"] = json.dumps(base_argv)
    subprocess.run([sys.executable, str(script)], env=e, capture_output=True, cwd=ROOT)
    sid = next(
        d.name for d in sessions.iterdir() if d.is_dir() and not d.name.startswith(".")
    )
    print(f"会话 {sid} 由进程 1 建立")

    # 两个进程同时对同一会话发消息
    procs = []
    for tag, text in (("A", "a-two"), ("B", "b-two")):
        e = dict(env)
        e["TAG"] = tag
        e["SCRIPT"] = f"{text}\n/exit\n"
        e["ARGV"] = json.dumps(base_argv + ["--resume", sid])
        procs.append(
            subprocess.Popen(
                [sys.executable, str(script)],
                env=e,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=ROOT,
            )
        )
    outs = [p.communicate() for p in procs]
    for i, (p, (o, er)) in enumerate(zip(procs, outs), 1):
        if p.returncode:
            print(f"\n===== 并发进程 {i} 完整栈 (rc={p.returncode}) =====")
            print(er.decode())
        else:
            print(f"  并发进程 {i}: rc=0")

    # 崩溃后 dump SQLite 状态
    import sqlite3

    db = home / "state" / "deepcode.sqlite3"
    if db.exists():
        c = sqlite3.connect(db)
        print("\n=== runtime_workers ===")
        for row in c.execute(
            "select id, pid, surface, stopped_at from runtime_workers"
        ):
            print("   ", row)
        print("=== threads ===")
        for row in c.execute("select id, title from threads"):
            print("   ", row)
        print("=== turns (thread, ordinal, home_worker, owner) ===")
        for row in c.execute(
            "select thread_id, ordinal, home_worker_id, execution_owner_id from turns"
        ):
            print("   ", row)
        c.close()

    # 检查 canonical
    f = sessions / sid / "session.jsonl"
    recs = []
    bad_lines = 0
    for line in f.read_text(errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            recs.append(json.loads(line))
        except Exception:
            bad_lines += 1
    msgs = [r for r in recs if r.get("_type") == "message"]
    print(f"\ncanonical: {len(recs)} 行, {len(msgs)} 条消息, 损坏行 {bad_lines}")
    for m in msgs:
        meta = m.get("metadata") or {}
        print(
            f"  {m.get('role'):<9} {str(m.get('content'))[:40]!r}"
            f"{'  toolCallId=' + str(meta.get('toolCallId')) if meta.get('toolCallId') else ''}"
        )

    # 合法性：每条 tool 记录都要有声明它的 assistant
    sys.path.insert(0, str(ROOT))
    from core.sessions.models import SessionMessage
    from core.sessions.transcript import visible_kernel_history

    hist = visible_kernel_history([SessionMessage.from_dict(m) for m in msgs])
    declared = set()
    problems = []
    for i, x in enumerate(hist):
        if x.get("role") == "assistant":
            for tc in x.get("tool_calls") or ():
                declared.add(str(tc.get("id")))
        elif x.get("role") == "tool":
            t = str(x.get("tool_call_id") or "")
            if t not in declared:
                problems.append(f"[{i}] 孤儿工具结果 {t}")
    print(
        f"\n重建 {len(hist)} 条 → {'✓ 合法' if not problems else '✗ ' + '; '.join(problems)}"
    )
    return 1 if (bad_lines or problems) else 0


if __name__ == "__main__":
    raise SystemExit(main())
