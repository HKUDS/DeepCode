"""The pressure gate prefers a measured number to an estimated one."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.agent_runtime.token_meter import (  # noqa: E402
    HeuristicTokenMeter,
    ProviderAnchoredTokenMeter,
)


def _conversation(turns: int) -> list[dict[str, object]]:
    messages: list[dict[str, object]] = [{"role": "system", "content": "sys"}]
    for index in range(turns):
        messages.append({"role": "user", "content": f"question {index}"})
        messages.append({"role": "assistant", "content": f"answer {index}"})
    return messages


def test_without_a_report_it_is_the_heuristic() -> None:
    meter = ProviderAnchoredTokenMeter()
    messages = _conversation(3)
    assert meter.measure(None, "m", messages) == HeuristicTokenMeter().measure(
        None, "m", messages
    )


def test_a_reported_prompt_size_anchors_the_next_measurement() -> None:
    """The estimator can be far off; the provider's number is not.

    Here the provider reports 50,000 for a conversation the estimator prices
    in the tens. Everything after that must be measured from the report, not
    from the estimate — otherwise the gate keeps trusting a number the
    provider has already contradicted.
    """
    meter = ProviderAnchoredTokenMeter()
    messages = _conversation(3)
    heuristic = HeuristicTokenMeter().measure(None, "m", messages) or 0
    assert heuristic < 5_000  # the estimator's view of this toy conversation

    meter.observe({"prompt_tokens": 50_000}, messages)
    assert meter.measure(None, "m", messages) == 50_000

    grown = [*messages, {"role": "user", "content": "one more question"}]
    anchored = meter.measure(None, "m", grown)
    assert anchored is not None
    # The report, plus the heuristic price of only what was appended.
    assert 50_000 < anchored < 50_200


def test_a_rewritten_history_drops_the_anchor() -> None:
    """Compaction invalidates the sample: it no longer prices this history."""
    meter = ProviderAnchoredTokenMeter()
    messages = _conversation(4)
    meter.observe({"prompt_tokens": 50_000}, messages)

    compacted = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "SUMMARY OF EVERYTHING BEFORE"},
        *messages[-2:],
    ]
    measured = meter.measure(None, "m", compacted)
    assert measured == HeuristicTokenMeter().measure(None, "m", compacted)
    # And it stays on the heuristic until a new report arrives.
    assert meter.measure(None, "m", compacted) == measured


def test_a_report_without_a_prompt_size_is_ignored() -> None:
    meter = ProviderAnchoredTokenMeter()
    messages = _conversation(2)
    meter.observe({"completion_tokens": 12}, messages)
    assert meter.measure(None, "m", messages) == HeuristicTokenMeter().measure(
        None, "m", messages
    )


def test_input_tokens_is_accepted_as_the_report_key() -> None:
    """Anthropic-shaped usage reports the same fact under another name."""
    meter = ProviderAnchoredTokenMeter()
    messages = _conversation(2)
    meter.observe({"input_tokens": 31_000}, messages)
    assert meter.measure(None, "m", messages) == 31_000
