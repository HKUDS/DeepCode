"""Action fusion: run a deterministic sequence of commands as one tool call.

Each model turn re-sends the whole prompt, so a five-command workflow costs five
round trips and five copies of the context. Fusion collapses it: the model
declares the sequence once, the harness executes it, and one result carries the
per-step evidence. The saving is real but it is *not* the interesting part.

The interesting part is attribution. A fused sequence must make it possible to
say exactly which step failed, what it printed, and that the steps after it were
deliberately not run — otherwise the harness has traded a retryable failure for
an unattributable one, and a model that cannot tell where the sequence broke
will guess. So :class:`StepOutcome` records ``exit_code``, ``skipped`` and a
bounded ``output`` per step, and :class:`FusionOutcome` reports where the
sequence stopped and under which contract.

Two rules make the primitive safe to hand to a model:

* **No private exec path.** Every step is dispatched through the *same* tool
  object that owns command screening, the sandbox, environment scrubbing and the
  output cap. Fusion controls sequencing, never execution; a step that the
  policy screen refuses is refused here too, because there is no second
  implementation of the screen to drift out of sync.
* **A refusal ends the sequence.** A step blocked by policy means the premise of
  the workflow is broken, so the remaining steps are skipped even when the
  caller asked to continue past errors. Continuing past a *failure* is a
  judgement call the caller may make; continuing past a *refusal* is a way to
  walk a blocked workflow in through the back door.

Output is bounded at three levels — per step, in total, and by the number of
steps — because a fused call that returns everything is a context blowout that
presents itself as a helpful feature.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Sequence

from core.agent_runtime.tools.base import Tool, ToolResult

__all__ = [
    "DEFAULT_MAX_STEPS",
    "DEFAULT_MAX_TOTAL_CHARS",
    "DEFAULT_STEP_CHARS",
    "FusionOutcome",
    "RunStepsTool",
    "StepFusion",
    "StepOutcome",
    "StepRequest",
    "parse_steps",
]

DEFAULT_MAX_STEPS = 12
DEFAULT_MAX_TOTAL_CHARS = 20_000
DEFAULT_STEP_CHARS = 4_000
DEFAULT_STEP_TIMEOUT_S = 300

# Contract values. The renderer and the metadata both use these, so a caller
# never has to infer the sequence's fate from prose.
CONTRACT_OK = "ok"
CONTRACT_STEP_FAILED = "step_failed"
CONTRACT_REFUSED = "refused"
CONTRACT_ERROR = "error"

_STEP_KEYS = frozenset({"command", "label", "timeout_s"})


# --------------------------------------------------------------------------
# The request
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class StepRequest:
    """One command in a fused sequence."""

    command: str
    label: str = ""
    timeout_s: int | None = None

    def __post_init__(self) -> None:
        if not self.command.strip():
            raise ValueError("step command must not be empty")
        if self.timeout_s is not None and self.timeout_s <= 0:
            raise ValueError("step timeout_s must be > 0")

    @property
    def display(self) -> str:
        return self.label or self.command

    def to_dict(self) -> dict[str, Any]:
        return {
            "command": self.command,
            "label": self.label,
            "timeout_s": self.timeout_s,
        }


def parse_steps(
    raw: Any, *, max_steps: int = DEFAULT_MAX_STEPS
) -> tuple[StepRequest, ...]:
    """Parse the model-supplied step list, or explain why it is unusable.

    Fails closed on anything unexpected. A step list is a *request for a sequence
    of side effects*, so guessing at a malformed entry would mean inventing
    commands the caller never wrote; the caller gets a specific error instead.
    """
    if not isinstance(raw, (list, tuple)):
        raise ValueError("steps must be a list")
    if not raw:
        raise ValueError("steps must not be empty")
    if len(raw) > max_steps:
        raise ValueError(
            f"steps must contain at most {max_steps} entries, got {len(raw)}"
        )

    steps: list[StepRequest] = []
    for index, entry in enumerate(raw, start=1):
        if isinstance(entry, str):
            steps.append(StepRequest(command=entry))
            continue
        if not isinstance(entry, dict):
            raise ValueError(f"step {index} must be a string or an object")
        unknown = set(entry) - _STEP_KEYS
        if unknown:
            raise ValueError(
                f"step {index} has unsupported keys: {', '.join(sorted(unknown))}"
            )
        command = entry.get("command")
        if not isinstance(command, str):
            raise ValueError(f"step {index} needs a string 'command'")
        label = entry.get("label", "")
        if label is None:
            label = ""
        if not isinstance(label, str):
            raise ValueError(f"step {index} 'label' must be a string")
        timeout = entry.get("timeout_s")
        if timeout is not None and not isinstance(timeout, int):
            raise ValueError(f"step {index} 'timeout_s' must be an integer")
        steps.append(StepRequest(command=command, label=label, timeout_s=timeout))
    return tuple(steps)


# --------------------------------------------------------------------------
# The result
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class StepOutcome:
    """What one step did, attributed and bounded."""

    index: int
    command: str
    label: str
    exit_code: int | None
    is_error: bool
    skipped: bool
    output: str
    truncated: bool

    @property
    def ok(self) -> bool:
        return not self.skipped and self.exit_code == 0 and not self.is_error

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "command": self.command,
            "label": self.label,
            "exit_code": self.exit_code,
            "is_error": self.is_error,
            "skipped": self.skipped,
            "truncated": self.truncated,
            "output_chars": len(self.output),
        }


@dataclass(frozen=True, slots=True)
class FusionOutcome:
    """The sequence's fate plus every step's evidence."""

    steps: tuple[StepOutcome, ...]
    contract: str
    stopped_at: int | None
    output_chars: int

    @property
    def executed(self) -> int:
        return sum(1 for step in self.steps if not step.skipped)

    @property
    def failed_step(self) -> StepOutcome | None:
        return next(
            (step for step in self.steps if not step.ok and not step.skipped), None
        )

    @property
    def ok(self) -> bool:
        return self.contract == CONTRACT_OK

    def to_dict(self) -> dict[str, Any]:
        return {
            "contract": self.contract,
            "steps": [step.to_dict() for step in self.steps],
            "stopped_at": self.stopped_at,
            "executed": self.executed,
            "total_steps": len(self.steps),
            "output_chars": self.output_chars,
        }

    def render(self, *, max_chars: int = DEFAULT_MAX_TOTAL_CHARS) -> str:
        """Model-visible text: one headed block per step, in order.

        ``max_chars`` is enforced here as well as in the budget, because the
        renderer is also the seam a caller can use to re-render a stored outcome
        into a smaller budget.
        """
        header = (
            f"[run_steps: {self.executed}/{len(self.steps)} executed, "
            f"contract={self.contract}"
        )
        if self.stopped_at is not None:
            header += f", stopped at step {self.stopped_at}"
        header += "]"

        blocks: list[str] = []
        for step in self.steps:
            if step.skipped:
                blocks.append(
                    f"--- step {step.index} [skipped] {step.label or step.command} ---"
                )
                continue
            status = (
                f"exit {step.exit_code}"
                if step.exit_code is not None
                else "no exit code"
            )
            suffix = " [output truncated]" if step.truncated else ""
            blocks.append(
                f"--- step {step.index} [{status}]{suffix} {step.command} ---"
            )
            if step.output:
                blocks.append(step.output)

        text = "\n".join([header, *blocks])
        if len(text) <= max_chars:
            return text
        return text[:max_chars] + "\n...[run_steps output truncated]"


# --------------------------------------------------------------------------
# The fusion engine
# --------------------------------------------------------------------------

# A step executor returns whatever the owning tool returns: a ``ToolResult``
# when it has status to report, or a plain string.
StepExecutor = Callable[[str, int | None], Awaitable[Any]]


@dataclass
class _StepResult:
    """Normalised view of one executor return value.

    ``unrunnable`` and ``fault`` are the two ways a step can fail without an exit
    code, and they are not interchangeable. ``unrunnable`` means the tool
    deliberately declined to run the command (policy screen, preflight refusal,
    a timeout) and reported that as an error; ``fault`` means the harness got a
    value it cannot interpret at all. Both stop the sequence, but only the first
    is a statement about the command.
    """

    text: str
    exit_code: int | None
    is_error: bool
    unrunnable: bool = False
    fault: bool = False


# The bash tool reports every pre-execution refusal as an "Error: ..." *string*,
# not as a ToolResult: an empty command, a preflight refusal, a policy screen, a
# timeout and a spawn failure all take that shape. Reading only the ToolResult
# flag would therefore classify a blocked command as a success, so the prefix is
# part of the contract.
_ERROR_PREFIX = "Error:"


def _normalise(raw: Any) -> _StepResult:
    """Read a tool return value without trusting its shape (dynamic boundary)."""
    if isinstance(raw, ToolResult):
        text = str(raw)
        metadata = raw.metadata if isinstance(raw.metadata, dict) else {}
        exit_code = metadata.get("exit_code")
        if not isinstance(exit_code, int) or isinstance(exit_code, bool):
            exit_code = None
        is_error = bool(raw.is_error)
        return _StepResult(
            text=text,
            exit_code=exit_code,
            is_error=is_error,
            unrunnable=is_error and exit_code is None,
        )
    if raw is None:
        # Nothing came back at all: a harness fault, not a declined command.
        return _StepResult(text="", exit_code=None, is_error=True, fault=True)
    if isinstance(raw, str):
        if raw.lstrip().startswith(_ERROR_PREFIX):
            return _StepResult(text=raw, exit_code=None, is_error=True, unrunnable=True)
        # A bare string that is not an error carries no status. It is read as
        # output with an unknown exit code rather than as a claimed success.
        return _StepResult(text=raw, exit_code=None, is_error=False)
    return _StepResult(text=str(raw), exit_code=None, is_error=False)


class StepFusion:
    """Execute a :class:`StepRequest` sequence through an injected executor."""

    def __init__(
        self,
        executor: StepExecutor,
        *,
        max_steps: int = DEFAULT_MAX_STEPS,
        max_total_chars: int = DEFAULT_MAX_TOTAL_CHARS,
        max_step_chars: int = DEFAULT_STEP_CHARS,
    ) -> None:
        if max_steps <= 0:
            raise ValueError("max_steps must be > 0")
        if max_total_chars <= 0:
            raise ValueError("max_total_chars must be > 0")
        if max_step_chars <= 0:
            raise ValueError("max_step_chars must be > 0")
        self._executor = executor
        self.max_steps = max_steps
        self.max_total_chars = max_total_chars
        self.max_step_chars = max_step_chars

    async def run(
        self,
        steps: Sequence[StepRequest],
        *,
        stop_on_error: bool = True,
    ) -> FusionOutcome:
        """Run *steps* in order and report the sequence's fate.

        A policy refusal ends the sequence unconditionally; an ordinary non-zero
        exit does so only when ``stop_on_error`` is set.
        """
        outcomes: list[StepOutcome] = []
        stopped_at: int | None = None
        contract = CONTRACT_OK
        remaining_chars = self.max_total_chars

        for position, step in enumerate(steps, start=1):
            if stopped_at is not None:
                outcomes.append(self._skipped(step, position))
                continue

            try:
                raw = await self._invoke(step)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # pragma: no cover - defensive boundary
                outcomes.append(
                    StepOutcome(
                        index=position,
                        command=step.command,
                        label=step.label,
                        exit_code=None,
                        is_error=True,
                        skipped=False,
                        output=f"{type(exc).__name__}: {exc}",
                        truncated=False,
                    )
                )
                contract = CONTRACT_ERROR
                stopped_at = position
                continue

            result = _normalise(raw)
            budget = self._step_budget(remaining_chars, len(steps) - position + 1)
            output = result.text[:budget]
            truncated = len(result.text) > len(output)
            remaining_chars -= len(output)

            outcomes.append(
                StepOutcome(
                    index=position,
                    command=step.command,
                    label=step.label,
                    exit_code=result.exit_code,
                    is_error=result.is_error,
                    skipped=False,
                    output=output,
                    truncated=truncated,
                )
            )

            failed = result.is_error or (result.exit_code not in (None, 0))
            if not failed:
                continue
            if result.fault:
                # An uninterpretable return value is a harness-level fault. There
                # is no policy question to weigh, so it always stops the run.
                contract = CONTRACT_ERROR
                stopped_at = position
            elif result.unrunnable:
                contract = CONTRACT_REFUSED
                stopped_at = position
            elif stop_on_error:
                contract = CONTRACT_STEP_FAILED
                stopped_at = position

        return FusionOutcome(
            steps=tuple(outcomes),
            contract=contract,
            stopped_at=stopped_at,
            output_chars=sum(len(step.output) for step in outcomes),
        )

    def _step_budget(self, remaining_chars: int, steps_left: int) -> int:
        """Output budget for one step.

        The remaining budget is shared evenly among the steps still to run, so a
        verbose step cannot starve the evidence of every step behind it — the
        last step of a build/test/lint sequence is usually the one that matters.
        Within a step, ``max_step_chars`` remains the only cap, so a short
        sequence still gets full output for each command.
        """
        if remaining_chars <= 0:
            return 0
        fair_share = remaining_chars // max(1, steps_left)
        return min(self.max_step_chars, max(0, fair_share))

    async def _invoke(self, step: StepRequest) -> Any:
        return await self._executor(step.command, step.timeout_s)

    @staticmethod
    def _skipped(step: StepRequest, position: int) -> StepOutcome:
        return StepOutcome(
            index=position,
            command=step.command,
            label=step.label,
            exit_code=None,
            is_error=False,
            skipped=True,
            output="",
            truncated=False,
        )


# --------------------------------------------------------------------------
# The tool
# --------------------------------------------------------------------------


class RunStepsTool(Tool):
    """Fuse a command sequence into one tool call, screened step by step.

    ``bash`` is the same tool instance the model can call directly. Fusion
    borrows its ``execute`` rather than re-implementing screening, so the policy
    screen, sandbox fence, environment scrub and output cap cannot diverge
    between the two entry points.
    """

    def __init__(self, bash: Any, *, fusion: StepFusion | None = None):
        self._bash = bash
        self._fusion = fusion or StepFusion(self._dispatch)

    @property
    def name(self) -> str:
        return "run_steps"

    @property
    def description(self) -> str:
        return (
            "Run a fixed sequence of shell commands in one call, in order, and "
            "return each step's exit code and output. Use it when the next few "
            "commands are already known (build, then test, then lint) to avoid "
            "a round trip per command. Every step is screened exactly like the "
            "bash tool; a blocked step stops the sequence. Set stop_on_error to "
            "false only to collect independent results."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "steps": {
                    "type": "array",
                    "description": (
                        "Commands to run in order. Each entry is either a command "
                        "string or an object with 'command', an optional 'label' "
                        "for the report, and an optional 'timeout_s'."
                    ),
                    "maxItems": DEFAULT_MAX_STEPS,
                },
                "stop_on_error": {
                    "type": "boolean",
                    "description": "Stop at the first failing step (default true).",
                },
            },
            "required": ["steps"],
            "additionalProperties": False,
        }

    @property
    def read_only(self) -> bool:
        return False

    @property
    def exclusive(self) -> bool:
        """A sequence of arbitrary commands must not interleave with other tools."""
        return True

    def uses(self, bash: Any) -> bool:
        """Whether *bash* is the execution path this tool delegates to."""
        return bash is self._bash

    async def _dispatch(self, command: str, timeout_s: int | None) -> Any:
        kwargs: dict[str, Any] = {"command": command}
        if timeout_s is not None:
            kwargs["timeout"] = timeout_s
        return await self._bash.execute(**kwargs)

    async def execute(self, **kwargs: Any) -> Any:
        try:
            steps = parse_steps(kwargs.get("steps"), max_steps=self._fusion.max_steps)
        except ValueError as exc:
            return ToolResult(
                f"Error: {exc}", is_error=True, metadata={"contract": "invalid"}
            )

        stop_on_error = kwargs.get("stop_on_error", True)
        if not isinstance(stop_on_error, bool):
            stop_on_error = True

        outcome = await self._fusion.run(steps, stop_on_error=stop_on_error)
        return ToolResult(
            outcome.render(max_chars=self._fusion.max_total_chars),
            is_error=not outcome.ok,
            metadata={
                "contract": outcome.contract,
                "stopped_at": outcome.stopped_at,
                "executed": outcome.executed,
                "total_steps": len(outcome.steps),
                "exit_codes": [
                    step.exit_code for step in outcome.steps if not step.skipped
                ],
            },
        )
