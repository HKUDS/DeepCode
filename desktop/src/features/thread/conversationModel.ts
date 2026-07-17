import type { Item, Turn } from "../../generated/app-server";

export interface ConversationTurn {
  id: string;
  turn: Turn | null;
  userMessages: Item[];
  assistantMessages: Item[];
  executionItems: Item[];
  completion: Item | null;
}

interface MutableConversationTurn extends ConversationTurn {
  sortOrdinal: number;
}

function createGroup(turn: Turn): MutableConversationTurn {
  return {
    id: turn.id,
    turn,
    userMessages: [],
    assistantMessages: [],
    executionItems: [],
    completion: null,
    sortOrdinal: turn.ordinal,
  };
}

export function buildConversationTurns(
  turns: Turn[],
  items: Item[],
): ConversationTurn[] {
  const groups = new Map(
    turns.map((turn) => [turn.id, createGroup(turn)]),
  );
  let orphanOrdinal = turns.reduce(
    (largest, turn) => Math.max(largest, turn.ordinal),
    0,
  );

  for (const item of [...items].sort((left, right) => {
    const leftTurn = groups.get(left.turnId)?.sortOrdinal ?? Number.MAX_SAFE_INTEGER;
    const rightTurn =
      groups.get(right.turnId)?.sortOrdinal ?? Number.MAX_SAFE_INTEGER;
    return leftTurn - rightTurn || left.ordinal - right.ordinal;
  })) {
    let group = groups.get(item.turnId);
    if (!group) {
      orphanOrdinal += 1;
      group = {
        id: item.turnId,
        turn: null,
        userMessages: [],
        assistantMessages: [],
        executionItems: [],
        completion: null,
        sortOrdinal: orphanOrdinal,
      };
      groups.set(item.turnId, group);
    }
    switch (item.kind) {
      case "user_message":
        group.userMessages.push(item);
        break;
      case "assistant_message":
        group.assistantMessages.push(item);
        break;
      case "completion":
        group.completion = item;
        break;
      default:
        group.executionItems.push(item);
    }
  }

  return [...groups.values()]
    .sort((left, right) => left.sortOrdinal - right.sortOrdinal)
    .map((group) => ({
      id: group.id,
      turn: group.turn,
      userMessages: group.userMessages,
      assistantMessages: group.assistantMessages,
      executionItems: group.executionItems,
      completion: group.completion,
    }));
}

export function turnDurationSeconds(
  turn: Turn | null,
  now = Date.now(),
): number | null {
  if (!turn?.startedAt) return null;
  const start = new Date(turn.startedAt).getTime();
  const end = turn.completedAt ? new Date(turn.completedAt).getTime() : now;
  if (!Number.isFinite(start) || !Number.isFinite(end)) return null;
  return Math.max(0, Math.round((end - start) / 1000));
}

export function formatTurnDuration(seconds: number): string {
  if (seconds < 60) return `${seconds}s`;
  const minutes = Math.floor(seconds / 60);
  const remainder = seconds % 60;
  if (minutes < 60) return `${minutes}m ${remainder.toString().padStart(2, "0")}s`;
  const hours = Math.floor(minutes / 60);
  const minuteRemainder = minutes % 60;
  return `${hours}h ${minuteRemainder.toString().padStart(2, "0")}m`;
}
