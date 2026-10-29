import { ArrowDown } from "lucide-react";
import {
  type MouseEvent,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
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

// How near the end still counts as at the end: room for a small nudge and for
// fractional scroll positions.
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
  // Where the view last stood at the end, as the browser clamped it.
  const endTopRef = useRef(0);
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
  const setFollowing = useCallback((following: boolean) => {
    followingRef.current = following;
    setShowJumpToLatest(!following);
  }, []);
  const pinToEnd = useCallback(() => {
    const viewport = scrollViewportRef.current;
    if (!viewport) return;
    viewport.scrollTop = viewport.scrollHeight;
    endTopRef.current = viewport.scrollTop;
  }, []);
  const followLatest = useCallback(() => {
    setFollowing(true);
    pinToEnd();
  }, [pinToEnd, setFollowing]);

  // Follow the end of the conversation while the user reads it. Whatever
  // makes the transcript taller — a new item, streamed text, Markdown laying
  // out after it arrives, the composer's clearance growing — resizes the
  // content box or the viewport, and a following view is pinned to the end at
  // once. Never smoothly: a smooth scroll passes through positions that read
  // as the user scrolling away.
  //
  // Only the user leaves the end: by scrolling above where the view last stood
  // at it, or by opening a step to read it (see below). A scroll event can
  // also arrive after more content landed, so the view is short of the end
  // without anyone moving it; the next resize pins it again. Back at the end,
  // or with "Latest", the view follows once more.
  //
  // The scroller exists once there is a conversation. A Session opens empty
  // and its history arrives after, so attach when it appears.
  useEffect(() => {
    const viewport = scrollViewportRef.current;
    const content = contentRef.current;
    if (!viewport || !content) return;

    const atEnd = () =>
      viewport.scrollHeight - viewport.scrollTop - viewport.clientHeight <=
      FOLLOW_THRESHOLD;
    const handleScroll = () => {
      if (atEnd()) {
        endTopRef.current = viewport.scrollTop;
        setFollowing(true);
      } else if (viewport.scrollTop < endTopRef.current) {
        setFollowing(false);
      }
    };
    const handleResize = () => {
      if (followingRef.current || atEnd()) followLatest();
    };

    viewport.addEventListener("scroll", handleScroll, { passive: true });
    const observer =
      typeof ResizeObserver === "undefined"
        ? null
        : new ResizeObserver(handleResize);
    // The border box: the composer's clearance is the content's padding.
    observer?.observe(content, { box: "border-box" });
    observer?.observe(viewport);
    followLatest();
    return () => {
      viewport.removeEventListener("scroll", handleScroll);
      observer?.disconnect();
    };
  }, [hasConversation, followLatest, setFollowing]);

  // Opening a step is reading it: keep it where it is rather than pinning the
  // end and pushing it off screen. Closing one pushes nothing away. The click
  // comes before the toggle, so `open` is still the step's current state, and
  // keyboard toggles click too.
  const handleContentClick = (event: MouseEvent<HTMLDivElement>) => {
    const summary =
      event.target instanceof Element ? event.target.closest("summary") : null;
    const step = summary?.parentElement;
    if (step instanceof HTMLDetailsElement && !step.open) setFollowing(false);
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
        <div
          className={styles.conversation}
          ref={contentRef}
          onClick={handleContentClick}
        >
          {groupedTurns.map((group) => (
            <TurnBlock
              key={group.id}
              group={group}
              approvalsByItem={approvalsByItem}
              selectedItemId={selectedItemId}
              transcriptMode={transcriptMode}
              following={!showJumpToLatest}
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
          onClick={followLatest}
        >
          <ArrowDown size={14} />
          {t("thread.latest", "Latest")}
        </button>
      ) : null}
    </div>
  );
}
