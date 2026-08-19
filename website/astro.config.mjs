// The DeepCode website: a hand-crafted landing at `/` and the teaching
// guides under `/guide/`, rendered by Starlight from ../docs/guide (the
// single source of truth — see scripts/sync-guides.mjs).
import { defineConfig } from "astro/config";
import starlight from "@astrojs/starlight";

export default defineConfig({
  site: "https://hkuds.github.io",
  integrations: [
    starlight({
      title: "DeepCode",
      description: "Open agentic coding — guides taught from the source.",
      customCss: ["./src/styles/starlight-theme.css"],
      social: [
        {
          icon: "github",
          label: "GitHub",
          href: "https://github.com/HKUDS/DeepCode",
        },
      ],
      sidebar: [
        { label: "Start here", items: ["guide", "guide/getting-started"] },
        {
          label: "Daily driving",
          items: ["guide/the-tui", "guide/sessions", "guide/models"],
        },
        {
          label: "Compounding",
          items: ["guide/skills-and-memory", "guide/goals-and-headless"],
        },
      ],
    }),
  ],
});
