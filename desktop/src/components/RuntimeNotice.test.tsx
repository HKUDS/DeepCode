import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import { __setLocaleForTests, initI18n } from "../app/i18n";
import type { SidecarStatus } from "../rpc/contracts";
import { RuntimeNotice } from "./RuntimeNotice";

const stopped: SidecarStatus = {
  phase: "stopped",
  message: "The browser needs a new access link",
  errorCode: "AUTH_REQUIRED",
  launchSource: "browser",
  serverInfo: null,
};

beforeEach(() => {
  initI18n();
  __setLocaleForTests("en");
});
afterEach(() => {
  cleanup();
  __setLocaleForTests("en");
});

it.each(["en", "zh-CN"] as const)(
  "explains browser authorization in %s without offering a futile reconnect",
  (locale) => {
    __setLocaleForTests(locale);
    render(
      <RuntimeNotice
        runtime={stopped}
        error={null}
        busy={false}
        reconnectOnly
        onRestart={vi.fn()}
        onDismissError={vi.fn()}
      />,
    );
    expect(screen.getByRole("alert").textContent).toContain("deepcode web");
    expect(screen.getByRole("alert").textContent).toContain(
      locale === "en" ? "Browser access required" : "需要授权浏览器访问",
    );
    expect(screen.getByRole("alert").textContent).not.toContain("APP_SERVER_OFFLINE");
    expect(screen.queryByRole("button")).toBeNull();
  },
);

it("keeps reconnection available for network failures", () => {
  const reconnect = vi.fn();
  render(
    <RuntimeNotice
      runtime={{ ...stopped, errorCode: "CONNECTION_LOST", message: "Network unavailable" }}
      error={null}
      busy={false}
      reconnectOnly
      onRestart={reconnect}
      onDismissError={vi.fn()}
    />,
  );
  fireEvent.click(screen.getByRole("button", { name: "Reconnect" }));
  expect(reconnect).toHaveBeenCalledOnce();
  expect(screen.getByRole("alert").textContent).toContain(
    "The service is unavailable. Reconnect when it is ready.",
  );
});

it("says a request timed out, not that the connection was lost", () => {
  render(
    <RuntimeNotice
      runtime={{ ...stopped, phase: "ready", errorCode: undefined }}
      error={{ code: "RESPONSE_TIMEOUT", message: "timed out", retryable: true }}
      busy={false}
      onRestart={vi.fn()}
      onDismissError={vi.fn()}
    />,
  );
  const alert = screen.getByRole("alert").textContent ?? "";
  expect(alert).toContain("No response from DeepCode");
  expect(alert).not.toContain("lost");
});

it("keeps native restart and ordinary application error dismissal", () => {
  const restart = vi.fn(), dismiss = vi.fn();
  render(
    <RuntimeNotice
      runtime={{ ...stopped, launchSource: "native", errorCode: undefined }}
      error={{ code: "PROJECT_NOT_TRUSTED", message: "Trust this project", retryable: false }}
      busy={false}
      onRestart={restart}
      onDismissError={dismiss}
    />,
  );
  expect(screen.getByRole("alert").textContent).toContain("Trust this project");
  fireEvent.click(screen.getByRole("button", { name: "Restart service" }));
  fireEvent.click(screen.getByRole("button", { name: "Dismiss error" }));
  expect(restart).toHaveBeenCalledOnce();
  expect(dismiss).toHaveBeenCalledOnce();
});

it("says an uncertain request plainly, without the method it was", () => {
  render(
    <RuntimeNotice
      runtime={{ ...stopped, phase: "ready", errorCode: undefined }}
      error={{
        code: "RESULT_UNKNOWN",
        message: "turn/start may have been applied. Reconnect and inspect its state before trying again.",
        retryable: false,
      }}
      busy={false}
      onRestart={vi.fn()}
      onDismissError={vi.fn()}
    />,
  );
  const alert = screen.getByRole("alert").textContent ?? "";
  expect(alert).toContain("The last action may have gone through");
  expect(alert).not.toContain("turn/start");
});
