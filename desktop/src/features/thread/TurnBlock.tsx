import {
  AlertTriangle,
  BookOpen,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  Clock3,
  Cpu,
  FilePenLine,
  FlaskConical,
  ListTree,
  ListChecks,
  PackageOpen,
  RotateCcw,
  ScrollText,
  Search,
  Sparkles,
  Target,
  TerminalSquare,
  Wrench,
} from "lucide-react";
import type { TFunction } from "i18next";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import type {
  Approval,
  ApprovalDecision,
  Item,
} from "../../generated/app-server";
import type { DesktopInspectorTab } from "../../app/useDesktopUi";
import { ApprovalCard } from "../execution/ApprovalCard";
import { isWarning, presentItem } from "../execution/itemPresentation";
import {
  formatTurnDuration,
  goalPrompt,
  turnDurationSeconds,
  type ConversationTurn,
  type GoalPrompt,
  type TimelineActivityGroup,
} from "./conversationModel";
import { MarkdownContent } from "./MarkdownContent";
import { ReasoningBlock } from "./ReasoningBlock";
import type { TranscriptMode } from "./transcriptMode";
import styles from "./ThreadConversation.module.css";

interface TurnBlockProps {
  group: ConversationTurn;
  approvalsByItem: ReadonlyMap<string, Approval>;
  selectedItemId: string | null;
  transcriptMode: TranscriptMode;
  busy: boolean;
  onSelectItem(itemId: string): void;
  onOpenInspector(tab?: DesktopInspectorTab): void;
  onRespondToApproval(approvalId: string, decision: ApprovalDecision): void;
  onRetryTurn(turnId: string): void;
  onCancelQueuedTurn(turnId: string): void;
}

const activeTurnStatuses = new Set(["queued", "running", "waiting_approval"]);
const summaryVisibleItemKinds = new Set<Item["kind"]>([
  "file_change",
  "diff",
  "test_result",
  "approval_request",
  "error",
]);

interface SkillInvocationView {
  skillId: string;
  name: string;
  revision?: string;
  invocation?: string;
}

interface ItemActivityView {
  kind: string;
  label: string;
  subject: string | null;
}

