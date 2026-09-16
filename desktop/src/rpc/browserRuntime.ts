import type {
  DiagnosticsSnapshot,
  MethodParams,
  MethodResults,
} from "../generated/app-server";
import type {
  AnyRpcNotification,
  BridgeError,
  ClientRuntime,
  RpcMethod,
  SidecarStatus,
} from "./contracts";

export class BrowserRuntimeError extends Error implements BridgeError {
  constructor(
    readonly code: string,
    message: string,
    readonly retryable = false,
    readonly data?: unknown,
  ) {
    super(message);
  }
}

interface Pending {
  socket: WebSocket;
  resolve(value: unknown): void;
  reject(error: unknown): void;
  timer: ReturnType<typeof setTimeout>;
}
interface BrowserOptions {
  chooseDirectory(): Promise<string | null>;
  buildId?: string;
  requestTimeout?: number;
  reconnectDelays?: number[];
}

/** One browser connection. Retrying transport never retries arbitrary mutations. */
export class BrowserRuntime implements ClientRuntime {
  readonly host = {
    kind: "browser" as const,
    nativeOpen: false,
    updates: false,
  };
  private socket: WebSocket | null = null;
  private connecting: Promise<void> | null = null;
  private serial = 0;
  private pending = new Map<number, Pending>();
  private notifications = new Set<(value: AnyRpcNotification) => void>();
  private statuses = new Set<(value: SidecarStatus) => void>();
  private state: SidecarStatus = {
    phase: "stopped",
    message: null,
    launchSource: "browser",
    serverInfo: null,
  };
  private retryTimer: ReturnType<typeof setTimeout> | null = null;
  private reconnectAttempt = 0;
  private projects = new Set<string>();
  private stopped = false;
  private ticket: string | null;

  constructor(private readonly options: BrowserOptions) {
    this.ticket = new URLSearchParams(location.hash.slice(1)).get("ticket");
    if (this.ticket)
      history.replaceState(null, "", location.pathname + location.search);
    window.addEventListener("online", this.wake);
    document.addEventListener("visibilitychange", this.wake);
  }

  private wake = () => {
    if (
      !this.stopped &&
      document.visibilityState !== "hidden" &&
      this.state.phase !== "ready"
    ) {
      this.reconnectAttempt = 0;
      void this.connect().catch(() => this.scheduleReconnect());
    }
  };

  private emitStatus(
    phase: SidecarStatus["phase"],
    message: string | null = null,
    errorCode: string | null = null,
  ): void {
    this.state = { ...this.state, phase, message, errorCode };
    for (const receive of this.statuses) receive(this.state);
  }

  private async http(path: string, init: RequestInit = {}): Promise<Response> {
    const response = await fetch(path, {
      ...init,
      credentials: "same-origin",
      cache: "no-store",
      signal: AbortSignal.timeout(15000),
    });
    if (!response.ok) {
      if (response.status === 401)
        throw new BrowserRuntimeError(
          "AUTH_REQUIRED",
          "Run deepcode web in your terminal to open a new browser access link. No DeepCode account is needed.",
        );
      throw new BrowserRuntimeError(
        "HTTP_ERROR",
        await response.text(),
        response.status >= 500,
      );
    }
    return response;
  }

  private connect(): Promise<void> {
    if (this.stopped)
      return Promise.reject(
        new BrowserRuntimeError(
          "DISCONNECTED",
          "This browser connection is closed",
        ),
      );
    if (
      this.state.phase === "ready" &&
      this.socket?.readyState === WebSocket.OPEN
    )
      return Promise.resolve();
    if (this.connecting) return this.connecting;
    if (this.retryTimer) {
      clearTimeout(this.retryTimer);
      this.retryTimer = null;
    }
    this.connecting = this.open()
      .catch((error) => {
        this.emitStatus(
          "stopped",
          error instanceof Error ? error.message : String(error),
          error instanceof BrowserRuntimeError ? error.code : "CONNECTION_LOST",
        );
        if (!(error instanceof BrowserRuntimeError) || error.retryable)
          this.scheduleReconnect();
        throw error;
      })
      .finally(() => {
        this.connecting = null;
      });
    return this.connecting;
  }

