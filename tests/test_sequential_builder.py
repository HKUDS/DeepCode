"""P2-A9: sequential chain builder (GenAI lesson 17 SequentialBuilder)."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.loop.sequential_builder import (
    ChainStage,
    SequentialChain,
    run_sequential,
)


def test_chain_executes_in_order():
    chain = SequentialChain(name="pipeline")
    chain.add(ChainStage(name="a", task="step a"))
    chain.add(ChainStage(name="b", task="step b"))
    chain.add(ChainStage(name="c", task="step c"))

    calls: list[str] = []

    async def executor(stage, task, previous_result):
        calls.append(stage.name)
        return f"result-{stage.name}"

    results = run_sequential_async(chain, executor)
    assert calls == ["a", "b", "c"]
    assert results == ["result-a", "result-b", "result-c"]


def test_previous_result_flows_forward():
    chain = SequentialChain(name="p")
    chain.add(ChainStage(name="first", task="produce a number"))
    chain.add(
        ChainStage(name="second", task="use {previous_result} to continue")
    )

    seen: list[str] = []

    async def executor(stage, task, previous_result):
        seen.append(task)
        if stage.name == "first":
            return "42"
        return "done"

    run_sequential_async(chain, executor)
    assert seen[0] == "produce a number"  # no placeholder → task verbatim
    assert "use 42 to continue" in seen[1]


def test_stage_result_referenced_by_placeholder_only():
    # A stage that does not use the placeholder still receives the result
    # as an argument; only the rendered task changes.
    chain = SequentialChain(name="p")
    chain.add(ChainStage(name="x", task="x task"))
    chain.add(ChainStage(name="y", task="y task without placeholder"))

    previous_values: list[Any] = []

    async def executor(stage, task, previous_result):
        previous_values.append(previous_result)
        return "out"

    run_sequential_async(chain, executor)
    assert previous_values[0] is None  # first stage: no predecessor
    assert previous_values[1] == "out"  # second stage sees first's result


def test_validation_rejects_duplicates_and_empty():
    chain = SequentialChain(name="bad")
    chain.add(ChainStage(name="dup", task="t1"))
    chain.add(ChainStage(name="dup", task="t2"))
    chain.add(ChainStage(name="", task="t3"))
    errors = chain.validate()
    assert any("duplicate" in e for e in errors)
    assert any("empty" in e for e in errors)


def test_run_sequential_raises_on_invalid_chain():
    chain = SequentialChain(name="bad")
    chain.add(ChainStage(name="", task=""))

    async def executor(stage, task, previous_result):
        return "never"

    with pytest.raises(ValueError, match="invalid sequential chain"):
        run_sequential_async(chain, executor)


def test_on_stage_done_observer_fires():
    chain = SequentialChain(name="p")
    chain.add(ChainStage(name="a", task="a"))
    chain.add(ChainStage(name="b", task="b"))

    observed: list[tuple[str, Any]] = []

    def on_done(stage, result):
        observed.append((stage.name, result))

    async def executor(stage, task, previous_result):
        return f"r-{stage.name}"

    run_sequential_async(chain, executor, on_stage_done=on_done)
    assert observed == [("a", "r-a"), ("b", "r-b")]


def run_sequential_async(chain, executor, **kw):
    import asyncio

    return asyncio.run(run_sequential(chain, executor, **kw))
