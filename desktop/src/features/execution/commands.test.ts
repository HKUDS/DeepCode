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
    expect(parseComposerCommand("/permission full-auto")).toEqual({
      ok: true,
      command: { type: "permission", mode: "full_auto" },
    });
    expect(parseComposerCommand("/rename Architecture review")).toEqual({
      ok: true,
      command: { type: "rename", title: "Architecture review" },
    });
  });

  it("returns actionable errors and filtered suggestions", () => {
    expect(parseComposerCommand("/permission unsafe")).toEqual({
      ok: false,
      message: "Usage: /permission <approval | plan | full-auto>",
    });
    expect(matchingCommands("/re").map((command) => command.name)).toEqual([
      "review",
      "rename",
    ]);
    expect(matchingCommands("normal prompt")).toEqual([]);
  });
});
