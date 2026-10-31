import i18n from "i18next";
import { afterEach, beforeEach, expect, it } from "vitest";

import { __setLocaleForTests, initI18n } from "../app/i18n";
import { runtimeNoticeText } from "./runtimeNoticeText";

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
    "HOST_ACTION",
  ];
  const texts = codes.flatMap((code) =>
    runtimeNoticeText(code, "service text", { serviceOffline: false, reconnectOnly: true }, t),
  );
  // Names and units stay as they are: DeepCode, the command, MiB.
  const allowed = new Set(["DeepCode", "deepcode", "web", "MiB"]);
  const english = (text: string) =>
    (text.match(/[A-Za-z]{2,}/g) ?? []).filter((word) => !allowed.has(word));
  expect(texts.filter((text) => english(text).length)).toEqual([]);
});
