import i18n from "i18next";
import { afterEach, beforeEach, expect, it } from "vitest";

import { __setLocaleForTests, initI18n } from "../../app/i18n";
import { attachmentErrorText } from "./attachmentErrorText";

beforeEach(() => {
  initI18n();
  __setLocaleForTests("zh-CN");
});
afterEach(() => {
  __setLocaleForTests("en");
});

it("words the browser's upload limits, and keeps any other error's message", () => {
  const t = i18n.t.bind(i18n);
  const tooLarge = Object.assign(new Error("Choose up to 8 files, each at most 10 MiB"), {
    code: "UPLOAD_TOO_LARGE",
  });
  const noThread = Object.assign(new Error("Select a trusted workspace"), { code: "NO_THREAD" });
  expect(attachmentErrorText(tooLarge, t)).toBe("最多选择 8 个文件，每个不超过 10 MiB。");
  expect(attachmentErrorText(noThread, t)).toBe("上传文件前，请先选择一个已信任的工作区。");
  expect(attachmentErrorText(new Error("disk full"), t)).toBe("disk full");
});
