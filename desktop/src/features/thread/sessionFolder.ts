import { createContext, useContext } from "react";

/** The folder the open Session works in, so its steps can read short. */
export const SessionFolderContext = createContext<string | null>(null);

export function useSessionFolder(): string | null {
  return useContext(SessionFolderContext);
}

// `cd <folder> &&` or `cd <folder>;` at the start, the folder bare or quoted.
const LEADING_CD = /^\s*cd\s+(?:"([^"]+)"|'([^']+)'|([^\s;&|]+))\s*(?:&&|;)\s*/;

function withoutTrailingSlash(path: string): string {
  return path.length > 1 ? path.replace(/[\\/]+$/, "") : path;
}

/**
 * A command as people read it. Agents often start every command with
 * `cd <the Session's folder> &&`: on a list of steps that prefix repeats what
 * the reader knows and pushes the command itself out of view, so it is left
 * out. A `cd` into any other folder stays, since it says where the command
 * ran. Only the display changes; the step's detail keeps the full command.
 */
export function readableCommand(command: string, folder: string | null): string {
  if (!folder) return command;
  const match = LEADING_CD.exec(command);
  if (!match) return command;
  const target = match[1] ?? match[2] ?? match[3] ?? "";
  if (withoutTrailingSlash(target) !== withoutTrailingSlash(folder)) return command;
  const rest = command.slice(match[0].length);
  return rest.trim() ? rest : command;
}
