"""Regression cover for ``LoopDetector.should_abort()`` in
``utils/loop_detector.py``.

``should_abort`` probed the detector with ``check_tool_call("")``, which
unconditionally appended the empty name to ``tool_history`` despite the
"Check without adding to history" comment. The workflow calls
``should_abort`` once per iteration, so five consecutive iterations left
the history as five identical ``""`` entries and the repeat check fired a
bogus ``loop_detected`` abort ("Loop detected:  called 5 times
consecutively"), killing healthy runs.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from utils.loop_detector import LoopDetector


def test_should_abort_does_not_pollute_history():
    detector = LoopDetector()
    for i in range(5):
        detector.check_tool_call(f"tool_{i}")
    before = list(detector.tool_history)

    for _ in range(5):
        assert detector.should_abort() is False

    assert detector.tool_history == before
    assert detector.get_abort_reason() is None


def test_real_repeat_is_still_detected():
    detector = LoopDetector()
    status = {}
    for _ in range(5):
        status = detector.check_tool_call("write_file")

    assert status["should_stop"] is True
    assert status["status"] == "loop_detected"
    assert "write_file" in status["message"]
    # The abort path reports the same genuine loop.
    assert detector.should_abort() is True
    assert "write_file" in detector.get_abort_reason()


@pytest.mark.parametrize("max_repeats", [5, 10, 12, 20])
def test_configured_repeat_limit_stops_at_the_requested_call(max_repeats):
    detector = LoopDetector(max_repeats=max_repeats)

    for _ in range(max_repeats - 1):
        assert detector.check_tool_call("write_file")["should_stop"] is False
        assert detector.should_abort() is False
        assert detector.get_abort_reason() is None

    result = detector.check_tool_call("write_file")

    assert result["status"] == "loop_detected"
    assert result["should_stop"] is True
    assert detector.should_abort() is True
    assert "write_file" in detector.get_abort_reason()


@pytest.mark.parametrize("max_repeats", [12, 20])
def test_different_tool_restarts_a_long_repeat_sequence(max_repeats):
    detector = LoopDetector(max_repeats=max_repeats)

    for _ in range(3):
        for _ in range(max_repeats - 1):
            assert detector.check_tool_call("write_file")["should_stop"] is False
        assert detector.check_tool_call("read_file")["should_stop"] is False

    for _ in range(max_repeats - 1):
        assert detector.check_tool_call("write_file")["should_stop"] is False
    assert detector.check_tool_call("write_file")["status"] == "loop_detected"


@pytest.mark.parametrize(("max_repeats", "history_bound"), [(5, 10), (12, 12)])
def test_history_stays_bounded_without_a_repeated_tool(max_repeats, history_bound):
    detector = LoopDetector(max_repeats=max_repeats)

    for index in range(200):
        tool = "write_file" if index % 2 == 0 else "read_file"
        assert detector.check_tool_call(tool)["should_stop"] is False
        assert len(detector.tool_history) <= history_bound

    assert detector.get_status_summary()["recent_tools"] == [
        "read_file",
        "write_file",
        "read_file",
        "write_file",
        "read_file",
    ]
