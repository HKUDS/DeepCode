import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
} from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import type { ProviderTestResult } from "../../generated/app-server";
import type { ConnectionCatalogController } from "./useConnectionCatalog";
import { ConnectionProbe } from "./ConnectionProbe";

afterEach(cleanup);

test("unsaved probe uses current form and hides a result after edits", async () => {
  let complete!: (value: ProviderTestResult) => void;
  const probe = vi.fn(
    () =>
      new Promise<ProviderTestResult>((resolve) => {
        complete = resolve;
      }),
  );
  const controller = { test: probe } as unknown as ConnectionCatalogController;
  const connection = {
    id: "local",
    protocol: "openai_responses" as const,
    apiBase: "http://localhost:1234/v1",
  };
  const { rerender } = render(
    <ConnectionProbe connection={connection} controller={controller} />,
  );
  fireEvent.change(screen.getByRole("textbox"), { target: { value: "model" } });
  fireEvent.click(
    screen.getByRole("button", { name: "Verify Agent compatibility" }),
  );
  expect(probe).toHaveBeenCalledWith("local", "model", {
    connection,
    mode: "agent",
  });
  rerender(
    <ConnectionProbe
      connection={{ ...connection, apiBase: "http://localhost:4321/v1" }}
      controller={controller}
    />,
  );
  await act(async () =>
    complete({
      connectionId: "local",
      status: "ready",
      ok: true,
      latencyMs: 1,
      modelCount: 1,
      error: null,
      stages: [
        {
          id: "credential",
          status: "passed",
          detail: "Credential",
          latencyMs: 0,
          modelCount: null,
          modelId: null,
        },
        {
          id: "catalog",
          status: "passed",
          detail: "Catalog",
          latencyMs: 0,
          modelCount: 1,
          modelId: null,
        },
        {
          id: "model",
          status: "passed",
          detail: "Model",
          latencyMs: 1,
          modelCount: null,
          modelId: "model",
        },
      ],
    }),
  );
  expect(screen.queryByText("Model request verified")).toBeNull();
  expect(
    screen.getByText(
      "Settings changed. Verify again to check this configuration.",
    ),
  ).toBeTruthy();
});
