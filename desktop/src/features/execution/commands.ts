import type { DesktopPermissionMode } from "../../app/useWorkspaceController";

export type ComposerCommand =
  | { type: "new" }
  | { type: "paper" }
  | { type: "review" }
  | { type: "fork" }
  | { type: "rename"; title: string }
  | { type: "model"; model: string | null }
  | { type: "permission"; mode: DesktopPermissionMode };

export interface CommandDefinition {
  name: string;
  usage: string;
  description: string;
}

export const commandDefinitions: CommandDefinition[] = [
  { name: "new", usage: "/new", description: "Create a code Session" },
  { name: "paper", usage: "/paper", description: "Create a Paper2Code Session" },
  { name: "review", usage: "/review", description: "Open the Review workbench" },
  { name: "fork", usage: "/fork", description: "Fork into an isolated worktree" },
  { name: "rename", usage: "/rename ", description: "Rename this Session" },
  { name: "model", usage: "/model ", description: "Set a model or use default" },
  {
    name: "permission",
    usage: "/permission ",
    description: "Set approval, plan, or full-auto mode",
  },
];

export type CommandParseResult =
  | { ok: true; command: ComposerCommand }
  | { ok: false; message: string };

function requireNoArgument(name: string, argument: string): CommandParseResult | null {
  return argument
    ? { ok: false, message: `/${name} does not accept an argument.` }
    : null;
}

export function parseComposerCommand(value: string): CommandParseResult | null {
  const input = value.trim();
  if (!input.startsWith("/")) return null;
  const separator = input.search(/\s/);
  const name = (
    separator === -1 ? input.slice(1) : input.slice(1, separator)
  ).toLocaleLowerCase();
  const argument = separator === -1 ? "" : input.slice(separator).trim();

  switch (name) {
    case "new":
    case "paper":
    case "review":
    case "fork": {
      const error = requireNoArgument(name, argument);
      return error ?? { ok: true, command: { type: name } };
    }
    case "rename":
      return argument
        ? { ok: true, command: { type: "rename", title: argument } }
        : { ok: false, message: "Usage: /rename <Session title>" };
    case "model":
      return argument
        ? {
            ok: true,
            command: {
              type: "model",
              model: argument === "default" ? null : argument,
            },
          }
        : { ok: false, message: "Usage: /model <model id | default>" };
    case "permission": {
      const aliases: Record<string, DesktopPermissionMode> = {
        approval: "default",
        default: "default",
        plan: "plan",
        "full-auto": "full_auto",
        full_auto: "full_auto",
      };
      const mode = aliases[argument.toLocaleLowerCase()];
      return mode
        ? { ok: true, command: { type: "permission", mode } }
        : {
            ok: false,
            message: "Usage: /permission <approval | plan | full-auto>",
          };
    }
    default:
      return { ok: false, message: `Unknown command: /${name || "…"}` };
  }
}

export function matchingCommands(value: string): CommandDefinition[] {
  const input = value.trimStart();
  if (!input.startsWith("/") || input.includes("\n")) return [];
  const query = input.slice(1).split(/\s/, 1)[0].toLocaleLowerCase();
  return commandDefinitions.filter((definition) =>
    definition.name.startsWith(query),
  );
}
