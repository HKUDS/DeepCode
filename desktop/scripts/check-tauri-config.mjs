import { readFile } from "node:fs/promises";
import { resolve } from "node:path";
import process from "node:process";
import { fileURLToPath } from "node:url";

const desktopRoot = resolve(fileURLToPath(new URL("..", import.meta.url)));
const tauriRoot = resolve(desktopRoot, "src-tauri");

async function readJson(path) {
  return JSON.parse(await readFile(path, "utf8"));
}

function isRecord(value) {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function fail(message) {
  throw new Error(`Invalid Tauri configuration: ${message}`);
}

function mergeConfig(base, overlay) {
  if (!isRecord(base) || !isRecord(overlay)) return overlay;
  const merged = { ...base };
  for (const [key, value] of Object.entries(overlay)) {
    merged[key] =
      isRecord(value) && isRecord(merged[key])
        ? mergeConfig(merged[key], value)
        : value;
  }
  return merged;
}

const overlays = [];
let requireUpdater = false;
for (let index = 2; index < process.argv.length; index += 1) {
  const argument = process.argv[index];
  if (argument === "--config") {
    const value = process.argv[index + 1];
    if (!value) fail("--config requires a JSON file");
    overlays.push(resolve(process.cwd(), value));
    index += 1;
  } else if (argument === "--require-updater") {
    requireUpdater = true;
  } else {
    fail(`unknown argument ${argument}`);
  }
}

const [baseConfig, capability, ...overlayConfigs] = await Promise.all([
  readJson(resolve(tauriRoot, "tauri.conf.json")),
  readJson(resolve(tauriRoot, "capabilities", "main.json")),
  ...overlays.map(readJson),
]);
const config = overlayConfigs.reduce(mergeConfig, baseConfig);

const updater = config.plugins?.updater;
if (!isRecord(updater)) {
  fail(
    "plugins.updater must be an object; the updater plugin panics at startup when it receives null",
  );
}
if (typeof updater.pubkey !== "string") {
  fail("plugins.updater.pubkey must be a string");
}
if (!Array.isArray(updater.endpoints)) {
  fail("plugins.updater.endpoints must be an array");
}
for (const endpoint of updater.endpoints) {
  let parsed;
  try {
    parsed = new URL(endpoint);
  } catch {
    fail("updater endpoints must be absolute URLs");
  }
  if (
    parsed.protocol !== "https:" ||
    parsed.username ||
    parsed.password ||
    parsed.hash
  ) {
    fail(
      "updater endpoints must use HTTPS without credentials or URL fragments",
    );
  }
}
if (requireUpdater) {
  if (updater.pubkey.trim().length < 32) {
    fail("release updater public key is missing or invalid");
  }
  if (updater.endpoints.length === 0) {
    fail("release updater endpoints must not be empty");
  }
}
for (const unsafeFlag of [
  "dangerousInsecureTransportProtocol",
  "dangerousAcceptInvalidCerts",
  "dangerousAcceptInvalidHostnames",
]) {
  if (updater[unsafeFlag] === true) {
    fail(`plugins.updater.${unsafeFlag} must not be enabled`);
  }
}

const permissions = new Set(capability.permissions ?? []);
for (const required of [
  "updater:allow-check",
  "updater:allow-download-and-install",
  "process:allow-restart",
]) {
  if (!permissions.has(required)) {
    fail(`main capability is missing ${required}`);
  }
}

console.log(
  requireUpdater
    ? "Merged release updater configuration is structurally valid."
    : "Tauri plugin configuration is structurally valid.",
);
