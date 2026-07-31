import { useCallback, useEffect, useRef, useState } from "react";

import type { MethodParams, MethodResults } from "../../generated/app-server";
import type { DesktopRuntime } from "../../rpc/contracts";

type AutomationInventory = MethodResults["automation/list"];
type AutomationRunPage = MethodResults["automation/runs"];

const AUTOMATION_PAGE_SIZE = 50;
const AUTOMATION_RUN_PAGE_SIZE = 50;

function message(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

export function useAutomations(
  runtime: DesktopRuntime,
  projectId: string | null,
  expandedRunAutomationId: string | null = null,
) {
  const [inventory, setInventory] = useState<AutomationInventory | null>(null);
  const [inventoryKey, setInventoryKey] = useState<string | null>(null);
  const [settledKey, setSettledKey] = useState<string | null>(null);
  const [runs, setRuns] = useState<Record<string, AutomationRunPage>>({});
  const [loading, setLoading] = useState(false);
  const [loadingMoreAutomations, setLoadingMoreAutomations] = useState(false);
  const [loadingRunsFor, setLoadingRunsFor] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const generation = useRef(0);
  const resourceKey = projectId ?? "__none__";

  const refresh = useCallback(async () => {
    const requestGeneration = ++generation.current;
    setLoadingMoreAutomations(false);
    setLoadingRunsFor(null);
    if (!projectId) {
      setInventory(null);
      setInventoryKey(null);
      setSettledKey(resourceKey);
      setRuns({});
      setLoading(false);
      setError(null);
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const result = await runtime.request("automation/list", {
        projectId,
        limit: AUTOMATION_PAGE_SIZE,
        offset: 0,
      });
      if (generation.current !== requestGeneration) return;
      setInventory(result);
      setInventoryKey(resourceKey);
      setSettledKey(resourceKey);
    } catch (cause) {
      if (generation.current !== requestGeneration) return;
      setSettledKey(resourceKey);
      setError(message(cause));
    } finally {
      if (generation.current === requestGeneration) setLoading(false);
    }
  }, [projectId, resourceKey, runtime]);

  useEffect(() => {
    const requestGeneration = ++generation.current;
    if (projectId) {
      void runtime
        .request("automation/list", {
          projectId,
          limit: AUTOMATION_PAGE_SIZE,
          offset: 0,
        })
        .then((result) => {
          if (generation.current !== requestGeneration) return;
          setLoadingMoreAutomations(false);
          setLoadingRunsFor(null);
          setRuns({});
          setInventory(result);
          setInventoryKey(resourceKey);
          setSettledKey(resourceKey);
          setError(null);
        })
        .catch((cause: unknown) => {
          if (generation.current !== requestGeneration) return;
          setLoadingMoreAutomations(false);
          setLoadingRunsFor(null);
          setRuns({});
          setSettledKey(resourceKey);
          setError(message(cause));
        })
        .finally(() => {
          if (generation.current === requestGeneration) setLoading(false);
        });
    } else {
      void Promise.resolve().then(() => {
        if (generation.current !== requestGeneration) return;
        setLoadingMoreAutomations(false);
        setLoadingRunsFor(null);
        setRuns({});
        setInventory(null);
        setInventoryKey(null);
        setSettledKey(resourceKey);
        setLoading(false);
        setError(null);
      });
    }
    return () => {
      generation.current += 1;
    };
  }, [projectId, resourceKey, runtime]);

  const loadMoreAutomations = useCallback(async () => {
    if (
      !projectId ||
      !inventory?.hasMore ||
      inventory.nextOffset === null
    ) {
      return null;
    }
    const requestGeneration = generation.current;
    const offset = inventory.nextOffset;
    setLoadingMoreAutomations(true);
    setError(null);
    try {
      const result = await runtime.request("automation/list", {
        projectId,
        limit: AUTOMATION_PAGE_SIZE,
        offset,
      });
      if (generation.current !== requestGeneration) return null;
      setInventory((current) => {
        if (!current) return result;
        return {
          ...result,
          automations: mergeUniqueBy(
            current.automations,
            result.automations,
            (automation) => automation.id,
          ),
          latestRuns: mergeUniqueBy(
            current.latestRuns,
            result.latestRuns,
            (run) => run.automationId,
          ),
        };
      });
      return result;
    } catch (cause) {
      if (generation.current === requestGeneration) setError(message(cause));
      return null;
    } finally {
      if (generation.current === requestGeneration) {
        setLoadingMoreAutomations(false);
      }
    }
  }, [inventory, projectId, runtime]);

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
          requestId: `desktop-${crypto.randomUUID()}`,
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
      const requestGeneration = generation.current;
      setLoadingRunsFor(automationId);
      setError(null);
      try {
        const result = await runtime.request("automation/runs", {
          automationId,
          limit: AUTOMATION_RUN_PAGE_SIZE,
          offset: 0,
        });
        if (generation.current !== requestGeneration) return null;
        setRuns((current) => ({
          ...current,
          [automationId]: result,
        }));
        return result;
      } catch (cause) {
        if (generation.current === requestGeneration) setError(message(cause));
        return null;
      } finally {
        if (generation.current === requestGeneration) {
          setLoadingRunsFor((current) =>
            current === automationId ? null : current,
          );
        }
      }
    },
    [runtime],
  );

  const loadMoreRuns = useCallback(
    async (automationId: string) => {
      const currentPage = runs[automationId];
      if (!currentPage?.hasMore || currentPage.nextOffset === null) return null;
      const requestGeneration = generation.current;
      setLoadingRunsFor(automationId);
      setError(null);
      try {
        const result = await runtime.request("automation/runs", {
          automationId,
          limit: AUTOMATION_RUN_PAGE_SIZE,
          offset: currentPage.nextOffset,
        });
        if (generation.current !== requestGeneration) return null;
        setRuns((current) => {
          const previous = current[automationId];
          if (!previous) return { ...current, [automationId]: result };
          return {
            ...current,
            [automationId]: {
              ...result,
              runs: mergeUniqueBy(
                previous.runs,
                result.runs,
                (run) => run.id,
              ),
            },
          };
        });
        return result;
      } catch (cause) {
        if (generation.current === requestGeneration) setError(message(cause));
        return null;
      } finally {
        if (generation.current === requestGeneration) {
          setLoadingRunsFor((current) =>
            current === automationId ? null : current,
          );
        }
      }
    },
    [runs, runtime],
  );

  const refreshFromNotification = useCallback(async () => {
    await Promise.all([
      refresh(),
      ...(expandedRunAutomationId
        ? [loadRuns(expandedRunAutomationId)]
        : []),
    ]);
  }, [expandedRunAutomationId, loadRuns, refresh]);

  useEffect(() => {
    let disposed = false;
    let cleanup: (() => void) | null = null;
    void runtime
      .onNotification((notification) => {
        if (
          !disposed &&
          projectId &&
          (notification.method === "automation.updated" ||
            notification.method === "server.warning")
        ) {
          void refreshFromNotification();
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
  }, [projectId, refreshFromNotification, runtime]);

  const visibleInventory = inventoryKey === resourceKey ? inventory : null;
  return {
    inventory: visibleInventory,
    runs,
    loading:
      loading || (projectId !== null && settledKey !== resourceKey),
    loadingMoreAutomations,
    loadingRunsFor,
    error,
    refresh,
    loadMoreAutomations,
    create,
    update,
    remove,
    runNow,
    loadRuns,
    loadMoreRuns,
  };
}

function mergeUniqueBy<T>(
  current: readonly T[],
  incoming: readonly T[],
  key: (item: T) => string,
): T[] {
  const merged = [...current];
  const positions = new Map(
    merged.map((item, index) => [key(item), index] as const),
  );
  for (const item of incoming) {
    const itemKey = key(item);
    const position = positions.get(itemKey);
    if (position === undefined) {
      positions.set(itemKey, merged.length);
      merged.push(item);
    } else {
      merged[position] = item;
    }
  }
  return merged;
}
