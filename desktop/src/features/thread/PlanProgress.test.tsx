import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import type { TurnPlanState } from "../../app/workspaceState";
import { PlanProgress } from "./PlanProgress";

const plan: TurnPlanState = {
  turnId: "turn-1",
  explanation: "Verify each layer before moving on.",
  updatedAt: "2026-07-23T01:00:00Z",
  steps: [
    { step: "Inspect the repository", status: "completed" },
    { step: "Repair the projection", status: "in_progress" },
    { step: "Run the regression suite", status: "pending" },
  ],
};

describe("PlanProgress", () => {
  afterEach(cleanup);

  it("shows the current step and exposes the complete structured plan", () => {
    render(<PlanProgress plan={plan} />);

    const trigger = screen.getByRole("button", { name: "Step 2 / 3" });
    expect(trigger.getAttribute("aria-expanded")).toBe("false");

    fireEvent.pointerEnter(trigger);

    expect(screen.getByRole("region", { name: "Execution plan" })).toBeTruthy();
    expect(screen.getByText("Inspect the repository")).toBeTruthy();
    expect(screen.getByText("Repair the projection").closest("li")?.getAttribute(
      "aria-current",
    )).toBe("step");
    expect(trigger.getAttribute("aria-expanded")).toBe("true");
  });

  it("can be pinned with a click and dismissed with Escape", () => {
    render(<PlanProgress plan={plan} />);

    const trigger = screen.getByRole("button", { name: "Step 2 / 3" });
    fireEvent.click(trigger);
    fireEvent.pointerLeave(trigger);
    expect(screen.getByRole("region", { name: "Execution plan" })).toBeTruthy();

    fireEvent.keyDown(trigger, { key: "Escape" });
    expect(
      screen.queryByRole("region", { name: "Execution plan" }),
    ).toBeNull();
  });
});
