import i18n from "i18next";
import { afterEach, beforeEach, expect, it } from "vitest";

import { __setLocaleForTests, initI18n } from "../../app/i18n";
import type { SkillInfo } from "../../generated/app-server";
import { skillOriginLabel, skillStatusLabel } from "./skillPresentation";

const STATUSES = Object.keys({ active: 1, shadowed: 1, disabled: 1, invalid: 1 } satisfies Record<
  SkillInfo["status"],
  1
>) as SkillInfo["status"][];

function skill(overrides: Partial<SkillInfo>): SkillInfo {
  return { originKind: "local", originLabel: "project:agents", scope: "project", sourceRoot: "agents", ...overrides } as SkillInfo;
}

beforeEach(() => {
  initI18n();
  __setLocaleForTests("zh-CN");
});
afterEach(() => {
  __setLocaleForTests("en");
});

it("says where a Skill comes from instead of its raw source", () => {
  const t = i18n.t.bind(i18n);
  expect(skillOriginLabel(skill({}), t)).toBe("当前项目 · .agents");
  expect(skillOriginLabel(skill({ scope: "user", sourceRoot: "claude" }), t)).toBe("用户设置 · .claude");
  expect(skillOriginLabel(skill({ originKind: "bundled", originLabel: "DeepCode bundled" }), t)).toBe(
    "DeepCode 内置",
  );
  // A provider is named by its own id.
  expect(skillOriginLabel(skill({ originKind: "provider", originLabel: "executor:acme" }), t)).toBe(
    "executor:acme",
  );
});

it("says every Skill status in Chinese", () => {
  const t = i18n.t.bind(i18n);
  const labels = STATUSES.map((status) => skillStatusLabel(status, t));
  expect(labels.filter((label) => /[A-Za-z]/.test(label))).toEqual([]);
});
