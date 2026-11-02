import {
  AlertTriangle,
  CheckCircle2,
  CircleSlash,
  ListChecks,
  XCircle,
} from "lucide-react";
import type { TFunction } from "i18next";
import { useTranslation } from "react-i18next";

import type { GoalCheck, GoalReview } from "../../generated/app-server";
import { formatOutcomeTime, reviewResultLabel } from "./goalPresentation";
import styles from "./GoalChecksView.module.css";

function commandText(command: readonly string[]): string {
  return command
    .map((part) => (/^[\w./:=@%+,-]+$/.test(part) ? part : JSON.stringify(part)))
    .join(" ");
}

function CheckList({ checks }: { checks: readonly GoalCheck[] }) {
  return (
    <ul className={styles.checkList}>
      {checks.map((check, index) => (
        <li key={`${index}:${check.requirement}`}>
          <span>{check.requirement}</span>
          {check.command.length ? (
            <code>{commandText(check.command)}</code>
          ) : null}
        </li>
      ))}
    </ul>
  );
}

/** The agent's proposed checklist, waiting for the user's decision. */
export function GoalCheckProposal({
  checks,
  busy,
  onConfirm,
  onDismiss,
}: {
  checks: readonly GoalCheck[];
  busy: boolean;
  onConfirm(): void;
  onDismiss(): void;
}) {
  const { t } = useTranslation();
  return (
    <section
      className={styles.proposal}
      aria-label={t("goal.checks.proposalLabel", "Proposed checks")}
    >
      <header>
        <ListChecks size={14} aria-hidden="true" />
        <div>
          <strong>
            {t("goal.checks.proposed", {
              count: checks.length,
              defaultValue_one: "The agent proposed {{count}} check",
              defaultValue_other: "The agent proposed {{count}} checks",
            })}
          </strong>
          <small>
            {t(
              "goal.checks.proposedNote",
              "Once you use them, DeepCode runs each command itself whenever the Goal is claimed complete, and turns the claim back if one fails.",
            )}
          </small>
        </div>
      </header>
      <CheckList checks={checks} />
      <footer>
        <button type="button" onClick={onDismiss} disabled={busy}>
          {t("goal.checks.dismiss", "Dismiss")}
        </button>
        <button
          className={styles.primary}
          type="button"
          onClick={onConfirm}
          disabled={busy}
        >
          {t("goal.checks.use", "Use these checks")}
        </button>
      </footer>
    </section>
  );
}

/** The checklist the user confirmed. */
export function GoalConfirmedChecks({
  checks,
  busy,
  onRemove,
}: {
  checks: readonly GoalCheck[];
  busy: boolean;
  onRemove(): void;
}) {
  const { t } = useTranslation();
  return (
    <section
      className={styles.confirmed}
      aria-label={t("goal.checks.title", "Checks")}
    >
      <header>
        <strong>{t("goal.checks.title", "Checks")}</strong>
        <small>
          {t(
            "goal.checks.confirmedNote",
            "DeepCode runs these commands before it accepts completion.",
          )}
        </small>
      </header>
      <CheckList checks={checks} />
      <button
        className={styles.quiet}
        type="button"
        onClick={onRemove}
        disabled={busy}
      >
        {t("goal.checks.remove", "Remove checks")}
      </button>
    </section>
  );
}

function stageLabel(stage: GoalReview["stage"], t: TFunction): string {
  switch (stage) {
    case "rule":
      return t("goal.review.stage.rule", "Rule check");
    case "checks":
      return t("goal.review.stage.checks", "Your checks");
    case "reviewer":
      return t("goal.review.stage.reviewer", "Independent review");
    case "error":
      return t("goal.review.stage.error", "Check failed to run");
    case "other":
      return t("goal.review.stage.other", "Completion check");
  }
}

function findingLabel(
  finding: GoalReview["requirements"][number]["finding"],
  t: TFunction,
): string {
  switch (finding) {
    case "proved":
      return t("goal.review.finding.proved", "Proved");
    case "contradicted":
      return t("goal.review.finding.contradicted", "Not met");
    case "incomplete":
      return t("goal.review.finding.incomplete", "Partly done");
    case "weak":
      return t("goal.review.finding.weak", "Weak evidence");
    case "missing":
      return t("goal.review.finding.missing", "No evidence");
  }
}

