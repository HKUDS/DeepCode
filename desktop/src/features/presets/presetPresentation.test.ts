import i18n from "i18next";
import { afterEach, beforeEach, expect, it } from "vitest";

import { __setLocaleForTests, initI18n } from "../../app/i18n";
import { presetTrustLabel } from "./presetPresentation";

beforeEach(() => {
  initI18n();
  __setLocaleForTests("zh-CN");
});
afterEach(() => {
  __setLocaleForTests("en");
});

it("says where every agent preset is defined, with the shared scope names", () => {
  const t = i18n.t.bind(i18n);
  const labels = (["system", "user", "project"] as const).map((trust) => presetTrustLabel(trust, t));
  expect(labels).toEqual(["内置", "用户设置", "当前项目"]);
});
