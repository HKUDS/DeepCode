import { useCallback, useEffect, useRef, useState } from "react";

import { focusableWithin } from "../components/dialogStack";
import { useEscapeLayer } from "./escapeLayer";
import { NARROW_LAYOUT } from "./layout";
import { useMediaQuery } from "./useMediaQuery";

/**
 * The sidebar as a drawer on a narrow window.
 *
 * It is open only while the window is narrow, and a wider window ends the
 * request, so a drawer left open can neither hide anything nor come back by
 * itself. Opening moves focus into it, unless something in it already took
 * focus (the search field, for Cmd+K). Whenever it closes with focus left in
 * it or on nothing, focus goes to the menu button; the button sits in content
 * that is inert while the drawer is open, so this waits for the drawer to
 * close.
 */
export function useNavigationDrawer(drawerId: string) {
  const narrow = useMediaQuery(NARROW_LAYOUT);
  const [requested, setRequested] = useState(false);
  // State adjusted during render: a wider window ends the request.
  if (!narrow && requested) setRequested(false);
  const open = narrow && requested;
  const buttonRef = useRef<HTMLButtonElement | null>(null);

  const wasOpen = useRef(false);

  const close = useCallback(() => setRequested(false), []);
  useEscapeLayer(close, open);

  useEffect(() => {
    const opened = open && !wasOpen.current;
    const closed = !open && wasOpen.current;
    wasOpen.current = open;
    const drawer = document.getElementById(drawerId);
    if (!drawer) return;
    const active = document.activeElement;
    if (opened && !drawer.contains(active)) {
      focusableWithin(drawer)[0]?.focus();
    } else if (closed && (!active || active === document.body || drawer.contains(active))) {
      buttonRef.current?.focus();
    }
  }, [drawerId, open]);

  return {
    narrow,
    open,
    buttonRef,
    toggle: () => setRequested((value) => !value),
    reveal: () => setRequested(true),
    close,
  };
}
