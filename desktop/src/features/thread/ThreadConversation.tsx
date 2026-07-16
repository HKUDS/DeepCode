import { useEffect, useMemo, useRef } from "react";
import {
  AlertTriangle,
  CheckCircle2,
  ChevronRight,
  Clock3,
  FilePenLine,
  FlaskConical,
  ListChecks,
  PackageOpen,
  RotateCcw,
  ScrollText,
  TerminalSquare,
  Wrench,
} from "lucide-react";

import type {
  Approval,
  ApprovalDecision,
  Item,
  Turn,
} from "../../generated/app-server";
import type { DesktopInspectorTab } from "../../app/useDesktopUi";
import { ApprovalCard } from "../execution/ApprovalCard";
import { formatTimestamp, presentItem } from "../execution/itemPresentation";
import { MarkdownContent } from "./MarkdownContent";
import styles from "./ThreadConversation.module.css";

interface ThreadConversationProps {
  turns: Turn[];
  items: Item[];
  approvals: Approval[];
  selectedItemId: string | null;
  busy: boolean;
  onSelectItem(itemId: string): void;
  onOpenInspector(tab?: DesktopInspectorTab): void;
  onRespondToApproval(approvalId: string, decision: ApprovalDecision): void;
  onRetryTurn(turnId: string): void;
  onCancelQueuedTurn(turnId: string): void;
}

const activityKinds = new Set<Item["kind"]>([
  "command_execution",
  "tool_call",
  "file_change",
  "diff",
  "test_result",
  "artifact",
  "workflow_stage",
]);

function ActivityIcon({ kind }: { kind: Item["kind"] }) {
  const props = { size: 15, strokeWidth: 1.8 };
  switch (kind) {
    case "command_execution":
      return <TerminalSquare {...props} />;
    case "file_change":
    case "diff":
      return <FilePenLine {...props} />;
    case "test_result":
      return <FlaskConical {...props} />;
    case "artifact":
      return <PackageOpen {...props} />;
    case "workflow_stage":
      return <ScrollText {...props} />;
    default:
      return <Wrench {...props} />;
  }
}

