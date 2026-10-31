import i18n from "i18next";
import { afterEach, beforeEach, expect, it } from "vitest";

import { __setLocaleForTests, initI18n } from "../../../app/i18n";
import type { DiagnosticsCheck } from "../../../generated/app-server";
import { diagnosticsCheckText } from "./diagnosticsCheckText";

const check = (id: string, status: DiagnosticsCheck["status"], detail: string): DiagnosticsCheck => ({
  id,
  label: `${id} label`,
  status,
  detail,
});

beforeEach(() => {
  initI18n();
  __setLocaleForTests("zh-CN");
});
afterEach(() => {
  __setLocaleForTests("en");
});

it("words the service's checks, keeping what is specific to the machine", () => {
  const t = i18n.t.bind(i18n);
  expect(diagnosticsCheckText(check("database", "ok", "SQLite integrity check passed"), t)).toEqual([
    "数据库",
    "数据库完整性检查通过。",
  ]);
  expect(diagnosticsCheckText(check("sessions", "warning", "not writable"), t)[1]).toBe(
    "会话历史目录不可写。",
  );
  expect(diagnosticsCheckText(check("config", "error", "ValueError: bad key"), t)[1]).toBe(
    "ValueError: bad key",
  );
  expect(diagnosticsCheckText(check("git", "ok", "/usr/bin/git"), t)).toEqual(["Git", "/usr/bin/git"]);
  // A check this client does not know is shown as the service reports it.
  expect(diagnosticsCheckText(check("gpu", "ok", "Metal"), t)).toEqual(["gpu label", "Metal"]);
});
