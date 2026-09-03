import { useCallback, useEffect, useRef, useState } from "react";

import type {
  ConfigScope,
  JsonObject,
  McpInventory,
  McpOAuthFlow,
  McpPresetInventory,
  McpProbeResult,
  Project,
} from "../../generated/app-server";
import type { DesktopRuntime } from "../../rpc/contracts";

interface McpCatalogState {
  key: string;
  inventory: McpInventory | null;
  presets: McpPresetInventory | null;
  loading: boolean;
  error: string | null;
}

export function useMcpCatalog(runtime: DesktopRuntime, project: Project | null) {
  const projectId = project?.id;
  const catalogKey = projectId ?? "__user__";
  const generation = useRef(0);
  const activeCatalogKey = useRef<string | null>(catalogKey);
  const [state, setState] = useState<McpCatalogState>({
    key: "",
    inventory: null,
    presets: null,
    loading: true,
    error: null,
  });

  const load = useCallback(async () => {
    // A mutation started for a previous project may finish after the user has
    // switched projects. Do not let its captured loader invalidate or replace
    // the active project's catalog request.
    if (activeCatalogKey.current !== catalogKey) return;
    const requestGeneration = ++generation.current;
    setState((current) => ({
      ...current,
      key: catalogKey,
      loading: true,
      error: null,
    }));
    try {
      const params = projectId ? { projectId } : {};
      const [inventory, presets] = await Promise.all([
        runtime.request("mcp/list", params),
        runtime.request("mcp/presets", params),
      ]);
      if (generation.current !== requestGeneration) return;
      setState({
        key: catalogKey,
        inventory,
        presets,
        loading: false,
        error: null,
      });
    } catch (cause) {
      if (generation.current !== requestGeneration) return;
      setState((current) => ({
        ...current,
        key: catalogKey,
        loading: false,
        error: cause instanceof Error ? cause.message : String(cause),
      }));
    }
  }, [catalogKey, projectId, runtime]);

  useEffect(() => {
    activeCatalogKey.current = catalogKey;
    void load();
    let active = true;
    let unsubscribe: (() => void) | null = null;
    if (typeof runtime.onNotification === "function") {
      void runtime
        .onNotification((notification) => {
          if (active && notification.method === "mcp.changed") void load();
        })
        .then((dispose) => {
          if (active) unsubscribe = dispose;
          else dispose();
        })
        .catch(() => {
          // The list remains usable when an embedding omits notifications.
        });
    }
    return () => {
      active = false;
      if (activeCatalogKey.current === catalogKey) {
        activeCatalogKey.current = null;
      }
      generation.current += 1;
      unsubscribe?.();
    };
  }, [catalogKey, load, runtime]);

  const mutate = useCallback(
    async (operation: () => Promise<McpInventory>) => {
      const result = await operation();
      await load();
      return result;
    },
    [load],
  );

  const upsert = useCallback(
    (name: string, scope: ConfigScope, server: JsonObject) =>
      mutate(() =>
        runtime.request("mcp/upsert", {
          ...(projectId ? { projectId } : {}),
          name,
          scope,
          server,
        }),
      ),
    [mutate, projectId, runtime],
  );

  const remove = useCallback(
    (name: string, scope: ConfigScope) =>
      mutate(() =>
        runtime.request("mcp/remove", {
          ...(projectId ? { projectId } : {}),
          name,
          scope,
        }),
      ),
    [mutate, projectId, runtime],
  );

  const addPreset = useCallback(
    (presetId: string) =>
      mutate(() =>
        runtime.request("mcp/preset/add", {
          ...(projectId ? { projectId } : {}),
          presetId,
          enabled: false,
        }),
      ),
    [mutate, projectId, runtime],
  );

  const setEnabled = useCallback(
    (name: string, enabled: boolean) =>
      mutate(() =>
        runtime.request("mcp/set-enabled", {
          ...(projectId ? { projectId } : {}),
          name,
          enabled,
        }),
      ),
    [mutate, projectId, runtime],
  );

  const probe = useCallback(
    async (name: string): Promise<McpProbeResult> => {
      const result = await runtime.request("mcp/probe", {
        ...(projectId ? { projectId } : {}),
        name,
      });
      await load();
      return result;
    },
    [load, projectId, runtime],
  );

  const startOAuth = useCallback(
    async (name: string): Promise<McpOAuthFlow> => {
      const result = await runtime.request("mcp/oauth/start", {
        ...(projectId ? { projectId } : {}),
        name,
        openBrowser: true,
      });
      await load();
      return result;
    },
    [load, projectId, runtime],
  );

  const logout = useCallback(
    async (name: string) => {
      await runtime.request("mcp/oauth/logout", {
        ...(projectId ? { projectId } : {}),
        name,
      });
      await load();
    },
    [load, projectId, runtime],
  );

  const current = state.key === catalogKey;
  return {
    inventory: current ? state.inventory : null,
    presets: current ? state.presets : null,
    loading: !current || state.loading,
    error: current ? state.error : null,
    refresh: load,
    upsert,
    remove,
    addPreset,
    setEnabled,
    probe,
    startOAuth,
    logout,
  };
}
