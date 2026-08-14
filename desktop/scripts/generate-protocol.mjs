import { readFile, writeFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";

import { compileFromFile } from "json-schema-to-typescript";

const here = dirname(fileURLToPath(import.meta.url));
const schemaPath = resolve(here, "../../protocol/app-server.schema.json");
const outputPath = resolve(here, "../src/generated/app-server.ts");
const banner =
  "/* AUTO-GENERATED from protocol/app-server.schema.json. DO NOT EDIT. */";
const generated = await compileFromFile(schemaPath, {
  bannerComment: banner,
  cwd: dirname(schemaPath),
});

if (process.argv.includes("--check")) {
  const current = await readFile(outputPath, "utf8").catch(() => "");
  if (current !== generated) {
    process.stderr.write(
      "Generated protocol types are stale. Run `npm run generate:protocol`.\n",
    );
    process.exitCode = 1;
  }
} else {
  await writeFile(outputPath, generated, "utf8");
}
