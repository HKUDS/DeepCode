import { useCallback, useEffect, useRef, useState } from "react";

import { focusableWithin } from "../components/dialogStack";
import { useEscapeLayer } from "./escapeLayer";
import { useMediaQuery } from "./useMediaQuery";

/** Where the sidebar stops being a column and becomes a drawer (App.module.css). */
export const NARROW_LAYOUT = "(max-width: 720px)";

/**
 * The sidebar as a drawer on a narrow window. It is open only while the
 * window is narrow, so one left open cannot hide or disable anything once
 * the window widens. Opening moves focus into it; Escape or the scrim closes
 * it and returns focus to the menu button; choosing a destination closes it.
 */
export function useNavigationDrawer(drawerId: string) {
  const narrow = useMediaQuery(NARROW_LAYOUT);
  const [requested, setRequested] = useState(false);
  // A wider window ends the request: narrowing it again later must not bring
  // back a drawer nobody asked for since. (State adjusted during render.)
  if (!narrow && requested) setRequested(false);
  const open = narrow && requested;
  const buttonRef = useRef<HTMLButtonElement | null>(null);
  // Set when the drawer is dismissed rather than left for a destination.
  // The menu button sits in the content that is inert while the drawer is
  // open, so it can take focus only once the drawer has closed.
  const returnFocus = useRef(false);

  const dismiss = useCallback(() => {
    returnFocus.current = true;
    setRequested(false);
  }, []);
  useEscapeLayer(dismiss, open);

  useEffect(() => {
    if (open) {
      const drawer = document.getElementById(drawerId);
      if (drawer) focusableWithin(drawer)[0]?.focus();
    } else if (returnFocus.current) {
      returnFocus.current = false;
      buttonRef.current?.focus();
    }
  }, [drawerId, open]);

  return {
    open,
    buttonRef,
    toggle: () => setRequested((value) => !value),
    dismiss,
    /** Close after the user picks where to go; focus follows the choice. */
    close: () => setRequested(false),
  };
}
