import { afterEach, expect, it } from "vitest";

import { __setLocaleForTests, initI18n } from "../../app/i18n";
import type { SidecarStatus } from "../../rpc/contracts";
import { runtimePhaseLabel } from "./runtimePhaseLabel";

const PHASES: SidecarStatus["phase"][] = [
  "starting",
  "ready",
  "stopping",
  "stopped",
  "crashed",
];

afterEach(() => __setLocaleForTests("en"));

it("names every service phase in the interface language", () => {
  const i18n = initI18n();
  __setLocaleForTests("zh-CN");
  const labels = PHASES.map((phase) => runtimePhaseLabel(phase, i18n.t));

  // The sidebar used to show the raw phase ("stopped") outside English.
  expect(labels.filter((label) => /[A-Za-z]/.test(label))).toEqual([]);
  expect(new Set(labels).size).toBe(PHASES.length);
  expect(runtimePhaseLabel("stopped", i18n.t)).toBe("本地智能体已停止");
});
