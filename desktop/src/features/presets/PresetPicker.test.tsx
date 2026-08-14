import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

afterEach(cleanup);

import type { AgentPresetEntry } from "../../generated/app-server";
import { PresetPicker } from "./PresetPicker";

const reader: AgentPresetEntry = {
  id: "code-reader",
  trust: "system",
  name: "Code reader",
  description: "Read-only investigator",
  tools: ["read", "grep", "glob"],
  broken: null,
};

const damaged: AgentPresetEntry = {
  id: "damaged",
  trust: "project",
  name: "damaged",
  description: "",
  tools: null,
  broken: "missing YAML frontmatter block",
};

describe("PresetPicker", () => {
  it("locks into a read-only label once the conversation has started", () => {
    render(
      <PresetPicker
        entries={[reader, damaged]}
        current="code-reader"
        locked
        busy={false}
        error={null}
        onSelect={vi.fn()}
      />,
    );
    const picker = screen.getByRole("combobox", { name: "Agent preset" });
    expect((picker as HTMLSelectElement).disabled).toBe(true);
    expect((picker as HTMLSelectElement).value).toBe("code-reader");
    expect(picker.closest("label")?.title).toMatch(/already started/);
  });

  it("still names a preset the roster no longer offers", () => {
    render(
      <PresetPicker
        entries={[]}
        current="retired-preset"
        locked
        busy={false}
        error={null}
        onSelect={vi.fn()}
      />,
    );
    expect(
      screen.getByRole("option", { name: "retired-preset" }),
    ).toBeTruthy();
  });

  it("renders nothing when there is nothing to offer or report", () => {
    const { container } = render(
      <PresetPicker
        entries={[damaged]}
        current={null}
        locked={false}
        busy={false}
        error={null}
        onSelect={vi.fn()}
      />,
    );
    expect(container.firstChild).toBeNull();
  });

  it("surfaces a refused switch on the control's title", () => {
    render(
      <PresetPicker
        entries={[reader]}
        current={null}
        locked={false}
        busy={false}
        error="agent preset is locked once the conversation has started"
        onSelect={vi.fn()}
      />,
    );
    const picker = screen.getByRole("combobox", { name: "Agent preset" });
    expect(picker.closest("label")?.title).toMatch(/locked once/);
  });
});
