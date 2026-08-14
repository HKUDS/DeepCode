/** Typed protocol relationships shared by the desktop runtime and test doubles. */

import type {
  DiagnosticsSnapshot,
  MethodParams,
  MethodResults,
  Notifications,
} from "../generated/app-server";

export type RpcMethod = keyof MethodParams & keyof MethodResults;
export type NotificationMethod = keyof Notifications;

export interface RpcRequest<M extends RpcMethod = RpcMethod> {
  jsonrpc: "2.0";
  id: number;
  method: M;
  params: MethodParams[M];
}

export interface RpcSuccess<M extends RpcMethod = RpcMethod> {
  jsonrpc: "2.0";
  id: number;
  result: MethodResults[M];
}

export interface RpcErrorData {
  code: string;
  retryable: boolean;
  correlationId: string;
  details?: Record<string, unknown>;
}

export interface RpcFailure {
  jsonrpc: "2.0";
  id: number | string | null;
  error: {
    code: number;
    message: string;
    data: RpcErrorData;
  };
}

export interface RpcNotification<M extends NotificationMethod = NotificationMethod> {
  jsonrpc: "2.0";
  method: M;
  params: Notifications[M];
}

export type AnyRpcRequest = {
  [M in RpcMethod]: RpcRequest<M>;
}[RpcMethod];

export type AnyRpcNotification = {
  [M in NotificationMethod]: RpcNotification<M>;
}[NotificationMethod];

export interface RpcTransport {
  request<M extends RpcMethod>(
    method: M,
    params: MethodParams[M],
  ): Promise<MethodResults[M]>;
}

export interface SidecarStatus {
  phase: "starting" | "ready" | "stopping" | "stopped" | "crashed";
  message: string | null;
  launchSource: string | null;
  serverInfo: MethodResults["initialize"] | null;
}

export interface BridgeError {
  code: string;
  message: string;
  retryable: boolean;
  data?: unknown;
}

export interface DesktopUpdateInfo {
  currentVersion: string;
  version: string;
  date: string | null;
  body: string | null;
}

export interface DesktopUpdateProgress {
  phase: "started" | "progress" | "finished";
  downloadedBytes: number;
  totalBytes: number | null;
}

export interface DesktopRuntime extends RpcTransport {
  status(): Promise<SidecarStatus>;
  restart(): Promise<SidecarStatus>;
  pickDirectory(): Promise<string | null>;
  pickFile(): Promise<string | null>;
  pickContextFiles(): Promise<string[]>;
  exportDiagnostics(
    diagnostics: DiagnosticsSnapshot,
  ): Promise<string | null>;
  checkForUpdate(): Promise<DesktopUpdateInfo | null>;
  installUpdate(
    listener: (progress: DesktopUpdateProgress) => void,
  ): Promise<void>;
  onNotification(listener: (notification: AnyRpcNotification) => void): Promise<() => void>;
  onStatus(listener: (status: SidecarStatus) => void): Promise<() => void>;
  onLog(listener: (message: string) => void): Promise<() => void>;
}
