from __future__ import annotations

import json

import pytest

from core.events import AgentReasoningDelta, Event, serialize_event
from core.reasoning import (
    ReasoningAvailability,
    ReasoningChannel,
    ReasoningPayload,
)


def test_reasoning_payload_round_trips_current_schema() -> None:
    payload = ReasoningPayload(
        summary_text="summary",
        trace_text="trace",
        availability=ReasoningAvailability.AVAILABLE,
        effort="high",
        duration_ms=1200,
        streaming=False,
    )

    assert ReasoningPayload.from_dict(payload.to_dict()) == payload


def test_reasoning_payload_reads_legacy_summary_item() -> None:
    payload = ReasoningPayload.from_dict({"text": "legacy summary"})

    assert payload.summary_text == "legacy summary"
    assert payload.trace_text == ""
    assert payload.availability is ReasoningAvailability.AVAILABLE


def test_reasoning_payload_is_immutable_and_rejects_negative_duration() -> None:
    original = ReasoningPayload(effort="medium")
    updated = original.with_delta(ReasoningChannel.SUMMARY, "checking")

    assert original.summary_text == ""
    assert updated.summary_text == "checking"
    assert updated.streaming is True
    with pytest.raises(ValueError, match="non-negative"):
        ReasoningPayload(duration_ms=-1)


def test_reasoning_protocol_serializes_channel_as_json_string() -> None:
    payload = serialize_event(
        Event(
            "1",
            AgentReasoningDelta(
                "reasoning-1",
                ReasoningChannel.PROVIDER_TRACE,
                "trace",
            ),
        )
    )

    assert payload["msg"]["channel"] == "provider_trace"
    assert json.loads(json.dumps(payload))["msg"]["channel"] == "provider_trace"
