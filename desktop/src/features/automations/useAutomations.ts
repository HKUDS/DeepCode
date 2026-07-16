import { useCallback, useEffect, useRef, useState } from "react";

import type {
  AutomationRun,
  MethodParams,
  MethodResults,
} from "../../generated/app-server";
import type { DesktopRuntime } from "../../rpc/contracts";

type AutomationInventory = MethodResults["automation/list"];

function message(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

export function useAutomations(
  runtime: DesktopRuntime,
  projectId: string | null,
) {
  const [inventory, setInventory] = useState<AutomationInventory | null>(null);
  const [loadedKey, setLoadedKey] = useState<string | null>(null);
  const [runs, setRuns] = useState<Record<string, AutomationRun[]>>({});
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const generation = useRef(0);
  const resourceKey = projectId ?? "__none__";

  const refresh = useCallback(async () => {
    const requestGeneration = ++generation.current;
    if (!projectId) {
      setInventory(null);
      setLoadedKey(resourceKey);
      setRuns({});
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const result = await runtime.request("automation/list", { projectId });
      if (generation.current !== requestGeneration) return;
      setInventory(result);
      setLoadedKey(resourceKey);
    } catch (cause) {
      if (generation.current !== requestGeneration) return;
      setError(message(cause));
    } finally {
      if (generation.current === requestGeneration) setLoading(false);
    }
  }, [projectId, resourceKey, runtime]);

  useEffect(() => {
    const requestGeneration = ++generation.current;
    if (projectId) {
      void runtime
        .request("automation/list", { projectId })
        .then((result) => {
          if (generation.current !== requestGeneration) return;
          setInventory(result);
          setLoadedKey(resourceKey);
          setError(null);
        })
        .catch((cause: unknown) => {
          if (generation.current !== requestGeneration) return;
          setError(message(cause));
        })
        .finally(() => {
          if (generation.current === requestGeneration) setLoading(false);
        });
    }
    return () => {
      generation.current += 1;
    };
  }, [projectId, resourceKey, runtime]);

  useEffect(() => {
    let disposed = false;
    let cleanup: (() => void) | null = null;
    void runtime
      .onNotification((notification) => {
        if (
          !disposed &&
          projectId &&
          notification.method === "automation.updated"
        ) {
          void refresh();
        }
      })
      .then((unsubscribe) => {
        if (disposed) unsubscribe();
        else cleanup = unsubscribe;
      })
      .catch((cause: unknown) => {
        if (!disposed) setError(message(cause));
      });
    return () => {
      disposed = true;
      cleanup?.();
    };
  }, [projectId, refresh, runtime]);

  const create = useCallback(
    async (params: Omit<MethodParams["automation/create"], "projectId">) => {
      if (!projectId) return null;
      setLoading(true);
      setError(null);
      try {
        const result = await runtime.request("automation/create", {
          ...params,
          projectId,
        });
        await refresh();
        return result;
      } catch (cause) {
        setError(message(cause));
        return null;
      } finally {
        setLoading(false);
      }
    },
    [projectId, refresh, runtime],
  );

  const update = useCallback(
    async (params: MethodParams["automation/update"]) => {
      setLoading(true);
      setError(null);
      try {
        const result = await runtime.request("automation/update", params);
        await refresh();
        return result.automation;
      } catch (cause) {
        setError(message(cause));
        return null;
      } finally {
        setLoading(false);
      }
    },
    [refresh, runtime],
  );

  const remove = useCallback(
    async (automationId: string) => {
      setLoading(true);
      setError(null);
      try {
        const result = await runtime.request("automation/remove", {
          automationId,
        });
        await refresh();
        return result.removed;
      } catch (cause) {
        setError(message(cause));
        return false;
      } finally {
        setLoading(false);
      }
    },
    [refresh, runtime],
  );

  const runNow = useCallback(
    async (automationId: string) => {
      setLoading(true);
      setError(null);
      try {
        const result = await runtime.request("automation/run", {
          automationId,
        });
        await refresh();
        return result;
      } catch (cause) {
        setError(message(cause));
        return null;
      } finally {
        setLoading(false);
      }
    },
    [refresh, runtime],
  );

  const loadRuns = useCallback(
    async (automationId: string) => {
      setError(null);
      try {
        const result = await runtime.request("automation/runs", {
          automationId,
          limit: 100,
        });
        setRuns((current) => ({
          ...current,
          [automationId]: result.runs,
        }));
        return result.runs;
      } catch (cause) {
        setError(message(cause));
        return [];
      }
    },
    [runtime],
  );

  const visibleInventory =
    loadedKey === resourceKey ? inventory : null;
  return {
    inventory: visibleInventory,
    runs,
    loading:
      loading || (projectId !== null && loadedKey !== resourceKey),
    error,
    refresh,
    create,
    update,
    remove,
    runNow,
    loadRuns,
  };
}
