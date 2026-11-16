import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { initI18n } from "../../app/i18n";
import { mergeGoalReviews } from "../../app/workspaceState";
import type { Goal, GoalReview } from "../../generated/app-server";
import { GoalRail } from "./GoalRail";

initI18n();

const goal: Goal = {
  id: "goal_1",
  threadId: "thread-1",
  objective: "Fix the cart",
  status: "active",
  tokenBudget: null,
  tokensUsed: 0,
  timeUsedSeconds: 0,
  skillIds: [],
  checks: [],
  proposedChecks: [
    { requirement: "Cart tests pass", command: ["python", "-m", "pytest", "-q"] },
    { requirement: "Zero stock removes the item", command: [] },
  ],
  createdAt: "2026-11-02T01:00:00Z",
  updatedAt: "2026-11-02T01:00:00Z",
};

function review(overrides: Partial<GoalReview> = {}): GoalReview {
  return {
    goalId: "goal_1",
    turnId: "turn-1",
    recordedAt: "2026-11-02T01:05:00Z",
    stage: "reviewer",
    result: "rejected",
    justification: "An independent review did not accept the claim.",
    summary: "Zero stock is kept.",
    requirements: [
      { requirement: "Cart tests pass", finding: "proved", evidence: "ran pytest" },
      {
        requirement: "Zero stock removes the item",
        finding: "contradicted",
        evidence: "remove() keeps qty 0",
      },
    ],
    checks: [
      {
        requirement: "Cart tests pass",
        command: ["python", "-m", "pytest", "-q"],
        exitCode: 0,
        problem: "",
        seconds: 1.2,
        output: "",
      },
    ],
    checked: [],
    tokens: 900,
    ...overrides,
  };
}

function renderRail(props: Partial<Parameters<typeof GoalRail>[0]> = {}) {
  const onConfirmChecks = vi.fn(async () => {});
  const onDismissChecks = vi.fn(async () => {});
  render(
    <GoalRail
      goal={goal}
      outcome={null}
      turns={[]}
      enabled
      busy={false}
      skills={[]}
      onSet={async () => true}
      onPause={async () => {}}
      onResume={async () => {}}
      onContinue={async () => {}}
      onClear={async () => {}}
      onConfirmChecks={onConfirmChecks}
      onDismissChecks={onDismissChecks}
      onSelectEvidence={() => {}}
      {...props}
    />,
  );
  return { onConfirmChecks, onDismissChecks };
}

afterEach(cleanup);

describe("GoalRail checks and reviews", () => {
  it("asks the user to use or dismiss the agent's proposed checks", () => {
    const { onConfirmChecks, onDismissChecks } = renderRail();
    const proposal = screen.getByRole("region", { name: "Proposed checks" });
    expect(within(proposal).getByText("The agent proposed 2 checks")).toBeTruthy();
    expect(within(proposal).getByText("python -m pytest -q")).toBeTruthy();

    fireEvent.click(within(proposal).getByRole("button", { name: "Use these checks" }));
    expect(onConfirmChecks).toHaveBeenCalledWith(goal.proposedChecks);
    fireEvent.click(within(proposal).getByRole("button", { name: "Dismiss" }));
    expect(onDismissChecks).toHaveBeenCalled();
  });

  it("shows the latest review's verdict on the card and its table when opened", () => {
    renderRail({
      goal: { ...goal, proposedChecks: [] },
      reviews: [review({ result: "passed", recordedAt: "2026-11-02T01:01:00Z" }), review()],
    });
    expect(screen.queryByRole("region", { name: "Proposed checks" })).toBeNull();
    expect(screen.getByText("Completion claim turned back")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: /Fix the cart/ }));
    const latest = screen.getByRole("region", { name: "Latest completion check" });
    expect(within(latest).getByText("1 of 2 requirements proved")).toBeTruthy();
    const row = within(latest).getByText("Zero stock removes the item").closest("tr");
    expect(row && within(row).getByText("Not met")).toBeTruthy();
    expect(within(latest).getByText("1 earlier check")).toBeTruthy();
  });

  it("keeps a failed check's output with the check", () => {
    renderRail({
      goal: { ...goal, proposedChecks: [] },
      reviews: [
        review({
          stage: "checks",
          requirements: [],
          justification: "DeepCode ran the Goal's 1 confirmed check(s); 1 did not pass:",
          checks: [
            {
              requirement: "Cart tests pass",
              command: ["python", "-m", "pytest", "-q"],
              exitCode: 1,
              problem: "",
              seconds: 0.8,
              output: "1 failed",
            },
          ],
        }),
      ],
    });
    fireEvent.click(screen.getByRole("button", { name: /Fix the cart/ }));
    const latest = screen.getByRole("region", { name: "Latest completion check" });
    expect(within(latest).getByText(/exited 1/)).toBeTruthy();
    expect(within(latest).getByText("1 failed")).toBeTruthy();
    // The rejection text repeats the output, so it is not shown twice.
    expect(within(latest).queryByText(/did not pass/)).toBeNull();
  });
});

describe("mergeGoalReviews", () => {
  it("adds newly published reviews once, oldest first", () => {
    const first = review({ recordedAt: "2026-11-02T01:01:00Z" });
    const second = review({ recordedAt: "2026-11-02T01:02:00Z" });
    expect(mergeGoalReviews([first], [second, first])).toEqual([first, second]);
    expect(mergeGoalReviews([second], [first])).toEqual([first, second]);
  });
});
