import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { ClientRuntime } from "../../../rpc/contracts";
import { ServiceCard } from "./ServiceCard";

vi.mock("../../../platform/confirmAction", () => ({
  confirmAction: vi.fn(async () => true),
}));
vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: (_key: string, fallback: string) => fallback }),
}));
afterEach(cleanup);

function runtime(shared: boolean) {
  return {
    status: vi.fn(async () => ({
      serverInfo: shared
        ? { serviceInfo: { shutdownScope: "connection" } }
        : {},
    })),
    serviceStatus: vi.fn(async () => ({
      phase: "ready",
      activeTurns: 1,
      queuedTurns: 2,
      terminals: 0,
    })),
    stopService: vi.fn(async () => {}),
  };
}

describe("native service controls", () => {
  it("does not expose global stop for an embedded host", async () => {
    const host = runtime(false);
    render(<ServiceCard runtime={host as unknown as ClientRuntime} />);
    await waitFor(() => expect(host.status).toHaveBeenCalledOnce());
    expect(screen.queryByRole("button")).toBeNull();
    expect(host.serviceStatus).not.toHaveBeenCalled();
  });

  it("reads current activity and stops only after an explicit action", async () => {
    const host = runtime(true);
    const view = render(
      <ServiceCard runtime={host as unknown as ClientRuntime} />,
    );
    const button = await screen.findByRole("button", {
      name: "Stop background service",
    });
    expect(host.stopService).not.toHaveBeenCalled();
    fireEvent.click(button);
    await waitFor(() => expect(host.stopService).toHaveBeenCalledOnce());
    expect(host.serviceStatus).toHaveBeenCalledTimes(2);
    view.unmount();
    expect(host.stopService).toHaveBeenCalledOnce();
  });
});
