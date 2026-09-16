import type { TerminalInfo, MethodResults } from "../../generated/app-server";
import type { RpcTransport } from "../../rpc/contracts";

/** Output-only recovery. Input writes are deliberately outside this reader. */
export class TerminalOutputReader {
  private offset = 0;
  private observedHead = 0;
  private revision = 0;
  private active = true;
  private ended = false;
  private pending: Promise<void> | null = null;
  private cancelWrite: (() => void) | null = null;

  constructor(
    private readonly runtime: RpcTransport,
    readonly terminal: TerminalInfo,
    private readonly write: (data: string) => Promise<void>,
    private readonly truncate: () => void,
    private readonly exit: (code: number | null) => void,
    private readonly onError: (error: unknown) => void,
  ) {}

  stop(): void {
    this.active = false;
    this.cancelWrite?.();
  }

  receive(nextOffset: number): void {
    if (!this.active || nextOffset <= this.offset) return;
    this.observedHead = Math.max(this.observedHead, nextOffset);
    if (!this.pending) void this.recover().catch(this.onError);
  }

  recover(): Promise<void> {
    if (!this.active) return Promise.resolve();
    this.revision++;
    if (!this.pending) this.pending = this.read();
    return this.pending;
  }

  private async read(): Promise<void> {
    let limit = 16 * 1024;
    try {
      while (this.active) {
        const revision = this.revision;
        const before = this.offset;
        let through: number | undefined;
        let page: MethodResults["terminal/read"];
        for (;;) {
          try {
            page = await this.runtime.request("terminal/read", {
              threadId: this.terminal.threadId,
              terminalId: this.terminal.terminalId,
              offset: this.offset,
              limit,
              ...(through === undefined ? {} : { through }),
            });
          } catch (error) {
            if (!this.active) return;
            if (
              (error as { code?: string } | null)?.code ===
                "RESPONSE_TOO_LARGE" &&
              limit > 4
            ) {
              limit = Math.max(4, Math.floor(limit / 2));
              continue;
            }
            throw error;
          }
          if (!this.active) return;
          const bytes = new TextEncoder().encode(page.data).length;
          if (
            page.terminalId !== this.terminal.terminalId ||
            page.threadId !== this.terminal.threadId ||
            page.offset < this.offset ||
            (!page.truncated && page.offset !== this.offset) ||
            page.nextOffset !== page.offset + bytes ||
            page.headOffset < page.nextOffset ||
            (page.hasMore && bytes === 0)
          ) {
            throw new Error(
              "Terminal output cursor is inconsistent; reopen the terminal view",
            );
          }
          if (page.truncated) this.truncate();
          if (page.data) await this.render(page.data);
          if (!this.active) return;
          this.offset = page.nextOffset;
          this.observedHead = Math.max(this.observedHead, page.headOffset);
          through ??= page.headOffset;
          // Eviction can pass a previously captured cutoff; start a new window.
          if (page.truncated) through = undefined;
          if (page.truncated && !page.hasMore) {
            this.revision++;
            break;
          }
          if (!page.hasMore) break;
        }
        if (this.observedHead > this.offset && this.offset === before) {
          throw new Error("Terminal output recovery made no progress");
        }
        if (
          page.exited &&
          this.offset === page.headOffset &&
          !page.truncated &&
          !this.ended
        ) {
          this.ended = true;
          this.exit(page.exitCode);
        }
        if (revision === this.revision && this.observedHead <= this.offset)
          return;
      }
    } finally {
      this.pending = null;
    }
  }

  private render(data: string): Promise<void> {
    // One cancellable renderer write, without accumulating handlers on a
    // never-settled cancellation promise during a long-lived terminal session.
    return new Promise((resolve, reject) => {
      let settled = false;
      const finish = (error?: unknown) => {
        if (settled) return;
        settled = true;
        this.cancelWrite = null;
        if (error === undefined) resolve();
        else reject(error);
      };
      this.cancelWrite = () => finish();
      Promise.resolve()
        .then(() => (this.active ? this.write(data) : undefined))
        .then(() => finish(), finish);
    });
  }
}
