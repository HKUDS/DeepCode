"""Retained tool calls must consume the same context budget as their content."""

from core.agent_runtime.compaction import TailRetainingCompactionStrategy


def test_old_large_tool_call_does_not_fit_as_zero_length_content():
    messages = [
        {"role": "user", "content": "old task " * 100},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "write-1",
                    "type": "function",
                    "function": {"name": "write_file", "arguments": "x" * 50_000},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "write-1", "content": "ok"},
        {"role": "user", "content": "Continue with the next task."},
        {"role": "assistant", "content": "Working on it."},
    ]

    compacted = TailRetainingCompactionStrategy().build_history(
        messages, "Saved old work."
    )

    assert compacted[1:] == messages[-2:]
    assert compacted[0]["compaction"]["retain"] == 2


def test_large_tool_call_in_current_user_turn_still_keeps_its_pair():
    current_turn = [
        {"role": "user", "content": "Write this file."},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "write-1",
                    "type": "function",
                    "function": {"name": "write_file", "arguments": "x" * 50_000},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "write-1", "content": "ok"},
    ]
    messages = [{"role": "user", "content": "older task"}] + current_turn

    compacted = TailRetainingCompactionStrategy().build_history(
        messages, "Saved old work."
    )

    assert compacted[1:] == current_turn