  private async open(): Promise<void> {
    this.emitStatus("starting", "Connecting to the local service…");
    if (this.ticket) {
      const ticket = this.ticket;
      this.ticket = null;
      await this.http("/auth/exchange", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ticket }),
      });
    }
    await this.http("/api/session");
    if (this.stopped) return;
    const socket = new WebSocket(
      `${location.protocol === "https:" ? "wss:" : "ws:"}//${location.host}/api/rpc`,
    );
    this.socket = socket;
    await new Promise<void>((resolve, reject) => {
      let initialized = false;
      const timeout = setTimeout(() => {
        reject(
          new BrowserRuntimeError(
            "CONNECTION_LOST",
            "Connection timed out",
            true,
          ),
        );
        socket.close();
      }, 15000);
      socket.onmessage = (message) => {
        if (this.socket !== socket || this.stopped) return;
        try {
          if (
            typeof message.data !== "string" ||
            new TextEncoder().encode(message.data).length > 1024 * 1024
          )
            throw new Error("Invalid service frame");
          const value = JSON.parse(message.data);
          if (typeof value.id === "number") {
            const pending = this.pending.get(value.id);
            if (!pending) return;
            this.pending.delete(value.id);
            clearTimeout(pending.timer);
            if (value.error)
              pending.reject(
                new BrowserRuntimeError(
                  value.error.data?.code ?? "RPC_ERROR",
                  value.error.message,
                  value.error.data?.retryable === true,
                  value.error.data?.details,
                ),
              );
            else pending.resolve(value.result);
          } else if (value.method && value.params) {
            for (const receive of this.notifications) receive(value);
          }
        } catch {
          socket.close(1002, "Invalid service response");
        }
      };
      socket.onopen = () => {
        void this.send("initialize", {
          protocolVersion: "1.0",
          clientInfo: { name: "deepcode-web", version: "1", surface: "web" },
        })
          .then((info) => {
            if (this.socket !== socket || this.stopped) return;
            if (
              info.protocolVersion !== "1.0" ||
              (this.options.buildId &&
                info.serviceInfo?.frontendBuildId !== this.options.buildId)
            ) {
              throw new BrowserRuntimeError(
                "WEB_BUILD_MISMATCH",
                "This page and service have different builds. Reload the page or reinstall the complete release.",
              );
            }
            initialized = true;
            clearTimeout(timeout);
            this.state = { ...this.state, serverInfo: info };
            this.reconnectAttempt = 0;
            this.emitStatus("ready");
            // Existing mounted catalogs already consume these invalidations.
            for (const notification of [
              {
                method: "settings.changed",
                params: { configRevision: "reconnected" },
              },
              { method: "plugins.changed", params: {} },
              { method: "mcp.changed", params: {} },
            ] as const)
              for (const receive of this.notifications)
                receive({ jsonrpc: "2.0", ...notification });
            for (const projectId of this.projects)
              for (const receive of this.notifications)
                receive({
                  jsonrpc: "2.0",
                  method: "skills.changed",
                  params: { projectId },
                });
            resolve();
          })
          .catch((error) => {
            clearTimeout(timeout);
            reject(error);
            socket.close();
          });
      };
      socket.onerror = () => {
        /* close settles pending requests and the handshake */
      };
      socket.onclose = () => {
        clearTimeout(timeout);
        const error = new BrowserRuntimeError(
          "CONNECTION_LOST",
          "Connection lost; the service may still be running",
          true,
        );
        for (const [id, pending] of this.pending) {
          if (pending.socket === socket) {
            clearTimeout(pending.timer);
            this.pending.delete(id);
            pending.reject(error);
          }
        }
        if (this.socket === socket) {
          this.socket = null;
          if (!this.stopped && initialized) {
            this.emitStatus("starting", "Reconnecting…");
            this.scheduleReconnect();
          }
        }
        if (!initialized) reject(error);
      };
    });
  }

  private scheduleReconnect(): void {
    if (this.stopped || this.retryTimer) return;
    const delays = this.options.reconnectDelays ?? [
      250, 500, 1000, 2000, 4000, 8000,
    ];
    if (this.reconnectAttempt >= delays.length) {
      this.emitStatus(
        "stopped",
        "The service is unavailable. Reconnect when it is ready.",
        "CONNECTION_LOST",
      );
      return;
    }
    this.retryTimer = setTimeout(() => {
      this.retryTimer = null;
      void this.connect().catch(() => {});
    }, delays[this.reconnectAttempt++]);
  }

  private send<M extends RpcMethod>(
    method: M,
    params: MethodParams[M],
  ): Promise<MethodResults[M]> {
    const socket = this.socket;
    if (!socket || socket.readyState !== WebSocket.OPEN)
      return Promise.reject(
        new BrowserRuntimeError(
          "NOT_CONNECTED",
          "Not connected; the request was not sent",
          true,
        ),
      );
    if (this.pending.size >= 64)
      return Promise.reject(
        new BrowserRuntimeError(
          "CLIENT_BUSY",
          "Too many pending requests",
          true,
        ),
      );
    const id = ++this.serial;
    const frame = JSON.stringify({ jsonrpc: "2.0", id, method, params });
    if (
      new TextEncoder().encode(frame).length >
      (this.state.serverInfo?.capabilities.maxMessageBytes ?? 1024 * 1024)
    )
      return Promise.reject(
        new BrowserRuntimeError(
          "REQUEST_TOO_LARGE",
          "Request exceeds the service message limit",
        ),
      );
    return new Promise((resolve, reject) => {
      const timer = setTimeout(
        () => {
          this.pending.delete(id);
          reject(
            new BrowserRuntimeError(
              "CONNECTION_LOST",
              "The response timed out; the task may still be running",
              true,
            ),
          );
          socket.close();
        },
        this.options.requestTimeout ??
          (method === "provider/test" ? 120000 : 30000),
      );
      this.pending.set(id, {
        socket,
        resolve: (value) => resolve(value as MethodResults[M]),
        reject,
        timer,
      });
      try {
        socket.send(frame);
      } catch (error) {
        clearTimeout(timer);
        this.pending.delete(id);
        reject(error);
      }
    });
  }

  async request<M extends RpcMethod>(
    method: M,
    params: MethodParams[M],
  ): Promise<MethodResults[M]> {
    // Freeze the caller's intent once, before any response can be lost.
    const original = JSON.parse(JSON.stringify(params)) as MethodParams[M];
    const policy = this.state.serverInfo?.capabilities.requestRetry;
    const key = policy?.keyedMethods[method];
    const values = original as Record<string, unknown>;
    if (typeof values.projectId === "string" && this.projects.size < 500)
      this.projects.add(values.projectId);
    const safe =
      policy?.readMethods.includes(method) ||
      (key &&
        typeof values[key] === "string" &&
        (values[key] as string).trim().length > 0);
    for (let attempt = 0; ; attempt++) {
      try {
        if (this.state.phase !== "ready") {
          if (!safe)
            throw new BrowserRuntimeError(
              "NOT_CONNECTED",
              "Not connected; the request was not sent",
              true,
            );
          await this.connect();
        }
        if (attempt > 0) {
          const currentPolicy =
            this.state.serverInfo?.capabilities.requestRetry;
          const stillSafe =
            currentPolicy?.readMethods.includes(method) ||
            (key && currentPolicy?.keyedMethods[method] === key);
          if (!stillSafe)
            throw new BrowserRuntimeError(
              "RESULT_UNKNOWN",
              "The reconnected service no longer advertises safe retry for this operation. Inspect its state before trying again.",
            );
        }
        return await this.send(method, original);
      } catch (error) {
        const lost =
          error instanceof BrowserRuntimeError &&
          error.code === "CONNECTION_LOST";
        const pending =
          error instanceof BrowserRuntimeError &&
          error.code === "INPUT_DELIVERY_PENDING";
        if (lost && !safe)
          throw new BrowserRuntimeError(
            "RESULT_UNKNOWN",
            `${method} may have been applied. Reconnect and inspect its state before trying again.`,
            false,
            { method },
          );
        if (!safe || (!lost && !pending) || attempt >= 2) throw error;
        await new Promise((resolve) =>
          setTimeout(resolve, 250 * (attempt + 1)),
        );
      }
    }
  }

  async status(): Promise<SidecarStatus> {
    try {
      await this.connect();
    } catch {
      /* state carries the actionable error */
    }
    return this.state;
  }
  async restart(): Promise<SidecarStatus> {
    this.reconnectAttempt = 0;
    const socket = this.socket;
    if (socket) {
      this.emitStatus("starting", "Reconnecting…");
      socket.close();
      this.socket = null;
    }
    return this.status();
  }
  async onStatus(
    receive: (status: SidecarStatus) => void,
  ): Promise<() => void> {
    this.statuses.add(receive);
    return () => this.statuses.delete(receive);
  }
  async onNotification(
    receive: (note: AnyRpcNotification) => void,
  ): Promise<() => void> {
    this.notifications.add(receive);
    return () => this.notifications.delete(receive);
  }
  async onLog(_receive: (message: string) => void): Promise<() => void> {
    void _receive;
    return () => {};
  }
  pickDirectory(): Promise<string | null> {
    return this.options.chooseDirectory();
  }

  private chooseFiles(multiple: boolean): Promise<File[]> {
    return new Promise((resolve) => {
      const input = document.createElement("input");
      input.type = "file";
      input.multiple = multiple;
      input.onchange = () => {
        resolve(Array.from(input.files ?? []));
        input.remove();
      };
      input.addEventListener(
        "cancel",
        () => {
          resolve([]);
          input.remove();
        },
        { once: true },
      );
      input.hidden = true;
      document.body.append(input);
      input.click();
    });
  }
  private async upload(
    threadId: string | undefined,
    multiple: boolean,
  ): Promise<string[]> {
    if (!threadId)
      throw new BrowserRuntimeError(
        "NO_THREAD",
        "Select a trusted workspace before uploading files",
      );
    const files = await this.chooseFiles(multiple);
    if (files.length > 8 || files.some((file) => file.size > 10 * 1024 * 1024))
      throw new BrowserRuntimeError(
        "UPLOAD_TOO_LARGE",
        "Choose up to 8 files, each at most 10 MiB",
      );
    const paths: string[] = [];
    for (const file of files) {
      const query = new URLSearchParams({ threadId, name: file.name });
      const response = await this.http(`/api/uploads?${query}`, {
        method: "POST",
        headers: { "Content-Type": "application/octet-stream" },
        body: file,
      });
      paths.push((await response.json()).path);
    }
    return paths;
  }
  async pickContextFiles(threadId?: string): Promise<string[]> {
    return this.upload(threadId, true);
  }
  async pickFile(threadId?: string): Promise<string | null> {
    return (await this.upload(threadId, false))[0] ?? null;
  }
  async downloadFile(threadId: string, path: string): Promise<void> {
    const response = await this.http(
      `/api/download?${new URLSearchParams({ threadId, path })}`,
    );
    this.download(await response.blob(), path.split("/").at(-1) ?? "download");
  }
  private download(blob: Blob, name: string): void {
    const url = URL.createObjectURL(blob),
      link = document.createElement("a");
    link.href = url;
    link.download = name;
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  async exportDiagnostics(snapshot: DiagnosticsSnapshot): Promise<string> {
    this.download(
      new Blob([JSON.stringify(snapshot, null, 2)], {
        type: "application/json",
      }),
      "deepcode-diagnostics.json",
    );
    return "deepcode-diagnostics.json";
  }
  async openPath(path: string): Promise<void> {
    await navigator.clipboard.writeText(path);
  }
  async checkForUpdate(): Promise<null> {
    throw new BrowserRuntimeError(
      "HOST_ACTION",
      "Update the service installation on its host machine",
    );
  }
  async installUpdate(): Promise<void> {
    throw new BrowserRuntimeError(
      "HOST_ACTION",
      "Update the service installation on its host machine",
    );
  }
  async logout(): Promise<void> {
    await this.http("/auth/logout", { method: "POST" });
    this.dispose();
    this.emitStatus(
      "stopped",
      "Run deepcode web in your terminal to open a new browser access link. No DeepCode account is needed.",
      "AUTH_REQUIRED",
    );
  }
  dispose(): void {
    this.stopped = true;
    if (this.retryTimer) clearTimeout(this.retryTimer);
    this.socket?.close();
    window.removeEventListener("online", this.wake);
    document.removeEventListener("visibilitychange", this.wake);
  }
}
