import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { __setLocaleForTests, initI18n } from "./app/i18n";

const browser = vi.hoisted(() => ({
  chooseDirectory: null as null | (() => Promise<string | null>),
}));

vi.mock("./App", () => ({ App: () => null }));
vi.mock("./rpc/browserRuntime", () => ({
  BrowserRuntime: class {
    constructor(options: { chooseDirectory(): Promise<string | null> }) {
      browser.chooseDirectory = options.chooseDirectory;
    }
    dispose() {}
    async logout() {}
    async request() {
      return {
        path: "/home/me",
        parent: "/home",
        home: "/home/me",
        folders: [{ name: "code", path: "/home/me/code" }],
        truncated: false,
      };
    }
  },
}));
vi.stubGlobal("__WEB_BUILD_ID__", "test");

import { BrowserShell } from "./BrowserShell";

initI18n();

describe("BrowserShell", () => {
  afterEach(() => {
    cleanup();
    __setLocaleForTests("en");
  });

  it("shows the web shell and folder picker in the interface language", async () => {
    __setLocaleForTests("zh-CN");
    render(<BrowserShell />);
    expect(screen.getByRole("button", { name: "退出登录" })).toBeTruthy();

    act(() => void browser.chooseDirectory?.());
    expect(
      await screen.findByRole("dialog", { name: "在服务所在机器上打开项目" }),
    ).toBeTruthy();
    expect(screen.getByLabelText("项目文件夹")).toBeTruthy();
    expect(screen.getByRole("button", { name: "打开项目" })).toBeTruthy();
    expect(await screen.findByRole("button", { name: "code" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "上一级" })).toBeTruthy();
  });
});
