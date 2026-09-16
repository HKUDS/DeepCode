import { test, expect } from "@playwright/test";
import {
  execFileSync,
  spawn,
  spawnSync,
  type ChildProcess,
} from "node:child_process";
import {
  existsSync,
  mkdirSync,
  mkdtempSync,
  readFileSync,
  rmSync,
  writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { resolve, join } from "node:path";
import { fileURLToPath } from "node:url";

const repository = fileURLToPath(new URL("../../", import.meta.url));
const python =
  process.env.DEEPCODE_TEST_PYTHON ??
  join(
    repository,
    ".venv",
    process.platform === "win32" ? "Scripts/python.exe" : "bin/python",
  );
const live = process.env.DEEPCODE_WEB_LIVE === "1";
const installed = process.env.DEEPCODE_WEB_PACKAGE_DIR;
const standalone = process.env.DEEPCODE_WEB_BINARY;
let root: string,
  workspace: string,
  environment: NodeJS.ProcessEnv,
  processHandle: ChildProcess;
const cwd = () => (installed ? root : repository);
const command = (...args: string[]) =>
  execFileSync(python, args, {
    cwd: cwd(),
    env: environment,
    encoding: "utf8",
    timeout: 40000,
  });
function link() {
  if (standalone)
    return JSON.parse(
      execFileSync(
        standalone,
        [
          "--web",
          "--database",
          join(root, "state.sqlite3"),
          "--port",
          "0",
          "--no-open",
          "--json",
        ],
        { cwd: root, env: environment, encoding: "utf8", timeout: 40000 },
      ),
    ).url as string;
  return JSON.parse(
    command(
      "-m",
      "deepcode",
      "web",
      "--database",
      join(root, "state.sqlite3"),
      "--no-open",
      "--json",
    ),
  ).url as string;
}

test.beforeAll(async () => {
  root = mkdtempSync(join(tmpdir(), "deepcode-web-e2e-"));
  workspace = join(root, "workspace");
  mkdirSync(workspace);
  mkdirSync(join(root, "home"));
  writeFileSync(join(workspace, "example.py"), "answer = 41\n");
  for (const args of [
    ["init", "-q"],
    ["config", "user.name", "Web test"],
    ["config", "user.email", "web-test@example.invalid"],
    ["add", "."],
    ["commit", "-qm", "fixture"],
  ])
    execFileSync("git", args, { cwd: workspace });
  writeFileSync(join(workspace, "example.py"), "answer = 42\n");
  if (live)
    execFileSync(
      python,
      [
        "-m",
        "tests.app_server.web_worker",
        join(root, "home", "deepcode_config.json"),
        "--prepare-live-config",
      ],
      { cwd: repository },
    );
  environment = {
    ...process.env,
    DEEPCODE_HOME: join(root, "home"),
    DEEPCODE_SESSIONS_DIR: join(root, "home", "sessions"),
    ...(installed ? { PYTHONPATH: installed } : {}),
  };
  for (const key of Object.keys(environment))
    if (/_API_KEY$/.test(key)) delete environment[key];
  if (standalone) link();
  else
    processHandle = spawn(
      python,
      installed
        ? [
            "-m",
            "app_server.service",
            "--database",
            join(root, "state.sqlite3"),
            "--port",
            "0",
          ]
        : [
            "-m",
            "tests.app_server.web_worker",
            root,
            ...(live ? ["--live"] : []),
          ],
      { cwd: cwd(), env: environment, stdio: "ignore" },
    );
  await expect
    .poll(
      () => existsSync(join(root, "state.sqlite3.service", "instance.json")),
      { timeout: 15000 },
    )
    .toBe(true);
});

test.afterAll(() => {
  try {
    command(
      "-m",
      "deepcode",
      "service",
      "stop",
      "--database",
      join(root, "state.sqlite3"),
      "--cancel-running",
      "--timeout",
      "2",
      "--json",
    );
  } finally {
    if (processHandle && processHandle.exitCode === null)
      processHandle.kill("SIGTERM");
    if (root) rmSync(root, { recursive: true, force: true });
  }
});

test("packaged browser: project, approval, reconnect, upload, diff and terminal", async ({
  page,
  context,
}, testInfo) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  page.on("dialog", (dialog) => dialog.accept());
  await page.goto(link());
  await expect(
    page
      .getByRole("button", { name: "Open project folder", exact: true })
      .first(),
  ).toBeEnabled();
  expect(page.url()).not.toContain("ticket=");
  await page
    .getByRole("button", { name: "Open project folder", exact: true })
    .first()
    .click();
  await page.getByLabel("Project folder", { exact: true }).fill(workspace);
  await page.getByRole("button", { name: "Open project", exact: true }).click();
  await page.getByRole("button", { name: "Trust folder", exact: true }).click();
  await expect(
    page.getByText("Trusted", { exact: true }).first(),
  ).toBeVisible();
  // Source-controlled labels make this independent of generated CSS names.
  await page
    .getByRole("button", { name: /New (thread|session|task)/i })
    .first()
    .click();
  const composer = page
    .getByRole("textbox", { name: /message|prompt|instruction/i })
    .first();
  await expect(composer).toBeEnabled();

  const attachment = join(root, "context.txt");
  writeFileSync(
    attachment,
    "Browser upload acceptance: preserve the public API.\n",
  );
  const chooser = page.waitForEvent("filechooser");
  await page.getByRole("button", { name: "Attach workspace files" }).click();
  await (await chooser).setFiles(attachment);
  await expect(page.getByLabel("Attached context files")).toContainText(
    "context.txt",
  );

  if (!installed && !standalone) {
    await composer.fill(
      live
        ? "Work only in this workspace. Create arithmetic.py with triangular(n), sum 1 through n, rejecting negative n. Create test_arithmetic.py with standard-library unittest for 0, 1, 10, 100 and negative input. Use write for these two files, then run exactly python3 -m unittest -v. No dependencies, network, git changes or subagents. Finish after tests pass."
        : "Verify the browser approval flow and finish.",
    );
    await composer.press("Enter");
    if (live) {
      await expect(page.getByLabel("Thread conversation")).toContainText(
        "Create arithmetic.py",
      );
      await page.reload();
      const deadline = Date.now() + 240000;
      while (Date.now() < deadline) {
        const approve = page.getByRole("button", {
          name: "Allow once",
          exact: true,
        });
        if (await approve.count()) {
          const card = approve.first().locator("..").locator("..");
          await card.getByText("Review arguments", { exact: true }).click();
          const args = JSON.parse(await card.locator("pre").innerText());
          const tool = await card.locator("strong").first().innerText();
          if (tool === "write") {
            expect([
              join(workspace, "arithmetic.py"),
              join(workspace, "test_arithmetic.py"),
            ]).toContain(resolve(workspace, args.file_path));
          } else {
            expect(tool).toBe("bash");
            expect(["python3 -m unittest -v", "pwd", "ls"]).toContain(
              args.command.trim(),
            );
          }
          await approve.first().click();
        }
        if (
          existsSync(join(workspace, "test_arithmetic.py")) &&
          (await page
            .getByRole("button", { name: /Stop (turn|task|generation)/i })
            .count()) === 0 &&
          (await approve.count()) === 0
        )
          break;
        await page.waitForTimeout(500);
      }
      expect(existsSync(join(workspace, "arithmetic.py"))).toBe(true);
      await expect(
        page.getByText(/^Worked for|^Work completed/).first(),
      ).toBeVisible();
      const result = spawnSync(python, ["-m", "unittest", "-v"], {
        cwd: workspace,
        encoding: "utf8",
      });
      expect(result.status).toBe(0);
      await testInfo.attach("generated-code", {
        body: readFileSync(join(workspace, "arithmetic.py")),
        contentType: "text/plain",
      });
      await testInfo.attach("independent-tests", {
        body: result.stdout + result.stderr,
        contentType: "text/plain",
      });
    } else {
      await expect(
        page.getByRole("button", { name: "Allow once", exact: true }),
      ).toBeVisible();
      await page.reload();
      await expect(
        page.getByRole("button", { name: "Allow once", exact: true }),
      ).toBeVisible();
      await context.setOffline(true);
      await context.setOffline(false);
      await page
        .getByRole("button", { name: "Allow once", exact: true })
        .click();
      await expect(
        page.getByText("done", { exact: true }).first(),
      ).toBeVisible();
    }
    const admissions = JSON.parse(
      command(
        "-c",
        "import sqlite3,json,sys; c=sqlite3.connect(sys.argv[1]); print(json.dumps(c.execute(\"select count(*) from event_log where type in ('turn.started','turn.queued')\").fetchone()[0]))",
        join(root, "state.sqlite3"),
      ),
    );
    expect(admissions).toBe(1);
    await testInfo.attach("task-admissions", {
      body: JSON.stringify({ admissions }),
      contentType: "application/json",
    });
  }

  await page.reload();
  await expect(
    page.getByText("Trusted", { exact: true }).first(),
  ).toBeVisible();
  await page.getByRole("button", { name: "Settings", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "Service updates" }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Copy configuration path" }),
  ).toBeVisible();
  await page
    .getByRole("combobox", { name: "Default Session access", exact: true })
    .selectOption("read_only");
  await page
    .getByRole("button", { name: "Save safety settings", exact: true })
    .click();
  await expect(page.getByText(/Effective default: Read only/)).toBeVisible();
  await page
    .getByRole("combobox", { name: "Default Session access", exact: true })
    .selectOption("ask");
  await page
    .getByRole("button", { name: "Save safety settings", exact: true })
    .click();
  await expect(page.getByText(/Effective default: Ask/)).toBeVisible();
  await page
    .getByRole("button", { name: "Close settings", exact: true })
    .click();
  await page
    .getByRole("button", { name: /Review/ })
    .first()
    .click();
  await expect(
    page.getByText("example.py", { exact: true }).first(),
  ).toBeVisible();
  await page
    .getByRole("button", { name: "Open in editor", exact: true })
    .first()
    .click();
  const downloaded = page.waitForEvent("download");
  await page.getByRole("button", { name: "Download", exact: true }).click();
  const file = await downloaded;
  expect(readFileSync((await file.path())!, "utf8")).toBe("answer = 42\n");
  await page.getByRole("tab", { name: "terminal", exact: true }).click();
  await page
    .getByRole("button", { name: "Start terminal", exact: true })
    .click();
  await expect(page.getByText(/^PID \d+$/)).toBeVisible();
  const pid = await page.getByText(/^PID \d+$/).innerText();
  const terminal = page.locator(".xterm-helper-textarea");
  await terminal.pressSequentially("printf 'once\\n' >> browser-pty.txt");
  await terminal.press("Enter");
  await expect
    .poll(() => existsSync(join(workspace, "browser-pty.txt")))
    .toBe(true);
  expect(readFileSync(join(workspace, "browser-pty.txt"), "utf8")).toBe(
    "once\n",
  );
  await page.reload();
  await page.getByRole("button", { name: "Review", exact: true }).click();
  await page.getByRole("tab", { name: "terminal", exact: true }).click();
  await expect(page.getByText(pid, { exact: true })).toBeVisible();
  expect(readFileSync(join(workspace, "browser-pty.txt"), "utf8")).toBe(
    "once\n",
  );
  await page.screenshot({
    path: testInfo.outputPath("web-ready.png"),
    fullPage: true,
  });
  expect(errors).toEqual([]);
  await page.getByRole("button", { name: "Sign out", exact: true }).click();
  await expect(page.getByText(/Signed out/).first()).toBeVisible();
});


