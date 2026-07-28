import { useCallback, useEffect, useState } from "react";

export type TranscriptMode = "normal" | "verbose" | "summary";

export const transcriptModes: readonly TranscriptMode[] = [
  "normal",
  "verbose",
  "summary",
];

const STORAGE_KEY = "deepcode.desktop.transcriptMode";

function isTranscriptMode(value: string | null): value is TranscriptMode {
  return transcriptModes.some((mode) => mode === value);
}

function initialMode(): TranscriptMode {
  try {
    const stored = localStorage.getItem(STORAGE_KEY);
    return isTranscriptMode(stored) ? stored : "normal";
  } catch {
    return "normal";
  }
}

export function nextTranscriptMode(mode: TranscriptMode): TranscriptMode {
  const index = transcriptModes.indexOf(mode);
  return transcriptModes[(index + 1) % transcriptModes.length];
}

export function useTranscriptMode() {
  const [mode, setMode] = useState<TranscriptMode>(initialMode);

  const selectMode = useCallback((next: TranscriptMode) => {
    setMode(next);
    try {
      localStorage.setItem(STORAGE_KEY, next);
    } catch {
      // Private storage modes may reject writes; the in-memory choice remains.
    }
  }, []);

  useEffect(() => {
    const handleKeyDown = (event: KeyboardEvent) => {
      if (
        event.ctrlKey &&
        !event.altKey &&
        !event.metaKey &&
        !event.shiftKey &&
        event.key.toLowerCase() === "o"
      ) {
        event.preventDefault();
        setMode((current) => {
          const next = nextTranscriptMode(current);
          try {
            localStorage.setItem(STORAGE_KEY, next);
          } catch {
            // Keep the active UI responsive even when storage is unavailable.
          }
          return next;
        });
      }
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, []);

  return { mode, selectMode };
}
