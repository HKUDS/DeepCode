import { ArrowDown } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import type {
  Approval,
  ApprovalDecision,
  Item,
  Turn,
} from "../../generated/app-server";
import type { TurnPlanState } from "../../app/workspaceState";
import type { DesktopInspectorTab } from "../../app/useDesktopUi";
import { buildConversationTurns } from "./conversationModel";
import { PlanProgress } from "./PlanProgress";
import styles from "./ThreadConversation.module.css";
import { TurnBlock } from "./TurnBlock";
import type { TranscriptMode } from "./transcriptMode";

interface ThreadConversationProps {
  turns: Turn[];
  items: Item[];
  approvals: Approval[];
  plansByTurnId: Record<string, TurnPlanState>;
  selectedItemId: string | null;
  transcriptMode: TranscriptMode;
  busy: boolean;
  onSelectItem(itemId: string): void;
  onOpenInspector(tab?: DesktopInspectorTab): void;
  onRespondToApproval(approvalId: string, decision: ApprovalDecision): void;
  onRetryTurn(turnId: string): void;
  onCancelQueuedTurn(turnId: string): void;
}

const FOLLOW_THRESHOLD = 120;

export function ThreadConversation({
  turns,
  items,
  approvals,
  plansByTurnId,
  selectedItemId,
  transcriptMode,
  busy,
  onSelectItem,
  onOpenInspector,
  onRespondToApproval,
  onRetryTurn,
  onCancelQueuedTurn,
}: ThreadConversationProps) {
  const { t } = useTranslation();
  const scrollViewportRef = useRef<HTMLDivElement | null>(null);
  const endRef = useRef<HTMLDivElement | null>(null);
  const followingRef = useRef(true);
  const [showJumpToLatest, setShowJumpToLatest] = useState(false);
  const groupedTurns = useMemo(
    () => buildConversationTurns(turns, items),
    [items, turns],
  );
  const hasConversation = groupedTurns.length > 0;
  const approvalsByItem = useMemo(
    () => new Map(approvals.map((approval) => [approval.itemId, approval])),
    [approvals],
  );
  const activeTurn = useMemo(
    () =>
      [...turns]
        .reverse()
        .find(
          (turn) =>
            turn.status === "queued" ||
            turn.status === "running" ||
            turn.status === "waiting_approval",
        ) ?? null,
    [turns],
  );
  const activePlan = activeTurn ? plansByTurnId[activeTurn.id] ?? null : null;
  const latestItem = items.at(-1);
  const itemUpdate = latestItem
    ? `${latestItem.id}:${latestItem.status}:${latestItem.updatedAt}:${JSON.stringify(latestItem.payload).length}`
    : `${turns.at(-1)?.id ?? "empty"}:${turns.at(-1)?.status ?? "idle"}`;
  const latestUpdate = `${itemUpdate}:${activePlan?.updatedAt ?? "no-plan"}`;
  const scrollToLatest = useCallback((behavior: ScrollBehavior) => {
    const viewport = scrollViewportRef.current;
    if (typeof viewport?.scrollTo === "function") {
      viewport.scrollTo({ top: viewport.scrollHeight, behavior });
      return;
    }
    const end = endRef.current;
    if (typeof end?.scrollIntoView === "function") {
      end.scrollIntoView({ block: "end", behavior });
    }
  }, []);

  // The scroller only exists once there is a conversation; a Session opens
  // empty and its history arrives after, so attach when it appears and start
  // at the bottom at once: a smooth scroll from the top would read as the
  // user scrolling away.
  useEffect(() => {
    const viewport = scrollViewportRef.current;
    if (!viewport) return;

    const handleScroll = () => {
      const distanceFromBottom =
        viewport.scrollHeight - viewport.scrollTop - viewport.clientHeight;
      const nearBottom = distanceFromBottom <= FOLLOW_THRESHOLD;
      followingRef.current = nearBottom;
      setShowJumpToLatest(!nearBottom);
    };

    viewport.addEventListener("scroll", handleScroll, { passive: true });
    scrollToLatest("auto");
    return () => viewport.removeEventListener("scroll", handleScroll);
  }, [hasConversation, scrollToLatest]);

  useEffect(() => {
    if (!followingRef.current) return;
    scrollToLatest(latestItem?.status === "in_progress" ? "smooth" : "auto");
  }, [latestItem?.status, latestUpdate, scrollToLatest]);

  const jumpToLatest = () => {
    followingRef.current = true;
    setShowJumpToLatest(false);
    scrollToLatest("smooth");
  };

  if (!hasConversation) {
    return (
      <div className={styles.conversationFrame}>
        <div className={styles.conversationScroller}>
          <div className={styles.empty}>
            <span className={styles.emptyRail} aria-hidden="true">
              <i />
              <i />
              <i />
            </span>
            <h2>{t("thread.empty.title", "Start with a task.")}</h2>
            <p>
              {t(
                "thread.empty.body",
                "Describe the outcome, the constraints, and how DeepCode should verify the result.",
              )}
            </p>
            <small>
              {t(
                "thread.empty.note",
                "Conversation, tools, approvals, and review stay in this Session.",
              )}
            </small>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div
      className={styles.conversationFrame}
      data-plan-active={Boolean(activePlan)}
    >
      <div
        className={styles.conversationScroller}
        aria-label={t("thread.conversation", "Thread conversation")}
        ref={scrollViewportRef}
      >
        <div className={styles.conversation}>
          {groupedTurns.map((group) => (
            <TurnBlock
              key={group.id}
              group={group}
              approvalsByItem={approvalsByItem}
              selectedItemId={selectedItemId}
              transcriptMode={transcriptMode}
              busy={busy}
              onSelectItem={onSelectItem}
              onOpenInspector={onOpenInspector}
              onRespondToApproval={onRespondToApproval}
              onRetryTurn={onRetryTurn}
              onCancelQueuedTurn={onCancelQueuedTurn}
            />
          ))}
          <div className={styles.conversationEnd} ref={endRef} />
        </div>
      </div>

      {activePlan ? (
        <div className={styles.planProgressDock}>
          <PlanProgress plan={activePlan} />
        </div>
      ) : null}

      {showJumpToLatest ? (
        <button
          type="button"
          className={styles.jumpToLatest}
          onClick={jumpToLatest}
        >
          <ArrowDown size={14} />
          {t("thread.latest", "Latest")}
        </button>
      ) : null}
    </div>
  );
}
