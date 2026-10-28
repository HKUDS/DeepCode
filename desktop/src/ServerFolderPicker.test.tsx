import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { initI18n } from "./app/i18n";
import { type FolderListing, ServerFolderPicker } from "./ServerFolderPicker";

initI18n();

const tree: Record<string, FolderListing> = {
  "/home/me": {
    path: "/home/me",
    parent: "/home",
    home: "/home/me",
    folders: [
      { name: "code", path: "/home/me/code" },
      { name: "notes", path: "/home/me/notes" },
    ],
    truncated: false,
  },
  "/home/me/code": {
    path: "/home/me/code",
    parent: "/home/me",
    home: "/home/me",
    folders: [],
    truncated: false,
  },
};

function fakeService() {
  return vi.fn(async (path?: string) => {
    const listing = tree[path ?? "/home/me"];
    if (!listing) throw new Error("folder does not exist");
    return listing;
  });
}

describe("ServerFolderPicker", () => {
  afterEach(() => {
    cleanup();
    vi.useRealTimers();
  });

  it("opens at the home folder and walks into and out of folders", async () => {
    const list = fakeService();
    const onClose = vi.fn();
    render(<ServerFolderPicker listDirectories={list} onClose={onClose} />);

    fireEvent.click(await screen.findByRole("button", { name: "code" }));
    expect(await screen.findByText("No subfolders")).toBeTruthy();
    expect(
      (screen.getByLabelText("Project folder") as HTMLInputElement).value,
    ).toBe("/home/me/code");

    fireEvent.click(screen.getByRole("button", { name: "Up one level" }));
    expect(await screen.findByRole("button", { name: "notes" })).toBeTruthy();
    expect(list).toHaveBeenLastCalledWith("/home/me");

    fireEvent.click(screen.getByRole("button", { name: "Open project" }));
    expect(onClose).toHaveBeenCalledWith("/home/me");
  });

  it("lists a typed path once typing pauses and keeps an unknown one", async () => {
    const list = fakeService();
    const onClose = vi.fn();
    render(<ServerFolderPicker listDirectories={list} onClose={onClose} />);
    await screen.findByRole("button", { name: "code" });

    vi.useFakeTimers();
    const input = screen.getByLabelText("Project folder");
    fireEvent.change(input, { target: { value: "/nowhere" } });
    expect(list).toHaveBeenCalledTimes(1);
    await act(async () => {
      vi.advanceTimersByTime(400);
    });
    vi.useRealTimers();
    await waitFor(() => expect(list).toHaveBeenLastCalledWith("/nowhere"));
    expect(
      await screen.findByText(
        "This folder can't be listed. You can still type its path.",
      ),
    ).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "Open project" }));
    expect(onClose).toHaveBeenCalledWith("/nowhere");
  });

  it("cancels without a path", async () => {
    const onClose = vi.fn();
    render(
      <ServerFolderPicker listDirectories={fakeService()} onClose={onClose} />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(onClose).toHaveBeenCalledWith(null);
  });
});
