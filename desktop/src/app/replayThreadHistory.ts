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
): Promise<void> {
  let after = 0;
  let limit = DEFAULT_REPLAY_LIMIT;

  for (;;) {
    let result: MethodResults["event/replay"];
    try {
      result = await runtime.request("event/replay", {
        threadId,
        after,
        limit,
      });
    } catch (error) {
      if (errorCode(error) === "RESPONSE_TOO_LARGE" && limit > 1) {
        limit = Math.max(1, Math.floor(limit / 2));
        continue;
      }
      throw error;
    }

    for (const event of result.events) {
      accept(event);
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
      return;
    }

    const last = result.events.at(-1);
    const nextAfter = compatible.nextAfter ?? last?.sequence ?? null;
    if (nextAfter === null || nextAfter <= after) {
      throw new Error("event/replay did not advance its cursor");
    }
    after = nextAfter;
  }
}
