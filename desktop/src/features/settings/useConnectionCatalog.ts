import { useCallback, useEffect, useState } from "react";

import type {
  ConnectionCatalogResult,
  ModelCatalogResult,
  ProviderTestResult,
  ProviderUpsertParams,
} from "../../generated/app-server";
import type { DesktopRuntime } from "../../rpc/contracts";

export interface ConnectionCatalogController {
  catalog: ConnectionCatalogResult | null;
  loading: boolean;
  error: string | null;
  reload(): Promise<void>;
  upsert(connection: ProviderUpsertParams["connection"]): Promise<void>;
  remove(connectionId: string): Promise<void>;
  test(connectionId: string, model?: string): Promise<ProviderTestResult>;
  models(connectionId: string, refresh?: boolean): Promise<ModelCatalogResult>;
}

export function useConnectionCatalog(
  runtime: DesktopRuntime,
  projectId: string | null,
): ConnectionCatalogController {
  const [state, setState] = useState<{
    projectId: string | null;
    catalog: ConnectionCatalogResult | null;
    loading: boolean;
    error: string | null;
  }>({
    projectId,
    catalog: null,
    loading: true,
    error: null,
  });

  const reload = useCallback(async () => {
    setState((current) => ({
      ...current,
      projectId,
      loading: true,
      error: null,
    }));
    try {
      const result = await runtime.request("provider/list", {
        ...(projectId ? { projectId } : {}),
      });
      setState({
        projectId,
        catalog: result,
        loading: false,
        error: null,
      });
    } catch (cause) {
      setState((current) => ({
        ...current,
        projectId,
        loading: false,
        error: errorMessage(cause),
      }));
    }
  }, [projectId, runtime]);

  useEffect(() => {
    let cancelled = false;
    void runtime
      .request("provider/list", {
        ...(projectId ? { projectId } : {}),
      })
      .then((catalog) => {
        if (!cancelled) {
          setState({
            projectId,
            catalog,
            loading: false,
            error: null,
          });
        }
      })
      .catch((cause) => {
        if (!cancelled) {
          setState({
            projectId,
            catalog: null,
            loading: false,
            error: errorMessage(cause),
          });
        }
      });
    return () => {
      cancelled = true;
    };
  }, [projectId, runtime]);

  const upsert = useCallback(
    async (connection: ProviderUpsertParams["connection"]) => {
      try {
        const result = await runtime.request("provider/upsert", { connection });
        setState({
          projectId,
          catalog: result,
          loading: false,
          error: null,
        });
      } catch (cause) {
        setState((current) => ({ ...current, error: errorMessage(cause) }));
        throw cause;
      }
    },
    [projectId, runtime],
  );

  const remove = useCallback(
    async (connectionId: string) => {
      try {
        const result = await runtime.request("provider/remove", {
          connectionId,
        });
        setState({
          projectId,
          catalog: result,
          loading: false,
          error: null,
        });
      } catch (cause) {
        setState((current) => ({ ...current, error: errorMessage(cause) }));
        throw cause;
      }
    },
    [projectId, runtime],
  );

  const test = useCallback(
    async (connectionId: string, model?: string) => {
      try {
        const result = await runtime.request("provider/test", {
          connectionId,
          ...(projectId ? { projectId } : {}),
          ...(model ? { model } : {}),
        });
        setState((current) => ({ ...current, error: null }));
        return result;
      } catch (cause) {
        setState((current) => ({ ...current, error: errorMessage(cause) }));
        throw cause;
      }
    },
    [projectId, runtime],
  );

  const models = useCallback(
    (connectionId: string, refresh = false) =>
      runtime.request("model/list", {
        connectionId,
        ...(projectId ? { projectId } : {}),
        refresh,
      }),
    [projectId, runtime],
  );

  const currentProject = state.projectId === projectId;
  return {
    catalog: currentProject ? state.catalog : null,
    loading: currentProject ? state.loading : true,
    error: currentProject ? state.error : null,
    reload,
    upsert,
    remove,
    test,
    models,
  };
}

function errorMessage(cause: unknown): string {
  return cause instanceof Error ? cause.message : String(cause);
}
