import { useCallback, useEffect, useRef, useState } from "react";

import type {
  PluginCatalogResult,
  PluginInfo,
} from "../../generated/app-server";
import type { DesktopRuntime } from "../../rpc/contracts";

interface PluginCatalogState extends PluginCatalogResult {
  loading: boolean;
  error: string | null;
}

const emptyState: PluginCatalogState = {
  plugins: [],
  diagnostics: [],
  revision: "",
  loading: true,
  error: null,
};

function message(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

export function usePluginCatalog(runtime: DesktopRuntime) {
  const [state, setState] = useState<PluginCatalogState>(emptyState);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const generation = useRef(0);

  const replace = useCallback((catalog: PluginCatalogResult) => {
    setState({ ...catalog, loading: false, error: null });
    setSelectedId((current) =>
      current && catalog.plugins.some((plugin) => plugin.id === current)
        ? current
        : (catalog.plugins[0]?.id ?? null),
    );
  }, []);

  const load = useCallback(async () => {
    const requestGeneration = ++generation.current;
    setState((current) => ({ ...current, loading: true, error: null }));
    try {
      const result = await runtime.request("plugins/list", {});
      if (generation.current === requestGeneration) replace(result);
    } catch (error: unknown) {
      if (generation.current !== requestGeneration) return;
      setState((current) => ({
        ...current,
        loading: false,
        error: message(error),
      }));
    }
  }, [replace, runtime]);

  useEffect(() => {
    void load();
    let active = true;
    let unsubscribe: (() => void) | null = null;
    void runtime
      .onNotification((notification) => {
        if (notification.method === "plugins.changed") void load();
      })
      .then((stop) => {
        if (active) unsubscribe = stop;
        else stop();
      })
      .catch(() => undefined);
    return () => {
      active = false;
      generation.current += 1;
      unsubscribe?.();
    };
  }, [load, runtime]);

  const mutate = useCallback(
    async (operation: () => Promise<PluginCatalogResult>) => {
      const requestGeneration = ++generation.current;
      setState((current) => ({ ...current, loading: true, error: null }));
      try {
        const result = await operation();
        if (generation.current === requestGeneration) replace(result);
      } catch (error: unknown) {
        if (generation.current !== requestGeneration) return;
        setState((current) => ({
          ...current,
          loading: false,
          error: message(error),
        }));
      }
    },
    [replace],
  );

  const add = useCallback(
    (path: string) =>
      mutate(() => runtime.request("plugins/add", { path })),
    [mutate, runtime],
  );
  const setEnabled = useCallback(
    (pluginId: string, enabled: boolean) =>
      mutate(() =>
        runtime.request("plugins/set-enabled", { pluginId, enabled }),
      ),
    [mutate, runtime],
  );
  const remove = useCallback(
    async (pluginId: string) => {
      const requestGeneration = ++generation.current;
      setState((current) => ({ ...current, loading: true, error: null }));
      try {
        await runtime.request("plugins/remove", { pluginId });
        if (generation.current !== requestGeneration) return;
        const result = await runtime.request("plugins/list", {});
        if (generation.current === requestGeneration) replace(result);
      } catch (error: unknown) {
        if (generation.current !== requestGeneration) return;
        setState((current) => ({
          ...current,
          loading: false,
          error: message(error),
        }));
      }
    },
    [replace, runtime],
  );

  const selected: PluginInfo | null =
    state.plugins.find((plugin) => plugin.id === selectedId) ?? null;
  return {
    ...state,
    selected,
    select: setSelectedId,
    refresh: load,
    add,
    setEnabled,
    remove,
  };
}
