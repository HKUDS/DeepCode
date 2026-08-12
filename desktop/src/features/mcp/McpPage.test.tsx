import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import type {
  McpInventory,
  McpPresetInventory,
  MethodParams,
  MethodResults,
  Project,
} from "../../generated/app-server";
import type { DesktopRuntime, RpcMethod } from "../../rpc/contracts";
import { McpPage } from "./McpPage";

const project: Project = {
  id: "project-1",
  canonicalPath: "/workspace/repo",
  displayName: "repo",
  trustState: "trusted",
  settings: {},
  createdAt: "2026-08-10T00:00:00Z",
  updatedAt: "2026-08-10T00:00:00Z",
  lastOpenedAt: "2026-08-10T00:00:00Z",
};

class McpRuntime {
  requests: Array<{ method: string; params: unknown }> = [];
  inventory: McpInventory = {
    servers: [],
    userConfigPath: "/home/user/.deepcode/deepcode_config.json",
    projectConfigPath: "/workspace/repo/deepcode_config.json",
  };
  presets: McpPresetInventory = {
    source: "https://github.com/HKUDS/nanobot",
    sourceRevision: "test-revision",
    presets: [
      {
        id: "context7",
        displayName: "Context7",
        category: "documentation",
        description: "Current library documentation.",
        docsUrl: "https://github.com/upstash/context7",
        transport: "stdio",
        auth: null,
        requires: "Node.js",
        note: "",
        requiredEnvironment: [],
        missingEnvironment: [],
        configured: false,
      },
    ],
  };

  async request<M extends RpcMethod>(
    method: M,
    params: MethodParams[M],
  ): Promise<MethodResults[M]> {
    this.requests.push({ method, params });
    if (method === "mcp/presets") {
      return this.presets as MethodResults[M];
    }
    if (method === "mcp/preset/add") {
      const request = params as MethodParams["mcp/preset/add"];
      this.presets = {
        ...this.presets,
        presets: this.presets.presets.map((preset) =>
          preset.id === request.presetId
            ? { ...preset, configured: true }
            : preset,
        ),
      };
      this.inventory = {
        ...this.inventory,
        servers: [mcpServer(request.presetId, { enabled: false })],
      };
      return this.inventory as MethodResults[M];
    }
    if (method === "mcp/set-enabled") {
      const request = params as MethodParams["mcp/set-enabled"];
      this.inventory = {
        ...this.inventory,
        servers: this.inventory.servers.map((server) =>
          server.id === request.name || server.name === request.name
            ? {
                ...server,
                enabled: request.enabled,
                configurationState: request.enabled ? "configured" : "disabled",
              }
            : server,
        ),
      };
      return this.inventory as MethodResults[M];
    }
    if (method === "mcp/probe") {
      const request = params as MethodParams["mcp/probe"];
      return {
        serverId: request.name,
        name: request.name,
        ok: true,
        transport: "stdio",
        toolCount: 3,
        resourceCount: 1,
        promptCount: 1,
        elapsedSeconds: 0.1,
        error: null,
      } as MethodResults[M];
    }
    if (method === "mcp/upsert") {
      const request = params as MethodParams["mcp/upsert"];
      this.inventory = {
        ...this.inventory,
        servers: [
          {
            id: request.name,
            name: request.name,
            pluginId: null,
            policyKey: null,
            transport: "stdio",
            command: String(request.server.command),
            args: [],
            cwd: null,
            url: null,
            auth: null,
            enabled: Boolean(request.server.enabled),
            required: false,
            enabledTools: null,
            disabledTools: [],
            startupTimeoutSeconds: 10,
            toolTimeoutSeconds: 60,
            approvalMode: "writes",
            description: null,
            envKeys: [],
            forwardedEnvKeys: [],
            requiredEnvKeys: [],
            missingEnvKeys: [],
            credentialEnvKeys: ["OPENROUTER_API_KEY"],
            headerKeys: [],
            source: request.scope,
            configurationState: "configured",
            configurationMessage: "Configuration is ready; connection is checked on use",
            authState: "not_required",
            runtimeState: "stopped",
            runtimeMessage: "Not connected in this process",
            toolCount: 0,
            resourceCount: 0,
            promptCount: 0,
          },
        ],
      };
    }
    return this.inventory as MethodResults[M];
  }
}

