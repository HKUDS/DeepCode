import { useCallback, useEffect, useState } from "react";

import type {
  ProviderDiscoverParams,
  ProviderDiscoverResult,
  ConnectionCatalogResult,
  ModelCatalogResult,
  ProviderTestResult,
  ProviderTestParams,
  ProviderLoginFlow,
  ProviderLogoutResult,
  ProviderUpsertParams,
} from "../../generated/app-server";
import type { ClientRuntime } from "../../rpc/contracts";

export interface ConnectionCatalogController {
  catalog: ConnectionCatalogResult | null;
  loading: boolean;
  error: string | null;
  reload(): Promise<void>;
  login(connectionId: string): Promise<ProviderLoginFlow>;
  pollLogin(flowId: string): Promise<ProviderLoginFlow>;
  cancelLogin(flowId: string): Promise<ProviderLoginFlow>;
  logout(connectionId: string): Promise<ProviderLogoutResult>;
  upsert(connection: ProviderUpsertParams["connection"]): Promise<void>;
  remove(connectionId: string): Promise<void>;
  test(
    connectionId: string,
    model?: string,
    options?: Pick<ProviderTestParams, "mode" | "connection">,
  ): Promise<ProviderTestResult>;
  models(connectionId: string, refresh?: boolean): Promise<ModelCatalogResult>;
  /** Probe an endpoint AS SHOWN in an editor form; nothing is stored. */
  discover(
    params: Omit<ProviderDiscoverParams, "projectId">,
  ): Promise<ProviderDiscoverResult>;
}

export function useConnectionCatalog(
  runtime: ClientRuntime,
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
    async (
      connectionId: string,
      model?: string,
      options?: Pick<ProviderTestParams, "mode" | "connection">,
    ) => {
      try {
        const result = await runtime.request("provider/test", {
          connectionId,
          ...options,
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

  const discover = useCallback(
    (params: Omit<ProviderDiscoverParams, "projectId">) =>
      runtime.request("provider/discover", {
        ...params,
        ...(projectId ? { projectId } : {}),
      }),
    [projectId, runtime],
  );

  const login = useCallback(
    (connectionId: string) =>
      runtime.request("provider/login/start", {
        connectionId,
        openBrowser: true,
      }),
    [runtime],
  );
  const pollLogin = useCallback(
    (flowId: string) => runtime.request("provider/login/poll", { flowId }),
    [runtime],
  );
  const cancelLogin = useCallback(
    (flowId: string) => runtime.request("provider/login/cancel", { flowId }),
    [runtime],
  );
  const logout = useCallback(
    (connectionId: string) =>
      runtime.request("provider/logout", { connectionId }),
    [runtime],
  );
  const currentProject = state.projectId === projectId;
  return {
    login,
    pollLogin,
    cancelLogin,
    logout,
    catalog: currentProject ? state.catalog : null,
    loading: currentProject ? state.loading : true,
    error: currentProject ? state.error : null,
    reload,
    upsert,
    remove,
    test,
    models,
    discover,
  };
}

function errorMessage(cause: unknown): string {
  return cause instanceof Error ? cause.message : String(cause);
}