function ResultIcon({ review }: { review: GoalReview }) {
  if (review.result === "passed") return <CheckCircle2 size={15} aria-hidden="true" />;
  if (review.result === "unavailable") return <CircleSlash size={15} aria-hidden="true" />;
  if (review.result === "impossible") return <AlertTriangle size={15} aria-hidden="true" />;
  return <XCircle size={15} aria-hidden="true" />;
}

/** The latest completion check in full, earlier ones in one line each. */
export function GoalReviewView({ reviews }: { reviews: readonly GoalReview[] }) {
  const { t } = useTranslation();
  const latest = reviews[reviews.length - 1];
  if (!latest) return null;
  const earlier = reviews.slice(0, -1).reverse();
  const proved = latest.requirements.filter((entry) => entry.finding === "proved").length;
  // A failed check's output is shown with the check itself.
  const text =
    latest.stage === "checks" && latest.result !== "unavailable"
      ? ""
      : latest.summary || latest.justification;
  return (
    <section
      className={styles.review}
      data-result={latest.result}
      aria-label={t("goal.review.title", "Latest completion check")}
    >
      <header>
        <ResultIcon review={latest} />
        <strong>{reviewResultLabel(latest, t)}</strong>
        <small>
          {stageLabel(latest.stage, t)} · {formatOutcomeTime(latest.recordedAt)}
        </small>
      </header>
      {text ? <p className={styles.reviewText}>{text}</p> : null}
      {latest.requirements.length ? (
        <div className={styles.requirements}>
          <span>
            {t("goal.review.requirementsProved", "{{proved}} of {{total}} requirements proved", {
              proved,
              total: latest.requirements.length,
            })}
          </span>
          <table>
            <thead>
              <tr>
                <th scope="col">{t("goal.review.requirement", "Requirement")}</th>
                <th scope="col">{t("goal.review.finding", "Finding")}</th>
                <th scope="col">{t("goal.review.evidence", "Evidence")}</th>
              </tr>
            </thead>
            <tbody>
              {latest.requirements.map((entry, index) => (
                <tr key={`${index}:${entry.requirement}`} data-finding={entry.finding}>
                  <td>{entry.requirement}</td>
                  <td>
                    <span className={styles.finding}>{findingLabel(entry.finding, t)}</span>
                  </td>
                  <td>{entry.evidence}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
      {latest.checks.length ? (
        <ul className={styles.runs}>
          {latest.checks.map((run, index) => {
            const passed = run.exitCode === 0;
            const status = passed
              ? t("goal.review.runPassed", "exited 0")
              : run.exitCode === null
                ? run.problem
                : t("goal.review.runFailed", "exited {{code}}", { code: run.exitCode });
            return (
              <li key={`${index}:${run.requirement}`} data-passed={passed}>
                <div>
                  {passed ? (
                    <CheckCircle2 size={12} aria-hidden="true" />
                  ) : (
                    <XCircle size={12} aria-hidden="true" />
                  )}
                  <code>{commandText(run.command)}</code>
                  <small>
                    {status} · {run.seconds}s
                  </small>
                </div>
                {!passed && run.output ? (
                  <details>
                    <summary>{t("goal.review.output", "Output")}</summary>
                    <pre>{run.output}</pre>
                  </details>
                ) : null}
              </li>
            );
          })}
        </ul>
      ) : null}
      {earlier.length ? (
        <details className={styles.earlier}>
          <summary>
            {t("goal.review.earlier", {
              count: earlier.length,
              defaultValue_one: "{{count}} earlier check",
              defaultValue_other: "{{count}} earlier checks",
            })}
          </summary>
          <ul>
            {earlier.map((review) => (
              <li key={`${review.turnId}|${review.recordedAt}|${review.stage}`}>
                <span data-result={review.result}>{reviewResultLabel(review, t)}</span>
                <small>
                  {stageLabel(review.stage, t)} · {formatOutcomeTime(review.recordedAt)}
                </small>
              </li>
            ))}
          </ul>
        </details>
      ) : null}
    </section>
  );
}
