import { expect, test } from "vitest";
import { connectionMutation, emptyDraft } from "./connectionDraft";

test("editing a declaration preserves capabilities, reasoning and compat", () => {
  const entry = {
    id: "model",
    inputModalities: ["text"] as ["text"],
    toolCalling: false,
    reasoningEfforts: false as const,
    compat: { temperature: false },
  };
  const value = connectionMutation({
    ...emptyDraft,
    id: "local",
    template: "custom",
    protocol: "openai_chat",
    auth: "none",
    compat: { systemRole: "developer" },
    manualModels: [entry, { id: "simple" }],
  });
  expect(value.manualModels).toEqual([entry, "simple"]);
  expect(value).toMatchObject({
    protocol: "openai_chat",
    auth: "none",
    compat: { systemRole: "developer" },
  });
  expect(value).not.toHaveProperty("apiKey");
});
