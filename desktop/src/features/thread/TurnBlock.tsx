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
  Sparkles,
  TerminalSquare,
  Wrench,
} from "lucide-react";
import { useEffect, useState } from "react";

import type {
  Approval,
  ApprovalDecision,
  Item,
} from "../../generated/app-server";
import type { DesktopInspectorTab } from "../../app/useDesktopUi";
import { ApprovalCard } from "../execution/ApprovalCard";
import { presentItem } from "../execution/itemPresentation";
import {
  formatTurnDuration,
  turnDurationSeconds,
  type ConversationTurn,
} from "./conversationModel";
import { MarkdownContent } from "./MarkdownContent";
import styles from "./ThreadConversation.module.css";

interface TurnBlockProps {
  group: ConversationTurn;
  approvalsByItem: ReadonlyMap<string, Approval>;
  selectedItemId: string | null;
  busy: boolean;
  onSelectItem(itemId: string): void;
  onOpenInspector(tab?: DesktopInspectorTab): void;
  onRespondToApproval(approvalId: string, decision: ApprovalDecision): void;
  onRetryTurn(turnId: string): void;
  onCancelQueuedTurn(turnId: string): void;
}

const activeTurnStatuses = new Set(["queued", "running", "waiting_approval"]);

interface SkillInvocationView {
  skillId: string;
  name: string;
  revision?: string;
  invocation?: string;
}

function itemSkills(item: Item): SkillInvocationView[] {
  const value = item.payload.skills;
  if (!Array.isArray(value)) return [];
  return value.flatMap((candidate) => {
    if (
      typeof candidate !== "object" ||
      candidate === null ||
      Array.isArray(candidate)
    ) {
      return [];
    }
    const skillId = candidate.skillId;
    const name = candidate.name;
    if (typeof skillId !== "string" || typeof name !== "string") return [];
    return [
      {
        skillId,
        name,
        revision:
          typeof candidate.revision === "string" ? candidate.revision : undefined,
        invocation:
          typeof candidate.invocation === "string"
            ? candidate.invocation
            : undefined,
      },
    ];
  });
}

function ActivityIcon({ kind }: { kind: Item["kind"] }) {
  const props = { size: 14, strokeWidth: 1.8 };
  switch (kind) {
    case "plan":
      return <ListChecks {...props} />;
    case "reasoning_summary":
      return <ScrollText {...props} />;
    case "command_execution":
      return <TerminalSquare {...props} />;
    case "file_change":
    case "diff":
      return <FilePenLine {...props} />;
    case "test_result":
      return <FlaskConical {...props} />;
    case "artifact":
      return <PackageOpen {...props} />;
    default:
      return <Wrench {...props} />;
  }
}

function approvalOutcome(status: Approval["status"]): string {
  switch (status) {
    case "approved_once":
      return "Allowed once";
    case "approved_session":
      return "Allowed for this Session";
    case "denied":
      return "Denied";
    case "cancelled":
      return "Cancelled";
    default:
      return "Approval resolved";
  }
}

function useElapsedNow(active: boolean): number {
  const [now, setNow] = useState(Date.now);

  useEffect(() => {
    if (!active) return;
    const interval = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(interval);
  }, [active]);

  return now;
}

function runLabel(group: ConversationTurn, now: number): string {
  const status = group.turn?.status;
  const duration = turnDurationSeconds(group.turn, now);
  const formatted = duration === null ? null : formatTurnDuration(duration);
  switch (status) {
    case "queued":
      return "Queued";
    case "running":
      return formatted ? `Working for ${formatted}` : "Working";
    case "waiting_approval":
      return formatted ? `Waiting after ${formatted}` : "Waiting for approval";
    case "failed":
      return formatted ? `Failed after ${formatted}` : "Run failed";
    case "interrupted":
      return formatted ? `Stopped after ${formatted}` : "Run stopped";
    default:
      return formatted ? `Worked for ${formatted}` : "Work completed";
  }
}

function shouldShowRunLedger(group: ConversationTurn, now: number): boolean {
  if (group.executionItems.length > 0 || group.completion) return true;
  if (group.turn?.status === "queued") return false;
  if (group.turn && group.turn.status !== "completed") return true;
  return (turnDurationSeconds(group.turn, now) ?? 0) > 0;
}

