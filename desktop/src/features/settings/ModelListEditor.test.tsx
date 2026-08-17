/**
 * Row identity and capacity fields.
 *
 * The editor used the array index as the React key with uncontrolled
 * capacity inputs, so removing a row left the previous row's DOM in place
 * showing a capacity that belonged to the deleted model — and saving wrote
 * it onto the surviving one.
 */

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { useState } from "react";
import { afterEach, expect, test } from "vitest";

import { ModelListEditor } from "./ModelListEditor";
import type { ManualModelEntry } from "../../generated/app-server";

afterEach(cleanup);

function Harness() {
  const [entries, setEntries] = useState<ManualModelEntry[]>([
    { id: "alpha", contextWindow: 111000 },
    { id: "beta", contextWindow: 222000 },
  ]);
  return <ModelListEditor entries={entries} onChange={setEntries} />;
}

const caps = () =>
  (screen.getAllByPlaceholderText("Inherit · e.g. 128K or 1M") as HTMLInputElement[]).map(
    (i) => i.value,
  );
const ids = () =>
  (screen.getAllByPlaceholderText("provider/model-id") as HTMLInputElement[]).map(
    (i) => i.value,
  );

test("a removed row takes its capacities with it", () => {
  render(<Harness />);
  screen.getAllByText("Capacities").forEach((s) => fireEvent.click(s));
  expect(caps()).toEqual(["111K", "", "222K", ""]);
  fireEvent.click(screen.getByRole("button", { name: /Remove model alpha/ }));
  expect(ids()).toEqual(["beta"]);
  expect(caps()).toEqual(["222K", ""]);
});

test("editing an id keeps the row's own capacity", () => {
  render(<Harness />);
  screen.getAllByText("Capacities").forEach((s) => fireEvent.click(s));
  const idInputs = screen.getAllByPlaceholderText(
    "provider/model-id",
  ) as HTMLInputElement[];
  fireEvent.change(idInputs[0], { target: { value: "alpha-2" } });
  expect(ids()).toEqual(["alpha-2", "beta"]);
  expect(caps()).toEqual(["111K", "", "222K", ""]);
});

test("unparseable capacity text stays visible without corrupting the value", () => {
  render(<Harness />);
  screen.getAllByText("Capacities").forEach((s) => fireEvent.click(s));
  const first = (screen.getAllByPlaceholderText("Inherit · e.g. 128K or 1M") as HTMLInputElement[])[0];
  fireEvent.change(first, { target: { value: "12KB" } });
  expect(first.value).toBe("12KB");
  fireEvent.change(first, { target: { value: "128K" } });
  expect(first.value).toBe("128K");
});
