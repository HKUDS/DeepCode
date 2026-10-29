export interface ConfirmActionOptions {
  title?: string;
  kind?: "info" | "warning" | "error";
  confirmLabel?: string;
  cancelLabel?: string;
  /** The action deletes or discards something: the in-app dialog colours
   * its confirm button as dangerous. Native dialogs show it as ``kind``. */
  destructive?: boolean;
}

export type ConfirmHandler = (
  message: string,
  options: ConfirmActionOptions,
) => Promise<boolean>;
let confirmHandler: ConfirmHandler = async (message) => window.confirm(message);
/** Install ``handler``; returns the one it replaced, for restoring later. */
export function setConfirmHandler(handler: ConfirmHandler): ConfirmHandler {
  const previous = confirmHandler;
  confirmHandler = handler;
  return previous;
}

/**
 * Ask for explicit user confirmation on every supported frontend surface.
 *
 * Tauri dialogs are asynchronous. Keeping that detail behind one boundary
 * prevents callers from accidentally treating the returned Promise as a
 * truthy confirmation, while the browser fallback keeps component tests and
 * browser-rendered component tests useful.
 */
export async function confirmAction(
  message: string,
  options: ConfirmActionOptions = {},
): Promise<boolean> {
  return confirmHandler(message, options);
}
