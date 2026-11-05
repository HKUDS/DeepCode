"""Paired capability/efficiency measurement for harness mechanism changes.

Why this module exists
----------------------
Every mechanism ported from SoL-Pi (Action Fusion, ObservationPack,
Evidence-Preserving Reducer, Online Context Compact) is justified by the claim
"same work, fewer tokens". That claim is *evidence*, not a design opinion, and
this repo already learned the hard way that a token saving can be faked by
doing less work. SoL-Pi's own acceptance rule is two gates applied per
mechanism, and it is worth copying exactly:

* **capability gate** — every capability metric stays within its predeclared
  tolerance (the "capability floor": savings that come from getting less done
  do not count);
* **efficiency gate** — at least one efficiency metric improves.

Three further rules are lifted from SoL-Pi's published direction list because
they are the difference between a measurement and a wish:

* ``M15`` *prove mechanism activation* — a mechanism that never fired cannot
  claim a gain; ``activated`` is recorded and checked, so "we turned it on and
  nothing got worse" is not reported as a win.
* ``M40`` *admit expensive mechanisms through paired, low-cost gates* — the
  unit of judgement is a **(base, candidate) pair on the same task**, never a
  candidate compared against a remembered baseline.
* ``R13`` *preserve exact model identity* — a pair produced by two different
  models/providers is not a pair. Model identity is part of the key and a
  mismatch invalidates the comparison instead of being quietly averaged away.

And ``M30`` *fail closed on negative or ambiguous terminal states*: an unknown
capability score is a rejection, not a pass.

Design notes
------------
* Control/data separation: the gate is a typed, inspectable object with
  numeric tolerances. Nothing here is expressed as prompt text, so prompt
  drift cannot loosen a gate.
* Explicit over magical: malformed records raise ``HarnessMetricsError`` with
  the offending line number rather than being silently skipped.
* Transactional and replayable (``M29``): the ledger is append-only JSONL,
  one record per line, fsync'd per append, and re-readable in order.

This module performs no network, no model calls, and no global state.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

__all__ = [
    "CapabilityOutcome",
    "EfficiencySample",
    "FogLedger",
    "FogObservation",
    "GateDecision",
    "HarnessLedger",
    "HarnessMetricsError",
    "HarnessRecord",
    "ModelIdentity",
    "judge_pair",
    "measure_post_compaction_fog",
]


class HarnessMetricsError(ValueError):
    """A ledger record is malformed, or a comparison is not well defined."""


# --------------------------------------------------------------------------
# Typed record parts (the dynamic boundary: parsed from disk, so every field is
# validated on the way in — a corrupt ledger must not become a passing gate).
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ModelIdentity:
    """Which model actually produced a measurement.

    ``R13`` exists because a harness tuned for one model is not evidence for
    another (the same finding HarnessDev reports as limited cross-model
    transfer). Recording identity is what makes that limit checkable.
    """

    provider: str
    model: str
    # Reasoning effort is part of identity, not metadata: at ``xhigh`` a model
    # spends a different amount of thinking than at ``low``, which moves both
    # capability and token count. SoL-Pi ran every comparison at one setting
    # for exactly this reason.
    effort: str = ""

    def key(self) -> tuple[str, str, str]:
        return (
            self.provider.strip().lower(),
            self.model.strip().lower(),
            self.effort.strip().lower(),
        )

    def __post_init__(self) -> None:
        if not self.provider.strip() or not self.model.strip():
            raise HarnessMetricsError(
                "model identity requires a non-empty provider and model "
                "(a measurement with no attributable model cannot gate anything)"
            )


@dataclass(frozen=True, slots=True)
class CapabilityOutcome:
    """Did the task still work, and how well.

    ``score`` is ``None`` when the run produced no usable grading signal. That
    is *not* a zero and not a pass — see ``judge_pair``, which fails closed.
    """

    passed: bool
    score: float | None = None
    detail: str = ""

    def __post_init__(self) -> None:
        if self.score is not None and not isinstance(self.score, (int, float)):
            raise HarnessMetricsError(
                f"capability score must be a number or None, got {type(self.score).__name__}"
            )


@dataclass(frozen=True, slots=True)
class EfficiencySample:
    """What the run cost.

    Token accounting is split hit/miss because the two are priced differently
    by providers and only the miss side moves with harness changes; a single
    blended "tokens" number hides the thing being optimised.
    """

    tokens_cache_hit: int = 0
    tokens_cache_miss: int = 0
    cost_usd: float | None = None
    turns: int = 0
    tool_calls: int = 0
    wall_seconds: float = 0.0

    def __post_init__(self) -> None:
        for name in ("tokens_cache_hit", "tokens_cache_miss", "turns", "tool_calls"):
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise HarnessMetricsError(
                    f"efficiency.{name} must be a non-negative int"
                )
        if self.wall_seconds < 0:
            raise HarnessMetricsError("efficiency.wall_seconds must be non-negative")
        if self.cost_usd is not None and self.cost_usd < 0:
            raise HarnessMetricsError(
                "efficiency.cost_usd must be non-negative when present"
            )

    @property
    def tokens_total(self) -> int:
        return self.tokens_cache_hit + self.tokens_cache_miss


@dataclass(frozen=True, slots=True)
class HarnessRecord:
    """One measurement of one variant of one task under one mechanism."""

    mechanism: str
    task_id: str
    variant: str
    model: ModelIdentity
    capability: CapabilityOutcome
    efficiency: EfficiencySample
    # M15: did the mechanism actually fire during this run? Default ``False``
    # so a record that forgets to state it cannot claim an activation.
    activated: bool = False
    notes: str = ""

    VARIANT_BASE = "base"
    VARIANT_CANDIDATE = "candidate"

    def __post_init__(self) -> None:
        for name, value in (
            ("mechanism", self.mechanism),
            ("task_id", self.task_id),
            ("variant", self.variant),
        ):
            if not isinstance(value, str) or not value.strip():
                raise HarnessMetricsError(f"record.{name} must be a non-empty string")

    @property
    def pairing_key(self) -> tuple[str, str]:
        """The identity of the *experiment*, independent of which arm ran."""
        return (self.mechanism.strip(), self.task_id.strip())

    def to_dict(self) -> dict[str, Any]:
        return {
            "mechanism": self.mechanism,
            "task_id": self.task_id,
            "variant": self.variant,
            "activated": self.activated,
            "notes": self.notes,
            "model": {
                "provider": self.model.provider,
                "model": self.model.model,
                "effort": self.model.effort,
            },
            "capability": {
                "passed": self.capability.passed,
                "score": self.capability.score,
                "detail": self.capability.detail,
            },
            "efficiency": {
                "tokens_cache_hit": self.efficiency.tokens_cache_hit,
                "tokens_cache_miss": self.efficiency.tokens_cache_miss,
                "cost_usd": self.efficiency.cost_usd,
                "turns": self.efficiency.turns,
                "tool_calls": self.efficiency.tool_calls,
                "wall_seconds": self.efficiency.wall_seconds,
            },
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> HarnessRecord:
        """Parse one record, raising on anything the schema does not allow.

        Written as an explicit field-by-field reader rather than
        ``cls(**payload)`` so that unknown keys and missing nested objects are
        loud failures: a ledger that silently dropped a field would let a
        malformed measurement gate a mechanism.
        """
        if not isinstance(payload, Mapping):
            raise HarnessMetricsError(
                f"record must be an object, got {type(payload).__name__}"
            )
        allowed = {
            "mechanism",
            "task_id",
            "variant",
            "activated",
            "notes",
            "model",
            "capability",
            "efficiency",
        }
        unknown = set(payload) - allowed
        if unknown:
            raise HarnessMetricsError(f"record has unknown key(s): {sorted(unknown)}")

        model = payload.get("model")
        capability = payload.get("capability")
        efficiency = payload.get("efficiency")
        if not isinstance(model, Mapping):
            raise HarnessMetricsError("record.model must be an object")
        if not isinstance(capability, Mapping):
            raise HarnessMetricsError("record.capability must be an object")
        if not isinstance(efficiency, Mapping):
            raise HarnessMetricsError("record.efficiency must be an object")

        def _require_str(source: Mapping[str, Any], key: str, where: str) -> str:
            value = source.get(key, "")
            if not isinstance(value, str):
                raise HarnessMetricsError(f"{where}.{key} must be a string")
            return value

        score = capability.get("score")
        if score is not None and not isinstance(score, (int, float)):
            raise HarnessMetricsError("capability.score must be a number or null")

        def _require_int(source: Mapping[str, Any], key: str, where: str) -> int:
            value = source.get(key, 0)
            if isinstance(value, bool) or not isinstance(value, int):
                raise HarnessMetricsError(f"{where}.{key} must be an integer")
            return value

        cost = efficiency.get("cost_usd")
        if cost is not None and not isinstance(cost, (int, float)):
            raise HarnessMetricsError("efficiency.cost_usd must be a number or null")
        wall = efficiency.get("wall_seconds", 0.0)
        if not isinstance(wall, (int, float)) or isinstance(wall, bool):
            raise HarnessMetricsError("efficiency.wall_seconds must be a number")

        return cls(
            mechanism=_require_str(payload, "mechanism", "record"),
            task_id=_require_str(payload, "task_id", "record"),
            variant=_require_str(payload, "variant", "record"),
            activated=bool(payload.get("activated", False)),
            notes=_require_str(payload, "notes", "record"),
            model=ModelIdentity(
                provider=_require_str(model, "provider", "model"),
                model=_require_str(model, "model", "model"),
                effort=_require_str(model, "effort", "model"),
            ),
            capability=CapabilityOutcome(
                passed=bool(capability.get("passed", False)),
                score=float(score) if score is not None else None,
                detail=_require_str(capability, "detail", "capability"),
            ),
            efficiency=EfficiencySample(
                tokens_cache_hit=_require_int(
                    efficiency, "tokens_cache_hit", "efficiency"
                ),
                tokens_cache_miss=_require_int(
                    efficiency, "tokens_cache_miss", "efficiency"
                ),
                cost_usd=float(cost) if cost is not None else None,
                turns=_require_int(efficiency, "turns", "efficiency"),
                tool_calls=_require_int(efficiency, "tool_calls", "efficiency"),
                wall_seconds=float(wall),
            ),
        )


# --------------------------------------------------------------------------
# The gate
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class GateDecision:
    """The verdict on one pair, plus every reason it was reached.

    ``accepted`` is the conjunction of the individual gates. ``reasons`` is
    always populated (both passes and failures) so a refusal can be explained
    without re-running the comparison.
    """

    accepted: bool
    capability_ok: bool
    efficiency_ok: bool
    activation_ok: bool
    identity_ok: bool
    deltas: dict[str, Any] = field(default_factory=dict)
    reasons: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "accepted": self.accepted,
            "capability_ok": self.capability_ok,
            "efficiency_ok": self.efficiency_ok,
            "activation_ok": self.activation_ok,
            "identity_ok": self.identity_ok,
            "deltas": dict(self.deltas),
            "reasons": list(self.reasons),
        }


def _pair_records(
    first: HarnessRecord, second: HarnessRecord
) -> tuple[HarnessRecord, HarnessRecord]:
    """Return ``(base, candidate)``, or raise if the two are not a pair."""
    if first.variant == second.variant:
        raise HarnessMetricsError(
            f"cannot pair two '{first.variant}' records for "
            f"{first.mechanism}/{first.task_id}: a comparison needs one base "
            f"and one candidate"
        )
    if first.pairing_key != second.pairing_key:
        raise HarnessMetricsError(
            "cannot pair records from different experiments: "
            f"{first.pairing_key} vs {second.pairing_key}"
        )
    if first.variant == HarnessRecord.VARIANT_BASE:
        return first, second
    return second, first


def judge_pair(
    base: HarnessRecord,
    candidate: HarnessRecord,
    *,
    capability_tolerance: float = 0.0,
    min_token_gain_fraction: float = 0.0,
) -> GateDecision:
    """Apply SoL-Pi's two gates (plus activation and identity) to one pair.

    Arguments are deliberately numeric and explicit; there is no default that
    silently permits a capability regression, because SoL-Pi's own published
    result on Terminal-Bench 4 (15/63 vs 18/63) is a reminder that per-mechanism
    losses accumulate once mechanisms are combined.

    ``capability_tolerance`` is the predeclared allowed drop (``0.0`` = none).
    ``min_token_gain_fraction`` is the minimum relative reduction in total
    tokens required to call the efficiency gate passed (``0.0`` = any
    improvement).
    """
    if capability_tolerance < 0:
        raise HarnessMetricsError("capability_tolerance must be non-negative")
    if min_token_gain_fraction < 0:
        raise HarnessMetricsError("min_token_gain_fraction must be non-negative")

    ordered_base, ordered_candidate = _pair_records(base, candidate)

    reasons: list[str] = []
    deltas: dict[str, Any] = {}

    # R13 — identical model identity, or the comparison is meaningless.
    identity_ok = ordered_base.model.key() == ordered_candidate.model.key()
    if identity_ok:
        reasons.append(f"model identity matched: {ordered_candidate.model.model}")
    else:
        reasons.append(
            "REJECT identity: base ran on "
            f"{ordered_base.model.provider}/{ordered_base.model.model}@{ordered_base.model.effort}, "
            f"candidate on {ordered_candidate.model.provider}/{ordered_candidate.model.model}"
            f"@{ordered_candidate.model.effort}"
        )

    # Capability floor. Fail closed on an unknown score (M30).
    base_score = ordered_base.capability.score
    cand_score = ordered_candidate.capability.score
    if base_score is None or cand_score is None:
        capability_ok = False
        reasons.append(
            "REJECT capability: score unavailable on at least one arm "
            "(ambiguous terminal state fails closed, M30)"
        )
    else:
        floor = base_score - capability_tolerance
        capability_ok = cand_score >= floor
        deltas["capability_delta"] = round(cand_score - base_score, 6)
        if capability_ok:
            reasons.append(
                f"capability held: {cand_score:g} >= {floor:g} (base {base_score:g})"
            )
        else:
            reasons.append(
                f"REJECT capability: {cand_score:g} < floor {floor:g} (base {base_score:g})"
            )
    # A run that reported failure outright is never a pass, whatever it scored.
    if not ordered_candidate.capability.passed:
        capability_ok = False
        reasons.append("REJECT capability: candidate run did not pass")

    # Efficiency: at least one metric must improve (SoL-Pi gate 2).
    base_tokens = ordered_base.efficiency.tokens_total
    cand_tokens = ordered_candidate.efficiency.tokens_total
    token_gain = 0.0 if base_tokens <= 0 else (base_tokens - cand_tokens) / base_tokens
    deltas["tokens_base"] = base_tokens
    deltas["tokens_candidate"] = cand_tokens
    deltas["token_gain_fraction"] = round(token_gain, 6)

    base_cost = ordered_base.efficiency.cost_usd
    cand_cost = ordered_candidate.efficiency.cost_usd
    cost_gain: float | None = None
    if base_cost is not None and cand_cost is not None and base_cost > 0:
        cost_gain = (base_cost - cand_cost) / base_cost
        deltas["cost_gain_fraction"] = round(cost_gain, 6)

    tokens_improved = (
        token_gain >= min_token_gain_fraction and cand_tokens < base_tokens
    )
    cost_improved = (
        cost_gain is not None
        and cost_gain > 0
        and cand_cost is not None
        and base_cost is not None
        and cand_cost < base_cost
    )
    efficiency_ok = tokens_improved or cost_improved
    if efficiency_ok:
        which = "tokens" if tokens_improved else "cost"
        reasons.append(
            f"efficiency improved on {which}: "
            f"{base_tokens}→{cand_tokens} tokens ({token_gain:+.1%})"
        )
    else:
        reasons.append(
            f"REJECT efficiency: no improvement ({base_tokens}→{cand_tokens} tokens, "
            f"{token_gain:+.1%}; gain floor {min_token_gain_fraction:.1%})"
        )

    # M15 — a mechanism that never fired cannot claim a win.
    activation_ok = bool(ordered_candidate.activated)
    if activation_ok:
        reasons.append("mechanism activation recorded on the candidate arm")
    else:
        reasons.append(
            "REJECT activation: candidate arm never fired the mechanism, so any "
            "difference is noise (M15)"
        )

    accepted = capability_ok and efficiency_ok and activation_ok and identity_ok
    return GateDecision(
        accepted=accepted,
        capability_ok=capability_ok,
        efficiency_ok=efficiency_ok,
        activation_ok=activation_ok,
        identity_ok=identity_ok,
        deltas=deltas,
        reasons=tuple(reasons),
    )


# --------------------------------------------------------------------------
# Post-compaction fog (observation only, M15 companion)
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class FogObservation:
    """Error-step rates inside and outside the post-compaction fog window.

    LangWatch measured that within roughly 28 steps after a context compaction
    the correction rate spikes (41.9% vs a 17.7% baseline, 2.37x): the agent
    keeps re-deriving what the summary lost. This observation is the cheap
    half of that finding — it only counts; it never decides when to compact or
    what to keep, so it can sit beside ``M15`` without moving any policy.

    ``post_*`` counts steps inside the window after each compaction checkpoint;
    ``baseline_*`` counts every other step of the same history, which is the
    within-run comparison the ratio needs. ``None`` rates mean no samples, not
    zero error.
    """

    window_steps: int
    compactions: int
    post_steps: int
    post_error_steps: int
    baseline_steps: int
    baseline_error_steps: int

    def __post_init__(self) -> None:
        if (
            not isinstance(self.window_steps, int)
            or isinstance(self.window_steps, bool)
            or self.window_steps <= 0
        ):
            raise HarnessMetricsError(
                f"fog.window_steps must be a positive int, got {self.window_steps!r}"
            )
        for name in (
            "compactions",
            "post_steps",
            "post_error_steps",
            "baseline_steps",
            "baseline_error_steps",
        ):
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise HarnessMetricsError(f"fog.{name} must be a non-negative int")
        if self.post_error_steps > self.post_steps:
            raise HarnessMetricsError(
                "fog.post_error_steps cannot exceed fog.post_steps"
            )
        if self.baseline_error_steps > self.baseline_steps:
            raise HarnessMetricsError(
                "fog.baseline_error_steps cannot exceed fog.baseline_steps"
            )

    @property
    def post_error_rate(self) -> float | None:
        """Error-step share inside the fog window; ``None`` with no samples."""
        return self.post_error_steps / self.post_steps if self.post_steps else None

    @property
    def baseline_error_rate(self) -> float | None:
        """Error-step share outside the window; ``None`` with no samples."""
        return (
            self.baseline_error_steps / self.baseline_steps
            if self.baseline_steps
            else None
        )

    @property
    def fog_ratio(self) -> float | None:
        """Post/baseline error rate — LangWatch's 2.37x finding is this number.

        ``None`` when either rate is undefined or the baseline rate is zero:
        a division claiming infinite fog would not be a measurement.
        """
        post = self.post_error_rate
        baseline = self.baseline_error_rate
        if post is None or baseline is None or baseline <= 0.0:
            return None
        return post / baseline

    def to_dict(self) -> dict[str, int]:
        """The six raw counts — derived rates are recomputed by readers.

        Storing only counts keeps the ledger replayable: a persisted ratio
        would drift from its own counts the moment rounding changed, while
        ``FogObservation(**payload)`` round-trips exactly.
        """
        return {
            "window_steps": self.window_steps,
            "compactions": self.compactions,
            "post_steps": self.post_steps,
            "post_error_steps": self.post_error_steps,
            "baseline_steps": self.baseline_steps,
            "baseline_error_steps": self.baseline_error_steps,
        }


def measure_post_compaction_fog(
    messages: Sequence[Mapping[str, Any]],
    *,
    window_steps: int = 28,
) -> FogObservation:
    """Count error steps in the ``window_steps`` after each compaction.

    The seam is the checkpoint message TailRetainingCompactionStrategy emits:
    a message carrying a ``compaction.reset`` key. It is read by key only, so
    this module keeps its no-imports-of-the-compactor boundary. A checkpoint
    also records ``retain``, the number of verbatim pre-compaction messages
    kept behind it; those are old work and are skipped before the window
    opens, otherwise a large retained tail would swallow the whole window.

    A step is one assistant turn plus the tool results that follow it; a step
    errs when any of those results is a ``str`` starting with ``"Error:"`` —
    the runner's uniform error rendering. Structured tool results (dicts,
    block lists) and empty-output placeholders are never counted as errors.
    Steps outside every window are the baseline the post-compaction rate is
    compared against.

    Read-only: the message list is never mutated and the compaction policy is
    never consulted.
    """
    if (
        not isinstance(window_steps, int)
        or isinstance(window_steps, bool)
        or window_steps <= 0
    ):
        raise HarnessMetricsError(
            f"fog window_steps must be a positive int, got {window_steps!r}"
        )

    steps: list[tuple[int, bool]] = []  # (first message index, error flag)
    checkpoints: list[tuple[int, int]] = []  # (message index, retained messages)
    open_step: int | None = None

    for index, message in enumerate(messages):
        if not isinstance(message, Mapping):
            raise HarnessMetricsError(f"message {index} is not a mapping")

        compaction = message.get("compaction")
        if isinstance(compaction, Mapping) and compaction.get("reset"):
            retain = compaction.get("retain", 0)
            if not isinstance(retain, int) or isinstance(retain, bool) or retain < 0:
                raise HarnessMetricsError(
                    f"checkpoint at message {index} has a malformed retain count: "
                    f"{retain!r}"
                )
            checkpoints.append((index, retain))
            open_step = None
            continue

        role = message.get("role")
        if role == "assistant":
            steps.append((index, False))
            open_step = len(steps) - 1
        elif role == "tool" and open_step is not None:
            content = message.get("content")
            if isinstance(content, str) and content.startswith("Error:"):
                start, _ = steps[open_step]
                steps[open_step] = (start, True)

    post: set[int] = set()
    for checkpoint_index, retained in checkpoints:
        window_start = checkpoint_index + 1 + retained
        placed = 0
        for position, (start, _) in enumerate(steps):
            if start >= window_start:
                post.add(position)
                placed += 1
                if placed >= window_steps:
                    break

    post_steps = len(post)
    post_error_steps = sum(1 for position in post if steps[position][1])
    total_error_steps = sum(1 for _, errs in steps if errs)
    return FogObservation(
        window_steps=window_steps,
        compactions=len(checkpoints),
        post_steps=post_steps,
        post_error_steps=post_error_steps,
        baseline_steps=len(steps) - post_steps,
        baseline_error_steps=total_error_steps - post_error_steps,
    )


class FogLedger:
    """Append-only JSONL ledger of post-compaction fog observations.

    One JSON object per line on its own path — deliberately separate from
    :class:`HarnessLedger`: a fog row is six counts, not a paired harness
    record, and the two schemas must never share a file. Same write
    discipline (O_APPEND + fsync) so concurrent writers cannot interleave
    a partial line. No ``read_all``: consumers read the JSONL directly,
    which keeps this class a pure write side (E1).
    """

    def __init__(self, path: str | os.PathLike[str]) -> None:
        self.path = Path(path)

    def append(self, observation: FogObservation) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        line = (
            json.dumps(observation.to_dict(), ensure_ascii=False, sort_keys=True) + "\n"
        )
        # O_APPEND keeps concurrent writers from interleaving a partial line.
        fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
        try:
            os.write(fd, line.encode("utf-8"))
            os.fsync(fd)
        finally:
            os.close(fd)


# --------------------------------------------------------------------------
# Storage
# --------------------------------------------------------------------------


class HarnessLedger:
    """Append-only JSONL ledger of harness measurements.

    Replayable by construction: records are written one JSON object per line
    and read back in the order they were appended. Nothing rewrites the file,
    so a partially written tail is detectable rather than silently truncating
    history.
    """

    def __init__(self, path: str | os.PathLike[str]) -> None:
        self.path = Path(path)

    def append(self, record: HarnessRecord) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(record.to_dict(), ensure_ascii=False, sort_keys=True) + "\n"
        # O_APPEND keeps concurrent writers from interleaving a partial line.
        fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
        try:
            os.write(fd, line.encode("utf-8"))
            os.fsync(fd)
        finally:
            os.close(fd)

    def read_all(self, *, strict: bool = True) -> list[HarnessRecord]:
        """Read every record in append order.

        ``strict`` (the default) raises on the first malformed line — a ledger
        that cannot be replayed is not evidence. Pass ``strict=False`` to
        collect the valid prefix and the bad lines instead, which is what a
        diagnostic caller wants.
        """
        if not self.path.exists():
            return []
        records: list[HarnessRecord] = []
        with self.path.open("r", encoding="utf-8") as handle:
            for lineno, raw in enumerate(handle, start=1):
                text = raw.strip()
                if not text:
                    continue
                try:
                    payload = json.loads(text)
                    records.append(HarnessRecord.from_dict(payload))
                except (json.JSONDecodeError, HarnessMetricsError) as exc:
                    if strict:
                        raise HarnessMetricsError(
                            f"{self.path}:{lineno}: unusable record ({exc})"
                        ) from exc
                    continue
        return records

    def quarantine(self) -> list[tuple[int, str]]:
        """Return ``(line_number, raw_text)`` for every line that fails to parse."""
        bad: list[tuple[int, str]] = []
        if not self.path.exists():
            return bad
        with self.path.open("r", encoding="utf-8") as handle:
            for lineno, raw in enumerate(handle, start=1):
                text = raw.strip()
                if not text:
                    continue
                try:
                    HarnessRecord.from_dict(json.loads(text))
                except (json.JSONDecodeError, HarnessMetricsError):
                    bad.append((lineno, text))
        return bad

    def pair_up(
        self, mechanism: str | None = None
    ) -> list[tuple[HarnessRecord, HarnessRecord]]:
        """Return every complete (base, candidate) pair in the ledger.

        A pairing key with records from only one arm is skipped here rather
        than guessed at: an unpaired arm is an unfinished experiment, and
        inventing its counterpart is exactly the failure ``M40`` guards against.
        """
        bases: dict[tuple[str, str], HarnessRecord] = {}
        candidates: dict[tuple[str, str], HarnessRecord] = {}
        for record in self.read_all():
            if mechanism is not None and record.mechanism != mechanism:
                continue
            target = (
                bases if record.variant == HarnessRecord.VARIANT_BASE else candidates
            )
            target[record.pairing_key] = record
        shared = sorted(set(bases) & set(candidates))
        return [(bases[key], candidates[key]) for key in shared]

    def judge_all(
        self,
        *,
        mechanism: str | None = None,
        capability_tolerance: float = 0.0,
        min_token_gain_fraction: float = 0.0,
    ) -> list[tuple[HarnessRecord, HarnessRecord, GateDecision]]:
        return [
            (
                base,
                candidate,
                judge_pair(
                    base,
                    candidate,
                    capability_tolerance=capability_tolerance,
                    min_token_gain_fraction=min_token_gain_fraction,
                ),
            )
            for base, candidate in self.pair_up(mechanism)
        ]

    def summary(self, mechanism: str | None = None) -> dict[str, Any]:
        """Aggregate verdicts across the ledger — the retained/refused split."""
        verdicts = self.judge_all(mechanism=mechanism)
        accepted = sum(1 for _, _, decision in verdicts if decision.accepted)
        tokens_base = sum(base.efficiency.tokens_total for base, _, _ in verdicts)
        tokens_cand = sum(cand.efficiency.tokens_total for _, cand, _ in verdicts)
        return {
            "pairs": len(verdicts),
            "accepted": accepted,
            "refused": len(verdicts) - accepted,
            "tokens_base": tokens_base,
            "tokens_candidate": tokens_cand,
            "token_gain_fraction": (
                round((tokens_base - tokens_cand) / tokens_base, 6)
                if tokens_base
                else 0.0
            ),
            "unpaired_or_malformed": len(self.quarantine()),
        }
