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

// Close enough to the end to count as reading the latest. A following view is
// pinned to the end, so anything further is the user scrolling away.
const FOLLOW_THRESHOLD = 40;

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
  const contentRef = useRef<HTMLDivElement | null>(null);
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
  const pinToLatest = useCallback(() => {
    const viewport = scrollViewportRef.current;
    if (viewport) viewport.scrollTop = viewport.scrollHeight;
  }, []);

  // Follow the end of the conversation while the user reads it. Whatever
  // makes the transcript taller — a new item, streamed text, Markdown laying
  // out after it arrives, the composer or plan dock taking room — resizes the
  // content or the viewport, and a following view is pinned to the end at
  // once. Never smoothly: the scroll events of a smooth scroll pass through
  // positions that read as the user scrolling away. Only the user leaves the
  // end, by scrolling up; reaching it again, or "Latest", follows once more.
  //
  // The scroller exists once there is a conversation. A Session opens empty
  // and its history arrives after, so attach when it appears.
  useEffect(() => {
    const viewport = scrollViewportRef.current;
    const content = contentRef.current;
    if (!viewport || !content) return;

    const handleScroll = () => {
      const distanceFromEnd =
        viewport.scrollHeight - viewport.scrollTop - viewport.clientHeight;
      const following = distanceFromEnd <= FOLLOW_THRESHOLD;
      followingRef.current = following;
      setShowJumpToLatest(!following);
    };
    const followResize = () => {
      if (followingRef.current) pinToLatest();
    };

    viewport.addEventListener("scroll", handleScroll, { passive: true });
    const observer =
      typeof ResizeObserver === "undefined"
        ? null
        : new ResizeObserver(followResize);
    observer?.observe(content);
    observer?.observe(viewport);
    pinToLatest();
    return () => {
      viewport.removeEventListener("scroll", handleScroll);
      observer?.disconnect();
    };
  }, [hasConversation, pinToLatest]);

  const jumpToLatest = () => {
    followingRef.current = true;
    setShowJumpToLatest(false);
    pinToLatest();
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
        <div className={styles.conversation} ref={contentRef}>
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
