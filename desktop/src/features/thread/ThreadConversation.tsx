import { ArrowDown } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import type {
  Approval,
  ApprovalDecision,
  Item,
  Turn,
} from "../../generated/app-server";
import type { DesktopInspectorTab } from "../../app/useDesktopUi";
import { buildConversationTurns } from "./conversationModel";
import styles from "./ThreadConversation.module.css";
import { TurnBlock } from "./TurnBlock";

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

const FOLLOW_THRESHOLD = 120;

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
  const scrollViewportRef = useRef<HTMLDivElement | null>(null);
  const endRef = useRef<HTMLDivElement | null>(null);
  const followingRef = useRef(true);
  const [showJumpToLatest, setShowJumpToLatest] = useState(false);
  const groupedTurns = useMemo(
    () => buildConversationTurns(turns, items),
    [items, turns],
  );
  const approvalsByItem = useMemo(
    () => new Map(approvals.map((approval) => [approval.itemId, approval])),
    [approvals],
  );
  const latestItem = items.at(-1);
  const latestUpdate = latestItem
    ? `${latestItem.id}:${latestItem.status}:${latestItem.updatedAt}:${JSON.stringify(latestItem.payload).length}`
    : `${turns.at(-1)?.id ?? "empty"}:${turns.at(-1)?.status ?? "idle"}`;
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
    return () => viewport.removeEventListener("scroll", handleScroll);
  }, []);

  useEffect(() => {
    if (!followingRef.current) return;
    scrollToLatest(latestItem?.status === "in_progress" ? "smooth" : "auto");
  }, [latestItem?.status, latestUpdate, scrollToLatest]);

  const jumpToLatest = () => {
    followingRef.current = true;
    setShowJumpToLatest(false);
    scrollToLatest("smooth");
  };

  if (groupedTurns.length === 0) {
    return (
      <div className={styles.conversationFrame}>
        <div className={styles.conversationScroller}>
          <div className={styles.empty}>
            <span className={styles.emptyRail} aria-hidden="true">
              <i />
              <i />
              <i />
            </span>
            <h2>Start with a task.</h2>
            <p>
              Describe the outcome, the constraints, and how DeepCode should
              verify the result.
            </p>
            <small>
              Conversation, tools, approvals, and review stay in this Session.
            </small>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className={styles.conversationFrame}>
      <div
        className={styles.conversationScroller}
        aria-label="Thread conversation"
        ref={scrollViewportRef}
      >
        <div className={styles.conversation}>
          {groupedTurns.map((group) => (
            <TurnBlock
              key={group.id}
              group={group}
              approvalsByItem={approvalsByItem}
              selectedItemId={selectedItemId}
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

      {showJumpToLatest ? (
        <button
          type="button"
          className={styles.jumpToLatest}
          onClick={jumpToLatest}
        >
          <ArrowDown size={14} />
          Latest
        </button>
      ) : null}
    </div>
  );
}
