/** Bookkeeping shared by every open Dialog (see Dialog.tsx). */

const FOCUSABLE = [
  "a[href]",
  "button:not([disabled])",
  "input:not([disabled]):not([type='hidden'])",
  "select:not([disabled])",
  "textarea:not([disabled])",
  "iframe",
  "summary",
  "[contenteditable='true']",
  "[tabindex]:not([tabindex='-1'])",
].join(",");

/** The controls Tab can reach inside ``root``, in document order. */
export function focusableWithin(root: HTMLElement): HTMLElement[] {
  return Array.from(root.querySelectorAll<HTMLElement>(FOCUSABLE)).filter(
    (element) => {
      if (element.tabIndex < 0) return false;
      if (element.closest("[hidden], [inert]")) return false;
      const style = getComputedStyle(element);
      return style.display !== "none" && style.visibility !== "hidden";
    },
  );
}

/** Open dialogs, innermost last; only the innermost one holds focus. */
const openDialogs: HTMLElement[] = [];

export function pushDialog(panel: HTMLElement): void {
  openDialogs.push(panel);
}

export function popDialog(panel: HTMLElement): void {
  const index = openDialogs.lastIndexOf(panel);
  if (index >= 0) openDialogs.splice(index, 1);
}

export function isTopDialog(panel: HTMLElement): boolean {
  return openDialogs[openDialogs.length - 1] === panel;
}

/**
 * Focus ``target`` once a dialog has closed. The opener can be disabled for
 * a moment (busy while the dialog's answer is applied, like the folder
 * picker's "Open project folder"), so retry for a few frames — unless the
 * user has put focus somewhere else meanwhile. If it never takes focus (a
 * Save button that stays disabled once saved), focus goes to the dialog
 * still open underneath, if any, so it keeps holding the keyboard.
 */
export function restoreFocus(target: HTMLElement | null): void {
  const focusUnderlyingDialog = () => {
    const panel = openDialogs[openDialogs.length - 1];
    if (panel) (focusableWithin(panel)[0] ?? panel).focus();
  };
  const focusLost = () => {
    const active = document.activeElement;
    return !active || active === document.body;
  };
  if (!target?.isConnected) {
    focusUnderlyingDialog();
    return;
  }
  target.focus();
  if (document.activeElement === target) return;
  let frames = 30;
  const retry = () => {
    if (!focusLost()) return;
    if (target.isConnected) target.focus();
    if (document.activeElement === target) return;
    if (--frames > 0 && target.isConnected) requestAnimationFrame(retry);
    else focusUnderlyingDialog();
  };
  requestAnimationFrame(retry);
}

/** Reset between tests. */
export function __resetDialogsForTests(): void {
  openDialogs.length = 0;
}
