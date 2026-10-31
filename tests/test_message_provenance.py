"""Which user-role records are the Goal runtime's words, not the user's."""

from __future__ import annotations

import pytest

from core.domain.message_provenance import GoalPrompt, goal_prompt


@pytest.mark.parametrize(
    ("provenance", "expected"),
    [
        ({"source": "goal_continuation", "client": "internal"}, GoalPrompt.AUTOMATIC),
        # Starting, editing or continuing a Goal from a client.
        ({"source": "goal_continuation", "client": "desktop"}, GoalPrompt.REQUESTED),
        ({"source": "goal_continuation"}, GoalPrompt.REQUESTED),
        (
            {
                "source": "retry",
                "client": "internal",
                "retryOf": {"source": "goal_continuation", "client": "internal"},
            },
            GoalPrompt.REQUESTED,
        ),
        # The user's own words, first sent or retried.
        ({"source": "start", "client": "cli"}, None),
        ({"source": "steer", "client": "web"}, None),
        ({"source": "retry", "retryOf": {"source": "start"}}, None),
        ({"source": "retry"}, None),
        ({}, None),
    ],
)
def test_goal_prompt(provenance: dict, expected: GoalPrompt | None) -> None:
    assert goal_prompt(provenance) is expected
