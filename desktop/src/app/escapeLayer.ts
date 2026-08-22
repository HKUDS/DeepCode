/**
 * Escape belongs to the innermost open layer.
 *
 * Window-level Escape handlers do not compose: with a modal inside a modal
 * every handler fires, so dismissing an inner form also tore down the
 * dialog around it — losing a half-typed provider draft (API key included)
 * on one keystroke. Layers register here instead; a single listener
 * dispatches to the top of the stack, so Escape closes exactly one thing
 * and the next Escape closes the one behind it.
 */

import { useEffect, useRef } from "react";

const layers: Array<() => void> = [];
let listening = false;

function dispatchEscape(event: KeyboardEvent): void {
  if (event.key !== "Escape") return;
  const top = layers[layers.length - 1];
  if (!top) return;
  event.stopPropagation();
  top();
}

/** Own Escape while ``active``; unregisters on unmount or deactivation. */
export function useEscapeLayer(onEscape: () => void, active = true): void {
  // Latest-callback ref, written in an effect (never during render): a
  // caller's inline arrow must not re-register the layer on every render,
  // which would reorder the stack underneath a nested layer.
  const latest = useRef(onEscape);
  useEffect(() => {
    latest.current = onEscape;
  });

  useEffect(() => {
    if (!active) return;
    const handler = () => latest.current();
    layers.push(handler);
    if (!listening) {
      window.addEventListener("keydown", dispatchEscape);
      listening = true;
    }
    return () => {
      const index = layers.lastIndexOf(handler);
      if (index >= 0) layers.splice(index, 1);
      if (layers.length === 0 && listening) {
        window.removeEventListener("keydown", dispatchEscape);
        listening = false;
      }
    };
  }, [active]);
}

/** Reset between tests. */
export function __resetEscapeLayersForTests(): void {
  layers.length = 0;
  if (listening) {
    window.removeEventListener("keydown", dispatchEscape);
    listening = false;
  }
}
