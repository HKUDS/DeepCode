import type { Event, MethodResults } from "../generated/app-server";
import type { RpcTransport } from "../rpc/contracts";

const DEFAULT_REPLAY_LIMIT = 1000;

function errorCode(error: unknown): string | null {
  if (typeof error !== "object" || error === null || !("code" in error)) {
    return null;
  }
  const code = (error as { code?: unknown }).code;
  return typeof code === "string" ? code : null;
}

export async function replayThreadHistory(
  runtime: Pick<RpcTransport, "request">,
  threadId: string,
  accept: (event: Event) => void,
  options: { after?: number; isCurrent?: () => boolean } = {},
): Promise<number> {
  let after = options.after ?? 0;
  let through: number | undefined;
  let limit = DEFAULT_REPLAY_LIMIT;
  const isCurrent = options.isCurrent ?? (() => true);

  while (isCurrent()) {
    let result: MethodResults["event/replay"];
    try {
      result = await runtime.request("event/replay", {
        threadId,
        after,
        limit,
        ...(through !== undefined ? { through } : {}),
      });
    } catch (error) {
      if (!isCurrent()) return after;
      if (errorCode(error) === "RESPONSE_TOO_LARGE" && limit > 1) {
        limit = Math.max(1, Math.floor(limit / 2));
        continue;
      }
      throw error;
    }

    if (!isCurrent()) return after;
    if (result.headSequence !== undefined) {
      if (
        !Number.isSafeInteger(result.headSequence) ||
        result.headSequence < after ||
        (through !== undefined && through !== result.headSequence)
      ) {
        throw new Error("event/replay history changed; reload the Thread");
      }
      through = result.headSequence;
    }

    for (const event of result.events) {
      if (!isCurrent()) return after;
      if (
        event.threadId !== threadId ||
        event.sequence !== after + 1 ||
        (through !== undefined && event.sequence > through)
      ) {
        throw new Error(
          "event/replay returned a non-contiguous Thread history",
        );
      }
      accept(event);
      after = event.sequence;
    }

    const compatible = result as MethodResults["event/replay"] & {
      hasMore?: boolean;
      nextAfter?: number | null;
    };
    const hasMore =
      typeof compatible.hasMore === "boolean"
        ? compatible.hasMore
        : result.events.length === limit;
    if (!hasMore) {
      if (through !== undefined && after !== through) {
        throw new Error("event/replay ended before its captured head");
      }
      return after;
    }

    const last = result.events.at(-1);
    const nextAfter = compatible.nextAfter ?? last?.sequence ?? null;
    if (nextAfter === null || !result.events.length || nextAfter !== after) {
      throw new Error("event/replay did not advance its cursor");
    }
  }
  return after;
}
