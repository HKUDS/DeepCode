import { AlertTriangle, CheckCircle2, ChevronRight } from "lucide-react";
import { useTranslation } from "react-i18next";

import type { GoalOutcome } from "../../generated/app-server";
import { MarkdownContent } from "../thread/MarkdownContent";
import styles from "./GoalOutcomeView.module.css";
import { formatOutcomeTime, outcomeDecider } from "./goalPresentation";

/**
 * How a Goal ended: who decided it and why, and the work behind it. The
 * caller frames it (the Goal rail, or the Turn that decided it) and names it.
 */
export function GoalOutcomeView({
  outcome,
  headingId,
  showReason = true,
  onSelectEvidence,
}: {
  outcome: GoalOutcome;
  /** Lets the caller's landmark take its name from the heading. */
  headingId?: string;
  showReason?: boolean;
  onSelectEvidence(itemId: string): void;
}) {
  const { t } = useTranslation();
  const complete = outcome.status === "complete";
  return (
    <div className={styles.outcome} data-status={outcome.status}>
      <header>
        {complete ? (
          <CheckCircle2 size={15} aria-hidden="true" />
        ) : (
          <AlertTriangle size={15} aria-hidden="true" />
        )}
        <strong id={headingId}>
          {complete
            ? t("goal.reached.complete", "Goal complete")
            : t("goal.reached.blocked", "Goal blocked")}
        </strong>
        <small>
          {outcomeDecider(outcome.source, t)} · {formatOutcomeTime(outcome.decidedAt)}
        </small>
      </header>
      {showReason ? <MarkdownContent compact>{outcome.reason}</MarkdownContent> : null}
      {outcome.evidenceRefs.length ? (
        <div className={styles.related}>
          <span>{t("goal.relatedActivity", "Related activity")}</span>
          <ul>
            {outcome.evidenceRefs.map((evidence) => (
              <li key={evidence.itemId}>
                <button
                  type="button"
                  onClick={() => onSelectEvidence(evidence.itemId)}
                >
                  <span>{evidence.summary}</span>
                  <ChevronRight size={13} aria-hidden="true" />
                </button>
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </div>
  );
}
