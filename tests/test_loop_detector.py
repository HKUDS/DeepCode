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
