"""P2-C4: few-shot tool descriptions (GenAI lesson 04 show-and-tell).

Lesson 04: an example ("input → call → output") beats an abstract rule —
show and tell. The edit tool's description now carries a concrete call
example; these tests pin that the example exists and stays inside the P1-2
length budget.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.agent_runtime.tools.base import (
    _DESCRIPTION_MAX_CHARS,
    description_quality_issues,
)
from core.harness.tools.files import EditTool


def test_edit_description_has_example():
    tool = EditTool(str(ROOT))
    desc = tool.description
    assert "Example:" in desc
    assert 'edit(file_path=' in desc
    assert 'old_string=' in desc and 'new_string=' in desc


def test_edit_description_within_length_budget():
    desc = EditTool(str(ROOT)).description
    assert len(desc) <= _DESCRIPTION_MAX_CHARS
    assert description_quality_issues(desc) == []
