/**
 * Geometry behind the conversation-width splitter (#151).
 *
 * The conversation column is centred in the workspace — `width:
 * var(--conversation-width); margin: 0 auto` in ThreadConversation, Composer
 * and GoalRail — so its right edge sits at `workspaceCentre + width / 2`. The
 * handle rides that edge and derives each new share from the column's *rendered*
 * width plus the pointer delta, because a centred column grows on both sides at
 * once:
 *
 *     nextWidth = renderedWidth + 2 * deltaX
 *     share     = nextWidth / workspaceWidth * 100
 *
 * Measuring the rendered width is what keeps the grabbed edge under the cursor.
 * `conversationWidth` is not a plain percentage at its maximum: 100 means "the
 * width the column had before this setting existed" (`min(820px, 100%)`), which
 * on a window wider than 820px is *narrower* than 99%. An absolute
 * pointer-to-percentage mapping would have to pick one of the two scales and
 * would jump on the first pixel of movement; a delta from the measured edge
 * stays continuous either way.
 */

import { APPEARANCE_DEFAULTS, CONVERSATION_WIDTH_RANGE } from "../../app/appearance";

/** Lowest share the splitter writes — the slider's own minimum. */
export const SPLITTER_MIN = CONVERSATION_WIDTH_RANGE.min;

/**
 * Highest share the splitter writes. The slider's maximum is excluded: 100 is
 * the legacy cap, not "fill the workspace", so a drag that wrote it would
 * *shrink* the column on any window wider than 820px. The default stays
 * reachable from the slider and from the splitter's double-click reset.
 */
export const SPLITTER_MAX = CONVERSATION_WIDTH_RANGE.max - 1;

/** One keyboard gesture, in the slider's own step. */
export const SPLITTER_STEP = CONVERSATION_WIDTH_RANGE.step;

export interface Box {
  left: number;
  width: number;
}

function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value));
}

/**
 * Rounded share of the workspace, held inside the splitter's range.
 *
 * A drag lands on any whole share rather than on the slider's 5% step: that step
 * is the other control's own keyboard grain, and rounding a drag to it would
 * make the edge jump under the cursor.
 */
function shareOf(width: number, workspaceWidth: number): number {
  return clamp(Math.round((width / workspaceWidth) * 100), SPLITTER_MIN, SPLITTER_MAX);
}

/**
 * Width of the centred column, read off the workspace box and the handle's
 * centre line.
 *
 * The handle is anchored to the workspace while the column is centred in the
 * scroller inside it, so a classic scrollbar takes ~8px from the column and
 * moves its true edge by half of that. A drag is deltas off this reading, so the
 * offset shifts the mapping by about a percent of the workspace instead of
 * accumulating; reading the column directly would mean anchoring the handle from
 * inside the scroller, where it would scroll away with the transcript.
 */
export function measureColumnWidth(workspace: Box, handle: Box): number {
  const centre = workspace.left + workspace.width / 2;
  const edge = handle.left + handle.width / 2;
  return Math.max(0, (edge - centre) * 2);
}

/**
 * The share of the workspace the column currently occupies. A stored value is
 * already a share; the default (100) is a cap that says nothing about the share
 * it happens to take, so that one is measured rather than believed.
 */
export function projectPercent(
  stored: number,
  renderedWidth: number,
  workspaceWidth: number,
): number {
  if (stored !== APPEARANCE_DEFAULTS.conversationWidth) {
    return clamp(Math.round(stored), SPLITTER_MIN, SPLITTER_MAX);
  }
  if (!(workspaceWidth > 0)) return SPLITTER_MAX;
  return shareOf(renderedWidth, workspaceWidth);
}

/** Share that keeps the grabbed edge under the pointer. */
export function percentFromDrag(
  renderedWidth: number,
  workspaceWidth: number,
  deltaX: number,
): number {
  if (!(workspaceWidth > 0)) return SPLITTER_MAX;
  return shareOf(renderedWidth + 2 * deltaX, workspaceWidth);
}

/** Share after a keyboard gesture, or null when the key belongs to someone else. */
export function percentFromKey(current: number, key: string, step: number): number | null {
  switch (key) {
    case "ArrowLeft":
    case "ArrowUp":
      return clamp(current - step, SPLITTER_MIN, SPLITTER_MAX);
    case "ArrowRight":
    case "ArrowDown":
      return clamp(current + step, SPLITTER_MIN, SPLITTER_MAX);
    case "Home":
      return SPLITTER_MIN;
    case "End":
      return SPLITTER_MAX;
    default:
      return null;
  }
}
