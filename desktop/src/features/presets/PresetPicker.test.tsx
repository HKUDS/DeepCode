import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { AgentPresetEntry } from "../../generated/app-server";
import { PresetPicker } from "./PresetPicker";

afterEach(cleanup);

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
  it("offers Default plus healthy presets with their descriptions", () => {
    const onSelect = vi.fn();
    render(
      <PresetPicker
        entries={[reader, damaged]}
        current={null}
        locked={false}
        busy={false}
        error={null}
        onSelect={onSelect}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Agent preset" }));
    expect(screen.getByRole("option", { name: /Default/ })).toBeTruthy();
    expect(screen.getByText("Read-only investigator")).toBeTruthy();
    // A broken preset cannot compose a session, so it is never offered.
    expect(screen.queryByRole("option", { name: /damaged/ })).toBeNull();

    fireEvent.click(screen.getByRole("option", { name: /Code reader/ }));
    expect(onSelect).toHaveBeenCalledWith("code-reader");
  });

  it("locks into a read-only label once the conversation has started", () => {
    render(
      <PresetPicker
        entries={[reader]}
        current="code-reader"
        locked
        busy={false}
        error={null}
        onSelect={vi.fn()}
      />,
    );
    const trigger = screen.getByRole("button", { name: "Agent preset" });
    expect((trigger as HTMLButtonElement).disabled).toBe(true);
    expect(trigger.textContent).toContain("Code reader");
    expect(trigger.title).toMatch(/already started/);
  });

  it("still names a preset the roster no longer offers", () => {
    render(
      <PresetPicker
        entries={[reader]}
        current="retired-preset"
        locked
        busy={false}
        error={null}
        onSelect={vi.fn()}
      />,
    );
    expect(
      screen.getByRole("button", { name: "Agent preset" }).textContent,
    ).toContain("retired-preset");
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

  it("surfaces a refused switch on the trigger's title", () => {
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
    expect(
      screen.getByRole("button", { name: "Agent preset" }).title,
    ).toMatch(/locked once/);
  });
});
