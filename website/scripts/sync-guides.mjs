// Copy ../docs/guide/*.md into src/content/docs/guide/, adding Starlight
// frontmatter and rewriting repo-relative links for the website. docs/guide
// stays the single source of truth; the copies here are build products.
import { mkdir, readdir, readFile, rm, writeFile } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const source = join(here, "..", "..", "docs", "guide");
const target = join(here, "..", "src", "content", "docs", "guide");
const repoBlob = "https://github.com/HKUDS/DeepCode/blob/main";

function transform(name, raw) {
  // Title = the first H1; Starlight renders it, so drop the heading itself.
  const match = raw.match(/^# (.+)\n+/);
  if (!match) throw new Error(`${name}: expected a leading "# Title" heading`);
  const title = match[1];
  let body = raw.slice(match[0].length);

  // The bare directory link (../) → the docs tree on GitHub.
  body = body.replace(/\(\.\.\/\)/g, `(${repoBlob}/docs/)`);
  // Repo docs one level up: (../LOCAL_PLUGINS.md) → GitHub.
  body = body.replace(
    /\(\.\.\/([A-Za-z0-9_./-]+?)(#[a-z0-9-]+)?\)/g,
    (_, path, anchor) => `(${repoBlob}/docs/${path}${anchor ?? ""})`,
  );
  // The README pointer: (../../README.md#anchor) → GitHub. Runs before the
  // rule above cannot match it (that rule stops at the second "../").
  body = body.replace(
    new RegExp(`\\(${repoBlob}/docs/\\.\\./README\\.md(#[a-z0-9-]+)?\\)`, "g"),
    (_, anchor) => `(${repoBlob}/README.md${anchor ?? ""})`,
  );
  // Sibling guides (runs LAST — after every ../ form is already
  // resolved, so the relative output cannot be re-matched): (sessions.md) or (sessions.md#anchor) → relative site
  // routes, so they hold under any deployment base path. The index page
  // lives at /guide/, siblings at /guide/<slug>/ — one level deeper.
  const fromIndex = name === "README.md";
  body = body.replace(
    /\(([a-z-]+)\.md(#[a-z-]+)?\)/gi,
    (_, slug, anchor) => {
      if (slug.toLowerCase() === "readme")
        return `(${fromIndex ? "./" : "../"}${anchor ?? ""})`;
      return `(${fromIndex ? "" : "../"}${slug}/${anchor ?? ""})`;
    },
  );

  const front = ["---", `title: ${JSON.stringify(title)}`];
  if (name === "README.md") {
    front.push('slug: "guide"');
  }
  front.push("---", "", "");
  return front.join("\n") + body;
}

await rm(target, { recursive: true, force: true });
await mkdir(target, { recursive: true });
const names = (await readdir(source)).filter((n) => n.endsWith(".md")).sort();
if (names.length === 0) throw new Error(`no guides found in ${source}`);
for (const name of names) {
  const raw = await readFile(join(source, name), "utf8");
  const out = name === "README.md" ? "index.md" : name;
  await writeFile(join(target, out), transform(name, raw));
}
console.log(`synced ${names.length} guides → src/content/docs/guide/`);
