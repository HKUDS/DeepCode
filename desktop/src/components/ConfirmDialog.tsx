import { useId, useRef, type ReactNode } from "react";

import { Dialog } from "./Dialog";
import styles from "./ConfirmDialog.module.css";

export interface ConfirmDialogProps {
  title: string;
  /** The question, in plain words. Strings keep their line breaks. */
  children: ReactNode;
  confirmLabel: string;
  cancelLabel: string;
  /** ``danger`` colours the confirm button for destructive actions. */
  tone?: "default" | "danger";
  busy?: boolean;
  onConfirm(): void;
  onCancel(): void;
}

/**
 * A yes/no question over the page. Cancel has focus when it opens, so
 * Enter or a stray Space never confirms by accident; Escape and a press on
 * the backdrop cancel.
 */
export function ConfirmDialog({
  title,
  children,
  confirmLabel,
  cancelLabel,
  tone = "default",
  busy = false,
  onConfirm,
  onCancel,
}: ConfirmDialogProps) {
  const titleId = useId();
  const bodyId = useId();
  const cancelRef = useRef<HTMLButtonElement | null>(null);
  return (
    <Dialog
      role="alertdialog"
      className={styles.dialog}
      backdropClassName={styles.backdrop}
      closeOnBackdrop
      labelledBy={titleId}
      aria-describedby={bodyId}
      initialFocus={cancelRef}
      busy={busy}
      onClose={onCancel}
    >
      <h2 id={titleId}>{title}</h2>
      <div id={bodyId} className={styles.body}>
        {children}
      </div>
      <footer>
        <button ref={cancelRef} type="button" onClick={onCancel} disabled={busy}>
          {cancelLabel}
        </button>
        <button
          type="button"
          className={styles.confirm}
          data-tone={tone}
          onClick={onConfirm}
          disabled={busy}
        >
          {confirmLabel}
        </button>
      </footer>
    </Dialog>
  );
}
