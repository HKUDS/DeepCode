import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type KeyboardEvent,
  type PointerEvent as ReactPointerEvent,
} from "react";
import { useTranslation } from "react-i18next";

import { APPEARANCE_DEFAULTS, CONVERSATION_WIDTH_RANGE } from "../../app/appearance";
import { useAppearance } from "../../app/useAppearance";
import {
  measureColumnWidth,
  percentFromDrag,
  percentFromKey,
  projectPercent,
  SPLITTER_MAX,
  SPLITTER_MIN,
  SPLITTER_STEP,
} from "./conversationWidthResize";
import styles from "./ConversationSplitter.module.css";

/** The two boxes a drag is measured against, taken once at press time. */
interface Measurement {
  renderedWidth: number;
  workspaceWidth: number;
}

interface Drag extends Measurement {
  pointerId: number;
  startX: number;
  startPercent: number;
  preview: number | null;
}

/**
 * Drag handle on the right edge of the conversation column (#151).
 *
 * It edits the preference the settings slider edits. The pointer previews
 * `--conversation-width` as it moves and commits once on release, so a drag
 * costs one localStorage write instead of one per pointermove.
 *
 * Moves are mapped from the column's rendered width, not from where the pointer
 * sits, because 100% is the built-in 820px cap rather than the widest the pane
 * can get — see `conversationWidthResize.ts` for that arithmetic.
 */
export function ConversationSplitter() {
  const { appearance, set, previewConversationWidth } = useAppearance();
  const { t } = useTranslation();
  const handle = useRef<HTMLDivElement>(null);
  const drag = useRef<Drag | null>(null);
  const [dragging, setDragging] = useState(false);

  const measure = useCallback((): Measurement | null => {
    const element = handle.current;
    const workspace = element?.parentElement;
    if (!element || !workspace) return null;
    const workspaceBox = workspace.getBoundingClientRect();
    const handleBox = element.getBoundingClientRect();
    return {
      workspaceWidth: workspaceBox.width,
      renderedWidth: measureColumnWidth(workspaceBox, handleBox),
    };
  }, []);

  /** The share the stored preference currently renders as. */
  const currentPercent = useCallback(
    (measured?: Measurement | null) => {
      const box = measured ?? measure();
      return projectPercent(
        appearance.conversationWidth,
        box?.renderedWidth ?? 0,
        box?.workspaceWidth ?? 0,
      );
    },
    [appearance.conversationWidth, measure],
  );

  useEffect(() => {
    if (!dragging) return;

    const move = (event: PointerEvent) => {
      const state = drag.current;
      if (!state || event.pointerId !== state.pointerId) return;
      state.preview = percentFromDrag(
        state.renderedWidth,
        state.workspaceWidth,
        event.clientX - state.startX,
      );
      previewConversationWidth(state.preview);
    };

    const finish = () => {
      const state = drag.current;
      drag.current = null;
      setDragging(false);
      if (!state) return;
      if (state.preview === null || state.preview === state.startPercent) {
        // A press that never moved must not rewrite the preference: the default
        // reads as ~57% on a 1440px window, and committing that on a stray click
        // would silently narrow the column.
        previewConversationWidth(null);
        return;
      }
      set("conversationWidth", state.preview);
    };

    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", finish);
    window.addEventListener("pointercancel", finish);
    return () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", finish);
      window.removeEventListener("pointercancel", finish);
    };
  }, [dragging, previewConversationWidth, set]);

  // Switching threads mid-drag unmounts this with a preview still painted.
  useEffect(() => () => previewConversationWidth(null), [previewConversationWidth]);

  const onPointerDown = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (event.pointerType === "mouse" && event.button !== 0) return;
    const measured = measure();
    if (!measured) return;
    drag.current = {
      pointerId: event.pointerId,
      startX: event.clientX,
      startPercent: currentPercent(measured),
      preview: null,
      ...measured,
    };
    // Keep receiving moves once the pointer leaves the 12px strip. jsdom has no
    // pointer capture, hence the guard.
    if (typeof event.currentTarget.setPointerCapture === "function") {
      event.currentTarget.setPointerCapture(event.pointerId);
    }
    event.preventDefault();
    setDragging(true);
  };

  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    const next = percentFromKey(currentPercent(), event.key, SPLITTER_STEP);
    if (next === null) return;
    event.preventDefault();
    set("conversationWidth", next);
  };

  const onDoubleClick = () => {
    set("conversationWidth", APPEARANCE_DEFAULTS.conversationWidth);
  };

  // Announcing a number while the stored value is the cap would be a guess: 100
  // is 100% of a narrow workspace and ~57% of a wide one. The gaps close as soon
  // as the two controls write a real share.
  const percent =
    appearance.conversationWidth === APPEARANCE_DEFAULTS.conversationWidth
      ? undefined
      : appearance.conversationWidth;
  const value =
    percent === undefined
      ? {}
      : {
          "aria-valuemin": SPLITTER_MIN,
          "aria-valuemax": SPLITTER_MAX,
          "aria-valuenow": percent,
          "aria-valuetext": `${percent}${CONVERSATION_WIDTH_RANGE.unit}`,
        };

  return (
    <div
      ref={handle}
      className={styles.splitter}
      data-dragging={dragging}
      role="separator"
      aria-orientation="vertical"
      aria-label={t("thread.splitterLabel", "Adjust conversation width")}
      title={t("thread.splitterHint", "Drag to resize · Double-click to reset")}
      tabIndex={0}
      onPointerDown={onPointerDown}
      onKeyDown={onKeyDown}
      onDoubleClick={onDoubleClick}
      {...value}
    />
  );
}
