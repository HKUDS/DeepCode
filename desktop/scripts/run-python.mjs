import { spawnSync } from "node:child_process";
import process from "node:process";

const [, , script, ...scriptArgs] = process.argv;
if (!script) {
  console.error("usage: node scripts/run-python.mjs <script.py> [args...]");
  process.exit(2);
}

const configured = process.env.DEEPCODE_PYTHON?.trim();
const candidates = configured
  ? [{ command: configured, prefix: [] }]
  : process.platform === "win32"
    ? [
        { command: "py", prefix: ["-3.12"] },
        { command: "python", prefix: [] },
        { command: "python3", prefix: [] },
      ]
    : [
        { command: "python3", prefix: [] },
        { command: "python", prefix: [] },
      ];

for (const candidate of candidates) {
  const probe = spawnSync(candidate.command, [...candidate.prefix, "--version"], {
    encoding: "utf8",
    windowsHide: true,
  });
  if (probe.error || probe.status !== 0) continue;
  const result = spawnSync(
    candidate.command,
    [...candidate.prefix, script, ...scriptArgs],
    {
      stdio: "inherit",
      windowsHide: true,
    },
  );
  if (result.error) {
    console.error(result.error.message);
    process.exit(1);
  }
  process.exit(result.status ?? 1);
}

console.error(
  "No usable Python interpreter was found. Set DEEPCODE_PYTHON to an executable path.",
);
process.exit(1);
