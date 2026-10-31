import { useCallback, useRef, useState } from "react";

import { useEscapeLayer } from "./escapeLayer";
import { NARROW_LAYOUT } from "./layout";
import { useFocusHandoff } from "./useFocusHandoff";
import { useMediaQuery } from "./useMediaQuery";

/**
 * The sidebar as a drawer on a narrow window.
 *
 * It is open only while the window is narrow, and a wider window ends the
 * request, so a drawer left open can neither hide anything nor come back by
 * itself. Focus moves into it and back to the menu button (useFocusHandoff);
 * Cmd+K's search field may take focus first.
 */
export function useNavigationDrawer(drawerId: string) {
  const narrow = useMediaQuery(NARROW_LAYOUT);
  const [requested, setRequested] = useState(false);
  // State adjusted during render: a wider window ends the request.
  if (!narrow && requested) setRequested(false);
  const open = narrow && requested;
  const buttonRef = useRef<HTMLButtonElement | null>(null);

  const close = useCallback(() => setRequested(false), []);
  const reveal = useCallback(() => setRequested(true), []);
  useEscapeLayer(close, open);
  useFocusHandoff(open, drawerId, () => buttonRef.current);

  // The menu button is inert while the drawer is open, so it only reveals.
  return { narrow, open, buttonRef, reveal, close };
}
