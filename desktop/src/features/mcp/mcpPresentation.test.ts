import i18n from "i18next";
import { afterEach, beforeEach, expect, it } from "vitest";

import { __setLocaleForTests, initI18n } from "../../app/i18n";
import type { McpServerInfo } from "../../generated/app-server";
import {
  mcpApprovalLabel,
  mcpAuthLabel,
  mcpCategoryLabel,
  mcpConfigurationLabel,
  mcpConfigurationText,
  mcpRuntimeText,
  mcpSourceLabel,
  mcpStatusLabel,
  mcpTransportLabel,
} from "./mcpPresentation";

type Server = McpServerInfo;
// Every value of each type: a new one without a label fails the typecheck.
const SOURCES = Object.keys({ user: 1, project: 1, plugin: 1 } satisfies Record<Server["source"], 1>) as Server["source"][];
const TRANSPORTS = Object.keys({ stdio: 1, sse: 1, streamableHttp: 1 } satisfies Record<Server["transport"], 1>) as Server["transport"][];
const CONFIGURATIONS = Object.keys({
  configured: 1, invalid: 1, disabled: 1, blocked: 1, missing_credentials: 1,
} satisfies Record<Server["configurationState"], 1>) as Server["configurationState"][];
const AUTH = Object.keys({
  not_required: 1, login_required: 1, authorizing: 1, authenticated: 1,
} satisfies Record<Server["authState"], 1>) as Server["authState"][];
const APPROVALS = Object.keys({ auto: 1, prompt: 1, writes: 1, approve: 1 } satisfies Record<Server["approvalMode"], 1>) as Server["approvalMode"][];
const RUNTIME = Object.keys({
  stopped: 1, connecting: 1, tested: 1, connected: 1, failed: 1,
} satisfies Record<Server["runtimeState"], 1>) as Server["runtimeState"][];

function server(overrides: Partial<Server>): Server {
  return {
    enabled: true,
    configurationState: "configured",
    configurationMessage: "Configured command is not on PATH",
    runtimeState: "stopped",
    runtimeMessage: "spawn npx ENOENT",
    missingEnvKeys: [],
    ...overrides,
  } as Server;
}

// Names and protocols stay as they are.
const KEPT = new Set(["HTTP", "SSE", "API", "DeepCode"]);
const english = (text: string) => (text.match(/[A-Za-z]{2,}/g) ?? []).filter((w) => !KEPT.has(w));

beforeEach(() => {
  initI18n();
  __setLocaleForTests("zh-CN");
});
afterEach(() => {
  __setLocaleForTests("en");
});

it("says every MCP state in Chinese, not as its raw value", () => {
  const t = i18n.t.bind(i18n);
  const labels = [
    ...SOURCES.map((value) => mcpSourceLabel(value, t)),
    ...TRANSPORTS.map((value) => mcpTransportLabel(value, t)),
    ...CONFIGURATIONS.map((value) => mcpConfigurationLabel(value, t)),
    ...AUTH.map((value) => mcpAuthLabel(value, t)),
    ...APPROVALS.map((value) => mcpApprovalLabel(value, t)),
    ...RUNTIME.map((value) => mcpStatusLabel(server({ runtimeState: value }), t)),
    ...RUNTIME.filter((value) => value !== "failed").map((value) =>
      mcpRuntimeText(server({ runtimeState: value }), t),
    ),
    ...["configured", "disabled", "missing_credentials"].map((value) =>
      mcpConfigurationText(server({ configurationState: value as Server["configurationState"] }), t),
    ),
    ...["browser", "docs", "web", "api", "design", "productivity", "code", "database"].map(
      (value) => mcpCategoryLabel(value, t),
    ),
  ];
  expect(labels.filter((label) => english(label).length)).toEqual([]);
});

it("keeps the service's words where they name the problem", () => {
  const t = i18n.t.bind(i18n);
  expect(mcpRuntimeText(server({ runtimeState: "failed" }), t)).toBe("spawn npx ENOENT");
  expect(mcpConfigurationText(server({ configurationState: "invalid" }), t)).toBe(
    "Configured command is not on PATH",
  );
  expect(mcpCategoryLabel("robotics", t)).toBe("robotics");
});

it("says that auto and approve add no checks over the Session's access", () => {
  const t = i18n.t.bind(i18n);
  expect(mcpApprovalLabel("approve", t)).toBe(mcpApprovalLabel("auto", t));
});
