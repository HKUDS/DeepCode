import type { FitAddon } from "@xterm/addon-fit";
import type { Terminal } from "@xterm/xterm";
import { useEffect, useRef, useState } from "react";

import type { TerminalInfo } from "../../generated/app-server";
import type { DesktopRuntime } from "../../rpc/contracts";
import styles from "./TerminalPanel.module.css";

interface TerminalPanelProps {
  runtime: DesktopRuntime;
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
  const [terminalInfo, setTerminalInfo] = useState<TerminalInfo | null>(null);
  const [ended, setEnded] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [rendererReady, setRendererReady] = useState(false);

  useEffect(() => {
    if (!active || !host.current || terminal.current || initializing.current) return;
    initializing.current = true;
    const hostElement = host.current;
    void (async () => {
      const [{ Terminal: XTerm }, { FitAddon: XTermFitAddon }] = await Promise.all([
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
        if (current) {
          void runtime.request("terminal/write", {
            threadId: current.threadId,
            terminalId: current.terminalId,
            data,
          });
        }
      });
      observer.current = new ResizeObserver(() => {
        if (!activeRef.current || !info.current) return;
        fitAddon.fit();
        void runtime.request("terminal/resize", {
          threadId: info.current.threadId,
          terminalId: info.current.terminalId,
          columns: Math.max(20, instance.cols),
          rows: Math.max(5, instance.rows),
        });
      });
      observer.current.observe(hostElement);
      setRendererReady(true);
    })().catch((cause) => {
      initializing.current = false;
      setError(cause instanceof Error ? cause.message : String(cause));
    });
  }, [active, runtime]);

  useEffect(
    () => {
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
    },
    [],
  );

  useEffect(() => {
    activeRef.current = active;
    if (active) fit.current?.fit();
  }, [active]);

  useEffect(() => {
    let disposed = false;
    let cleanup: () => void = () => undefined;
    void runtime.onNotification((notification) => {
      if (disposed || !threadId) return;
      if (notification.method === "terminal.output") {
        const current = info.current;
        if (
          current &&
          notification.params.threadId === threadId &&
          notification.params.terminalId === current.terminalId
        ) {
          terminal.current?.write(notification.params.data);
        }
      }
      if (
        notification.method === "terminal.exit" &&
        notification.params.terminalId === info.current?.terminalId
      ) {
        terminal.current?.write(
          `\r\n\x1b[90m[process exited ${notification.params.exitCode ?? "unknown"}]\x1b[0m\r\n`,
        );
        setEnded(true);
        info.current = null;
        setTerminalInfo(null);
      }
    }).then((unsubscribe) => {
      if (disposed) unsubscribe();
      else cleanup = unsubscribe;
    });
    return () => {
      disposed = true;
      cleanup();
    };
  }, [runtime, threadId]);

  useEffect(
    () => () => {
      const current = info.current;
      if (current) {
        void runtime.request("terminal/close", {
          threadId: current.threadId,
          terminalId: current.terminalId,
        });
        info.current = null;
      }
    },
    [runtime, threadId],
  );

  const start = async () => {
    if (!threadId || !enabled) return;
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
      info.current = result.terminal;
      setTerminalInfo(result.terminal);
      terminal.current?.focus();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    }
  };

  const close = async () => {
    const current = info.current;
    if (!current) return;
    await runtime.request("terminal/close", {
      threadId: current.threadId,
      terminalId: current.terminalId,
    });
    info.current = null;
    setTerminalInfo(null);
    setEnded(true);
    terminal.current?.write("\r\n\x1b[90m[terminal closed]\x1b[0m\r\n");
  };

  return (
    <div className={styles.panel} data-active={active}>
      <div className={styles.toolbar}>
        <span>{terminalInfo ? `PID ${terminalInfo.pid}` : ended ? "Exited" : "No session"}</span>
        {terminalInfo ? (
          <button type="button" onClick={() => void close()}>
            Close
          </button>
        ) : (
          <button
            type="button"
            onClick={() => void start()}
            disabled={!enabled || !rendererReady}
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