function mcpServer(
  name: string,
  overrides: Partial<McpInventory["servers"][number]> = {},
): McpInventory["servers"][number] {
  return {
    id: name,
    name,
    pluginId: null,
    policyKey: null,
    transport: "stdio",
    command: "npx",
    args: [],
    cwd: null,
    url: null,
    auth: null,
    enabled: true,
    required: false,
    enabledTools: null,
    disabledTools: [],
    startupTimeoutSeconds: 10,
    toolTimeoutSeconds: 60,
    approvalMode: "writes",
    description: null,
    envKeys: [],
    forwardedEnvKeys: [],
    requiredEnvKeys: [],
    missingEnvKeys: [],
    credentialEnvKeys: [],
    headerKeys: [],
    source: "user",
    configurationState: "configured",
    configurationMessage: "Configuration is ready; connection is checked on use",
    authState: "not_required",
    runtimeState: "stopped",
    runtimeMessage: "Not connected in this process",
    toolCount: 0,
    resourceCount: 0,
    promptCount: 0,
    ...overrides,
  };
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

test("adds a credential-bound stdio MCP server through the shared RPC", async () => {
  const runtime = new McpRuntime();
  render(
    <McpPage
      runtime={runtime as unknown as DesktopRuntime}
      project={project}
    />,
  );

  await screen.findByText("No MCP servers configured");
  fireEvent.change(screen.getByLabelText("Name"), {
    target: { value: "openspace" },
  });
  fireEvent.change(screen.getByLabelText("Command"), {
    target: { value: "openspace-mcp" },
  });
  fireEvent.change(screen.getByLabelText("Credential bindings"), {
    target: { value: "OPENROUTER_API_KEY=openrouter" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Add custom server" }));

  await screen.findByRole("heading", { name: "openspace" });
  const upsert = runtime.requests.find(
    (request) => request.method === "mcp/upsert",
  );
  expect(upsert?.params).toMatchObject({
    projectId: project.id,
    name: "openspace",
    scope: "user",
    server: {
      type: "stdio",
      command: "openspace-mcp",
      credentialEnv: {
        OPENROUTER_API_KEY: { credentialRef: "provider:openrouter" },
      },
    },
  });
  await waitFor(() => expect(runtime.requests[0].method).toBe("mcp/list"));
});

test("shows Plugin MCP servers without exposing native edit actions", async () => {
  const runtime = new McpRuntime();
  runtime.inventory = {
    ...runtime.inventory,
    servers: [
      {
        id: "review-tools--context",
        name: "review-tools/context",
        pluginId: "review-tools",
        policyKey: "context",
        transport: "streamableHttp",
        command: null,
        args: [],
        cwd: null,
        url: "http://127.0.0.1:8765/mcp",
        auth: null,
        enabled: true,
        required: false,
        enabledTools: null,
        disabledTools: [],
        startupTimeoutSeconds: 10,
        toolTimeoutSeconds: 60,
        approvalMode: "writes",
        description: null,
        envKeys: [],
        forwardedEnvKeys: [],
        requiredEnvKeys: [],
        missingEnvKeys: [],
        credentialEnvKeys: [],
        headerKeys: [],
        source: "plugin",
        configurationState: "configured",
        configurationMessage: "Configuration is ready; connection is checked on use",
        authState: "not_required",
        runtimeState: "stopped",
        runtimeMessage: "Not connected in this process",
        toolCount: 0,
        resourceCount: 0,
        promptCount: 0,
      },
    ],
  };

  render(
    <McpPage
      runtime={runtime as unknown as DesktopRuntime}
      project={project}
    />,
  );

  await screen.findByRole("heading", { name: "review-tools/context" });
  expect(screen.getByText("Plugin policy")).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Edit" })).toBeNull();
  expect(screen.queryByRole("button", { name: "Remove" })).toBeNull();
});

test("adds a bundled preset disabled, tests it, then enables agent use", async () => {
  const runtime = new McpRuntime();
  render(
    <McpPage
      runtime={runtime as unknown as DesktopRuntime}
      project={project}
    />,
  );

  await screen.findByRole("heading", { name: "Context7" });
  fireEvent.click(screen.getByRole("button", { name: "Add server" }));

  await screen.findByRole("heading", { name: "context7" });
  expect(runtime.requests).toContainEqual({
    method: "mcp/preset/add",
    params: {
      projectId: project.id,
      presetId: "context7",
      enabled: false,
    },
  });
  await screen.findByText(
    "Added Context7 in disabled state. No package was downloaded and no process was started. Test it before enabling.",
  );
  fireEvent.click(screen.getByRole("button", { name: "Test connection" }));

  await screen.findByText(
    "context7 passed its connection test: 3 tools · 1 resources · 1 prompts. The test connection is now closed.",
  );
  expect(runtime.requests).toContainEqual({
    method: "mcp/probe",
    params: { projectId: project.id, name: "context7" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Enable" }));
  await screen.findByText(
    "Enabled context7. The agent can now call its tools when a task needs them.",
  );
  expect(runtime.requests).toContainEqual({
    method: "mcp/set-enabled",
    params: { projectId: project.id, name: "context7", enabled: true },
  });
});