test("browser access: missing and used links, sign-out, and fresh-link recovery", async ({ page, context }) => {
  const accessLink = link();
  const base = new URL(accessLink).origin;
  await page.goto(base);
  const notice = page.getByRole("alert");
  await expect(notice).toContainText("Browser access required");
  await expect(notice).toContainText("deepcode web");
  await expect(notice).not.toContainText("APP_SERVER_OFFLINE");
  await expect(notice.getByRole("button", { name: "Reconnect" })).toHaveCount(0);
  await expect(notice.locator("span")).toHaveCSS("white-space", "normal");

  await page.goto(accessLink);
  await expect(page.getByRole("button", { name: "Open project folder", exact: true }).first()).toBeEnabled();
  await expect(notice).toHaveCount(0);

  await context.clearCookies();
  await page.goto(accessLink);
  await expect(notice).toContainText("Browser access required");
  await page.goto(link());
  await expect(page.getByRole("button", { name: "Open project folder", exact: true }).first()).toBeEnabled();
  await expect(notice).toHaveCount(0);

  await page.getByRole("button", { name: "Sign out", exact: true }).click();
  await expect(notice).toContainText("Browser access required");
  await expect(notice.getByRole("button", { name: "Reconnect" })).toHaveCount(0);
  await page.goto(link());
  await expect(page.getByRole("button", { name: "Open project folder", exact: true }).first()).toBeEnabled();
  await expect(notice).toHaveCount(0);
});
