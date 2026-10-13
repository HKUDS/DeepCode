import { describe, expect, it } from "vitest";

import { initI18n } from "../../app/i18n";
import { matchingCommands, parseComposerCommand } from "./commands";

const i18n = initI18n();
const t = i18n.getFixedT("en");

describe("Composer commands", () => {
  it("parses commands into real Desktop operations", () => {
    expect(parseComposerCommand("/new", t)).toEqual({
      ok: true,
      command: { type: "new" },
    });
    expect(parseComposerCommand("/model default", t)).toEqual({
      ok: true,
      command: { type: "model", model: null },
    });
    expect(parseComposerCommand("/permissions full-access", t)).toEqual({
      ok: true,
      command: { type: "permission", accessPreset: "full_access" },
    });
    expect(parseComposerCommand("/permissions inherit", t)).toEqual({
      ok: true,
      command: { type: "permission", accessPreset: null },
    });
    expect(parseComposerCommand("/permission plan", t)).toEqual({
      ok: true,
      command: { type: "permission", accessPreset: "read_only" },
    });
    expect(parseComposerCommand("/rename Architecture review", t)).toEqual({
      ok: true,
      command: { type: "rename", title: "Architecture review" },
    });
  });

  it("returns actionable errors and filtered suggestions", () => {
    expect(parseComposerCommand("/permissions unsafe", t)).toEqual({
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

  it("explains a mistyped command in the interface language", () => {
    const zh = i18n.getFixedT("zh-CN");

    expect(parseComposerCommand("/rename", zh)).toEqual({
      ok: false,
      message: "用法：/rename <会话标题>",
    });
    expect(parseComposerCommand("/review now", zh)).toEqual({
      ok: false,
      message: "/review 不接受参数。",
    });
    expect(parseComposerCommand("/nope", zh)).toEqual({
      ok: false,
      message: "未知命令：/nope",
    });
  });
});
