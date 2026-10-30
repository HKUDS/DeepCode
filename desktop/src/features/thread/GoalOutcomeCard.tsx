import { AlertTriangle, CheckCircle2, ChevronRight } from "lucide-react";
import { useTranslation } from "react-i18next";

import type { GoalOutcome } from "../../generated/app-server";
import { formatOutcomeTime, outcomeDecider } from "../goal/goalPresentation";
import styles from "./GoalOutcomeCard.module.css";
import { MarkdownContent } from "./MarkdownContent";

/** How a Goal ended, shown at the end of the Turn that decided it. */
export function GoalOutcomeCard({
  outcome,
  onSelectEvidence,
}: {
  outcome: GoalOutcome;
  onSelectEvidence(itemId: string): void;
}) {
  const { t } = useTranslation();
  const complete = outcome.status === "complete";
  const heading = complete
    ? t("goal.reached.complete", "Goal complete")
    : t("goal.reached.blocked", "Goal blocked");
  return (
    <section
      className={styles.card}
      data-status={outcome.status}
      aria-label={heading}
    >
      <header>
        {complete ? (
          <CheckCircle2 size={16} aria-hidden="true" />
        ) : (
          <AlertTriangle size={16} aria-hidden="true" />
        )}
        <strong>{heading}</strong>
        <small>
          {outcomeDecider(outcome.source, t)} · {formatOutcomeTime(outcome.decidedAt)}
        </small>
      </header>
      <MarkdownContent compact>{outcome.reason}</MarkdownContent>
      {outcome.evidenceRefs.length ? (
        <div className={styles.evidence}>
          <span>{t("goal.evidence", "Evidence")}</span>
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
    </section>
  );
}
