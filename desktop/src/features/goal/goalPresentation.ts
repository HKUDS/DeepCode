import type { TFunction } from "i18next";

import type { Goal, GoalOutcome, Turn } from "../../generated/app-server";

export type GoalPresentationStatus =
  | "working"
  | "readyToContinue"
  | "finishing"
  | "paused"
  | "blocked"
  | "budget_limited"
  | "complete";

export interface GoalPresentation {
  status: GoalPresentationStatus;
  linkedTurnCount: number;
  liveTurnId: string | null;
}

/** A Goal's state, in words. */
export function goalStatusLabel(
  status: GoalPresentationStatus,
  t: TFunction,
): string {
  switch (status) {
    case "working":
      return t("goal.status.working", "Working");
    case "readyToContinue":
      return t("goal.status.readyToContinue", "Ready to continue");
    case "finishing":
      return t("goal.status.finishing", "Finishing");
    case "paused":
      return t("goal.status.paused", "Paused");
    case "blocked":
      return t("goal.status.blocked", "Needs input");
    case "budget_limited":
      return t("goal.status.budgetLimited", "Budget reached");
    case "complete":
      return t("goal.status.complete", "Complete");
  }
}

/** Derive transient Goal UI state without extending the persisted ledger. */
export function deriveGoalPresentation(
  goal: Goal,
  turns: readonly Turn[],
  decidingTurnId: string | null = null,
): GoalPresentation {
  const linkedTurns = turns.filter((turn) => turn.goalId === goal.id);
  const liveTurn =
    linkedTurns
      .filter((turn) => !isTerminal(turn.status))
      .sort((left, right) => right.ordinal - left.ordinal)[0] ?? null;

  let status: GoalPresentationStatus;
  if (goal.status === "active") {
    status = liveTurn ? "working" : "readyToContinue";
  } else if (goal.status === "complete" && decidingTurnId) {
    const decidingTurn = turns.find((turn) => turn.id === decidingTurnId);
    status =
      decidingTurn && !isTerminal(decidingTurn.status)
        ? "finishing"
        : "complete";
  } else {
    status = goal.status;
  }

  return {
    status,
    linkedTurnCount: linkedTurns.length,
    liveTurnId: liveTurn?.id ?? null,
  };
}

function isTerminal(status: Turn["status"]): boolean {
  return (
    status === "completed" ||
    status === "failed" ||
    status === "interrupted"
  );
}

/** Who decided a Goal's outcome, as the user reads it. */
export function outcomeDecider(
  source: GoalOutcome["source"],
  t: TFunction,
): string {
  switch (source) {
    case "user":
      return t("goal.decidedBy.user", "Decided by you");
    case "agent":
      return t("goal.decidedBy.agent", "Decided by the agent");
    case "runtime":
      return t("goal.decidedBy.runtime", "Decided by DeepCode");
    case "migration":
      return t("goal.decidedBy.migration", "Carried over from an earlier version");
  }
}

export function formatOutcomeTime(value: string): string {
  const timestamp = new Date(value);
  if (Number.isNaN(timestamp.getTime())) return value;
  return timestamp.toLocaleString(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  });
}
