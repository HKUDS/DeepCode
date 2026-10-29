import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { useRef, useState } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { __resetEscapeLayersForTests } from "../app/escapeLayer";
import { initI18n } from "../app/i18n";
import { confirmAction } from "../platform/confirmAction";
import { ConfirmHost } from "./ConfirmHost";
import { Dialog, type DialogProps } from "./Dialog";
import { __resetDialogsForTests } from "./dialogStack";

initI18n();

afterEach(() => {
  cleanup();
  __resetEscapeLayersForTests();
  __resetDialogsForTests();
});

function Harness({
  dialogProps = {},
  nested = false,
}: {
  dialogProps?: Partial<DialogProps>;
  nested?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [innerOpen, setInnerOpen] = useState(false);
  return (
    <>
      <button type="button" onClick={() => setOpen(true)}>
        Open
      </button>
      <button type="button">Behind</button>
      {open ? (
        <Dialog
          labelledBy="harness-title"
          backdropClassName="backdrop"
          onClose={() => setOpen(false)}
          {...dialogProps}
        >
          <h2 id="harness-title">Harness</h2>
          <button type="button">First</button>
          <input aria-label="Middle" />
          <button type="button" onClick={() => setInnerOpen(true)}>
            Last
          </button>
          {nested && innerOpen ? (
            <Dialog
              label="Inner"
              backdropClassName="inner-backdrop"
              onClose={() => setInnerOpen(false)}
            >
              <button type="button">Inner action</button>
            </Dialog>
          ) : null}
        </Dialog>
      ) : null}
    </>
  );
}

function openHarness() {
  const opener = screen.getByRole("button", { name: "Open" });
  opener.focus();
  fireEvent.click(opener);
  return opener;
}

describe("Dialog", () => {
  it("is a labelled modal dialog that takes focus when it opens", () => {
    render(<Harness />);
    openHarness();

    const dialog = screen.getByRole("dialog", { name: "Harness" });
    expect(dialog.getAttribute("aria-modal")).toBe("true");
    expect(document.activeElement).toBe(
      screen.getByRole("button", { name: "First" }),
    );
  });

  it("keeps Tab and Shift+Tab inside the dialog", () => {
    render(<Harness />);
    openHarness();
    const first = screen.getByRole("button", { name: "First" });
    const last = screen.getByRole("button", { name: "Last" });

    last.focus();
    fireEvent.keyDown(last, { key: "Tab" });
    expect(document.activeElement).toBe(first);

    fireEvent.keyDown(first, { key: "Tab", shiftKey: true });
    expect(document.activeElement).toBe(last);

    // Focus that lands behind the dialog is pulled back in.
    screen.getByRole("button", { name: "Behind" }).focus();
    expect(document.activeElement).toBe(first);
  });

  it("closes on Escape and gives focus back to the opener", () => {
    render(<Harness />);
    const opener = openHarness();

    fireEvent.keyDown(document.activeElement!, { key: "Escape" });

    expect(screen.queryByRole("dialog")).toBeNull();
    expect(document.activeElement).toBe(opener);
  });

  it("ignores Escape while busy and while an IME is composing", () => {
    const onClose = vi.fn();
    const { rerender } = render(
      <Dialog label="Busy" busy onClose={onClose}>
        <button type="button">Wait</button>
      </Dialog>,
    );
    fireEvent.keyDown(screen.getByRole("button"), { key: "Escape" });
    expect(onClose).not.toHaveBeenCalled();

    rerender(
      <Dialog label="Busy" onClose={onClose}>
        <button type="button">Wait</button>
      </Dialog>,
    );
    fireEvent.keyDown(screen.getByRole("button"), {
      key: "Escape",
      isComposing: true,
    });
    expect(onClose).not.toHaveBeenCalled();
    fireEvent.keyDown(screen.getByRole("button"), { key: "Escape" });
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("closes the innermost dialog first and traps focus there", () => {
    render(<Harness nested />);
    openHarness();
    const last = screen.getByRole("button", { name: "Last" });
    last.focus();
    fireEvent.click(last);

    const inner = screen.getByRole("button", { name: "Inner action" });
    expect(document.activeElement).toBe(inner);
    fireEvent.keyDown(inner, { key: "Tab" });
    expect(document.activeElement).toBe(inner);

    fireEvent.keyDown(inner, { key: "Escape" });
    expect(screen.queryByRole("dialog", { name: "Inner" })).toBeNull();
    expect(screen.getByRole("dialog", { name: "Harness" })).toBeTruthy();
    expect(document.activeElement).toBe(last);
  });

  it("hands focus to the dialog underneath when the opener cannot take it", async () => {
    function SaveThenConfirm() {
      const [saved, setSaved] = useState(false);
      const [asking, setAsking] = useState(false);
      return (
        <Dialog label="Settings" backdropClassName="backdrop" onClose={() => {}}>
          <select aria-label="Scope" />
          <button type="button" disabled={saved} onClick={() => setAsking(true)}>
            Save
          </button>
          {asking ? (
            <Dialog
              label="Confirm"
              backdropClassName="inner"
              onClose={() => {
                setAsking(false);
                setSaved(true);
              }}
            >
              <button type="button">Cancel</button>
            </Dialog>
          ) : null}
        </Dialog>
      );
    }
    render(<SaveThenConfirm />);
    const save = screen.getByRole("button", { name: "Save" });
    save.focus();
    fireEvent.click(save);
    fireEvent.keyDown(screen.getByRole("button", { name: "Cancel" }), {
      key: "Escape",
    });
    // Save is disabled now; focus lands back inside Settings, not on body.
    await waitFor(() =>
      expect(document.activeElement).toBe(
        screen.getByRole("combobox", { name: "Scope" }),
      ),
    );
  });

  it("closes from the backdrop only when allowed", () => {
    const { container, unmount } = render(<Harness />);
    openHarness();
    fireEvent.mouseDown(container.ownerDocument.querySelector(".backdrop")!);
    expect(screen.getByRole("dialog")).toBeTruthy();
    // A press inside the panel never counts as the backdrop.
    fireEvent.mouseDown(screen.getByRole("button", { name: "First" }));
    expect(screen.getByRole("dialog")).toBeTruthy();
    unmount();
    __resetEscapeLayersForTests();
    __resetDialogsForTests();

    render(<Harness dialogProps={{ closeOnBackdrop: true }} />);
    openHarness();
    fireEvent.mouseDown(document.querySelector(".backdrop")!);
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("closes an anchored popover on a press outside its root", () => {
    function Popover() {
      const rootRef = useRef<HTMLDivElement | null>(null);
      const [open, setOpen] = useState(true);
      return (
        <>
          <div ref={rootRef}>
            <button type="button">Trigger</button>
            {open ? (
              <Dialog
                label="Popover"
                dismissOnPressOutside={rootRef}
                onClose={() => setOpen(false)}
              >
                <button type="button">Inside</button>
              </Dialog>
            ) : null}
          </div>
          <p>Elsewhere</p>
        </>
      );
    }
    render(<Popover />);
    const popover = screen.getByRole("dialog", { name: "Popover" });
    expect(popover.getAttribute("aria-modal")).toBeNull();

    fireEvent.mouseDown(screen.getByRole("button", { name: "Trigger" }));
    expect(screen.getByRole("dialog")).toBeTruthy();
    fireEvent.mouseDown(screen.getByText("Elsewhere"));
    expect(screen.queryByRole("dialog")).toBeNull();
  });
});

describe("ConfirmHost", () => {
  it("answers confirmAction with an in-app dialog that defaults to Cancel", async () => {
    const nativeConfirm = vi.spyOn(window, "confirm");
    render(
      <>
        <button type="button">Remove</button>
        <ConfirmHost />
      </>,
    );
    const opener = screen.getByRole("button", { name: "Remove" });
    opener.focus();

    let answer!: Promise<boolean>;
    act(() => {
      answer = confirmAction("Remove the server?", {
        title: "Remove server",
        confirmLabel: "Remove",
        destructive: true,
      });
    });
    const dialog = await screen.findByRole("alertdialog", {
      name: "Remove server",
    });
    expect(dialog.textContent).toContain("Remove the server?");
    expect(document.activeElement?.textContent).toBe("Cancel");

    fireEvent.keyDown(document.activeElement!, { key: "Escape" });
    await expect(answer).resolves.toBe(false);
    await waitFor(() => expect(screen.queryByRole("alertdialog")).toBeNull());
    expect(document.activeElement).toBe(opener);

    act(() => {
      answer = confirmAction("Remove the server?", { confirmLabel: "Remove" });
    });
    const second = await screen.findByRole("alertdialog", {
      name: "Please confirm",
    });
    fireEvent.click(
      second.querySelector("footer button:last-child") as HTMLButtonElement,
    );
    await expect(answer).resolves.toBe(true);
    expect(nativeConfirm).not.toHaveBeenCalled();
  });

  it("gives the native confirm back when it unmounts", async () => {
    const nativeConfirm = vi
      .spyOn(window, "confirm")
      .mockReturnValue(true);
    const { unmount } = render(<ConfirmHost />);
    unmount();
    await expect(confirmAction("Continue?")).resolves.toBe(true);
    expect(nativeConfirm).toHaveBeenCalledWith("Continue?");
  });
});
