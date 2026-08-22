import { invoke } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";
import { open } from "@tauri-apps/plugin-dialog";

import type {
  DiagnosticsSnapshot,
  MethodParams,
  MethodResults,
} from "../generated/app-server";
import type {
  AnyRpcNotification,
  BridgeError,
  DesktopRuntime,
  DesktopUpdateInfo,
  DesktopUpdateProgress,
  RpcMethod,
  SidecarStatus,
} from "./contracts";
import type { Update } from "@tauri-apps/plugin-updater";

export class DesktopRuntimeError extends Error implements BridgeError {
  readonly code: string;
  readonly retryable: boolean;
  readonly data?: unknown;

  constructor(error: BridgeError) {
    super(error.message);
    this.name = "DesktopRuntimeError";
    this.code = error.code;
    this.retryable = error.retryable;
    this.data = error.data;
  }
}

export function normalizeBridgeError(error: unknown): DesktopRuntimeError {
  if (error instanceof DesktopRuntimeError) return error;
  if (typeof error === "object" && error !== null) {
    const candidate = error as Partial<BridgeError>;
    if (typeof candidate.message === "string") {
      return new DesktopRuntimeError({
        code: typeof candidate.code === "string" ? candidate.code : "DESKTOP_ERROR",
        message: candidate.message,
        retryable: candidate.retryable === true,
        data: candidate.data,
      });
    }
  }
  return new DesktopRuntimeError({
    code: "DESKTOP_ERROR",
    message: error instanceof Error ? error.message : String(error),
    retryable: true,
  });
}

export function normalizeUpdaterError(error: unknown): DesktopRuntimeError {
  const normalized = normalizeBridgeError(error);
  if (
    normalized.message
      .toLowerCase()
      .includes("updater does not have any endpoints")
  ) {
    return new DesktopRuntimeError({
      code: "UPDATER_NOT_CONFIGURED",
      message: "This build does not configure a signed update channel.",
      retryable: false,
    });
  }
  return normalized;
}

class TauriDesktopRuntime implements DesktopRuntime {
  private pendingUpdate: Update | null = null;

  async request<M extends RpcMethod>(
    method: M,
    params: MethodParams[M],
  ): Promise<MethodResults[M]> {
    try {
      return await invoke<MethodResults[M]>("rpc_request", { method, params });
    } catch (error) {
      throw normalizeBridgeError(error);
    }
  }

  async status(): Promise<SidecarStatus> {
    return invoke<SidecarStatus>("sidecar_status");
  }

  async restart(): Promise<SidecarStatus> {
    try {
      return await invoke<SidecarStatus>("sidecar_restart");
    } catch (error) {
      throw normalizeBridgeError(error);
    }
  }

  async pickDirectory(): Promise<string | null> {
    const selection = await open({ directory: true, multiple: false });
    return typeof selection === "string" ? selection : null;
  }

  async pickFile(): Promise<string | null> {
    const selection = await open({
      directory: false,
      multiple: false,
      filters: [
        {
          name: "Research documents",
          extensions: ["pdf", "md", "markdown", "txt", "docx", "html", "htm"],
        },
      ],
    });
    return typeof selection === "string" ? selection : null;
  }

  async pickContextFiles(): Promise<string[]> {
    const selection = await open({
      directory: false,
      multiple: true,
    });
    if (typeof selection === "string") return [selection];
    return Array.isArray(selection) ? selection : [];
  }

  async exportDiagnostics(
    diagnostics: DiagnosticsSnapshot,
  ): Promise<string | null> {
    try {
      return await invoke<string | null>("export_diagnostics", { diagnostics });
    } catch (error) {
      throw normalizeBridgeError(error);
    }
  }

  async openPath(path: string): Promise<void> {
    try {
      await invoke<void>("open_path", { path });
    } catch (error) {
      throw normalizeBridgeError(error);
    }
  }

  async checkForUpdate(): Promise<DesktopUpdateInfo | null> {
    try {
      if (this.pendingUpdate) {
        await this.pendingUpdate.close();
        this.pendingUpdate = null;
      }
      const { check } = await import("@tauri-apps/plugin-updater");
      const update = await check({ timeout: 15_000 });
      if (!update) return null;
      this.pendingUpdate = update;
      return {
        currentVersion: update.currentVersion,
        version: update.version,
        date: update.date ?? null,
        body: update.body ?? null,
      };
    } catch (error) {
      throw normalizeUpdaterError(error);
    }
  }

  async installUpdate(
    listener: (progress: DesktopUpdateProgress) => void,
  ): Promise<void> {
    const update = this.pendingUpdate;
    if (!update) {
      throw normalizeBridgeError(new Error("No checked update is pending."));
    }
    let downloadedBytes = 0;
    let totalBytes: number | null = null;
    try {
      await update.downloadAndInstall((event) => {
        if (event.event === "Started") {
          downloadedBytes = 0;
          totalBytes = event.data.contentLength ?? null;
          listener({ phase: "started", downloadedBytes, totalBytes });
        } else if (event.event === "Progress") {
          downloadedBytes += event.data.chunkLength;
          listener({ phase: "progress", downloadedBytes, totalBytes });
        } else {
          listener({ phase: "finished", downloadedBytes, totalBytes });
        }
      });
      this.pendingUpdate = null;
      const { relaunch } = await import("@tauri-apps/plugin-process");
      await relaunch();
    } catch (error) {
      throw normalizeUpdaterError(error);
    }
  }

  async onNotification(
    listener: (notification: AnyRpcNotification) => void,
  ): Promise<() => void> {
    return listen<AnyRpcNotification>("deepcode://rpc-notification", (event) => {
      listener(event.payload);
    });
  }

  async onStatus(listener: (status: SidecarStatus) => void): Promise<() => void> {
    return listen<SidecarStatus>("deepcode://sidecar-state", (event) => {
      listener(event.payload);
    });
  }

  async onLog(listener: (message: string) => void): Promise<() => void> {
    return listen<string>("deepcode://sidecar-log", (event) => {
      listener(event.payload);
    });
  }
}

export const tauriRuntime: DesktopRuntime = new TauriDesktopRuntime();
