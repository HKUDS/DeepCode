import { useCallback, useEffect, useRef, useState } from "react";

import type {
  ConfigScope,
  JsonObject,
  McpInventory,
} from "../../generated/app-server";
import type { DesktopRuntime } from "../../rpc/contracts";

function message(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

export function useMcpInventory(
  runtime: DesktopRuntime,
  projectId: string | null,
) {
  const [inventory, setInventory] = useState<McpInventory | null>(null);
  const [loadedKey, setLoadedKey] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const generation = useRef(0);
  const resourceKey = projectId ?? "__user__";

  const refresh = useCallback(async () => {
    const requestGeneration = ++generation.current;
    setLoading(true);
    setError(null);
    try {
      const result = await runtime.request("mcp/list", {
        ...(projectId ? { projectId } : {}),
      });
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
    void runtime
      .request("mcp/list", {
        ...(projectId ? { projectId } : {}),
      })
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
    return () => {
      generation.current += 1;
    };
  }, [projectId, resourceKey, runtime]);

  const upsert = useCallback(
    async (name: string, scope: ConfigScope, server: JsonObject) => {
      const requestGeneration = ++generation.current;
      setLoading(true);
      setError(null);
      try {
        const result = await runtime.request("mcp/upsert", {
          name,
          scope,
          server,
          ...(projectId ? { projectId } : {}),
        });
        if (generation.current !== requestGeneration) return false;
        setInventory(result);
        setLoadedKey(resourceKey);
        return true;
      } catch (cause) {
        if (generation.current !== requestGeneration) return false;
        setError(message(cause));
        return false;
      } finally {
        if (generation.current === requestGeneration) setLoading(false);
      }
    },
    [projectId, resourceKey, runtime],
  );

  const remove = useCallback(
    async (name: string, scope: ConfigScope) => {
      const requestGeneration = ++generation.current;
      setLoading(true);
      setError(null);
      try {
        const result = await runtime.request("mcp/remove", {
          name,
          scope,
          ...(projectId ? { projectId } : {}),
        });
        if (generation.current !== requestGeneration) return false;
        setInventory(result);
        setLoadedKey(resourceKey);
        return true;
      } catch (cause) {
        if (generation.current !== requestGeneration) return false;
        setError(message(cause));
        return false;
      } finally {
        if (generation.current === requestGeneration) setLoading(false);
      }
    },
    [projectId, resourceKey, runtime],
  );

  return {
    inventory: loadedKey === resourceKey ? inventory : null,
    loading: loading || loadedKey !== resourceKey,
    error,
    refresh,
    upsert,
    remove,
  };
}
