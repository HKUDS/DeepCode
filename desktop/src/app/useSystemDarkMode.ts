import { useSyncExternalStore } from "react";

const DARK_QUERY = "(prefers-color-scheme: dark)";

function mediaQuery(): MediaQueryList | null {
  if (typeof window === "undefined" || typeof window.matchMedia !== "function") {
    return null;
  }
  return window.matchMedia(DARK_QUERY);
}

function subscribe(listener: () => void): () => void {
  const query = mediaQuery();
  if (!query) return () => undefined;
  query.addEventListener("change", listener);
  return () => query.removeEventListener("change", listener);
}

function snapshot(): boolean {
  return mediaQuery()?.matches ?? false;
}

export function useSystemDarkMode(): boolean {
  return useSyncExternalStore(subscribe, snapshot, () => false);
}
