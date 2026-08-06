import { useCallback, useSyncExternalStore } from "react";

import {
  APPEARANCE_DEFAULTS,
  applyAppearance,
  readAppearance,
  writeAppearance,
  type AppearanceState,
} from "./appearance";

/**
 * One module-level store, read through `useSyncExternalStore`.
 *
 * The shell mounts this to apply saved preferences at startup and the settings
 * page mounts it to edit them. Per-component `useState` would give those two
 * separate copies that silently disagree, and threading a controller down
 * through the workspace would be prop drilling for a value that is neither
 * server state nor scoped to a subtree. `useSystemDarkMode` uses the same
 * pattern for the same reason.
 */

let state: AppearanceState = readAppearance();
let applied = false;
const listeners = new Set<() => void>();

function root(): HTMLElement | null {
  return typeof document === "undefined" ? null : document.documentElement;
}

function flush(): void {
  const element = root();
  if (element) applyAppearance(state, element);
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  // The first subscriber marks the app as mounted; paint the saved
  // preferences now rather than waiting for the first edit.
  if (!applied) {
    applied = true;
    flush();
  }
  return () => listeners.delete(listener);
}

function snapshot(): AppearanceState {
  return state;
}

function commit(next: AppearanceState): void {
  state = next;
  writeAppearance(state);
  flush();
  for (const listener of listeners) listener();
}

export interface AppearanceController {
  appearance: AppearanceState;
  /** Update one preference; the others are untouched. */
  set<K extends keyof AppearanceState>(key: K, value: AppearanceState[K]): void;
  reset(): void;
}

export function useAppearance(): AppearanceController {
  const appearance = useSyncExternalStore(subscribe, snapshot, snapshot);

  const set = useCallback(
    <K extends keyof AppearanceState>(key: K, value: AppearanceState[K]) => {
      commit({ ...state, [key]: value });
    },
    [],
  );

  const reset = useCallback(() => commit({ ...APPEARANCE_DEFAULTS }), []);

  return { appearance, set, reset };
}

/** Reset module state between tests. */
export function __resetAppearanceStoreForTests(): void {
  state = readAppearance();
  applied = false;
  listeners.clear();
}
