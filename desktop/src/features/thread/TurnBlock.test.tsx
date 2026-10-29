import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { initI18n } from "../../app/i18n";
import type { Item, Turn } from "../../generated/app-server";
import { buildConversationTurns } from "./conversationModel";
import type { TranscriptMode } from "./transcriptMode";
import { TurnBlock } from "./TurnBlock";

initI18n();

const turn: Turn = {
  id: "turn-1",
  threadId: "thread-1",
  ordinal: 1,
  prompt: "Inspect and repair",
  status: "completed",
  stopReason: "completed",
  errorCode: null,
  errorMessage: null,
  startedAt: "2026-07-23T01:00:00Z",
  completedAt: "2026-07-23T01:00:10Z",
};

function item(
  id: string,
  ordinal: number,
  kind: Item["kind"],
  summary: string,
  payload: Item["payload"],
): Item {
  return {
    id,
    threadId: turn.threadId,
    turnId: turn.id,
    ordinal,
    kind,
    status: "completed",
    summary,
    payload,
    createdAt: "2026-07-23T01:00:01Z",
    updatedAt: "2026-07-23T01:00:02Z",
  };
}

interface TurnView {
  transcriptMode?: TranscriptMode;
  status?: Turn["status"];
  selectedItemId?: string | null;
  following?: boolean;
}

function turnBlock(items: Item[], view: TurnView = {}) {
  const [group] = buildConversationTurns(
    [{ ...turn, status: view.status ?? turn.status }],
    items,
  );
  return (
    <TurnBlock
      group={group}
      approvalsByItem={new Map()}
      selectedItemId={view.selectedItemId ?? null}
      transcriptMode={view.transcriptMode ?? "normal"}
      following={view.following ?? true}
      busy={false}
      onSelectItem={vi.fn()}
      onOpenInspector={vi.fn()}
      onRespondToApproval={vi.fn()}
      onRetryTurn={vi.fn()}
      onCancelQueuedTurn={vi.fn()}
    />
  );
}

function renderTurn(items: Item[], view: TurnView = {}) {
  const result = render(turnBlock(items, view));
  return {
    ...result,
    update: (next: Item[], nextView: TurnView = view) =>
      result.rerender(turnBlock(next, nextView)),
  };
}

