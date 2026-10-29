import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import {
  setConfirmHandler,
  type ConfirmActionOptions,
} from "../platform/confirmAction";
import { ConfirmDialog } from "./ConfirmDialog";

interface PendingConfirm {
  id: number;
  message: string;
  options: ConfirmActionOptions;
  resolve(confirmed: boolean): void;
}

let nextId = 0;

/**
 * Answers confirmAction() with an in-app dialog instead of the browser's
 * native window.confirm, which cannot be styled, translated or labelled
 * and blocks the whole page. Mounted by the Web shell; the desktop app
 * keeps its native Tauri dialog.
 */
export function ConfirmHost() {
  const { t } = useTranslation();
  const [queue, setQueue] = useState<PendingConfirm[]>([]);

  useEffect(() => {
    const pending = new Set<PendingConfirm>();
    const previous = setConfirmHandler(
      (message, options) =>
        new Promise<boolean>((resolve) => {
          const request: PendingConfirm = {
            id: ++nextId,
            message,
            options,
            resolve: (confirmed) => {
              pending.delete(request);
              resolve(confirmed);
            },
          };
          pending.add(request);
          setQueue((current) => [...current, request]);
        }),
    );
    return () => {
      setConfirmHandler(previous);
      for (const request of [...pending]) request.resolve(false);
    };
  }, []);

  const current = queue[0];
  if (!current) return null;
  const settle = (confirmed: boolean) => {
    current.resolve(confirmed);
    setQueue((pending) => pending.filter((request) => request !== current));
  };
  return (
    <ConfirmDialog
      key={current.id}
      title={current.options.title ?? t("confirm.title", "Please confirm")}
      confirmLabel={current.options.confirmLabel ?? t("confirm.ok", "OK")}
      cancelLabel={current.options.cancelLabel ?? t("confirm.cancel", "Cancel")}
      tone={
        current.options.destructive || current.options.kind === "error"
          ? "danger"
          : "default"
      }
      onConfirm={() => settle(true)}
      onCancel={() => settle(false)}
    >
      {current.message}
    </ConfirmDialog>
  );
}
