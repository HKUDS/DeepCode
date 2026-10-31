import { useEffect, useRef } from "react";

import { focusableWithin } from "../components/dialogStack";

/**
 * Focus for a layer that makes the page behind it inert (the narrow drawer,
 * the floating review panel). The control that opened it is in that page, so
 * it loses focus: on opening, focus moves into the layer, unless something in
 * it already took focus; on closing with focus left in the layer or on
 * nothing, focus goes to ``returnTo()``. Nothing happens on mount.
 */
export function useFocusHandoff(
  open: boolean,
  layerId: string,
  returnTo: () => HTMLElement | null,
): void {
  const wasOpen = useRef(false);
  const latestReturnTo = useRef(returnTo);
  useEffect(() => {
    latestReturnTo.current = returnTo;
  });

  useEffect(() => {
    const opened = open && !wasOpen.current;
    const closed = !open && wasOpen.current;
    wasOpen.current = open;
    const layer = document.getElementById(layerId);
    const active = document.activeElement;
    if (opened && layer && !layer.contains(active)) {
      (focusableWithin(layer)[0] ?? layer).focus();
    } else if (
      closed &&
      (!active || active === document.body || Boolean(layer?.contains(active)))
    ) {
      latestReturnTo.current()?.focus();
    }
  }, [layerId, open]);
}
