"""P2-A6: tool-call trace chain (GenAI lesson 17 visibility pillar)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.observability.trace import TraceChain, render_chain_text


def test_chain_serialises_jsonl_with_spans():
    chain = TraceChain(session_key="s1", turn_id="t1", model="m")
    chain.add("read", "ok", 12, arguments={"path": "a.py"}, result="def foo")
    chain.add(
        "bash",
        "error",
        800,
        arguments="pytest",
        error="exit 1",
        reasoning="verify tests",
    )
    line = chain.to_jsonl()
    payload = json.loads(line)
    assert payload["session_key"] == "s1"
    assert payload["span_count"] == 2
    assert payload["spans"][0]["tool_name"] == "read"
    assert payload["spans"][1]["status"] == "error"
    assert "verify tests" in payload["spans"][1]["reasoning_preview"]


def test_span_previews_truncated():
    chain = TraceChain(session_key="s", turn_id="t")
    chain.add("bash", "ok", 1, result="x" * 5000)
    span = chain.spans[0]
    assert span.result_preview is not None
    assert len(span.result_preview) < 2500
    assert "truncated" in span.result_preview


def test_summary_aggregates_statuses_and_duration():
    chain = TraceChain(session_key="s", turn_id="t")
    chain.add("read", "ok", 10)
    chain.add("grep", "ok", 20)
    chain.add("bash", "denied", 0)
    summary = chain.summary()
    assert summary["spans"] == 3
    assert summary["by_status"] == {"ok": 2, "denied": 1}
    assert summary["total_duration_ms"] == 30
    assert summary["tools"] == ["read", "grep", "bash"]


def test_render_chain_text_includes_reasoning():
    chain = TraceChain(session_key="s", turn_id="t")
    chain.add("edit", "ok", 5, reasoning="fix the typo")
    text = render_chain_text(chain)
    assert "# Trace" in text
    assert "edit [ok] 5ms" in text
    assert "why: fix the typo" in text


def test_empty_chain_roundtrips():
    chain = TraceChain(session_key="s", turn_id="t")
    assert chain.summary()["spans"] == 0
    assert json.loads(chain.to_jsonl())["span_count"] == 0
