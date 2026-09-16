import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type {
  AnyRpcNotification,
  ClientRuntime,
  SidecarStatus,
} from "../../rpc/contracts";
import { TerminalPanel } from "./TerminalPanel";

const state = vi.hoisted(() => ({
  instances: [] as Array<{
    text: string;
    input: (data: string) => void;
    disposed: boolean;
  }>,
}));
vi.mock("@xterm/xterm", () => ({
  Terminal: class {
    text = "";
    cols = 80;
    rows = 24;
    disposed = false;
    input = (data: string) => {
      void data;
    };
    constructor() {
      state.instances.push(this);
    }
    loadAddon() {}
    open() {}
    focus() {}
    clear() {
      this.text = "";
    }
    reset() {
      this.text = "";
    }
    write(data: string, done?: () => void) {
      this.text += data;
      done?.();
    }
    onData(callback: (data: string) => void) {
      this.input = callback;
      return { dispose() {} };
    }
    dispose() {
      this.disposed = true;
    }
  },
}));
vi.mock("@xterm/addon-fit", () => ({
  FitAddon: class {
    fit() {}
  },
}));

function harness(initial = true) {
  const info = {
    terminalId: "term_test",
    threadId: "thread-1",
    pid: 42,
    columns: 80,
    rows: 24,
    workspacePath: "/tmp",
  };
  const backend = {
    exists: initial,
    text: "startup\r\n",
    exited: false,
    phase: "ready" as SidecarStatus["phase"],
  };
  const notifications = new Set<(value: AnyRpcNotification) => void>();
  const statuses = new Set<(value: SidecarStatus) => void>();
  const status = () =>
    ({
      phase: backend.phase,
      serverInfo: {
        capabilities: { methods: ["terminal/list", "terminal/read"] },
      },
    }) as SidecarStatus;
  const request = vi.fn(
    async (method: string, params: { offset?: number; data?: string }) => {
      if (method === "terminal/list")
        return {
          terminals: backend.exists
            ? [
                {
                  terminal: info,
                  exited: backend.exited,
                  exitCode: backend.exited ? 0 : null,
                },
              ]
            : [],
        };
      if (method === "terminal/create") {
        backend.exists = true;
        return { terminal: info };
      }
      if (method === "terminal/close") {
        backend.exited = true;
        return { accepted: true };
      }
      if (method === "terminal/write")
        return { written: params.data?.length ?? 0 };
      if (method === "terminal/read") {
        const offset = params.offset ?? 0;
        return {
          terminalId: info.terminalId,
          threadId: info.threadId,
          data: backend.text.slice(offset),
          offset,
          nextOffset: backend.text.length,
          availableFrom: 0,
          headOffset: backend.text.length,
          hasMore: false,
          truncated: false,
          exited: backend.exited,
          exitCode: backend.exited ? 0 : null,
        };
      }
      throw new Error(method);
    },
  );
  const runtime = {
    request,
    status: async () => status(),
    onNotification: async (listener: (value: AnyRpcNotification) => void) => {
      notifications.add(listener);
      return () => notifications.delete(listener);
    },
    onStatus: async (listener: (value: SidecarStatus) => void) => {
      statuses.add(listener);
      return () => statuses.delete(listener);
    },
  } as unknown as ClientRuntime;
  const props = {
    runtime,
    threadId: info.threadId,
    enabled: true,
    active: true,
  };
  return { props, request, backend, statuses, notifications, status };
}

beforeEach(() => {
  state.instances.length = 0;
  vi.stubGlobal(
    "ResizeObserver",
    class {
      observe() {}
      disconnect() {}
    },
  );
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("TerminalPanel recovery", () => {
  it("reattaches an existing terminal and detaches without terminating it", async () => {
    const test = harness();
    const view = render(<TerminalPanel {...test.props} />);
    await screen.findByText("PID 42");
    await waitFor(() => expect(state.instances[0].text).toBe("startup\r\n"));
    view.unmount();
    expect(state.instances[0].disposed).toBe(true);
    render(<TerminalPanel {...test.props} />);
    await waitFor(() => expect(state.instances[1]?.text).toBe("startup\r\n"));
    expect(
      test.request.mock.calls.some(
        ([method]) =>
          method === "terminal/create" || method === "terminal/close",
      ),
    ).toBe(false);
  });

  it("recovers startup and reconnect output without resending terminal input", async () => {
    const test = harness(false);
    const view = render(<TerminalPanel {...test.props} />);
    await waitFor(() =>
      expect(
        (screen.getByText("Start terminal") as HTMLButtonElement).disabled,
      ).toBe(false),
    );
    fireEvent.click(screen.getByText("Start terminal"));
    await waitFor(() => expect(state.instances[0].text).toBe("startup\r\n"));
    act(() => state.instances[0].input("echo test\r"));
    test.backend.phase = "stopped";
    act(() => test.statuses.forEach((receive) => receive(test.status())));
    act(() => state.instances[0].input("must not send\r"));
    test.backend.text += "after reconnect\r\n";
    test.backend.phase = "ready";
    act(() => test.statuses.forEach((receive) => receive(test.status())));
    await waitFor(() =>
      expect(state.instances[0].text).toBe(test.backend.text),
    );
    expect(
      test.request.mock.calls.filter(([method]) => method === "terminal/write"),
    ).toHaveLength(1);
    expect(
      test.request.mock.calls.filter(
        ([method]) => method === "terminal/create",
      ),
    ).toHaveLength(1);
    fireEvent.click(screen.getByText("Close"));
    await screen.findByText("Exited");
    view.unmount();
    expect(
      test.request.mock.calls.filter(([method]) => method === "terminal/close"),
    ).toHaveLength(1);
  });

  it("recovers a dropped final output and exit on overflow warning", async () => {
    const test = harness();
    render(<TerminalPanel {...test.props} />);
    await waitFor(() =>
      expect(state.instances[0]?.text).toBe(test.backend.text),
    );
    test.backend.text += "final output\r\n";
    test.backend.exited = true;
    const warning: AnyRpcNotification = {
      jsonrpc: "2.0",
      method: "server.warning",
      params: {
        replayRequired: true,
        code: "NOTIFICATION_QUEUE_OVERFLOW",
        dropped: 1,
      },
    };
    act(() => test.notifications.forEach((receive) => receive(warning)));
    await screen.findByText("Exited");
    expect(state.instances[0].text).toBe(
      test.backend.text + "\r\n[process exited 0]\r\n",
    );
    act(() => test.notifications.forEach((receive) => receive(warning)));
    await act(async () => {});
    expect(state.instances[0].text.match(/process exited/g)).toHaveLength(1);
  });
});