describe("TurnBlock", () => {
  afterEach(cleanup);

  it("renders commentary, tools, and the final answer in ordinal order", () => {
    const items = [
      item("user", 1, "user_message", turn.prompt, { text: turn.prompt }),
      item("commentary", 2, "assistant_message", "Progress note", {
        text: "Progress note",
        phase: "commentary",
      }),
      item("read", 3, "command_execution", "Read configuration", {
        activity: {
          kind: "read",
          label: "Read",
          subject: "deepcode.toml",
        },
      }),
      item("final", 4, "assistant_message", "Repair complete", {
        text: "Repair complete",
        phase: "final_answer",
      }),
      item("completion", 5, "completion", "Turn complete", {
        stopReason: "completed",
      }),
    ];
    renderTurn(items);

    const commentary = screen.getByText("Progress note");
    const activity = screen.getByText("deepcode.toml");
    const finalAnswer = screen.getByText("Repair complete");
    expect(
      commentary.compareDocumentPosition(activity) &
        Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
    expect(
      activity.compareDocumentPosition(finalAnswer) &
        Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
  });

  it("shows unsaved updates as a warning, not as a failed step", () => {
    const notice = "Some updates in this turn could not be saved.";
    const { container } = renderTurn([
      item("user", 1, "user_message", turn.prompt, { text: turn.prompt }),
      item("final", 2, "assistant_message", "Repair complete", {
        text: "Repair complete",
        phase: "final_answer",
      }),
      item("notice", 3, "error", notice, {
        code: "UPDATES_NOT_SAVED",
        message: notice,
        severity: "warning",
      }),
      item("completion", 4, "completion", "Turn complete", {
        stopReason: "completed",
      }),
    ]);

    const row = container.querySelector('[data-warning="true"]');
    expect(row?.textContent).toContain(notice);
    expect(row?.textContent).toContain("Warning");
    expect(screen.queryByText("Execution error")).toBeNull();
    expect(row?.textContent).not.toMatch(/completed|failed/);
  });

  const run = (id: string, ordinal: number, command: string) =>
    item(id, ordinal, "command_execution", command, {
      activity: { kind: "run", label: "Run", subject: command },
      resultPreview: `${command} output`,
    });

  it("folds a run of steps into one line and renders them only when opened", async () => {
    renderTurn([
      item("user", 1, "user_message", turn.prompt, { text: turn.prompt }),
      run("lint", 2, "npm run lint"),
      { ...run("test", 3, "npm test"), status: "failed" },
      item("edit", 4, "file_change", "Edited parse.py", {}),
      item("final", 5, "assistant_message", "Done", { text: "Done", phase: "final_answer" }),
    ]);

    const group = screen.getByText("Steps").closest("details")!;
    expect(group.open).toBe(false);
    expect(group.textContent).toContain("1s");
    expect(group.textContent).toContain("Commands 2");
    expect(group.textContent).toContain("Edits 1");
    expect(group.textContent).toContain("Failed 1");
    expect(screen.queryByText("npm run lint")).toBeNull();

    fireEvent.click(group.querySelector("summary")!);
    expect(group.open).toBe(true);
    expect(await screen.findByText("npm run lint")).toBeTruthy();
    expect(screen.getByText("Edited parse.py")).toBeTruthy();
    expect(screen.getByText("Done")).toBeTruthy();
  });

  const note = (id: string, ordinal: number, text: string) =>
    item(id, ordinal, "assistant_message", text, { text, phase: "commentary" });

  it("keeps the run at work open while the Turn is live, and folds it once the agent moves on", () => {
    const steps = [run("lint", 1, "npm run lint"), run("test", 2, "npm test")];
    const { update } = renderTurn(
      [steps[0], { ...steps[1], status: "in_progress" }],
      { status: "running" },
    );
    expect(screen.getByText("Now: npm test").closest("details")?.open).toBe(true);
    expect(screen.getByText("npm run lint")).toBeTruthy();

    // Between steps it is still at work.
    update(steps);
    expect(screen.getByText("Working").closest("details")?.open).toBe(true);

    update([...steps, note("note", 3, "Lint and tests pass")]);
    expect(screen.getByText("Steps").closest("details")?.open).toBe(false);
  });

  it("names a live run's thinking in the user's words", () => {
    renderTurn(
      [
        run("lint", 1, "npm run lint"),
        run("test", 2, "npm test"),
        { ...item("think", 3, "reasoning_summary", "Thinking", {}), status: "in_progress" },
      ],
      { status: "running" },
    );
    expect(screen.getByText("Now: Thinking")).toBeTruthy();
  });

  it("keeps the user's own open or close over the default", () => {
    const steps = [run("lint", 1, "npm run lint"), run("test", 2, "npm test")];
    const { update } = renderTurn(steps, { status: "running" });
    const group = screen.getByText("Working").closest("details")!;

    fireEvent.click(group.querySelector("summary")!);
    update([...steps, run("build", 3, "npm run build")]);
    expect(group.open).toBe(false);

    fireEvent.click(group.querySelector("summary")!);
    update([...steps, run("build", 3, "npm run build"), note("note", 4, "Built")]);
    expect(group.open).toBe(true);
    expect(screen.getByText("npm run build")).toBeTruthy();
  });

  it("shows a live run's latest steps, and every step on request", () => {
    const steps = Array.from({ length: 25 }, (_, index) =>
      run(`step-${index}`, index + 1, `command ${index}`),
    );
    renderTurn(steps, { status: "running" });
    expect(screen.queryByText("command 4")).toBeNull();
    expect(screen.getByText("command 5")).toBeTruthy();
    expect(screen.getByText("command 24")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "Show all 25 steps" }));
    expect(screen.getByText("command 0")).toBeTruthy();
    expect(screen.queryByRole("button", { name: /Show all/ })).toBeNull();
  });

  it("keeps a finished run open for a reader who is up in it", () => {
    const steps = [run("lint", 1, "npm run lint"), run("test", 2, "npm test")];
    const { update } = renderTurn(steps, { status: "running", following: false });

    update([...steps, note("note", 3, "Lint and tests pass")]);
    expect(screen.getByText("Steps").closest("details")?.open).toBe(true);
    expect(screen.getByText("npm test")).toBeTruthy();
  });

  it("does not open a group because one of its steps is selected", () => {
    renderTurn([run("lint", 1, "npm run lint"), run("test", 2, "npm test")], {
      selectedItemId: "test",
    });
    expect(screen.getByText("Steps").closest("details")?.open).toBe(false);
  });

  it("starts every group in a newly chosen transcript mode's state", () => {
    const steps = [run("lint", 1, "npm run lint"), run("test", 2, "npm test")];
    const { update } = renderTurn(steps);
    expect(screen.getByText("Steps").closest("details")?.open).toBe(false);

    update(steps, { transcriptMode: "verbose" });
    expect(screen.getByText("Steps").closest("details")?.open).toBe(true);
  });

  it("opens every group in the verbose transcript", () => {
    renderTurn([run("lint", 1, "npm run lint"), run("test", 2, "npm test")], {
      transcriptMode: "verbose",
    });

    expect(screen.getByText("npm test").closest("details[open]")).not.toBeNull();
  });

  it("keeps a group's changes, not its steps, in the summary transcript", () => {
    renderTurn(
      [
        run("lint", 1, "npm run lint"),
        item("edit", 2, "file_change", "Edited parse.py", {}),
        run("test", 3, "npm test"),
      ],
      { transcriptMode: "summary" },
    );

    expect(screen.getByText("Edited parse.py")).toBeTruthy();
    expect(screen.queryByText("npm run lint")).toBeNull();
    expect(screen.queryByText("Commands 2")).toBeNull();
  });

  const instruction = "Continue working toward the active Thread Goal.";

  function goalNote(label: string) {
    return screen.getByText(label).closest("details");
  }

  it("shows an automatic Goal continuation as a note, not as the user's message", () => {
    const steer = "Also keep the public API unchanged.";
    renderTurn([
      item("goal", 1, "user_message", instruction, {
        text: instruction,
        source: "goal_continuation",
        client: "internal",
      }),
      item("steer", 2, "user_message", steer, {
        text: steer,
        source: "steer",
        client: "web",
      }),
    ]);

    const note = goalNote("Goal continued automatically");
    expect(note?.closest("article")?.dataset.goalPrompt).toBe("automatic");
    expect(note?.open).toBe(false);
    expect(note?.textContent).toContain(instruction);
    // What the user typed stays a message of their own.
    const message = screen.getByText(steer).closest("article");
    expect(message).not.toBeNull();
    expect(message?.dataset.goalPrompt).toBeUndefined();
    expect(screen.getByText(steer).closest("details")).toBeNull();
  });

  it("does not call a Goal Turn the user started automatic", () => {
    renderTurn([
      item("goal", 1, "user_message", instruction, {
        text: instruction,
        source: "goal_continuation",
        client: "desktop",
      }),
    ]);

    expect(goalNote("Working toward the Goal")?.textContent).toContain(instruction);
    expect(screen.queryByText("Goal continued automatically")).toBeNull();
  });

  it("keeps a retried Goal instruction a note, and a retried prompt a message", () => {
    const prompt = "Fix the parser";
    renderTurn([
      item("goal", 1, "user_message", instruction, {
        text: instruction,
        source: "retry",
        client: "internal",
        retryOf: { turnId: "turn-0", source: "goal_continuation", client: "internal" },
      }),
      item("prompt", 2, "user_message", prompt, {
        text: prompt,
        source: "retry",
        client: "internal",
        retryOf: { turnId: "turn-0", source: "start", client: "web" },
      }),
    ]);

    expect(goalNote("Working toward the Goal")?.textContent).toContain(instruction);
    const retried = screen.getByText(prompt).closest("article");
    expect(retried).not.toBeNull();
    expect(retried?.dataset.goalPrompt).toBeUndefined();
  });

  it("unfolds the Goal instruction in the verbose transcript", () => {
    renderTurn(
      [
        item("goal", 1, "user_message", instruction, {
          text: instruction,
          source: "goal_continuation",
          client: "internal",
        }),
      ],
      { transcriptMode: "verbose" },
    );
    expect(goalNote("Goal continued automatically")?.open).toBe(true);
  });
});
