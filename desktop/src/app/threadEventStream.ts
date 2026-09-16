import type { Event } from "../generated/app-server";
import type { RpcTransport } from "../rpc/contracts";
import { replayThreadHistory } from "./replayThreadHistory";

/** One selected Thread's contiguous cursor, shared by replay and live delivery.
 * Gapped live payloads are recovered from the durable log instead of retained
 * in an unbounded client queue. Stopping the stream fences late RPC results.
 */
export class ThreadEventStream {
  private cursor = 0;
  private observedHead = 0;
  private revision = 0;
  private active = true;
  private pending: Promise<void> | null = null;

  constructor(
    private readonly runtime: RpcTransport,
    readonly threadId: string,
    private readonly accept: (event: Event) => void,
    private readonly onError: (error: unknown) => void,
  ) {}

  stop(): void {
    this.active = false;
  }

  receive(event: Event): void {
    if (
      !this.active ||
      event.threadId !== this.threadId ||
      event.sequence <= this.cursor
    )
      return;
    this.observedHead = Math.max(this.observedHead, event.sequence);
    if (!this.pending && event.sequence === this.cursor + 1) {
      this.accept(event);
      this.cursor = event.sequence;
    } else if (!this.pending) {
      void this.recover().catch(this.onError);
    }
  }

  /** Also used for an overflow warning, including a lost final notification. */
  recover(): Promise<void> {
    if (!this.active) return Promise.resolve();
    this.revision += 1;
    if (!this.pending) this.pending = this.replay();
    return this.pending;
  }

  private async replay(): Promise<void> {
    try {
      while (this.active) {
        const revision = this.revision;
        const before = this.cursor;
        await replayThreadHistory(
          this.runtime,
          this.threadId,
          (event) => {
            this.accept(event);
            this.cursor = event.sequence;
          },
          { after: this.cursor, isCurrent: () => this.active },
        );
        if (!this.active) return;
        if (this.observedHead > this.cursor && before === this.cursor) {
          throw new Error(
            "event/replay cannot recover an observed event; reload the Thread",
          );
        }
        if (revision === this.revision && this.observedHead <= this.cursor)
          return;
      }
    } finally {
      this.pending = null;
    }
  }
}