function itemActivity(item: Item): ItemActivityView | null {
  const value = item.payload.activity;
  if (
    typeof value !== "object" ||
    value === null ||
    Array.isArray(value) ||
    typeof value.kind !== "string" ||
    typeof value.label !== "string"
  ) {
    return null;
  }
  return {
    kind: value.kind,
    label: value.label,
    subject: typeof value.subject === "string" ? value.subject : null,
  };
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

function ActivityIcon({
  kind,
  activityKind,
}: {
  kind: Item["kind"];
  activityKind?: string;
}) {
  const props = { size: 14, strokeWidth: 1.8 };
  switch (activityKind) {
    case "read":
      return <BookOpen {...props} />;
    case "search":
      return <Search {...props} />;
    case "list":
      return <ListTree {...props} />;
  }
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

function approvalOutcome(status: Approval["status"], t: TFunction): string {
  switch (status) {
    case "approved_once":
      return t("turn.approval.allowedOnce", "Allowed once");
    case "approved_session":
      return t("turn.approval.allowedSession", "Allowed for this Session");
    case "denied":
      return t("turn.approval.denied", "Denied");
    case "cancelled":
      return t("turn.approval.cancelled", "Cancelled");
    default:
      return t("turn.approval.resolved", "Approval resolved");
  }
}

function itemStatusText(status: Item["status"], t: TFunction): string {
  switch (status) {
    case "pending":
      return t("turn.itemStatus.pending", "pending");
    case "in_progress":
      return t("turn.itemStatus.inProgress", "in progress");
    case "completed":
      return t("turn.itemStatus.completed", "completed");
    case "failed":
      return t("turn.itemStatus.failed", "failed");
    case "declined":
      return t("turn.itemStatus.declined", "declined");
    default:
      return String(status).replaceAll("_", " ");
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

function runLabel(group: ConversationTurn, now: number, t: TFunction): string {
  const status = group.turn?.status;
  const duration = turnDurationSeconds(group.turn, now);
  const formatted = duration === null ? null : formatTurnDuration(duration);
  const options = { duration: formatted };
  switch (status) {
    case "queued":
      return t("turn.run.queued", "Queued");
    case "running":
      return formatted
        ? t("turn.run.workingFor", "Working for {{duration}}", options)
        : t("turn.run.working", "Working");
    case "waiting_approval":
      return formatted
        ? t("turn.run.waitingAfter", "Waiting after {{duration}}", options)
        : t("turn.run.waitingApproval", "Waiting for approval");
    case "failed":
      return formatted
        ? t("turn.run.failedAfter", "Failed after {{duration}}", options)
        : t("turn.run.failed", "Run failed");
    case "interrupted":
      return formatted
        ? t("turn.run.stoppedAfter", "Stopped after {{duration}}", options)
        : t("turn.run.stopped", "Run stopped");
    default:
      return formatted
        ? t("turn.run.workedFor", "Worked for {{duration}}", options)
        : t("turn.run.completed", "Work completed");
  }
}

function shouldShowRunStatus(group: ConversationTurn, now: number): boolean {
  if (group.timeline.length > 0 || group.completion) return true;
  if (group.turn?.status === "queued") return false;
  if (group.turn && group.turn.status !== "completed") return true;
  return (turnDurationSeconds(group.turn, now) ?? 0) > 0;
}

function ActivityItem({
  item,
  approval,
  active,
  busy,
  onSelectItem,
  onOpenInspector,
  onRespondToApproval,
  transcriptMode,
}: {
  item: Item;
  approval: Approval | undefined;
  active: boolean;
  busy: boolean;
  onSelectItem(itemId: string): void;
  onOpenInspector(tab?: DesktopInspectorTab): void;
  onRespondToApproval(approvalId: string, decision: ApprovalDecision): void;
  transcriptMode: TranscriptMode;
}) {
  const { t } = useTranslation();
  if (item.kind === "reasoning_summary") {
    return <ReasoningBlock item={item} mode={transcriptMode} />;
  }
  if (item.kind === "approval_request" && approval) {
    const pending = approval.status === "pending";
    return (
      <section
        className={styles.runApproval}
        data-pending={pending}
        data-status={approval.status}
      >
        <div className={styles.runApprovalHeading}>
          {pending ? <AlertTriangle size={16} /> : <CheckCircle2 size={16} />}
          <span>
            <strong>
              {pending
                ? t("approval.label", "Approval required")
                : approvalOutcome(approval.status, t)}
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
            {t("approval.decision", "Decision: {{status}}", {
              status: approval.status.replaceAll("_", " "),
            })}
          </span>
        )}
      </section>
    );
  }

  const presentation = presentItem(item);
  const activity = itemActivity(item);
  const warning = isWarning(item);
  const body = presentation.body;
  const hasBody = Boolean(body && body.trim() && body.trim() !== item.summary.trim());
  const heading = (
    <>
      {warning ? (
        <AlertTriangle size={14} strokeWidth={1.8} />
      ) : (
        <ActivityIcon kind={item.kind} activityKind={activity?.kind} />
      )}
      <span className={styles.runStepCopy}>
        <strong>
          {activity?.subject ?? (item.summary || presentation.label)}
        </strong>
        <small>{activity?.label ?? presentation.label}</small>
      </span>
      {warning ? null : (
        <span className={styles.runStepStatus}>
          {itemStatusText(item.status, t)}
        </span>
      )}
      {hasBody ? <ChevronRight className={styles.runStepChevron} size={14} /> : null}
    </>
  );

  if (!hasBody) {
    return (
      <div
        className={styles.runStep}
        data-active={active}
        data-status={item.status}
        data-warning={warning || undefined}
      >
        {heading}
      </div>
    );
  }

  const markdownDetail = item.kind === "plan";
  return (
    <details
      className={styles.runStep}
      data-active={active}
      data-status={item.status}
      data-warning={warning || undefined}
      open={
        item.status === "in_progress" ||
        active ||
        transcriptMode === "verbose"
      }
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
          {t("turn.inspectDetails", "Inspect details")}
        </button>
      </div>
    </details>
  );
}

/** Stands in for the instruction the Goal runtime sent; the text is one click away. */
function GoalPromptNote({
  kind,
  text,
  transcriptMode,
}: {
  kind: GoalPrompt;
  text: string;
  transcriptMode: TranscriptMode;
}) {
  const { t } = useTranslation();
  const label =
    kind === "automatic"
      ? t("turn.goalContinued", "Goal continued automatically")
      : t("turn.goalRequested", "Working toward the Goal");
  return (
    <details
      className={styles.goalPrompt}
      open={transcriptMode === "verbose"}
    >
      <summary>
        <Target size={12} aria-hidden="true" />
        <span>{label}</span>
        <ChevronDown size={12} aria-hidden="true" />
      </summary>
      <div className={styles.goalPromptText}>
        <small>{t("turn.goalInstruction", "Instruction sent to the model")}</small>
        <MarkdownContent>{text}</MarkdownContent>
      </div>
    </details>
  );
}

function RunStatus({ group }: { group: ConversationTurn }) {
  const active = Boolean(
    group.turn && activeTurnStatuses.has(group.turn.status),
  );
  const now = useElapsedNow(active);
  const { t } = useTranslation();
  if (!shouldShowRunStatus(group, now)) return null;

  return (
    <div className={styles.runStatus} data-status={group.turn?.status}>
      <Clock3 size={14} aria-hidden="true" />
      <span>{runLabel(group, now, t)}</span>
    </div>
  );
}

function ExplorationGroup({
  group,
  approvalsByItem,
  selectedItemId,
  busy,
  onSelectItem,
  onOpenInspector,
  onRespondToApproval,
  transcriptMode,
}: {
  group: TimelineActivityGroup;
  approvalsByItem: ReadonlyMap<string, Approval>;
  selectedItemId: string | null;
  busy: boolean;
  onSelectItem(itemId: string): void;
  onOpenInspector(tab?: DesktopInspectorTab): void;
  onRespondToApproval(approvalId: string, decision: ApprovalDecision): void;
  transcriptMode: TranscriptMode;
}) {
  const { t } = useTranslation();
  const active = group.items.some(
    (item) => item.status === "in_progress" || item.id === selectedItemId,
  );
  const completed = group.items.filter(
    (item) => item.status === "completed",
  ).length;

  return (
    <details
      className={styles.activityGroup}
      open={active || transcriptMode === "verbose"}
    >
      <summary>
        <Search size={14} aria-hidden="true" />
        <span>
          <strong>{t("turn.explored", "Explored project context")}</strong>
          <small>
            {t("turn.activities", "{{completed}}/{{total}} activities", {
              completed,
              total: group.items.length,
            })}
          </small>
        </span>
        <ChevronDown size={14} aria-hidden="true" />
      </summary>
      <div className={styles.activityGroupItems}>
        {group.items.map((item) => (
          <ActivityItem
            key={item.id}
            item={item}
            approval={approvalsByItem.get(item.id)}
            active={item.id === selectedItemId}
            busy={busy}
            onSelectItem={onSelectItem}
            onOpenInspector={onOpenInspector}
            onRespondToApproval={onRespondToApproval}
            transcriptMode={transcriptMode}
          />
        ))}
      </div>
    </details>
  );
}

function AssistantMessage({ item }: { item: Item }) {
  const { t } = useTranslation();
  const presentation = presentItem(item);
  const phase =
    item.payload.phase === "commentary" ? "commentary" : "final_answer";
  return (
    <article
      className={styles.assistantMessage}
      data-phase={phase}
      data-status={item.status}
    >
      <span className={styles.srOnly}>
        {phase === "commentary"
          ? t("turn.progressUpdate", "DeepCode progress update")
          : t("turn.response", "DeepCode response")}
      </span>
      <div
        className={styles.messageBody}
        aria-live={item.status === "in_progress" ? "polite" : undefined}
      >
        <MarkdownContent>{presentation.body ?? item.summary}</MarkdownContent>
        {item.status === "in_progress" ? (
          <span
            className={styles.streamingCursor}
            aria-label={t("turn.responding", "Agent is responding")}
          />
        ) : null}
      </div>
    </article>
  );
}

function timelineItems(group: ConversationTurn): Item[] {
  return group.timeline.flatMap((entry) =>
    entry.type === "item" ? [entry.item] : entry.items,
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
  transcriptMode,
}: TurnBlockProps) {
  const { t } = useTranslation();
  const queued = group.turn?.status === "queued";
  const turnId = group.turn?.id ?? null;
  const userMessages = group.userMessages.length
    ? group.userMessages.map((item) => ({
        id: item.id,
        text: presentItem(item).body ?? item.summary,
        skills: itemSkills(item),
        goalPrompt: goalPrompt(item),
      }))
    : group.turn?.prompt
      ? [
          {
            id: `${group.id}-prompt`,
            text: group.turn.prompt,
            skills: [],
            goalPrompt: null,
          },
        ]
      : [];
  const orderedItems = timelineItems(group);
  const lastExecutionItem =
    [...orderedItems]
      .reverse()
      .find((item) => item.kind !== "assistant_message") ?? null;
  const failed =
    group.turn?.status === "failed" ||
    group.turn?.status === "interrupted" ||
    group.completion?.status === "failed";
  const errorMessage =
    group.completion?.payload.stopReason === "application_restarted"
      ? t(
          "turn.restarted",
          "The previous process stopped. Retry from the same prompt.",
        )
      : group.turn?.errorMessage;

  return (
    <section className={styles.turnBlock} data-status={group.turn?.status}>
      {userMessages.map((message) => (
        <article className={styles.userMessage} data-queued={queued} key={message.id}>
          {message.goalPrompt ? (
            <GoalPromptNote
              kind={message.goalPrompt}
              text={message.text}
              transcriptMode={transcriptMode}
            />
          ) : (
            <div className={styles.userBubble}>
              <MarkdownContent>{message.text}</MarkdownContent>
            </div>
          )}
          {message.skills.length ? (
            <div
              className={styles.userSkills}
              aria-label={t("turn.skillsUsed", "Skills used in this turn")}
            >
              {message.skills.map((skill) => (
                <span
                  key={skill.skillId}
                  title={[skill.invocation, skill.revision].filter(Boolean).join(" · ")}
                >
                  <Sparkles size={12} />
                  {skill.name}
                </span>
              ))}
            </div>
          ) : null}
          {queued && turnId ? (
            <div className={styles.queueState}>
              <Clock3 size={12} />
              <span>{t("turn.run.queued", "Queued")}</span>
              <button
                type="button"
                onClick={() => onCancelQueuedTurn(turnId)}
                disabled={busy}
              >
                {t("turn.cancel", "Cancel")}
              </button>
            </div>
          ) : null}
        </article>
      ))}
      {group.turn?.executionProfile ? (
        <div className={styles.executionBadge}>
          <Cpu size={12} />
          <span>{group.turn.executionProfile.connectionId}</span>
          <i aria-hidden="true">/</i>
          <strong>{group.turn.executionProfile.modelId}</strong>
          {group.turn.executionProfile.reasoningEffort ? (
            <small>
              {t("turn.thinking", "Thinking {{effort}}", {
                effort: group.turn.executionProfile.reasoningEffort,
              })}
            </small>
          ) : null}
        </div>
      ) : null}

      <RunStatus group={group} />

      <div className={styles.timeline}>
        {group.timeline.map((entry) => {
          if (entry.type === "activity_group") {
            if (transcriptMode === "summary") return null;
            return (
              <ExplorationGroup
                key={entry.id}
                group={entry}
                approvalsByItem={approvalsByItem}
                selectedItemId={selectedItemId}
                busy={busy}
                onSelectItem={onSelectItem}
                onOpenInspector={onOpenInspector}
                onRespondToApproval={onRespondToApproval}
                transcriptMode={transcriptMode}
              />
            );
          }
          if (entry.item.kind === "assistant_message") {
            if (
              transcriptMode === "summary" &&
              entry.item.payload.phase === "commentary"
            ) {
              return null;
            }
            return <AssistantMessage key={entry.id} item={entry.item} />;
          }
          if (
            transcriptMode === "summary" &&
            !summaryVisibleItemKinds.has(entry.item.kind)
          ) {
            return null;
          }
          return (
            <div className={styles.activityEntry} key={entry.id}>
              <ActivityItem
                item={entry.item}
                approval={approvalsByItem.get(entry.item.id)}
                active={entry.item.id === selectedItemId}
                busy={busy}
                onSelectItem={onSelectItem}
                onOpenInspector={onOpenInspector}
                onRespondToApproval={onRespondToApproval}
                transcriptMode={transcriptMode}
              />
            </div>
          );
        })}
      </div>

      {errorMessage ? <p className={styles.runError}>{errorMessage}</p> : null}
      {failed || lastExecutionItem ? (
        <div className={styles.runActions}>
          {failed && turnId ? (
            <button
              type="button"
              onClick={() => onRetryTurn(turnId)}
              disabled={busy}
            >
              <RotateCcw size={14} />
              {t("turn.retry", "Retry")}
            </button>
          ) : null}
          {lastExecutionItem ? (
            <button
              type="button"
              onClick={() => {
                onSelectItem(lastExecutionItem.id);
                onOpenInspector("changes");
              }}
            >
              {t("turn.reviewChanges", "Review changes")}
              <ChevronRight size={14} />
            </button>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}
