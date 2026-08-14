import { useCallback, useEffect, useState } from "react";

import type { AgentPresetEntry } from "../../generated/app-server";
import type { DesktopRuntime } from "../../rpc/contracts";

export interface PresetCatalogState {
  entries: AgentPresetEntry[];
  current: string | null;
  busy: boolean;
  error: string | null;
}

function errorMessage(error: unknown): string {
  if (error instanceof Error) return error.message;
  return String(error);
}

/** Roster + current selection for the agent-preset picker.
 *
 * The roster is workspace-scoped (project roots override user and shipped
 * ones), so it reloads per project; the current selection is read from the
 * canonical session, so it reloads per thread. Selection errors — most
 * notably the server's "locked once the conversation has started" refusal —
 * surface on `error` for the control's tooltip rather than a dialog.
 *
 * Loaded state is keyed by its owner (projectId / threadId) and derived at
 * render time, so switching owners never needs a synchronous reset inside
 * an effect and a late-landing response for a previous owner is ignored.
 */
export function usePresetCatalog(
  runtime: DesktopRuntime,
  projectId: string | null,
  threadId: string | null,
): PresetCatalogState & { select(presetId: string | null): Promise<void> } {
  const [roster, setRoster] = useState<{
    projectId: string;
    entries: AgentPresetEntry[];
  } | null>(null);
  const [selection, setSelection] = useState<{
    threadId: string;
    current: string | null;
  } | null>(null);
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<{
    threadId: string;
    message: string;
  } | null>(null);

  useEffect(() => {
    if (!projectId) return;
    let stale = false;
    runtime
      .request("preset/list", { projectId })
      .then((result) => {
        if (!stale) setRoster({ projectId, entries: result.presets });
      })
      .catch(() => {
        // A deployment without presets simply renders no picker.
        if (!stale) setRoster({ projectId, entries: [] });
      });
    return () => {
      stale = true;
    };
  }, [projectId, runtime]);

  useEffect(() => {
    if (!threadId) return;
    let stale = false;
    runtime
      .request("preset/current", { threadId })
      .then((result) => {
        if (!stale) setSelection({ threadId, current: result.agentPreset });
      })
      .catch(() => {
        if (!stale) setSelection({ threadId, current: null });
      });
    return () => {
      stale = true;
    };
  }, [threadId, runtime]);

  const select = useCallback(
    async (presetId: string | null) => {
      if (!threadId) return;
      setBusy(true);
      setFailure(null);
      try {
        const result = await runtime.request("preset/select", {
          threadId,
          agentPreset: presetId,
        });
        setSelection({ threadId, current: result.agentPreset });
      } catch (cause) {
        setFailure({ threadId, message: errorMessage(cause) });
      } finally {
        setBusy(false);
      }
    },
    [runtime, threadId],
  );

  return {
    entries: roster?.projectId === projectId ? roster.entries : [],
    current: selection?.threadId === threadId ? selection.current : null,
    busy,
    error: failure?.threadId === threadId ? failure.message : null,
    select,
  };
}
