import { describe, expect, it } from "vitest";

import { matchingCommands, parseComposerCommand } from "./commands";

describe("Composer commands", () => {
  it("parses commands into real Desktop operations", () => {
    expect(parseComposerCommand("/new")).toEqual({
      ok: true,
      command: { type: "new" },
    });
    expect(parseComposerCommand("/model default")).toEqual({
      ok: true,
      command: { type: "model", model: null },
    });
    expect(parseComposerCommand("/permissions full-access")).toEqual({
      ok: true,
      command: { type: "permission", accessPreset: "full_access" },
    });
    expect(parseComposerCommand("/permissions inherit")).toEqual({
      ok: true,
      command: { type: "permission", accessPreset: null },
    });
    expect(parseComposerCommand("/permission plan")).toEqual({
      ok: true,
      command: { type: "permission", accessPreset: "read_only" },
    });
    expect(parseComposerCommand("/rename Architecture review")).toEqual({
      ok: true,
      command: { type: "rename", title: "Architecture review" },
    });
  });

  it("returns actionable errors and filtered suggestions", () => {
    expect(parseComposerCommand("/permissions unsafe")).toEqual({
      ok: false,
      message:
        "Usage: /permissions <ask | read-only | full-access | inherit>",
    });
    expect(matchingCommands("/re").map((command) => command.name)).toEqual([
      "review",
      "rename",
    ]);
    expect(matchingCommands("normal prompt")).toEqual([]);
  });
});
