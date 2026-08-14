import { useCallback, useEffect, useRef, useState } from "react";

import type { DiagnosticsSnapshot } from "../../generated/app-server";
import type { DesktopRuntime } from "../../rpc/contracts";

export function useDiagnostics(
  runtime: DesktopRuntime,
  projectId: string | null,
) {
  const [diagnostics, setDiagnostics] = useState<DiagnosticsSnapshot | null>(null);
  const [loadedKey, setLoadedKey] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const generation = useRef(0);
  const resourceKey = projectId ?? "__global__";

  const refresh = useCallback(async () => {
    const requestGeneration = ++generation.current;
    setLoading(true);
    setError(null);
    try {
      const result = await runtime.request("diagnostics/read", {
        ...(projectId ? { projectId } : {}),
      });
      if (generation.current === requestGeneration) {
        setDiagnostics(result.diagnostics);
        setLoadedKey(resourceKey);
      }
    } catch (cause) {
      if (generation.current === requestGeneration) {
        setError(cause instanceof Error ? cause.message : String(cause));
      }
    } finally {
      if (generation.current === requestGeneration) setLoading(false);
    }
  }, [projectId, resourceKey, runtime]);

  useEffect(() => {
    const requestGeneration = ++generation.current;
    void runtime
      .request("diagnostics/read", {
        ...(projectId ? { projectId } : {}),
      })
      .then((result) => {
        if (generation.current !== requestGeneration) return;
        setDiagnostics(result.diagnostics);
        setLoadedKey(resourceKey);
        setError(null);
      })
      .catch((cause: unknown) => {
        if (generation.current !== requestGeneration) return;
        setError(cause instanceof Error ? cause.message : String(cause));
      })
      .finally(() => {
        if (generation.current === requestGeneration) setLoading(false);
      });
    return () => {
      generation.current += 1;
    };
  }, [projectId, resourceKey, runtime]);

  return {
    diagnostics: loadedKey === resourceKey ? diagnostics : null,
    loading: loading || loadedKey !== resourceKey,
    error,
    refresh,
  };
}
