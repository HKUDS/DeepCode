import type {
  MethodResults,
  Turn,
  TurnStartParams,
} from "../generated/app-server";
import type { BridgeError, DesktopRuntime } from "../rpc/contracts";

export type InteractiveDelivery = "started" | "steered" | "queued";

export type InteractiveTurnResult =
  | {
      delivery: "started";
      messageId: string;
      turn: Turn;
      snapshot: MethodResults["turn/start"];
    }
  | {
      delivery: "steered";
      messageId: string;
      turn: Turn;
      duplicate: boolean;
    }
  | {
      delivery: "queued";
      messageId: string;
      turn: Turn;
      snapshot: MethodResults["turn/enqueue"];
    };

export interface InteractiveTurnInput {
  threadId: string;
  prompt: string;
  cachedActiveTurnId: string | null;
  skillIds?: string[];
  messageId?: string;
}

/**
 * Route one composer submission using only active-Turn state.
 *
 * The initially selected operation gets at most one race correction. Every
 * attempt reuses the same message ID, so a transport replay cannot duplicate
 * the user's input.
 */
export async function sendInteractiveTurn(
  runtime: DesktopRuntime,
  input: InteractiveTurnInput,
): Promise<InteractiveTurnResult> {
  const messageId = input.messageId ?? `desktop-${crypto.randomUUID()}`;
  if (input.cachedActiveTurnId) {
    try {
      return await steer(runtime, input, input.cachedActiveTurnId, messageId);
    } catch (error) {
      if (errorCode(error) === "NO_ACTIVE_TURN") {
        return startOrSteerOnce(runtime, input, messageId);
      }
      if (errorCode(error) === "EXPECTED_TURN_MISMATCH") {
        const actualTurnId = actualTurnIdFrom(error);
        if (actualTurnId) {
          return steerOrEnqueue(runtime, input, actualTurnId, messageId);
        }
      }
      if (crossedFinalInputBoundary(error)) {
        return enqueue(runtime, input, messageId);
      }
      throw error;
    }
  }

  return startOrSteerOnce(runtime, input, messageId);
}

export function latestExecutingTurn(
  turns: readonly Turn[],
  threadId?: string,
): Turn | null {
  return (
    turns
      .filter(
        (turn) =>
          (!threadId || turn.threadId === threadId) &&
          (turn.status === "running" || turn.status === "waiting_approval"),
      )
      .sort((left, right) => right.ordinal - left.ordinal)[0] ?? null
  );
}

async function start(
  runtime: DesktopRuntime,
  input: InteractiveTurnInput,
  messageId: string,
): Promise<InteractiveTurnResult> {
  const skillIds = input.skillIds ?? [];
  const snapshot = await runtime.request("turn/start", {
    threadId: input.threadId,
    prompt: input.prompt,
    messageId,
    ...(skillIds.length
      ? { skills: skillIds as TurnStartParams["skills"] }
      : {}),
  });
  return {
    delivery: "started",
    messageId,
    turn: snapshot.turn,
    snapshot,
  };
}

async function startOrSteerOnce(
  runtime: DesktopRuntime,
  input: InteractiveTurnInput,
  messageId: string,
): Promise<InteractiveTurnResult> {
  try {
    return await start(runtime, input, messageId);
  } catch (error) {
    if (errorCode(error) === "TURN_ALREADY_ACTIVE") {
      const actualTurnId = actualTurnIdFrom(error);
      if (actualTurnId) {
        return steerOrEnqueue(runtime, input, actualTurnId, messageId);
      }
    }
    throw error;
  }
}

async function enqueue(
  runtime: DesktopRuntime,
  input: InteractiveTurnInput,
  messageId: string,
): Promise<InteractiveTurnResult> {
  const skillIds = input.skillIds ?? [];
  const snapshot = await runtime.request("turn/enqueue", {
    threadId: input.threadId,
    prompt: input.prompt,
    messageId,
    ...(skillIds.length
      ? { skills: skillIds as TurnStartParams["skills"] }
      : {}),
  });
  return {
    delivery: "queued",
    messageId,
    turn: snapshot.turn,
    snapshot,
  };
}

async function steerOrEnqueue(
  runtime: DesktopRuntime,
  input: InteractiveTurnInput,
  expectedTurnId: string,
  messageId: string,
): Promise<InteractiveTurnResult> {
  try {
    return await steer(runtime, input, expectedTurnId, messageId);
  } catch (error) {
    if (errorCode(error) === "NO_ACTIVE_TURN" || crossedFinalInputBoundary(error)) {
      return enqueue(runtime, input, messageId);
    }
    throw error;
  }
}

async function steer(
  runtime: DesktopRuntime,
  input: InteractiveTurnInput,
  expectedTurnId: string,
  messageId: string,
): Promise<InteractiveTurnResult> {
  const result = await runtime.request("turn/steer", {
    threadId: input.threadId,
    expectedTurnId,
    prompt: input.prompt,
    messageId,
  });
  return {
    delivery: "steered",
    messageId,
    turn: result.turn,
    duplicate: result.duplicate,
  };
}

function errorCode(error: unknown): string | null {
  if (typeof error !== "object" || error === null) return null;
  const code = (error as Partial<BridgeError>).code;
  return typeof code === "string" ? code : null;
}

function actualTurnIdFrom(error: unknown): string | null {
  if (typeof error !== "object" || error === null) return null;
  const data = (error as Partial<BridgeError>).data;
  if (typeof data !== "object" || data === null) return null;
  const details =
    "details" in data && typeof data.details === "object" && data.details !== null
      ? data.details
      : data;
  if (!("actualTurnId" in details)) return null;
  const actualTurnId = details.actualTurnId;
  return typeof actualTurnId === "string" && actualTurnId
    ? actualTurnId
    : null;
}

function crossedFinalInputBoundary(error: unknown): boolean {
  if (errorCode(error) !== "TURN_NOT_STEERABLE") return false;
  if (typeof error !== "object" || error === null) return false;
  const data = (error as Partial<BridgeError>).data;
  if (typeof data !== "object" || data === null) return false;
  const details =
    "details" in data && typeof data.details === "object" && data.details !== null
      ? data.details
      : data;
  if (!("state" in details)) return false;
  return details.state === "closing" || details.state === "closed";
}
