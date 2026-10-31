import i18n from "i18next";
import { afterEach, beforeEach, expect, it } from "vitest";

import { __setLocaleForTests, initI18n } from "../app/i18n";
import { attachmentErrorText, runtimeNoticeText } from "./runtimeNoticeText";

beforeEach(() => {
  initI18n();
});
afterEach(() => {
  __setLocaleForTests("en");
});

it("words every connection report of the browser runtime in Chinese", () => {
  __setLocaleForTests("zh-CN");
  const t = i18n.t.bind(i18n);
  // Every code src/rpc/browserRuntime.ts raises itself.
  const codes = [
    "AUTH_REQUIRED",
    "CONNECTION_LOST",
    "RESPONSE_TIMEOUT",
    "RESULT_UNKNOWN",
    "NOT_CONNECTED",
    "DISCONNECTED",
    "CLIENT_BUSY",
    "REQUEST_TOO_LARGE",
    "WEB_BUILD_MISMATCH",
    "NO_THREAD",
    "UPLOAD_TOO_LARGE",
  ];
  const texts = codes.flatMap((code) =>
    runtimeNoticeText(code, "service text", { serviceOffline: false, browser: true }, t),
  );
  // Names and units stay as they are: DeepCode, the command, MiB.
  const allowed = new Set(["DeepCode", "deepcode", "web", "MiB"]);
  const english = (text: string) =>
    (text.match(/[A-Za-z]{2,}/g) ?? []).filter((word) => !allowed.has(word));
  expect(texts.filter((text) => english(text).length)).toEqual([]);
});

it("keeps the Desktop service's own message for codes it reuses", () => {
  // The Desktop relay says CONNECTION_LOST for a slow read and RESULT_UNKNOWN
  // for a slow write (app_server/native_client.py); its message is accurate.
  const t = i18n.t.bind(i18n);
  const slow = "The DeepCode service did not answer within 30s; it may be busy. Try again in a moment.";
  for (const code of ["CONNECTION_LOST", "RESULT_UNKNOWN"]) {
    expect(
      runtimeNoticeText(code, slow, { serviceOffline: false, browser: false }, t)[1],
    ).toBe(slow);
  }
});

it("words the browser's upload limits, and keeps any other error's message", () => {
  __setLocaleForTests("zh-CN");
  const t = i18n.t.bind(i18n);
  const tooLarge = Object.assign(new Error("Choose up to 8 files, each at most 10 MiB"), {
    code: "UPLOAD_TOO_LARGE",
  });
  expect(attachmentErrorText(tooLarge, t)).toBe("最多选择 8 个文件，每个不超过 10 MiB。");
  expect(attachmentErrorText(new Error("disk full"), t)).toBe("disk full");
});
