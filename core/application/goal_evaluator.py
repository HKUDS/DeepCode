"""Deterministic-first, read-only completion evaluation for Goals."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from core.application.llm_configuration_service import LLMConfigurationService
from core.application.test_service import TestRunResult, TestService
from core.application.turn_service import TurnSnapshot
from core.compat.runtime import DeepCodeRuntime
from core.config import load_config_for_workspace
from core.domain.execution_profile import ExecutionProfile, ExecutionSelection
from core.domain.goal import (
    GoalAttempt,
    GoalEvaluation,
    GoalRecord,
    GoalVerdict,
)
from core.domain.item import ItemKind


_PROMPT_DIRECTORY = Path(__file__).with_name("goal_prompts")
_MAX_FINAL_RESPONSE_CHARS = 20_000
_MAX_EVIDENCE_CHARS = 10_000
_MAX_EVIDENCE_TEXT_CHARS = 48_000


@dataclass(frozen=True, slots=True)
class GoalEvaluationContext:
    record: GoalRecord
    attempt: GoalAttempt
    turn: TurnSnapshot
    workspace: str


@dataclass(frozen=True, slots=True)
class SemanticDecision:
    verdict: GoalVerdict
    reason: str
    evidence_refs: tuple[str, ...]
    provider_name: str
    model_id: str
    tokens_used: int


class SemanticEvaluator(Protocol):
    async def evaluate(self, context: GoalEvaluationContext) -> SemanticDecision: ...


class GoalEvaluator:
    """Combine allowlisted test evidence with a read-only semantic decision."""

    def __init__(
        self,
        tests: TestService,
        semantic: SemanticEvaluator,
    ) -> None:
        self.tests = tests
        self.semantic = semantic

    async def evaluate(self, context: GoalEvaluationContext) -> GoalEvaluation:
        goal = context.record.goal
        verification: TestRunResult | None = None
        if goal.verification_command_id is not None:
            verification = await asyncio.to_thread(
                self.tests.run,
                goal.thread_id,
                context.turn.turn.id,
                goal.verification_command_id,
                timeout_seconds=goal.verification_timeout_seconds,
            )
            if verification.exit_code != 0 or verification.timed_out:
                reason = (
                    f"{verification.command.label} timed out."
                    if verification.timed_out
                    else (
                        f"{verification.command.label} failed with exit code "
                        f"{verification.exit_code}."
                    )
                )
                return GoalEvaluation(
                    goal_id=goal.id,
                    goal_revision=goal.definition_revision,
                    attempt_id=context.attempt.id,
                    turn_id=context.turn.turn.id,
                    verdict=GoalVerdict.CONTINUE,
                    reason=reason,
                    evidence_refs=(verification.item.id,),
                )

        decision = await self.semantic.evaluate(context)
        evidence_refs = decision.evidence_refs
        if verification is not None and verification.item.id not in evidence_refs:
            evidence_refs = (*evidence_refs, verification.item.id)
        return GoalEvaluation(
            goal_id=goal.id,
            goal_revision=goal.definition_revision,
            attempt_id=context.attempt.id,
            turn_id=context.turn.turn.id,
            verdict=decision.verdict,
            reason=decision.reason,
            evidence_refs=evidence_refs,
            evaluator_provider=decision.provider_name,
            evaluator_model=decision.model_id,
            tokens_used=decision.tokens_used,
        )


class ProviderSemanticEvaluator:
    """Evaluate Goal completion through the configured provider abstraction."""

    def __init__(
        self,
        llm_configuration: LLMConfigurationService,
    ) -> None:
        self.llm_configuration = llm_configuration
        self._template = _read_prompt("evaluator.md")

    async def evaluate(self, context: GoalEvaluationContext) -> SemanticDecision:
        goal = context.record.goal
        profile = self._profile(context)
        runtime = DeepCodeRuntime(load_config_for_workspace(context.workspace))
        provider = runtime.provider_for(execution_profile=profile)
        prompt = self._render_prompt(context)
        response = None
        parse_error: Exception | None = None
        for _attempt in range(goal.evaluator_retry_limit + 1):
            response = await provider.chat_with_retry(
                [
                    {
                        "role": "system",
                        "content": (
                            "You are a strict read-only completion evaluator. "
                            "Return only the requested JSON object."
                        ),
                    },
                    {"role": "user", "content": prompt},
                ],
                tools=None,
                model=profile.model_id,
                max_tokens=min(
                    goal.evaluator_max_tokens,
                    profile.max_output_tokens,
                ),
                temperature=goal.evaluator_temperature,
                reasoning_effort=profile.reasoning_effort,
                retry_mode="standard",
            )
            if response.finish_reason == "error":
                parse_error = RuntimeError(response.content or "evaluator request failed")
                continue
            try:
                verdict, reason, refs = self._parse(
                    response.content,
                    allowed_refs=self._allowed_evidence_refs(context),
                )
                return SemanticDecision(
                    verdict=verdict,
                    reason=reason,
                    evidence_refs=refs,
                    provider_name=profile.provider_name,
                    model_id=profile.model_id,
                    tokens_used=int(response.usage.get("total_tokens", 0)),
                )
            except (TypeError, ValueError, json.JSONDecodeError) as exc:
                parse_error = exc
        reason = (
            f"Completion evaluator failed: {parse_error}"
            if parse_error is not None
            else "Completion evaluator returned no usable response."
        )
        return SemanticDecision(
            verdict=GoalVerdict.ERROR,
            reason=reason,
            evidence_refs=(),
            provider_name=profile.provider_name,
            model_id=profile.model_id,
            tokens_used=(
                int(response.usage.get("total_tokens", 0))
                if response is not None
                else 0
            ),
        )

    def _profile(self, context: GoalEvaluationContext) -> ExecutionProfile:
        goal = context.record.goal
        if goal.evaluator_connection_id or goal.evaluator_model_id:
            return self.llm_configuration.resolve(
                context.workspace,
                ExecutionSelection(
                    connection_id=goal.evaluator_connection_id,
                    model_id=goal.evaluator_model_id,
                ),
                phase="implementation",
            )
        profile = context.turn.turn.execution_profile
        if profile is None:
            return self.llm_configuration.resolve(
                context.workspace,
                None,
                phase="implementation",
            )
        return profile

    def _render_prompt(self, context: GoalEvaluationContext) -> str:
        goal = context.record.goal
        final_response = ""
        evidence: list[str] = []
        for item in context.turn.items:
            if item.kind is ItemKind.ASSISTANT_MESSAGE:
                text = item.payload.get("text")
                if isinstance(text, str):
                    final_response = text
            if item.kind in {
                ItemKind.TEST_RESULT,
                ItemKind.FILE_CHANGE,
                ItemKind.DIFF,
                ItemKind.ERROR,
                ItemKind.COMPLETION,
            }:
                evidence.append(f"{item.id}: {item.summary}")
        final_response = final_response[-_MAX_FINAL_RESPONSE_CHARS:]
        evidence_text = ("\n".join(evidence) or "(no structured evidence was recorded)")
        evidence_text = evidence_text[-_MAX_EVIDENCE_CHARS:]
        rendered = (
            self._template.replace("{{OBJECTIVE}}", goal.objective)
            .replace(
                "{{ACCEPTANCE_CRITERIA}}",
                "\n".join(f"- {value}" for value in goal.acceptance_criteria)
                or "- The stated Goal is fully achieved.",
            )
            .replace(
                "{{FINAL_RESPONSE}}",
                final_response or "(no assistant response was recorded)",
            )
            .replace(
                "{{EVIDENCE}}",
                evidence_text,
            )
        )
        if len(rendered) > _MAX_EVIDENCE_TEXT_CHARS:
            raise ValueError("Goal evaluation context exceeds the configured bound")
        return rendered

    @staticmethod
    def _allowed_evidence_refs(context: GoalEvaluationContext) -> frozenset[str]:
        return frozenset(
            {context.turn.turn.id, *(item.id for item in context.turn.items)}
        )

    @staticmethod
    def _parse(
        content: str | None,
        *,
        allowed_refs: frozenset[str],
    ) -> tuple[GoalVerdict, str, tuple[str, ...]]:
        if not content or not content.strip():
            raise ValueError("evaluator response was empty")
        raw = content.strip()
        if raw.startswith("```"):
            raw = raw.removeprefix("```json").removeprefix("```")
            raw = raw.removesuffix("```").strip()
        try:
            value = json.loads(raw)
        except json.JSONDecodeError:
            start = raw.find("{")
            end = raw.rfind("}")
            if start < 0 or end <= start:
                raise
            value = json.loads(raw[start : end + 1])
        if not isinstance(value, dict):
            raise ValueError("evaluator output must be a JSON object")
        verdict = GoalVerdict(str(value.get("verdict", "")).lower())
        if verdict is GoalVerdict.ERROR:
            raise ValueError("the evaluator cannot emit the host-only error verdict")
        reason = str(value.get("reason", "")).strip()
        if not reason:
            raise ValueError("evaluator reason must not be empty")
        raw_refs = value.get("evidenceRefs", [])
        if not isinstance(raw_refs, list) or any(
            not isinstance(item, str) for item in raw_refs
        ):
            raise ValueError("evidenceRefs must be an array of strings")
        refs = tuple(dict.fromkeys(item for item in raw_refs if item in allowed_refs))
        return verdict, reason, refs


def _read_prompt(name: str) -> str:
    return (_PROMPT_DIRECTORY / name).read_text(encoding="utf-8").strip()


__all__ = [
    "GoalEvaluationContext",
    "GoalEvaluator",
    "ProviderSemanticEvaluator",
    "SemanticDecision",
    "SemanticEvaluator",
]
