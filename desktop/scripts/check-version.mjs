import { readFileSync } from "node:fs";
import process from "node:process";

const packageVersion = JSON.parse(
  readFileSync(new URL("../package.json", import.meta.url), "utf8"),
).version;
const tauriVersion = JSON.parse(
  readFileSync(
    new URL("../src-tauri/tauri.conf.json", import.meta.url),
    "utf8",
  ),
).version;
const cargoManifest = readFileSync(
  new URL("../src-tauri/Cargo.toml", import.meta.url),
  "utf8",
);
const cargoVersion = cargoManifest.match(
  /^\s*version\s*=\s*"([^"]+)"\s*$/m,
)?.[1];

const versions = new Map([
  ["desktop/package.json", packageVersion],
  ["desktop/src-tauri/tauri.conf.json", tauriVersion],
  ["desktop/src-tauri/Cargo.toml", cargoVersion],
]);
const mismatches = [...versions].filter(([, version]) => version !== packageVersion);
if (mismatches.length) {
  const detail = [...versions]
    .map(([file, version]) => `${file}: ${version ?? "missing"}`)
    .join("\n");
  throw new Error(`Desktop version metadata is inconsistent:\n${detail}`);
}

const tagIndex = process.argv.indexOf("--tag");
if (tagIndex !== -1) {
  const tag = process.argv[tagIndex + 1];
  const expected = `desktop-v${packageVersion}`;
  if (tag !== expected) {
    throw new Error(`Release tag must be ${expected}, received ${tag ?? "nothing"}`);
  }
}

console.log(packageVersion);
