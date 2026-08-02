import { useEffect, useState } from "react";

const HISTORY_LIMIT = 30;

function draftKey(threadId: string): string {
  return `deepcode.desktop.draft.${threadId}`;
}

function historyKey(threadId: string): string {
  return `deepcode.desktop.promptHistory.${threadId}`;
}

function attachmentsKey(threadId: string): string {
  return `deepcode.desktop.attachments.${threadId}`;
}

function readValue(key: string): string {
  try {
    return localStorage.getItem(key) ?? "";
  } catch {
    return "";
  }
}

function readHistory(threadId: string): string[] {
  try {
    const value = JSON.parse(localStorage.getItem(historyKey(threadId)) ?? "[]");
    return Array.isArray(value)
      ? value.filter((entry): entry is string => typeof entry === "string")
      : [];
  } catch {
    return [];
  }
}

function readAttachments(threadId: string): string[] {
  try {
    const value = JSON.parse(localStorage.getItem(attachmentsKey(threadId)) ?? "[]");
    return Array.isArray(value)
      ? value.filter((entry): entry is string => typeof entry === "string")
      : [];
  } catch {
    return [];
  }
}

export function usePromptDraft(threadId: string) {
  const [prompt, setPrompt] = useState(() => readValue(draftKey(threadId)));
  const [historyIndex, setHistoryIndex] = useState(-1);
  const [attachments, setAttachments] = useState(() =>
    readAttachments(threadId),
  );

  useEffect(() => {
    try {
      if (prompt) {
        localStorage.setItem(draftKey(threadId), prompt);
      } else {
        localStorage.removeItem(draftKey(threadId));
      }
    } catch {
      // Storage is optional; input must remain usable when it is unavailable.
    }
  }, [prompt, threadId]);

  useEffect(() => {
    try {
      if (attachments.length) {
        localStorage.setItem(
          attachmentsKey(threadId),
          JSON.stringify(attachments),
        );
      } else {
        localStorage.removeItem(attachmentsKey(threadId));
      }
    } catch {
      // Attachment draft persistence is optional.
    }
  }, [attachments, threadId]);

  const record = (value: string) => {
    const clean = value.trim();
    if (!clean) return;
    const previous = readHistory(threadId).filter((entry) => entry !== clean);
    try {
      localStorage.setItem(
        historyKey(threadId),
        JSON.stringify([clean, ...previous].slice(0, HISTORY_LIMIT)),
      );
    } catch {
      // History is a convenience and never an execution dependency.
    }
    setHistoryIndex(-1);
  };

  const updatePrompt = (value: string) => {
    setPrompt(value);
    setHistoryIndex(-1);
  };

  const browse = (direction: "older" | "newer") => {
    const history = readHistory(threadId);
    if (history.length === 0) return;
    const nextIndex =
      direction === "older"
        ? Math.min(historyIndex + 1, history.length - 1)
        : Math.max(historyIndex - 1, -1);
    setHistoryIndex(nextIndex);
    setPrompt(nextIndex === -1 ? "" : history[nextIndex]);
  };

  const addAttachments = (paths: string[]) => {
    setAttachments((current) => [...new Set([...current, ...paths])]);
  };

  const removeAttachment = (path: string) => {
    setAttachments((current) => current.filter((candidate) => candidate !== path));
  };

  const clearAttachments = () => setAttachments([]);

  return {
    prompt,
    setPrompt: updatePrompt,
    record,
    browse,
    browsingHistory: historyIndex >= 0,
    attachments,
    addAttachments,
    removeAttachment,
    clearAttachments,
  };
}
