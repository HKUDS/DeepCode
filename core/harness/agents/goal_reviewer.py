"""An independent reviewer for Goal completion claims.

The agent that did the work says the Goal is complete; a second agent, with
its own context, checks that claim against the workspace before the Goal is
recorded complete. The reviewer can look but not touch: it reads files,
searches, and runs commands (tests, builds, ``git diff``) in a sandbox where
the workspace is read-only. It cannot edit files, start sub-agents, use MCP
servers or ask anyone for approval.

The cheap rule check runs first: a claim whose latest check failed is
rejected without starting the reviewer.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from typing import Any

from core.agent_runtime.goal_verifier import (
    GoalCompletionRequest,
    GoalEvidenceItem,
    GoalVerdict,
    RuleBasedVerifier,
)
from core.domain.execution_security import (
    ApprovalPolicy,
    ExecutionSecurityProfile,
    FilesystemScope,
)
from core.domain.execution_permission import ExecutionPermissionMode

REVIEW_TIMEOUT_SECONDS = 900
REVIEW_MAX_ITERATIONS = 40
_TEXT_MAX_CHARS = 1_200
_LIST_MAX_ITEMS = 12

VERDICT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "verdict": {
            "type": "string",
            "enum": ["pass", "fail", "impossible"],
            "description": (
                "pass: every requirement of the Goal is met in the workspace "
                "now. fail: something is missing or broken, and the agent can "
                "fix it. impossible: the Goal cannot be met here at all."
            ),
        },
        "summary": {
            "type": "string",
            "description": "One or two sentences: what you found.",
        },
        "missing": {
            "type": "array",
            "items": {"type": "string"},
            "description": (
                "For fail or impossible: each unmet requirement, concrete "
                "enough to act on (file, test, behavior)."
            ),
        },
        "checked": {
            "type": "array",
            "items": {"type": "string"},
            "description": "What you actually checked: commands run, files read.",
        },
    },
    "required": ["verdict", "summary"],
}

REVIEWER_PROMPT = """\
## Your role: independent Goal reviewer

Another agent worked on the Goal below and now says it is complete. You did
not do that work. Decide whether the Goal is really met in the workspace as it
is now.

How to review:
1. Split the Goal into its requirements. Every part of it counts.
2. See what changed: `git status` and `git diff` when the workspace is a git
   repository, otherwise the files the evidence names.
3. Read the code that matters. Look for stubs, TODOs, hard-coded results,
   skipped or deleted tests, and requirements that were only partly done.
4. Re-run the checks that prove the work, such as the project's tests or build,
   when that takes a few minutes at most. Do not trust the agent's evidence
   alone when you can check it yourself.

