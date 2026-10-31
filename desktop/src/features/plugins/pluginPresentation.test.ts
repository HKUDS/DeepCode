import i18n from "i18next";
import { afterEach, beforeEach, expect, it } from "vitest";

import { __setLocaleForTests, initI18n } from "../../app/i18n";
import type { PluginInfo } from "../../generated/app-server";
import {
  pluginComponentLabel,
  pluginSeverityLabel,
  pluginStatusLabel,
} from "./pluginPresentation";

type Component = PluginInfo["components"][number];
const STATUSES = Object.keys({ active: 1, disabled: 1, invalid: 1 } satisfies Record<PluginInfo["status"], 1>) as PluginInfo["status"][];
const KINDS = Object.keys({ skills: 1, mcp: 1 } satisfies Record<Component["kind"], 1>) as Component["kind"][];
const COMPONENT_STATUSES = Object.keys({ ready: 1, unsupported: 1, invalid: 1 } satisfies Record<
  Component["status"],
  1
>) as Component["status"][];
type Severity = PluginInfo["diagnostics"][number]["severity"];
const SEVERITIES = Object.keys({ warning: 1, error: 1 } satisfies Record<Severity, 1>) as Severity[];

beforeEach(() => {
  initI18n();
  __setLocaleForTests("zh-CN");
});
afterEach(() => {
  __setLocaleForTests("en");
});

it("says every plugin and component state in Chinese", () => {
  const t = i18n.t.bind(i18n);
  const labels = [
    ...STATUSES.map((status) => pluginStatusLabel(status, t)),
    ...KINDS.flatMap((kind) =>
      COMPONENT_STATUSES.map((status) => pluginComponentLabel({ kind, status } as Component, t)),
    ),
    ...SEVERITIES.map((severity) => pluginSeverityLabel(severity, t)),
  ];
  expect(labels.filter((label) => (label.match(/[A-Za-z]{2,}/g) ?? []).some((w) => w !== "MCP"))).toEqual([]);
});