export function ThreadConversation({
  turns,
  items,
  approvals,
  selectedItemId,
  busy,
  onSelectItem,
  onOpenInspector,
  onRespondToApproval,
  onRetryTurn,
  onCancelQueuedTurn,
}: ThreadConversationProps) {
  const endRef = useRef<HTMLDivElement | null>(null);
  const turnsById = useMemo(
    () => new Map(turns.map((turn) => [turn.id, turn])),
    [turns],
  );
  const ordered = useMemo(
    () =>
      [...items].sort((left, right) => {
        const turnDifference =
          (turnsById.get(left.turnId)?.ordinal ?? 0) -
          (turnsById.get(right.turnId)?.ordinal ?? 0);
        return turnDifference || left.ordinal - right.ordinal;
      }),
    [items, turnsById],
  );
  const approvalsByItem = useMemo(
    () => new Map(approvals.map((approval) => [approval.itemId, approval])),
    [approvals],
  );
  const lastItem = ordered.at(-1);

  useEffect(() => {
    if (!lastItem) return;
    const end = endRef.current;
    if (typeof end?.scrollIntoView === "function") {
      end.scrollIntoView({
        block: "end",
        behavior: lastItem.status === "in_progress" ? "smooth" : "auto",
      });
    }
  }, [lastItem]);

  if (ordered.length === 0) {
    return (
      <div className={styles.empty}>
        <div className={styles.emptyMark} aria-hidden="true">
          <ListChecks size={21} strokeWidth={1.7} />
        </div>
        <h2>What should DeepCode build?</h2>
        <p>
          Describe the outcome, constraints, and how the result should be verified.
          DeepCode will keep the work and review trail in this Session.
        </p>
        <div className={styles.examples} aria-label="Example tasks">
          <span>Fix a failing test and verify the regression</span>
          <span>Review this branch for unsafe behavior</span>
          <span>Implement the next milestone from the plan</span>
        </div>
      </div>
    );
  }

  return (
    <div className={styles.conversation} aria-label="Thread conversation">
      {ordered.map((item) => {
        const presentation = presentItem(item);
        const approval = approvalsByItem.get(item.id);
        const active = item.id === selectedItemId;
        const turn = turnsById.get(item.turnId);

        if (item.kind === "user_message") {
          const queued = turn?.status === "queued";
          return (
            <article
              className={styles.userMessage}
              data-queued={queued}
              key={item.id}
            >
              <div className={styles.userBubble}>
                <MarkdownContent>
                  {presentation.body ?? item.summary}
                </MarkdownContent>
              </div>
              {queued ? (
                <div className={styles.queueState}>
                  <Clock3 size={12} />
                  <span>Queued</span>
                  <button
                    type="button"
                    onClick={() => onCancelQueuedTurn(item.turnId)}
                    disabled={busy}
                  >
                    Cancel
                  </button>
                </div>
              ) : null}
            </article>
          );
        }

        if (item.kind === "assistant_message") {
          return (
            <article className={styles.assistantMessage} key={item.id}>
              <div className={styles.messageMeta}>
                <strong>DeepCode</strong>
                <time dateTime={item.updatedAt}>{formatTimestamp(item.updatedAt)}</time>
              </div>
              <div
                className={styles.messageBody}
                aria-live={item.status === "in_progress" ? "polite" : undefined}
              >
                <MarkdownContent>
                  {presentation.body ?? item.summary}
                </MarkdownContent>
                {item.status === "in_progress" ? (
                  <span className={styles.streamingCursor} aria-label="Agent is responding" />
                ) : null}
              </div>
            </article>
          );
        }

        if (item.kind === "approval_request" && approval) {
          return (
            <section className={styles.approval} key={item.id}>
              <div className={styles.approvalHeading}>
                <AlertTriangle size={17} />
                <span>
                  <strong>Approval required</strong>
                  <small>{item.summary}</small>
                </span>
              </div>
              <ApprovalCard
                approval={approval}
                busy={busy}
                onRespond={onRespondToApproval}
              />
            </section>
          );
        }

        if (item.kind === "reasoning_summary" || item.kind === "plan") {
          return (
            <details
              className={styles.plan}
              key={item.id}
              open={item.kind === "plan" || item.status === "in_progress"}
            >
              <summary>
                {item.kind === "plan" ? (
                  <ListChecks size={16} />
                ) : (
                  <ScrollText size={16} />
                )}
                <strong>{presentation.label}</strong>
                <span>{item.status.replaceAll("_", " ")}</span>
              </summary>
              <div>
                <MarkdownContent compact>
                  {presentation.body ?? item.summary}
                </MarkdownContent>
              </div>
            </details>
          );
        }

        if (activityKinds.has(item.kind)) {
          return (
            <details
              className={styles.activity}
              data-active={active}
              data-status={item.status}
              key={item.id}
              open={item.status === "in_progress" || active}
            >
              <summary>
                <ActivityIcon kind={item.kind} />
                <span>
                  <strong>{item.summary || presentation.label}</strong>
                  <small>{presentation.label}</small>
                </span>
                <span className={styles.activityStatus}>
                  {item.status.replaceAll("_", " ")}
                </span>
                <ChevronRight className={styles.chevron} size={14} />
              </summary>
              {presentation.body ? <pre>{presentation.body}</pre> : null}
              <button
                type="button"
                className={styles.inspectAction}
                onClick={() => {
                  onSelectItem(item.id);
                  onOpenInspector("details");
                }}
              >
                Inspect details
              </button>
            </details>
          );
        }

        if (item.kind === "completion") {
          const retryable =
            item.status === "failed" ||
            turn?.status === "failed" ||
            turn?.status === "interrupted";
          const recoveredAfterRestart =
            item.payload.stopReason === "application_restarted";
          return (
            <section
              className={styles.completion}
              data-status={retryable ? "failed" : "completed"}
              key={item.id}
            >
              <CheckCircle2 size={18} />
              <span>
                <strong>{item.summary || "Turn complete"}</strong>
                <small>
                  {recoveredAfterRestart
                    ? "The previous process stopped. Retry from the same prompt."
                    : retryable
                      ? "Inspect the failure or retry this turn."
                      : "Review changes, tests, and execution details."}
                </small>
              </span>
              <div className={styles.completionActions}>
                {retryable ? (
                  <button
                    type="button"
                    onClick={() => onRetryTurn(item.turnId)}
                    disabled={busy}
                  >
                    <RotateCcw size={13} />
                    Retry
                  </button>
                ) : null}
                <button
                  type="button"
                  aria-label="Review turn details"
                  onClick={() => {
                    onSelectItem(item.id);
                    onOpenInspector("changes");
                  }}
                >
                  Review
                  <ChevronRight size={14} />
                </button>
              </div>
            </section>
          );
        }

        if (item.kind === "error") {
          return (
            <article className={styles.error} key={item.id}>
              <AlertTriangle size={17} />
              <span>
                <strong>{item.summary}</strong>
                <p>{presentation.body}</p>
              </span>
            </article>
          );
        }

        return null;
      })}
      <div ref={endRef} />
    </div>
  );
}
