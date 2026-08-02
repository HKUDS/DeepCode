import { isTauri } from "@tauri-apps/api/core";
import {
  confirm as confirmNative,
  type ConfirmDialogOptions,
} from "@tauri-apps/plugin-dialog";

export interface ConfirmActionOptions {
  title?: string;
  kind?: ConfirmDialogOptions["kind"];
  confirmLabel?: string;
  cancelLabel?: string;
}

/**
 * Ask for explicit user confirmation on every supported frontend surface.
 *
 * Tauri dialogs are asynchronous. Keeping that detail behind one boundary
 * prevents callers from accidentally treating the returned Promise as a
 * truthy confirmation, while the browser fallback keeps component tests and
 * the standalone Vite preview useful.
 */
export async function confirmAction(
  message: string,
  options: ConfirmActionOptions = {},
): Promise<boolean> {
  if (!isTauri()) return window.confirm(message);
  return confirmNative(message, {
    title: options.title ?? "DeepCode",
    kind: options.kind ?? "warning",
    okLabel: options.confirmLabel ?? "Continue",
    cancelLabel: options.cancelLabel ?? "Cancel",
  });
}
