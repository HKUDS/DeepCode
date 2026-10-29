/**
 * The one dialog primitive: every modal, confirmation and dialog-like
 * popover in the client renders through it, so they all behave alike.
 *
 * - Focus moves into the dialog when it opens (an `autoFocus` child or
 *   `initialFocus` wins; otherwise the first focusable control) and stays
 *   inside it while it is the topmost dialog: Tab and Shift+Tab wrap, and
 *   focus that lands outside is pulled back.
 * - Escape closes it through the shared Escape stack (see escapeLayer), so a
 *   nested dialog closes before the one behind it. A busy dialog ignores it.
 * - On close, focus returns to the control that opened it.
 * - It is a `role="dialog"` (or `alertdialog`) labelled by its title.
 *
 * It owns no visuals: callers pass the class names they already use, so
 * moving a component onto it leaves its look unchanged. With
 * `backdropClassName` it renders over the page (and is `aria-modal`);
 * without one it is an anchored popover.
 */

import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type HTMLAttributes,
  type ReactNode,
  type RefObject,
} from "react";

import { useEscapeLayer } from "../app/escapeLayer";
import {
  focusableWithin,
  isTopDialog,
  popDialog,
  pushDialog,
  restoreFocus,
} from "./dialogStack";

export interface DialogProps
  extends Omit<HTMLAttributes<HTMLElement>, "role" | "title"> {
  /** The element the dialog panel renders as (a form keeps Enter-to-submit). */
  as?: "div" | "section" | "form";
  role?: "dialog" | "alertdialog";
  /** Id of the visible title. Prefer this to ``label``. */
  labelledBy?: string;
  /** Accessible name when there is no visible title to point at. */
  label?: string;
  onClose(): void;
  /** While true, Escape, the backdrop and outside presses do nothing. */
  busy?: boolean;
  /** Render over the page on this backdrop (and mark the dialog modal). */
  backdropClassName?: string;
  /** A press on the backdrop closes the dialog. Only where nothing is lost. */
  closeOnBackdrop?: boolean;
  /**
   * Popovers: a press outside this element (normally the wrapper holding
   * the trigger and the popover) closes the dialog.
   */
  dismissOnPressOutside?: RefObject<HTMLElement | null>;
  /** The control to focus on open, instead of the first one. */
  initialFocus?: RefObject<HTMLElement | null>;
  /**
   * Where focus goes on close when the opener is gone (a menu item that
   * unmounted when the dialog opened). Defaults to the opener.
   */
  returnFocus?: RefObject<HTMLElement | null>;
  children: ReactNode;
}

export function Dialog({
  as = "div",
  role = "dialog",
  labelledBy,
  label,
  onClose,
  busy = false,
  backdropClassName,
  closeOnBackdrop = false,
  dismissOnPressOutside,
  initialFocus,
  returnFocus,
  children,
  ...rest
}: DialogProps) {
  const panelRef = useRef<HTMLElement | null>(null);
  // Read on the first render: by the time an effect runs, an autoFocus
  // child has already taken focus away from the opener.
  const [opener] = useState(() =>
    document.activeElement instanceof HTMLElement &&
    document.activeElement !== document.body
      ? document.activeElement
      : null,
  );
  const latest = useRef({ onClose, busy });
  useEffect(() => {
    latest.current = { onClose, busy };
  });

  useEscapeLayer(() => {
    if (!latest.current.busy) latest.current.onClose();
  });

  useEffect(() => {
    const panel = panelRef.current;
    if (!panel) return;
    pushDialog(panel);
    if (!panel.contains(document.activeElement)) {
      (initialFocus?.current ?? focusableWithin(panel)[0] ?? panel).focus();
    }

    const trapTab = (event: KeyboardEvent) => {
      if (event.key !== "Tab" || !isTopDialog(panel)) return;
      const items = focusableWithin(panel);
      const active = document.activeElement;
      if (items.length === 0) {
        event.preventDefault();
        panel.focus();
        return;
      }
      const first = items[0];
      const last = items[items.length - 1];
      const outside = !panel.contains(active) || active === panel;
      if (event.shiftKey && (active === first || outside)) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && (active === last || outside)) {
        event.preventDefault();
        first.focus();
      }
    };
    const keepFocus = (event: FocusEvent) => {
      if (!isTopDialog(panel)) return;
      if (event.target instanceof Node && panel.contains(event.target)) return;
      (focusableWithin(panel)[0] ?? panel).focus();
    };
    document.addEventListener("keydown", trapTab);
    document.addEventListener("focusin", keepFocus);

    return () => {
      document.removeEventListener("keydown", trapTab);
      document.removeEventListener("focusin", keepFocus);
      popDialog(panel);
      // Give focus back only if the dialog still had it: a press outside
      // that closed a popover has already put focus where the user wants.
      const active = document.activeElement;
      const hadFocus =
        !active || active === document.body || panel.contains(active);
      // Read at close, not at open: the element to return to may only
      // exist now (see GoalRail).
      // eslint-disable-next-line react-hooks/exhaustive-deps
      const returnTo = returnFocus?.current;
      const target = returnTo?.isConnected ? returnTo : opener;
      if (hadFocus) restoreFocus(target);
    };
  }, [initialFocus, opener, returnFocus]);

  useEffect(() => {
    if (!dismissOnPressOutside) return;
    const closeOnPressOutside = (event: PointerEvent | MouseEvent) => {
      const panel = panelRef.current;
      if (!panel || !isTopDialog(panel) || latest.current.busy) return;
      const root = dismissOnPressOutside.current ?? panel;
      if (event.target instanceof Node && root.contains(event.target)) return;
      latest.current.onClose();
    };
    document.addEventListener("mousedown", closeOnPressOutside);
    return () => document.removeEventListener("mousedown", closeOnPressOutside);
  }, [dismissOnPressOutside]);

  const setPanel = useCallback((node: HTMLElement | null) => {
    panelRef.current = node;
  }, []);
  const Panel = as;
  const panel = (
    <Panel
      {...rest}
      ref={setPanel}
      role={role}
      tabIndex={-1}
      aria-modal={backdropClassName !== undefined ? true : undefined}
      aria-labelledby={labelledBy}
      aria-label={labelledBy ? undefined : label}
      data-dialog=""
    >
      {children}
    </Panel>
  );
  if (backdropClassName === undefined) return panel;
  return (
    <div
      className={backdropClassName}
      role="presentation"
      onMouseDown={(event) => {
        if (
          closeOnBackdrop &&
          !latest.current.busy &&
          event.target === event.currentTarget
        ) {
          latest.current.onClose();
        }
      }}
    >
      {panel}
    </div>
  );
}
