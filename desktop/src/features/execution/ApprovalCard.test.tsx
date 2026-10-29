import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";

import { __setLocaleForTests, initI18n } from "../../app/i18n";
import type { Approval } from "../../generated/app-server";
import { ApprovalCard } from "./ApprovalCard";

function approval(request: Approval["request"]): Approval {
  return {
    id: "apr-1",
    threadId: "thread-1",
    turnId: "turn-1",
    itemId: "item-1",
    category: "command",
    status: "pending",
    request,
    decision: null,
    requestedAt: "2026-10-29T01:00:00Z",
    resolvedAt: null,
  };
}

describe("ApprovalCard", () => {
  beforeAll(() => {
    initI18n();
    __setLocaleForTests("en");
  });
  afterEach(cleanup);

  it("names what allowing for the session covers", () => {
    render(
      <ApprovalCard
        approval={approval({
          toolName: "bash",
          arguments: { command: "git status" },
          reason: "Running a command needs your approval.",
          sessionScope: {
            keys: ["cmd:git status"],
            target: "`git status …`",
            label: "Always allow `git status …` this session",
          },
        })}
        busy={false}
        onRespond={vi.fn()}
      />,
    );
    expect(
      screen.getByRole("button", { name: "Always allow `git status …` this session" }),
    ).toBeTruthy();
    expect(screen.getByText("Running a command needs your approval.")).toBeTruthy();
  });

  it("names the scope in Chinese too", () => {
    __setLocaleForTests("zh-CN");
    render(
      <ApprovalCard
        approval={approval({
          toolName: "write",
          sessionScope: { keys: ["edits:/ws"], target: "edits in this workspace", label: "" },
        })}
        busy={false}
        onRespond={vi.fn()}
      />,
    );
    expect(
      screen.getByRole("button", { name: "本次会话内始终允许 edits in this workspace" }),
    ).toBeTruthy();
    __setLocaleForTests("en");
  });

  it("keeps the generic label for a request without a scope", () => {
    render(
      <ApprovalCard
        approval={approval({ toolName: "bash", arguments: { command: "ls" } })}
        busy={false}
        onRespond={vi.fn()}
      />,
    );
    expect(screen.getByRole("button", { name: "Allow for Session" })).toBeTruthy();
  });
});
