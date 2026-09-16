import type { FitAddon } from "@xterm/addon-fit";
import type { Terminal } from "@xterm/xterm";
import { useCallback, useEffect, useRef, useState } from "react";

import type { TerminalInfo } from "../../generated/app-server";
import type { ClientRuntime } from "../../rpc/contracts";
import styles from "./TerminalPanel.module.css";
import { TerminalOutputReader } from "./terminalOutputReader";

interface TerminalPanelProps {
  runtime: ClientRuntime;
  threadId: string | null;
  enabled: boolean;
  active: boolean;
}

export function TerminalPanel({
  runtime,
  threadId,
  enabled,
  active,
}: TerminalPanelProps) {
  const host = useRef<HTMLDivElement | null>(null);
  const terminal = useRef<Terminal | null>(null);
  const fit = useRef<FitAddon | null>(null);
  const info = useRef<TerminalInfo | null>(null);
  const activeRef = useRef(active);
  const observer = useRef<ResizeObserver | null>(null);
  const inputDisposable = useRef<{ dispose(): void } | null>(null);
  const initializing = useRef(false);
  const disposed = useRef(false);
  const recovery = useRef<TerminalOutputReader | null>(null);
  const recoverable = useRef(false);
  const generation = useRef(0);
  const [terminalInfo, setTerminalInfo] = useState<TerminalInfo | null>(null);
  const [ended, setEnded] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [rendererReady, setRendererReady] = useState(false);
  const [connected, updateConnected] = useState(false);
  const connectionReady = useRef(false);
  const setConnected = useCallback((ready: boolean) => {
    connectionReady.current = ready;
    updateConnected(ready);
  }, []);
  const [busy, setBusy] = useState(false);

  const reportError = useCallback((cause: unknown) => {
    setError(cause instanceof Error ? cause.message : String(cause));
  }, []);

  const finish = useCallback((code: number | null) => {
    terminal.current?.write(`\r\n[process exited ${code ?? "unknown"}]\r\n`);
    setEnded(true);
    info.current = null;
    setTerminalInfo(null);
  }, []);

  const attach = useCallback(
    async (current: TerminalInfo) => {
      recovery.current?.stop();
      recovery.current = null;
      terminal.current?.reset();
      info.current = current;
      setTerminalInfo(current);
      setEnded(false);
      if (!recoverable.current) return;
      const reader = new TerminalOutputReader(
        runtime,
        current,
        (data) =>
          new Promise<void>((resolve) => {
            if (terminal.current) terminal.current.write(data, resolve);
            else resolve();
          }),
        () => {
          terminal.current?.reset();
          terminal.current?.write(
            "[Earlier terminal output is no longer available]\r\n",
          );
        },
        finish,
        reportError,
      );
      recovery.current = reader;
      await reader.recover();
    },
    [finish, reportError, runtime],
  );

  useEffect(() => {
    if (!active || !host.current || terminal.current || initializing.current)
      return;
    initializing.current = true;
    const hostElement = host.current;
    void (async () => {
      const [{ Terminal: XTerm }, { FitAddon: XTermFitAddon }] =
        await Promise.all([
          import("@xterm/xterm"),
          import("@xterm/addon-fit"),
          import("@xterm/xterm/css/xterm.css"),
        ]);
      if (disposed.current) return;
      const instance = new XTerm({
        cursorBlink: true,
        convertEol: true,
        fontFamily: '"SFMono-Regular", Consolas, monospace',
        // Left alone on purpose: the fit addon derives cols/rows from these,
        // and those numbers are sent to the PTY. Colour below is canvas-only.
        fontSize: 11,
        lineHeight: 1.25,
        scrollback: 4000,
        // xterm paints its own canvas, so these cannot be CSS variables. They
        // are the literal values of the --*-terminal tokens in tokens.css;
        // when they disagree, a seam shows between the panel and the terminal.
        theme: {
          background: "#151816",
          foreground: "#e6eae7",
          cursor: "#929cff",
          selectionBackground: "#4d5bd555",
        },
      });
      const fitAddon = new XTermFitAddon();
      instance.loadAddon(fitAddon);
      instance.open(hostElement);
      terminal.current = instance;
      fit.current = fitAddon;
      inputDisposable.current = instance.onData((data) => {
        const current = info.current;
        if (current && connectionReady.current) {
          void runtime
            .request("terminal/write", {
              threadId: current.threadId,
              terminalId: current.terminalId,
              data,
            })
            .catch(reportError);
        }
      });
      observer.current = new ResizeObserver(() => {
        if (!activeRef.current || !info.current || !connectionReady.current)
          return;
        fitAddon.fit();
        void runtime
          .request("terminal/resize", {
            threadId: info.current.threadId,
            terminalId: info.current.terminalId,
            columns: Math.max(20, instance.cols),
            rows: Math.max(5, instance.rows),
          })
          .catch(reportError);
      });
      observer.current.observe(hostElement);
      setRendererReady(true);
    })().catch((cause) => {
      initializing.current = false;
      setError(cause instanceof Error ? cause.message : String(cause));
    });
  }, [active, reportError, runtime]);

  useEffect(() => {
    disposed.current = false;
    return () => {
      disposed.current = true;
      observer.current?.disconnect();
      inputDisposable.current?.dispose();
      terminal.current?.dispose();
      observer.current = null;
      inputDisposable.current = null;
      terminal.current = null;
      fit.current = null;
    };
  }, []);

  useEffect(() => {
    activeRef.current = active;
    if (active) fit.current?.fit();
  }, [active]);

  useEffect(() => {
    if (!rendererReady || !threadId) return;
    terminal.current?.reset();
    let stopped = false;
    let refreshVersion = 0;
    const cleanups: Array<() => void> = [];
    const currentGeneration = ++generation.current;

    const refresh = async () => {
      const version = ++refreshVersion;
      const status = await runtime.status();
      if (stopped || version !== refreshVersion) return;
      if (status.phase !== "ready") {
        setConnected(false);
        return;
      }
      const methods = status.serverInfo?.capabilities.methods ?? [];
      recoverable.current =
        methods.includes("terminal/read") && methods.includes("terminal/list");
      if (recoverable.current) {
        const { terminals } = await runtime.request("terminal/list", {
          threadId,
        });
        if (stopped || version !== refreshVersion) return;
        const previous =
          recovery.current?.terminal.terminalId ?? info.current?.terminalId;
        const chosen =
          terminals.find((entry) => entry.terminal.terminalId === previous) ??
          terminals.filter((entry) => !entry.exited).at(-1) ??
          terminals.at(-1);
        if (chosen) {
          if (
            recovery.current?.terminal.terminalId === chosen.terminal.terminalId
          ) {
            await recovery.current.recover();
          } else {
            await attach(chosen.terminal);
          }
        } else if (previous) {
          recovery.current?.stop();
          recovery.current = null;
          info.current = null;
          setTerminalInfo(null);
          setEnded(true);
          terminal.current?.write(
            "\r\n[Terminal session is no longer available]\r\n",
          );
        }
      }
      if (!stopped && version === refreshVersion) setConnected(true);
    };

    const register = async (subscription: Promise<() => void>) => {
      const cleanup = await subscription;
      if (stopped) cleanup();
      else cleanups.push(cleanup);
    };
    void (async () => {
      await register(
        runtime.onNotification((notification) => {
          if (stopped) return;
          const current = recovery.current?.terminal ?? info.current;
          if (notification.method === "server.warning") {
            if (notification.params.replayRequired)
              void recovery.current?.recover().catch(reportError);
            return;
          }
          if (
            !current ||
            (notification.method !== "terminal.output" &&
              notification.method !== "terminal.exit") ||
            notification.params.threadId !== threadId ||
            notification.params.terminalId !== current.terminalId
          )
            return;
          if (notification.method === "terminal.output") {
            if (recovery.current)
              recovery.current.receive(notification.params.nextOffset ?? 0);
            else terminal.current?.write(notification.params.data);
          } else if (recovery.current) {
            void recovery.current.recover().catch(reportError);
          } else {
            finish(notification.params.exitCode);
          }
        }),
      );
      if (stopped) return;
      setEnded(false);
      setError(null);
      await register(
        runtime.onStatus((status) => {
          if (stopped) return;
          if (status.phase === "ready") void refresh().catch(reportError);
          else {
            refreshVersion++;
            setConnected(false);
          }
        }),
      );
      if (!stopped) await refresh();
    })().catch((cause) => {
      if (!stopped) reportError(cause);
    });

    return () => {
      stopped = true;
      generation.current = currentGeneration + 1;
      for (const cleanup of cleanups) cleanup();
      recovery.current?.stop();
      recovery.current = null;
      info.current = null;
      setTerminalInfo(null);
      setConnected(false);
      setBusy(false);
      // Detaching a view does not terminate the PTY. Only Close does that.
    };
  }, [
    attach,
    finish,
    rendererReady,
    reportError,
    runtime,
    setConnected,
    threadId,
  ]);

  const start = async () => {
    if (!threadId || !enabled || busy || !connected) return;
    const currentGeneration = generation.current;
    setBusy(true);
    setError(null);
    setEnded(false);
    terminal.current?.clear();
    try {
      fit.current?.fit();
      const result = await runtime.request("terminal/create", {
        threadId,
        columns: Math.max(20, terminal.current?.cols ?? 80),
        rows: Math.max(5, terminal.current?.rows ?? 24),
      });
      if (currentGeneration !== generation.current) return;
      await attach(result.terminal);
      terminal.current?.focus();
    } catch (cause) {
      if (currentGeneration === generation.current) reportError(cause);
    } finally {
      if (currentGeneration === generation.current) setBusy(false);
    }
  };

  const close = async () => {
    const current = info.current;
    if (!current || busy || !connected) return;
    const currentGeneration = generation.current;
    setBusy(true);
    try {
      await runtime.request("terminal/close", {
        threadId: current.threadId,
        terminalId: current.terminalId,
      });
      if (currentGeneration !== generation.current) return;
      if (recovery.current) await recovery.current.recover();
      else finish(null);
    } catch (cause) {
      if (currentGeneration === generation.current) reportError(cause);
    } finally {
      if (currentGeneration === generation.current) setBusy(false);
    }
  };

  return (
    <div className={styles.panel} data-active={active}>
      <div className={styles.toolbar}>
        <span>
          {terminalInfo
            ? `PID ${terminalInfo.pid}`
            : ended
              ? "Exited"
              : "No session"}
        </span>
        {terminalInfo ? (
          <button
            type="button"
            disabled={busy || !connected}
            onClick={() => void close()}
          >
            Close
          </button>
        ) : (
          <button
            type="button"
            onClick={() => void start()}
            disabled={!enabled || !rendererReady || !connected || busy}
          >
            Start terminal
          </button>
        )}
      </div>
      {error ? <p className={styles.error}>{error}</p> : null}
      <div className={styles.host} ref={host} />
    </div>
  );
}