function RunStep({
  item,
  approval,
  active,
  busy,
  onSelectItem,
  onOpenInspector,
  onRespondToApproval,
}: {
  item: Item;
  approval: Approval | undefined;
  active: boolean;
  busy: boolean;
  onSelectItem(itemId: string): void;
  onOpenInspector(tab?: DesktopInspectorTab): void;
  onRespondToApproval(approvalId: string, decision: ApprovalDecision): void;
}) {
  if (item.kind === "approval_request" && approval) {
    const pending = approval.status === "pending";
    return (
      <section
        className={styles.runApproval}
        data-pending={pending}
        data-status={approval.status}
      >
        <div className={styles.runApprovalHeading}>
          {pending ? <AlertTriangle size={15} /> : <CheckCircle2 size={15} />}
          <span>
            <strong>
              {pending ? "Approval required" : approvalOutcome(approval.status)}
            </strong>
            <small>{item.summary}</small>
          </span>
        </div>
        {pending ? (
          <ApprovalCard
            approval={approval}
            busy={busy}
            onRespond={onRespondToApproval}
          />
        ) : (
          <span className={styles.runApprovalDecision}>
            Decision: {approval.status.replaceAll("_", " ")}
          </span>
        )}
      </section>
    );
  }

  const presentation = presentItem(item);
  const body = presentation.body;
  const hasBody = Boolean(body && body.trim() && body.trim() !== item.summary.trim());
  const heading = (
    <>
      <ActivityIcon kind={item.kind} />
      <span className={styles.runStepCopy}>
        <strong>{item.summary || presentation.label}</strong>
        <small>{presentation.label}</small>
      </span>
      <span className={styles.runStepStatus}>
        {item.status.replaceAll("_", " ")}
      </span>
      {hasBody ? <ChevronRight className={styles.runStepChevron} size={13} /> : null}
    </>
  );

  if (!hasBody) {
    return (
      <div
        className={styles.runStep}
        data-active={active}
        data-status={item.status}
      >
        {heading}
      </div>
    );
  }

  const markdownDetail =
    item.kind === "plan" || item.kind === "reasoning_summary";
  return (
    <details
      className={styles.runStep}
      data-active={active}
      data-status={item.status}
      open={item.status === "in_progress" || active}
    >
      <summary>{heading}</summary>
      <div className={styles.runStepDetail}>
        {markdownDetail ? (
          <MarkdownContent compact>{body ?? ""}</MarkdownContent>
        ) : (
          <pre>{body}</pre>
        )}
        <button
          type="button"
          onClick={() => {
            onSelectItem(item.id);
            onOpenInspector("details");
          }}
        >
          Inspect details
        </button>
      </div>
    </details>
  );
}

