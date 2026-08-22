# DeepCode website

The project site: a hand-crafted landing at `/` and the teaching guides
under `/guide/`, built with Astro + Starlight.

```console
cd website
npm install
npm run dev        # local dev server
npm run build      # static site → dist/
```

The guide pages are **not** authored here. `scripts/sync-guides.mjs` copies
`../docs/guide/*.md` (the single source of truth) into
`src/content/docs/guide/` on every dev/build run, adding frontmatter and
rewriting repo-relative links for the web. Edit the guides in `docs/guide/`;
edit the landing in `src/pages/index.astro`.

Deploy: any static host serves `dist/` — Cloudflare Pages (build command
`npm run build`, output `dist`, root directory `website`) or GitHub Pages.
