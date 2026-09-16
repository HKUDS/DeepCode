export interface ConfirmActionOptions {
  title?: string;
  kind?: "info" | "warning" | "error";
  confirmLabel?: string;
  cancelLabel?: string;
}

type ConfirmHandler = (
  message: string,
  options: ConfirmActionOptions,
) => Promise<boolean>;
let confirmHandler: ConfirmHandler = async (message) => window.confirm(message);
export function setConfirmHandler(handler: ConfirmHandler): void {
  confirmHandler = handler;
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