function RunLedger({
  group,
  approvalsByItem,
  selectedItemId,
  busy,
  onSelectItem,
  onOpenInspector,
  onRespondToApproval,
  onRetryTurn,
}: Omit<TurnBlockProps, "onCancelQueuedTurn">) {
  const active = Boolean(
    group.turn && activeTurnStatuses.has(group.turn.status),
  );
  const hasPendingApproval = group.executionItems.some(
    (item) => approvalsByItem.get(item.id)?.status === "pending",
  );
  const now = useElapsedNow(active);
  const [expanded, setExpanded] = useState(false);
  const failed =
    group.turn?.status === "failed" ||
    group.turn?.status === "interrupted" ||
    group.completion?.status === "failed";
  const turnId = group.turn?.id ?? null;
  const open = expanded || active || hasPendingApproval || failed;
  const detailsId = `run-details-${group.id}`;

  if (!shouldShowRunLedger(group, now)) return null;

  return (
    <section
      className={styles.runLedger}
      data-open={open}
      data-status={failed ? "failed" : group.turn?.status}
    >
      <button
        type="button"
        className={styles.runSummary}
        aria-expanded={open}
        aria-controls={detailsId}
        onClick={() => setExpanded((current) => !current)}
      >
        <Clock3 size={14} />
        <strong>{runLabel(group, now)}</strong>
        {group.executionItems.length > 0 ? (
          <span>
            {group.executionItems.length}{" "}
            {group.executionItems.length === 1 ? "step" : "steps"}
          </span>
        ) : null}
        <ChevronRight className={styles.runChevron} size={14} />
      </button>

      {open ? (
        <div className={styles.runDetails} id={detailsId}>
          {group.executionItems.map((item) => (
            <RunStep
              key={item.id}
              item={item}
              approval={approvalsByItem.get(item.id)}
              active={item.id === selectedItemId}
              busy={busy}
              onSelectItem={onSelectItem}
              onOpenInspector={onOpenInspector}
              onRespondToApproval={onRespondToApproval}
            />
          ))}
          {group.completion?.payload.stopReason === "application_restarted" ? (
            <p className={styles.runError}>
              The previous process stopped. Retry from the same prompt.
            </p>
          ) : group.turn?.errorMessage ? (
            <p className={styles.runError}>{group.turn.errorMessage}</p>
          ) : null}
          <div className={styles.runActions}>
            {failed && turnId ? (
              <button
                type="button"
                onClick={() => onRetryTurn(turnId)}
                disabled={busy}
              >
                <RotateCcw size={13} />
                Retry
              </button>
            ) : null}
            <button
              type="button"
              onClick={() => {
                const selected =
                  group.completion ?? group.executionItems.at(-1) ?? null;
                if (selected) onSelectItem(selected.id);
                onOpenInspector("changes");
              }}
            >
              Review changes
              <ChevronRight size={13} />
            </button>
          </div>
        </div>
      ) : null}
    </section>
  );
}

export function TurnBlock({
  group,
  approvalsByItem,
  selectedItemId,
  busy,
  onSelectItem,
  onOpenInspector,
  onRespondToApproval,
  onRetryTurn,
  onCancelQueuedTurn,
}: TurnBlockProps) {
  const queued = group.turn?.status === "queued";
  const turnId = group.turn?.id ?? null;
  const userMessages = group.userMessages.length
    ? group.userMessages.map((item) => ({
        id: item.id,
        text: presentItem(item).body ?? item.summary,
        skills: itemSkills(item),
      }))
    : group.turn?.prompt
      ? [{ id: `${group.id}-prompt`, text: group.turn.prompt, skills: [] }]
      : [];

  return (
    <section className={styles.turnBlock} data-status={group.turn?.status}>
      {userMessages.map((message) => (
        <article className={styles.userMessage} data-queued={queued} key={message.id}>
          <div className={styles.userBubble}>
            <MarkdownContent>{message.text}</MarkdownContent>
          </div>
          {message.skills.length ? (
            <div className={styles.userSkills} aria-label="Skills used in this turn">
              {message.skills.map((skill) => (
                <span
                  key={skill.skillId}
                  title={[skill.invocation, skill.revision].filter(Boolean).join(" · ")}
                >
                  <Sparkles size={10} />
                  {skill.name}
                </span>
              ))}
            </div>
          ) : null}
          {queued && turnId ? (
            <div className={styles.queueState}>
              <Clock3 size={12} />
              <span>Queued</span>
              <button
                type="button"
                onClick={() => onCancelQueuedTurn(turnId)}
                disabled={busy}
              >
                Cancel
              </button>
            </div>
          ) : null}
        </article>
      ))}

      <RunLedger
        group={group}
        approvalsByItem={approvalsByItem}
        selectedItemId={selectedItemId}
        busy={busy}
        onSelectItem={onSelectItem}
        onOpenInspector={onOpenInspector}
        onRespondToApproval={onRespondToApproval}
        onRetryTurn={onRetryTurn}
      />

      {group.assistantMessages.map((item) => {
        const presentation = presentItem(item);
        return (
          <article className={styles.assistantMessage} key={item.id}>
            <span className={styles.srOnly}>DeepCode response</span>
            <div
              className={styles.messageBody}
              aria-live={item.status === "in_progress" ? "polite" : undefined}
            >
              <MarkdownContent>{presentation.body ?? item.summary}</MarkdownContent>
              {item.status === "in_progress" ? (
                <span
                  className={styles.streamingCursor}
                  aria-label="Agent is responding"
                />
              ) : null}
            </div>
          </article>
        );
      })}
    </section>
  );
}
