/**
 * Composer behavior preferences — per-machine, like appearance.
 *
 * One knob so far: what plain Enter does while a Turn is running. dsh ships
 * the same choice (`busyEnter: queue | steer`); DeepCode defaults to steer
 * because that has always been the desktop's behavior, so existing users
 * feel nothing. Cmd/Ctrl+Enter always performs the other behavior, which is
 * why this is a preference and not a mode: both verbs stay one keystroke
 * away.
 */

import { useCallback, useSyncExternalStore } from "react";

export type BusyEnterBehavior = "steer" | "queue";

export const BUSY_ENTER_BEHAVIORS: readonly BusyEnterBehavior[] = [
  "steer",
  "queue",
];

export const BUSY_ENTER_DEFAULT: BusyEnterBehavior = "steer";

const STORAGE_KEY = "deepcode.desktop.composer.v1";

interface ComposerBehaviorState {
  busyEnter: BusyEnterBehavior;
}

function sanitize(value: unknown): ComposerBehaviorState {
  const raw =
    typeof value === "object" && value !== null
      ? (value as Record<string, unknown>)
      : {};
  return {
    busyEnter: BUSY_ENTER_BEHAVIORS.includes(raw.busyEnter as BusyEnterBehavior)
      ? (raw.busyEnter as BusyEnterBehavior)
      : BUSY_ENTER_DEFAULT,
  };
}

function read(): ComposerBehaviorState {
  try {
    return sanitize(JSON.parse(localStorage.getItem(STORAGE_KEY) ?? "{}"));
  } catch {
    // A preference must never block startup.
    return { busyEnter: BUSY_ENTER_DEFAULT };
  }
}

function write(state: ComposerBehaviorState): void {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(state));
  } catch {
    // Quota or private mode: the session still honours the choice.
  }
}

// Module-level store shared by the settings row and the composer (the
// useAppearance pattern: two mount points, one source of truth).
let state: ComposerBehaviorState = read();
const listeners = new Set<() => void>();

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

function snapshot(): ComposerBehaviorState {
  return state;
}

function commit(next: ComposerBehaviorState): void {
  state = next;
  write(state);
  for (const listener of listeners) listener();
}

export interface ComposerBehaviorController {
  busyEnter: BusyEnterBehavior;
  setBusyEnter(value: BusyEnterBehavior): void;
}

export function useComposerBehavior(): ComposerBehaviorController {
  const current = useSyncExternalStore(subscribe, snapshot, snapshot);
  const setBusyEnter = useCallback((value: BusyEnterBehavior) => {
    commit({ ...state, busyEnter: value });
  }, []);
  return { busyEnter: current.busyEnter, setBusyEnter };
}

/** Reset module state between tests. */
export function __resetComposerBehaviorForTests(): void {
  state = read();
  listeners.clear();
}