You cannot change anything: you have no editing tools, and commands run with a
read-only workspace (the temporary directory stays writable). If a command
fails only because it could not write to the workspace ("Read-only file
system", "Operation not permitted"), that result tells you nothing about the
work; do not count it against the claim.

Judge the Goal as written. Do not ask for work beyond it, and do not fail a
claim over style. Pass only when every requirement is met. Use "impossible"
only when the Goal cannot be met in this environment at all, for example when
it needs credentials or a service that is not there, or contradicts itself.
"""


def reviewer_security_profile(
    parent: ExecutionSecurityProfile | None,
) -> ExecutionSecurityProfile:
    """The reviewer's own rules: nothing waits for approval, nothing writes.

    Writes are already impossible (no editing tools, read-only command
    sandbox), so no call needs a person; the user's permission rules still
    apply, and a rule that would ask denies instead.
    """

    return ExecutionSecurityProfile.from_legacy(
        ExecutionPermissionMode.FULL_AUTO,
        command_sandbox=True,
        filesystem_scope=FilesystemScope.WORKSPACE,
        approval_policy=ApprovalPolicy.NEVER,
        permission_rules=parent.permission_rules if parent is not None else (),
    )


def _clip(text: str, limit: int = _TEXT_MAX_CHARS) -> str:
    text = text.strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _evidence_lines(items: tuple[GoalEvidenceItem, ...]) -> str:
    lines = []
    for item in items:
        label = item.name or item.kind.replace("_", " ")
        lines.append(f"- [{item.status}] {label}: {item.summary}")
    return "\n".join(lines) or "- (none)"


def review_task(request: GoalCompletionRequest) -> str:
    """The reviewer's first message: the Goal, the claim, and its evidence."""

    parts = [
        "Review this completion claim.",
        f"Goal:\n{request.objective.strip()}",
        f"The agent's reason for calling it complete:\n{_clip(request.reason)}",
        "The agent's latest steps in this Turn (oldest first):\n"
        + _evidence_lines(request.evidence),
    ]
    if request.pending:
        parts.append(
            "Steps still running when the claim was made (no result yet):\n"
            + _evidence_lines(request.pending)
        )
    parts.append("Check the workspace, then submit your verdict.")
    return "\n\n".join(parts)


def verdict_from_result(result: dict[str, Any]) -> GoalVerdict:
    """Turn the reviewer's submission into the verdict the agent reads."""

    verdict = str(result.get("verdict") or "")
    summary = _clip(str(result.get("summary") or ""))
    missing = [
        _clip(str(entry), 400)
        for entry in (result.get("missing") or ())
        if str(entry).strip()
    ][:_LIST_MAX_ITEMS]
    if verdict == "pass":
        return GoalVerdict(
            confirmed=True,
            justification=f"An independent review passed the claim: {summary}",
        )
    if verdict not in {"fail", "impossible"}:
        raise ValueError(f"the reviewer returned an unknown verdict: {verdict!r}")
    text = (
        "An independent review found the Goal cannot be met here: "
        if verdict == "impossible"
        else "An independent review did not accept the claim: "
    ) + summary
    if missing:
        text += "\nStill missing:\n" + "\n".join(f"- {entry}" for entry in missing)
    return GoalVerdict(
        confirmed=False,
        justification=text,
        impossible=verdict == "impossible",
    )


ReviewRunner = Callable[[str, str], Awaitable[dict[str, Any] | None]]


class AgentReviewVerifier:
    """Rule check first, then an independent read-only reviewer agent.

    ``run_review(system_prompt_section, task)`` runs one reviewer session and
    returns its structured submission, or ``None`` when it submitted nothing.
    A reviewer that fails, times out or submits nothing raises: the Goal
    runtime fails closed and reports the error to the agent as a rejection.
    """

    def __init__(
        self,
        run_review: ReviewRunner,
        *,
        timeout_seconds: float = REVIEW_TIMEOUT_SECONDS,
    ) -> None:
        self._run_review = run_review
        self._timeout_seconds = timeout_seconds
        self._rule = RuleBasedVerifier()

    async def __call__(self, request: GoalCompletionRequest) -> GoalVerdict:
        ruled = await self._rule(request)
        if not ruled.confirmed:
            return ruled
        try:
            result = await asyncio.wait_for(
                self._run_review(REVIEWER_PROMPT, review_task(request)),
                timeout=self._timeout_seconds,
            )
        except TimeoutError as exc:
            raise RuntimeError(
                f"the independent review took longer than {int(self._timeout_seconds)}s"
            ) from exc
        if result is None:
            raise RuntimeError("the independent reviewer gave no verdict")
        return verdict_from_result(result)


def session_review_runner(
    *,
    workspace: str,
    build_session: Callable[..., Any],
    session_kwargs: dict[str, Any],
) -> ReviewRunner:
    """Run each review in a fresh read-only session built by ``build_session``.

    ``build_session`` is :func:`core.agent_setup.build_agent_session`;
    ``session_kwargs`` carry the parent's model and runtime choices.
    """

    async def run(section: str, task: str) -> dict[str, Any] | None:
        from core.agent_setup import SYSTEM_PROMPT
        from core.events import UserInput
        from core.harness.agents.structured_result import (
            CAPTURE_TOOL_NAME,
            StructuredResultCapture,
        )

        capture = StructuredResultCapture(VERDICT_SCHEMA)
        session, _model, _engine = build_session(
            workspace=workspace,
            system_prompt="\n\n".join(
                (SYSTEM_PROMPT, section, capture.prompt_addendum())
            ),
            allow_spawn=False,
            read_only_workspace=True,
            max_iterations=REVIEW_MAX_ITERATIONS,
            extra_tools=(capture.make_tool(),),
            tool_filter=lambda names: tuple(
                name
                for name in names
                if name in {"read", "grep", "glob", "bash", CAPTURE_TOOL_NAME}
            ),
            **session_kwargs,
        )
        try:
            async for _event in session.run_stream(UserInput(text=task)):
                pass
        finally:
            await session.aclose()
        rendered = capture.render()
        return json.loads(rendered) if rendered is not None else None

    return run


__all__ = [
    "REVIEWER_PROMPT",
    "VERDICT_SCHEMA",
    "AgentReviewVerifier",
    "review_task",
    "reviewer_security_profile",
    "session_review_runner",
    "verdict_from_result",
]
