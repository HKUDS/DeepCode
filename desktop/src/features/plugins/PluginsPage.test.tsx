import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import type {
  MethodParams,
  MethodResults,
  PluginCatalogResult,
} from "../../generated/app-server";
import type {
  AnyRpcNotification,
  DesktopRuntime,
  RpcMethod,
} from "../../rpc/contracts";
import { PluginsPage } from "./PluginsPage";

const plugin = {
  id: "plg_0123456789abcdef01234567",
  name: "review-tools",
  version: "1.0.0",
  description: "Review code changes",
  status: "active" as const,
  enabled: true,
  source: "linked-directory" as const,
  path: "/plugins/review-tools",
  schema: "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json" as const,
  manifestPath: "/plugins/review-tools/plugin.json",
  manifestRevision: `sha256:${"a".repeat(64)}`,
  components: [
    {
      kind: "skills" as const,
      status: "ready" as const,
      resource: "skills",
      itemCount: 1,
      diagnostics: [],
    },
  ],
  diagnostics: [],
  error: null,
};

class PluginRuntime {
  catalog: PluginCatalogResult = {
    plugins: [],
    diagnostics: [],
    revision: `sha256:${"0".repeat(64)}`,
  };
  requests: Array<{ method: string; params: unknown }> = [];
  listener: ((notification: AnyRpcNotification) => void) | null = null;

  async request<M extends RpcMethod>(
    method: M,
    params: MethodParams[M],
  ): Promise<MethodResults[M]> {
    this.requests.push({ method, params });
    if (method === "plugins/add") this.catalog = { ...this.catalog, plugins: [plugin] };
    if (method === "plugins/set-enabled") {
      const request = params as MethodParams["plugins/set-enabled"];
      this.catalog = {
        ...this.catalog,
        plugins: this.catalog.plugins.map((current) =>
          current.id === request.pluginId
            ? {
                ...current,
                enabled: request.enabled,
                status: request.enabled ? "active" : "disabled",
              }
            : current,
        ),
      };
    }
    if (method === "plugins/remove") {
      const removed = this.catalog.plugins[0] ?? plugin;
      this.catalog = { ...this.catalog, plugins: [] };
      return { removed: true, plugin: removed } as MethodResults[M];
    }
    return this.catalog as MethodResults[M];
  }

  async pickDirectory() {
    return "/plugins/review-tools";
  }

  async onNotification(listener: (notification: AnyRpcNotification) => void) {
    this.listener = listener;
    return () => {
      this.listener = null;
    };
  }
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

test("adds, disables, and unregisters a local Plugin", async () => {
  const runtime = new PluginRuntime();
  vi.spyOn(window, "confirm").mockReturnValue(true);
  render(<PluginsPage runtime={runtime as unknown as DesktopRuntime} />);

  expect(screen.getByText(/MCP servers join the shared tool runtime/)).toBeTruthy();
  await screen.findByText(/No Plugins registered/);
  fireEvent.click(screen.getByRole("button", { name: "Add local Plugin" }));
  await screen.findByRole("heading", { name: "review-tools" });

  fireEvent.click(screen.getByRole("button", { name: "Disable" }));
  await screen.findByRole("button", { name: "Enable" });
  fireEvent.click(screen.getByRole("button", { name: "Unregister" }));
  await screen.findByText(/No Plugins registered/);

  expect(runtime.requests.map((request) => request.method)).toEqual(
    expect.arrayContaining([
      "plugins/list",
      "plugins/add",
      "plugins/set-enabled",
      "plugins/remove",
    ]),
  );
});

test("reloads the registry after plugins.changed", async () => {
  const runtime = new PluginRuntime();
  render(<PluginsPage runtime={runtime as unknown as DesktopRuntime} />);
  await screen.findByText(/No Plugins registered/);
  runtime.catalog = { ...runtime.catalog, plugins: [plugin] };

  runtime.listener?.({
    jsonrpc: "2.0",
    method: "plugins.changed",
    params: {},
  });

  await screen.findByRole("heading", { name: "review-tools" });
  await waitFor(() =>
    expect(
      runtime.requests.filter((request) => request.method === "plugins/list"),
    ).toHaveLength(2),
  );
});
